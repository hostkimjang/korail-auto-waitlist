from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from multiprocessing import get_context
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from queue import Empty
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rail_waitlist.config import get_settings
from rail_waitlist.domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    WatchStatus,
)
from rail_waitlist.outbox_management.models import OutboxEvent
from rail_waitlist.provider_account_management.application import (
    get_next_provider_credential_version,
    update_provider_auth_status,
    upsert_provider_account,
)
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.provider_account_management.schemas import (
    RailProviderAccountUpsert,
    RailProviderAuthStatus,
)
from rail_waitlist.provider_circuit.models import ProviderCircuit
from rail_waitlist.reservations.contracts import (
    ReservationRequest,
    ReservationResult,
)
from rail_waitlist.reservations.execution_application import (
    ReservationExecutionDependencies,
    ReservationExecutionTarget,
    execute_reservation,
)
from rail_waitlist.services import (
    add_outbox_event,
    apply_watch_transition,
    begin_reservation_attempt,
    complete_reservation_attempt,
    get_or_create_provider_circuit,
    record_reservation_confirmation,
)
from rail_waitlist.srt_sidecar.reservation import SRT_RESERVATION_SOURCE
from rail_waitlist.watch_management.models import ReservationAttempt, Watch, WatchCandidate

_ISOLATED_OPT_IN = "POSTGRES_ACCEPTANCE_ISOLATED"
_ISOLATED_DATABASE_PREFIX = "rail_waitlist_acceptance_"
_PROCESS_TIMEOUT_SECONDS = 20
_PROCESS_SHUTDOWN_SECONDS = 5
_SRT_HANDOFF_URL = "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"

ProcessMessage = dict[str, int | str | None]
AdapterMode = Literal["not_available", "late_payment"]
ProbePhase = Literal["claim", "result"]


@dataclass(frozen=True, slots=True)
class AcceptanceFixture:
    target: ReservationExecutionTarget

    @property
    def watch_id(self) -> str:
        return self.target.watch_id

    @property
    def candidate_id(self) -> str:
        return self.target.candidate_id


def _require_isolated_database() -> str:
    if os.environ.get(_ISOLATED_OPT_IN, "").strip().lower() != "true":
        raise RuntimeError(
            "isolated PostgreSQL acceptance opt-in is required before database mutation"
        )
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("database URL is not configured")
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "postgresql":
        raise RuntimeError("reservation credential acceptance requires PostgreSQL")
    database_name = (parsed_url.database or "").strip().lower()
    if not database_name.startswith(_ISOLATED_DATABASE_PREFIX):
        raise RuntimeError(
            "isolated PostgreSQL acceptance requires a dedicated acceptance database name"
        )
    return database_url


def _engine_and_factory(
    *, application_name: str | None = None
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    # Validate the effective URL before create_async_engine can open any connection.
    database_url = _require_isolated_database()
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if application_name is not None:
        engine_options.update(
            {
                "connect_args": {"server_settings": {"application_name": application_name}},
                "pool_size": 1,
                "max_overflow": 0,
            }
        )
    engine = create_async_engine(database_url, **engine_options)
    if engine.dialect.name != "postgresql":
        engine.sync_engine.dispose()
        raise RuntimeError("reservation credential acceptance requires PostgreSQL")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _put(
    messages: Queue[ProcessMessage],
    kind: str,
    *,
    value: int | None = None,
    backend_pid: int | None = None,
    application_name: str | None = None,
) -> None:
    messages.put(
        {
            "kind": kind,
            "pid": os.getpid(),
            "value": value,
            "backend_pid": backend_pid,
            "application_name": application_name,
        }
    )


def _put_error(messages: Queue[ProcessMessage], role: str) -> None:
    # Do not expose DSNs, credential material, owner tokens, or provider exceptions.
    _put(messages, f"{role}_error")


async def _next_message(messages: Queue[ProcessMessage]) -> ProcessMessage:
    try:
        return await asyncio.to_thread(messages.get, True, _PROCESS_TIMEOUT_SECONDS)
    except Empty as error:
        raise AssertionError("reservation acceptance process did not report in time") from error


def _require_process_message(
    message: ProcessMessage,
    expected: str,
    *,
    parent_pid: int,
) -> None:
    kind = message.get("kind")
    if isinstance(kind, str) and kind.endswith("_error"):
        raise AssertionError(f"{kind} during reservation acceptance")
    if kind != expected:
        raise AssertionError(f"expected {expected}, received {kind}")
    if message.get("pid") == parent_pid:
        raise AssertionError("reservation acceptance did not use an independent child process")


async def _join_or_terminate(process: Any) -> None:
    await asyncio.to_thread(process.join, _PROCESS_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.terminate()
        await asyncio.to_thread(process.join, _PROCESS_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, _PROCESS_SHUTDOWN_SECONDS)
    if process.is_alive():
        raise AssertionError("reservation acceptance child did not stop after bounded cleanup")
    if process.exitcode != 0:
        raise AssertionError("reservation acceptance child exited unsuccessfully")


async def _update_provider_auth_status_in_transaction(
    session: AsyncSession,
    provider: Provider,
    status: RailProviderAuthStatus,
    *,
    expected_credential_version: int,
) -> None:
    await update_provider_auth_status(
        session,
        provider,
        status,
        expected_credential_version=expected_credential_version,
        commit=False,
    )


def _dependencies(
    factory: async_sessionmaker[AsyncSession],
) -> ReservationExecutionDependencies:
    return ReservationExecutionDependencies(
        session_factory=factory,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        apply_watch_transition=apply_watch_transition,
        begin_reservation_attempt=begin_reservation_attempt,
        add_outbox_event=add_outbox_event,
        complete_reservation_attempt=complete_reservation_attempt,
        record_reservation_confirmation=record_reservation_confirmation,
        update_provider_auth_status=_update_provider_auth_status_in_transaction,
        provider_call_errors=(RuntimeError, ValueError),
        srt_exact_reservation_source=SRT_RESERVATION_SOURCE,
    )


class DeterministicReservationAdapter:
    provider = Provider.SRT

    def __init__(
        self,
        *,
        mode: AdapterMode,
        messages: Queue[ProcessMessage],
        release_result: Event | None,
        application_name: str,
    ) -> None:
        self._mode = mode
        self._messages = messages
        self._release_result = release_result
        self._application_name = application_name

    async def reserve_once(self, request: ReservationRequest) -> ReservationResult:
        credential_version = request.expected_credential_version
        if credential_version is None:
            raise RuntimeError("reservation request did not carry a credential generation")
        _put(
            self._messages,
            "provider_called",
            value=credential_version,
            application_name=self._application_name,
        )
        if self._release_result is not None and not await asyncio.to_thread(
            self._release_result.wait,
            _PROCESS_TIMEOUT_SECONDS,
        ):
            raise TimeoutError("late result release was not signaled")
        observed_at = datetime.now(UTC)
        if self._mode == "late_payment":
            return ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source=SRT_RESERVATION_SOURCE,
                observed_at=observed_at,
                credential_version=credential_version,
                payment_deadline=observed_at + timedelta(minutes=20),
                official_handoff_url=_SRT_HANDOFF_URL,
            )
        return ReservationResult(
            outcome=ReservationOutcome.NOT_AVAILABLE,
            source="postgres-acceptance",
            observed_at=observed_at,
            credential_version=credential_version,
        )

    async def confirm_reservation(self, target: object) -> None:
        del target
        raise AssertionError("deterministic acceptance result must not require confirmation I/O")


async def _execute_process(
    target: ReservationExecutionTarget,
    *,
    mode: AdapterMode,
    start_requested: Event,
    release_result: Event | None,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
        if not isinstance(backend_pid, int):
            raise RuntimeError("reservation process did not report a PostgreSQL backend PID")
        _put(
            messages,
            "execution_ready",
            backend_pid=backend_pid,
            application_name=application_name,
        )
        if not await asyncio.to_thread(start_requested.wait, _PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("reservation execution start was not released")
        adapter = DeterministicReservationAdapter(
            mode=mode,
            messages=messages,
            release_result=release_result,
            application_name=application_name,
        )
        await execute_reservation(adapter, target, dependencies=_dependencies(factory))
        _put(messages, "execution_done", application_name=application_name)
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "execution")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_execution_process(
    target: ReservationExecutionTarget,
    mode: AdapterMode,
    start_requested: Event,
    release_result: Event | None,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _execute_process(
            target,
            mode=mode,
            start_requested=start_requested,
            release_result=release_result,
            application_name=application_name,
            messages=messages,
        )
    )


async def _save_credentials_process(
    verified_credential_version: int,
    *,
    start_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
        if not isinstance(backend_pid, int):
            raise RuntimeError("credential process did not report a PostgreSQL backend PID")
        _put(
            messages,
            "credential_ready",
            backend_pid=backend_pid,
            application_name=application_name,
        )
        if not await asyncio.to_thread(start_requested.wait, _PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("credential save start was not released")
        async with factory() as session:
            await upsert_provider_account(
                session,
                Provider.SRT,
                RailProviderAccountUpsert(
                    login_method="membership_number",
                    login_id=f"acceptance-generation-{verified_credential_version}",
                    password=f"acceptance-password-{verified_credential_version}",
                    enabled=True,
                ),
                verified_credential_version=verified_credential_version,
            )
        _put(
            messages,
            "credential_done",
            value=verified_credential_version,
            application_name=application_name,
        )
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "credential")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_credential_process(
    verified_credential_version: int,
    start_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _save_credentials_process(
            verified_credential_version,
            start_requested=start_requested,
            application_name=application_name,
            messages=messages,
        )
    )


async def _hold_watch_lock_process(
    watch_id: str,
    *,
    release_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            locked_watch_id = await session.scalar(
                select(Watch.id).where(Watch.id == watch_id).with_for_update()
            )
            if not isinstance(backend_pid, int) or locked_watch_id != watch_id:
                raise RuntimeError("watch lock holder did not acquire its fixture row")
            _put(
                messages,
                "holder_locked",
                backend_pid=backend_pid,
                application_name=application_name,
            )
            if not await asyncio.to_thread(
                release_requested.wait,
                _PROCESS_TIMEOUT_SECONDS,
            ):
                raise TimeoutError("watch lock holder was not released")
            await session.commit()
        _put(messages, "holder_done", application_name=application_name)
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "holder")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_watch_lock_holder(
    watch_id: str,
    release_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _hold_watch_lock_process(
            watch_id,
            release_requested=release_requested,
            application_name=application_name,
            messages=messages,
        )
    )


async def _hold_account_lock_process(
    *,
    release_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            account_id = await session.scalar(
                select(RailProviderAccount.id)
                .where(RailProviderAccount.provider == Provider.SRT)
                .with_for_update()
            )
            if not isinstance(backend_pid, int) or account_id is None:
                raise RuntimeError("account lock holder did not acquire its fixture row")
            _put(
                messages,
                "account_holder_locked",
                backend_pid=backend_pid,
                application_name=application_name,
            )
            if not await asyncio.to_thread(
                release_requested.wait,
                _PROCESS_TIMEOUT_SECONDS,
            ):
                raise TimeoutError("account lock holder was not released")
            await session.commit()
        _put(messages, "account_holder_done", application_name=application_name)
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "account_holder")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_account_lock_holder(
    release_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _hold_account_lock_process(
            release_requested=release_requested,
            application_name=application_name,
            messages=messages,
        )
    )


async def _probe_target_rows_process(
    target: ReservationExecutionTarget,
    *,
    phase: ProbePhase,
    expected_attempt_count: int,
    start_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
            if not isinstance(backend_pid, int):
                raise RuntimeError("target-row probe did not report a PostgreSQL backend PID")
            _put(
                messages,
                f"{phase}_probe_ready",
                backend_pid=backend_pid,
                application_name=application_name,
            )
            if not await asyncio.to_thread(start_requested.wait, _PROCESS_TIMEOUT_SECONDS):
                raise TimeoutError("target-row probe start was not released")
            watch_id = await session.scalar(
                select(Watch.id).where(Watch.id == target.watch_id).with_for_update(nowait=True)
            )
            candidate_id = await session.scalar(
                select(WatchCandidate.id)
                .where(WatchCandidate.id == target.candidate_id)
                .with_for_update(of=WatchCandidate, nowait=True)
            )
            attempt_ids = list(
                (
                    await session.scalars(
                        select(ReservationAttempt.id)
                        .where(ReservationAttempt.candidate_id == target.candidate_id)
                        .order_by(ReservationAttempt.id)
                        .with_for_update(nowait=True)
                    )
                ).all()
            )
            if watch_id != target.watch_id or candidate_id != target.candidate_id:
                raise RuntimeError("target-row probe could not lock its watch/candidate")
            if len(attempt_ids) != expected_attempt_count:
                raise RuntimeError("target-row probe observed an unexpected attempt count")
            _put(
                messages,
                f"{phase}_probe_done",
                value=len(attempt_ids),
                application_name=application_name,
            )
            await session.rollback()
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, f"{phase}_probe")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_target_rows_probe(
    target: ReservationExecutionTarget,
    phase: ProbePhase,
    expected_attempt_count: int,
    start_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _probe_target_rows_process(
            target,
            phase=phase,
            expected_attempt_count=expected_attempt_count,
            start_requested=start_requested,
            application_name=application_name,
            messages=messages,
        )
    )


async def _seed_account(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        next_version = await get_next_provider_credential_version(session, Provider.SRT)
        await upsert_provider_account(
            session,
            Provider.SRT,
            RailProviderAccountUpsert(
                login_method="membership_number",
                login_id="acceptance-initial-account",
                password="acceptance-initial-password",
                enabled=True,
            ),
            verified_credential_version=next_version,
        )
    return next_version


async def _seed_fixture(
    factory: async_sessionmaker[AsyncSession],
    *,
    prefix: str,
    now: datetime,
) -> AcceptanceFixture:
    async with factory.begin() as session:
        circuit = await get_or_create_provider_circuit(session, Provider.SRT, lock=True)
        circuit.state = ProviderCircuitState.CLOSED
        circuit.reason = None
        circuit.opened_at = None
        circuit.cooldown_until = None
        circuit.manual_resume_required = False
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            origin_node_id="ACCEPTANCE-DAEJEON",
            destination="수서",
            destination_node_id="ACCEPTANCE-SUSEO",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"pg-reservation-{prefix}-{uuid4().hex[:20]}",
        )
        candidate = WatchCandidate(
            train_number=f"SRT-{uuid4().hex[:8]}",
            departure_at=now + timedelta(days=1),
            arrival_at=now + timedelta(days=1, hours=2),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        return AcceptanceFixture(
            ReservationExecutionTarget(
                watch_id=watch.id,
                candidate_id=candidate.id,
                provider=Provider.SRT,
                origin=watch.origin,
                destination=watch.destination,
                origin_node_id=watch.origin_node_id or "",
                destination_node_id=watch.destination_node_id or "",
                train_number=candidate.train_number,
                departure_at=candidate.departure_at,
                arrival_at=candidate.arrival_at,
                seat_class=SeatClass.STANDARD.value,
                passenger_count=watch.passenger_count,
                reservation_episode_key=f"availability:{prefix}",
            )
        )


async def _seed_auth_required_watch(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> str:
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            origin_node_id="ACCEPTANCE-DAEJEON",
            destination="수서",
            destination_node_id="ACCEPTANCE-SUSEO",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key=f"pg-lock-order-auth-{uuid4().hex[:24]}",
        )
        watch.candidates.append(
            WatchCandidate(
                train_number=f"SRT-{uuid4().hex[:8]}",
                departure_at=now + timedelta(days=1),
                arrival_at=now + timedelta(days=1, hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="failed",
            )
        )
        session.add(watch)
        await session.flush()
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.AUTH_REQUIRED,
            reason="reservation_auth_required",
        )
        await session.commit()
        return watch.id


async def _collect_ready(
    messages: Queue[ProcessMessage],
    *,
    expected_kinds: tuple[str, ...],
) -> tuple[set[int], set[int]]:
    parent_pid = os.getpid()
    process_pids: set[int] = set()
    backend_pids: set[int] = set()
    remaining = list(expected_kinds)
    while remaining:
        message = await _next_message(messages)
        kind = message.get("kind")
        if not isinstance(kind, str) or kind not in remaining:
            if isinstance(kind, str) and kind.endswith("_error"):
                raise AssertionError(f"{kind} during reservation acceptance")
            raise AssertionError(f"unexpected readiness event ({kind})")
        _require_process_message(message, kind, parent_pid=parent_pid)
        process_pid = message.get("pid")
        backend_pid = message.get("backend_pid")
        if not isinstance(process_pid, int) or not isinstance(backend_pid, int):
            raise AssertionError("process readiness identity was incomplete")
        process_pids.add(process_pid)
        backend_pids.add(backend_pid)
        remaining.remove(kind)
    return process_pids, backend_pids


async def _verify_same_episode_single_provider_owner(
    factory: async_sessionmaker[AsyncSession],
    *,
    fixture: AcceptanceFixture,
) -> None:
    context = get_context("spawn")
    start_requested = context.Event()
    messages: Queue[ProcessMessage] = context.Queue()
    names = [f"reservation-claim-{index}-{uuid4().hex[:12]}" for index in range(2)]
    processes = [
        context.Process(
            target=_run_execution_process,
            args=(
                fixture.target,
                "not_available",
                start_requested,
                None,
                name,
                messages,
            ),
        )
        for name in names
    ]
    started: list[Any] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        process_pids, backend_pids = await _collect_ready(
            messages,
            expected_kinds=("execution_ready", "execution_ready"),
        )
        if len(process_pids) != 2 or len(backend_pids) != 2:
            raise AssertionError("concurrent reservation workers were not process/backend isolated")
        start_requested.set()
        done = 0
        provider_calls = 0
        while done < 2:
            message = await _next_message(messages)
            kind = message.get("kind")
            if isinstance(kind, str) and kind.endswith("_error"):
                raise AssertionError(f"{kind} during reservation acceptance")
            if kind == "provider_called":
                provider_calls += 1
            elif kind == "execution_done":
                done += 1
            else:
                raise AssertionError(f"unexpected reservation concurrency event ({kind})")
        if provider_calls != 1:
            raise AssertionError("one availability episode did not have exactly one provider owner")
    finally:
        start_requested.set()
        for process in started:
            await _join_or_terminate(process)
        messages.close()
        messages.join_thread()

    async with factory() as session:
        attempt_count = await session.scalar(
            select(func.count())
            .select_from(ReservationAttempt)
            .where(ReservationAttempt.candidate_id == fixture.candidate_id)
        )
        result_event_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == fixture.watch_id,
                OutboxEvent.event_type == "watch.reservation_result",
            )
        )
    if attempt_count != 1 or result_event_count != 1:
        raise AssertionError("single provider ownership did not persist one attempt/result")


async def _current_credential_version(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        version = await session.scalar(
            select(RailProviderAccount.credential_version).where(
                RailProviderAccount.provider == Provider.SRT
            )
        )
    if not isinstance(version, int):
        raise AssertionError("acceptance provider account generation is missing")
    return version


async def _wait_for_backend_lock(
    factory: async_sessionmaker[AsyncSession],
    *,
    application_name: str,
    backend_pid: int,
) -> None:
    statement = text(
        """
        SELECT
            activity.wait_event_type,
            EXISTS (
                SELECT 1
                FROM pg_locks AS waiting_lock
                WHERE waiting_lock.pid = activity.pid
                  AND waiting_lock.granted IS FALSE
            ) AS has_ungranted_lock
        FROM pg_stat_activity AS activity
        WHERE activity.pid = :backend_pid
          AND activity.application_name = :application_name
        """
    )
    deadline = asyncio.get_running_loop().time() + _PROCESS_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        async with factory() as session:
            row = (
                await session.execute(
                    statement,
                    {
                        "backend_pid": backend_pid,
                        "application_name": application_name,
                    },
                )
            ).one_or_none()
            await session.rollback()
        if row is not None and row.wait_event_type == "Lock" and row.has_ungranted_lock:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{application_name} did not enter the expected PostgreSQL lock wait")


async def _verify_login_save_reservation_lock_order(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    cleanup_watch_ids: list[str],
) -> None:
    context = get_context("spawn")
    fixture = await _seed_fixture(
        factory,
        prefix=f"lock-order-{uuid4().hex[:8]}",
        now=now,
    )
    auth_watch_id = await _seed_auth_required_watch(factory, now=now)
    cleanup_watch_ids.extend([fixture.watch_id, auth_watch_id])
    next_version = await _current_credential_version(factory) + 1
    holder_release = context.Event()
    credential_start = context.Event()
    execution_start = context.Event()
    probe_start = context.Event()
    messages: Queue[ProcessMessage] = context.Queue()
    holder_name = f"watch-holder-{uuid4().hex[:10]}"
    credential_name = f"credential-order-{uuid4().hex[:10]}"
    execution_name = f"reservation-order-{uuid4().hex[:10]}"
    probe_name = f"claim-probe-{uuid4().hex[:10]}"
    holder = context.Process(
        target=_run_watch_lock_holder,
        args=(auth_watch_id, holder_release, holder_name, messages),
    )
    credential = context.Process(
        target=_run_credential_process,
        args=(next_version, credential_start, credential_name, messages),
    )
    execution = context.Process(
        target=_run_execution_process,
        args=(
            fixture.target,
            "not_available",
            execution_start,
            None,
            execution_name,
            messages,
        ),
    )
    probe = context.Process(
        target=_run_target_rows_probe,
        args=(fixture.target, "claim", 0, probe_start, probe_name, messages),
    )
    started: list[Any] = []
    process_pids: set[int] = set()
    backend_pids: set[int] = set()
    try:
        holder.start()
        started.append(holder)
        holder_locked = await _next_message(messages)
        _require_process_message(holder_locked, "holder_locked", parent_pid=os.getpid())
        holder_process_pid = holder_locked.get("pid")
        holder_backend_pid = holder_locked.get("backend_pid")
        if not isinstance(holder_process_pid, int) or not isinstance(holder_backend_pid, int):
            raise AssertionError("watch holder backend identity was incomplete")
        process_pids.add(holder_process_pid)
        backend_pids.add(holder_backend_pid)

        credential.start()
        started.append(credential)
        credential_ready = await _next_message(messages)
        _require_process_message(
            credential_ready,
            "credential_ready",
            parent_pid=os.getpid(),
        )
        credential_process_pid = credential_ready.get("pid")
        credential_backend_pid = credential_ready.get("backend_pid")
        if not isinstance(credential_process_pid, int) or not isinstance(
            credential_backend_pid, int
        ):
            raise AssertionError("credential backend identity was incomplete")
        process_pids.add(credential_process_pid)
        backend_pids.add(credential_backend_pid)
        credential_start.set()
        # The production upsert now owns the account row and waits on the AUTH_REQUIRED watch.
        await _wait_for_backend_lock(
            factory,
            application_name=credential_name,
            backend_pid=credential_backend_pid,
        )

        execution.start()
        started.append(execution)
        execution_ready = await _next_message(messages)
        _require_process_message(
            execution_ready,
            "execution_ready",
            parent_pid=os.getpid(),
        )
        execution_process_pid = execution_ready.get("pid")
        execution_backend_pid = execution_ready.get("backend_pid")
        if not isinstance(execution_process_pid, int) or not isinstance(execution_backend_pid, int):
            raise AssertionError("reservation backend identity was incomplete")
        process_pids.add(execution_process_pid)
        backend_pids.add(execution_backend_pid)
        if len(process_pids) != 3 or len(backend_pids) != 3:
            raise AssertionError("lock-order chain did not use independent workers/backends")
        execution_start.set()
        # Reservation must wait on the account before it can lock its watch/candidate rows.
        await _wait_for_backend_lock(
            factory,
            application_name=execution_name,
            backend_pid=execution_backend_pid,
        )

        probe.start()
        started.append(probe)
        probe_ready = await _next_message(messages)
        _require_process_message(probe_ready, "claim_probe_ready", parent_pid=os.getpid())
        probe_process_pid = probe_ready.get("pid")
        probe_backend_pid = probe_ready.get("backend_pid")
        if not isinstance(probe_process_pid, int) or not isinstance(probe_backend_pid, int):
            raise AssertionError("claim probe backend identity was incomplete")
        process_pids.add(probe_process_pid)
        backend_pids.add(probe_backend_pid)
        if len(process_pids) != 4 or len(backend_pids) != 4:
            raise AssertionError("claim lock-order proof did not use independent workers/backends")
        probe_start.set()
        probe_done = await _next_message(messages)
        _require_process_message(probe_done, "claim_probe_done", parent_pid=os.getpid())
        if probe_done.get("value") != 0:
            raise AssertionError("claim probe observed an attempt before the durable claim")

        holder_release.set()
        completed: set[str] = set()
        provider_calls = 0
        while completed != {"holder_done", "credential_done", "execution_done"}:
            message = await _next_message(messages)
            kind = message.get("kind")
            if isinstance(kind, str) and kind.endswith("_error"):
                raise AssertionError(f"{kind} during lock-order acceptance")
            if kind == "provider_called":
                provider_calls += 1
            elif kind in {"holder_done", "credential_done", "execution_done"}:
                completed.add(kind)
            else:
                raise AssertionError(f"unexpected lock-order event ({kind})")
        if provider_calls != 1:
            raise AssertionError("lock-order reservation did not invoke one provider owner")
    finally:
        holder_release.set()
        credential_start.set()
        execution_start.set()
        probe_start.set()
        for process in started:
            await _join_or_terminate(process)
        messages.close()
        messages.join_thread()
    if await _current_credential_version(factory) != next_version:
        raise AssertionError("concurrent credential save did not persist its next generation")


async def _verify_result_transaction_lock_order(
    factory: async_sessionmaker[AsyncSession],
    *,
    fixture: AcceptanceFixture,
) -> None:
    context = get_context("spawn")
    execution_start = context.Event()
    provider_release = context.Event()
    account_release = context.Event()
    probe_start = context.Event()
    messages: Queue[ProcessMessage] = context.Queue()
    execution_name = f"result-order-{uuid4().hex[:10]}"
    account_name = f"result-account-holder-{uuid4().hex[:10]}"
    probe_name = f"result-probe-{uuid4().hex[:10]}"
    execution = context.Process(
        target=_run_execution_process,
        args=(
            fixture.target,
            "not_available",
            execution_start,
            provider_release,
            execution_name,
            messages,
        ),
    )
    account_holder = context.Process(
        target=_run_account_lock_holder,
        args=(account_release, account_name, messages),
    )
    probe = context.Process(
        target=_run_target_rows_probe,
        args=(fixture.target, "result", 1, probe_start, probe_name, messages),
    )
    started: list[Any] = []
    process_pids: set[int] = set()
    backend_pids: set[int] = set()
    try:
        execution.start()
        started.append(execution)
        execution_ready = await _next_message(messages)
        _require_process_message(
            execution_ready,
            "execution_ready",
            parent_pid=os.getpid(),
        )
        execution_process_pid = execution_ready.get("pid")
        execution_backend_pid = execution_ready.get("backend_pid")
        if not isinstance(execution_process_pid, int) or not isinstance(execution_backend_pid, int):
            raise AssertionError("result execution identity was incomplete")
        process_pids.add(execution_process_pid)
        backend_pids.add(execution_backend_pid)
        execution_start.set()
        provider_called = await _next_message(messages)
        _require_process_message(provider_called, "provider_called", parent_pid=os.getpid())

        account_holder.start()
        started.append(account_holder)
        account_locked = await _next_message(messages)
        _require_process_message(
            account_locked,
            "account_holder_locked",
            parent_pid=os.getpid(),
        )
        account_process_pid = account_locked.get("pid")
        account_backend_pid = account_locked.get("backend_pid")
        if not isinstance(account_process_pid, int) or not isinstance(account_backend_pid, int):
            raise AssertionError("result account-holder identity was incomplete")
        process_pids.add(account_process_pid)
        backend_pids.add(account_backend_pid)

        provider_release.set()
        await _wait_for_backend_lock(
            factory,
            application_name=execution_name,
            backend_pid=execution_backend_pid,
        )

        probe.start()
        started.append(probe)
        probe_ready = await _next_message(messages)
        _require_process_message(probe_ready, "result_probe_ready", parent_pid=os.getpid())
        probe_process_pid = probe_ready.get("pid")
        probe_backend_pid = probe_ready.get("backend_pid")
        if not isinstance(probe_process_pid, int) or not isinstance(probe_backend_pid, int):
            raise AssertionError("result probe identity was incomplete")
        process_pids.add(probe_process_pid)
        backend_pids.add(probe_backend_pid)
        if len(process_pids) != 3 or len(backend_pids) != 3:
            raise AssertionError("result lock-order proof did not use independent workers/backends")
        probe_start.set()
        probe_done = await _next_message(messages)
        _require_process_message(probe_done, "result_probe_done", parent_pid=os.getpid())
        if probe_done.get("value") != 1:
            raise AssertionError("result probe did not lock the durable attempt row")

        account_release.set()
        completed: set[str] = set()
        while completed != {"account_holder_done", "execution_done"}:
            message = await _next_message(messages)
            kind = message.get("kind")
            if isinstance(kind, str) and kind.endswith("_error"):
                raise AssertionError(f"{kind} during result lock-order acceptance")
            if kind in {"account_holder_done", "execution_done"}:
                completed.add(kind)
            else:
                raise AssertionError(f"unexpected result lock-order event ({kind})")
    finally:
        execution_start.set()
        provider_release.set()
        account_release.set()
        probe_start.set()
        for process in started:
            await _join_or_terminate(process)
        messages.close()
        messages.join_thread()

    async with factory() as session:
        attempt = await session.scalar(
            select(ReservationAttempt).where(
                ReservationAttempt.candidate_id == fixture.candidate_id
            )
        )
        watch = await session.get(Watch, fixture.watch_id)
        candidate = await session.get(WatchCandidate, fixture.candidate_id)
    if attempt is None or attempt.outcome is not ReservationOutcome.NOT_AVAILABLE:
        raise AssertionError("result lock-order fixture did not complete its reservation attempt")
    if watch is None or watch.status is not WatchStatus.WATCHING:
        raise AssertionError("result lock-order fixture did not resume observation")
    if candidate is None or candidate.state != "observed":
        raise AssertionError("result lock-order fixture candidate did not complete normally")


async def _state_snapshot(
    factory: async_sessionmaker[AsyncSession],
    fixture: AcceptanceFixture,
) -> tuple[object, ...]:
    async with factory() as session:
        watch = await session.get(Watch, fixture.watch_id)
        candidate = await session.get(WatchCandidate, fixture.candidate_id)
        attempts = list(
            (
                await session.scalars(
                    select(ReservationAttempt)
                    .where(ReservationAttempt.candidate_id == fixture.candidate_id)
                    .order_by(ReservationAttempt.attempt_sequence)
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == fixture.watch_id)
                    .order_by(OutboxEvent.created_at, OutboxEvent.id)
                )
            ).all()
        )
    if watch is None or candidate is None:
        raise AssertionError("late-result acceptance fixture disappeared")
    return (
        watch.status.value,
        watch.reservation_attempted,
        watch.payment_deadline,
        watch.official_booking_url,
        candidate.state,
        tuple(
            (
                attempt.id,
                attempt.outcome.value,
                attempt.finished_at,
                attempt.payment_deadline,
                attempt.official_handoff_url,
                attempt.credential_version,
                attempt.confirmation_outcome,
                attempt.confirmation_source,
                attempt.confirmation_observed_at,
            )
            for attempt in attempts
        ),
        tuple(
            (
                event.event_type,
                event.dedupe_key,
                json.dumps(event.payload, sort_keys=True, ensure_ascii=False),
            )
            for event in events
        ),
    )


async def _verify_late_result_fenced_after_credential_replacement(
    factory: async_sessionmaker[AsyncSession],
    *,
    fixture: AcceptanceFixture,
) -> None:
    context = get_context("spawn")
    execution_start = context.Event()
    result_release = context.Event()
    credential_start = context.Event()
    messages: Queue[ProcessMessage] = context.Queue()
    execution_name = f"late-result-{uuid4().hex[:12]}"
    credential_name = f"late-credential-{uuid4().hex[:12]}"
    old_version = await _current_credential_version(factory)
    reservation = context.Process(
        target=_run_execution_process,
        args=(
            fixture.target,
            "late_payment",
            execution_start,
            result_release,
            execution_name,
            messages,
        ),
    )
    credential = context.Process(
        target=_run_credential_process,
        args=(old_version + 1, credential_start, credential_name, messages),
    )
    started: list[Any] = []
    try:
        reservation.start()
        started.append(reservation)
        ready = await _next_message(messages)
        _require_process_message(ready, "execution_ready", parent_pid=os.getpid())
        execution_start.set()
        provider_called = await _next_message(messages)
        _require_process_message(provider_called, "provider_called", parent_pid=os.getpid())
        if provider_called.get("value") != old_version:
            raise AssertionError("late provider call did not use the original generation")

        credential.start()
        started.append(credential)
        credential_ready = await _next_message(messages)
        _require_process_message(
            credential_ready,
            "credential_ready",
            parent_pid=os.getpid(),
        )
        credential_start.set()
        credential_done = await _next_message(messages)
        _require_process_message(
            credential_done,
            "credential_done",
            parent_pid=os.getpid(),
        )
        if credential_done.get("value") != old_version + 1:
            raise AssertionError("credential replacement did not advance exactly one generation")
        before = await _state_snapshot(factory, fixture)
        if before[0] != WatchStatus.RESERVING.value or before[2] is not None:
            raise AssertionError("late-result fixture was not durably claimed before replacement")

        result_release.set()
        execution_done = await _next_message(messages)
        _require_process_message(
            execution_done,
            "execution_done",
            parent_pid=os.getpid(),
        )
        after = await _state_snapshot(factory, fixture)
        if after != before:
            raise AssertionError(
                "an old credential generation wrote watch/attempt/outbox/payment state"
            )
        if await _current_credential_version(factory) != old_version + 1:
            raise AssertionError("late result changed the replacement credential generation")
    finally:
        execution_start.set()
        credential_start.set()
        result_release.set()
        for process in started:
            await _join_or_terminate(process)
        messages.close()
        messages.join_thread()


async def _cleanup(
    factory: async_sessionmaker[AsyncSession],
    *,
    watch_ids: list[str],
) -> None:
    async with factory.begin() as session:
        await session.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(watch_ids)))
        await session.execute(delete(Watch).where(Watch.id.in_(watch_ids)))
        await session.execute(
            delete(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        await session.execute(
            delete(ProviderCircuit).where(ProviderCircuit.provider == Provider.SRT)
        )


async def verify() -> None:
    _require_isolated_database()
    engine: AsyncEngine | None = None
    watch_ids: list[str] = []
    try:
        engine, factory = _engine_and_factory()
        await _seed_account(factory)
        started_at = datetime.now(UTC)
        same_episode = await _seed_fixture(
            factory,
            prefix=f"same-episode-{uuid4().hex[:8]}",
            now=started_at,
        )
        watch_ids.append(same_episode.watch_id)
        await _verify_same_episode_single_provider_owner(factory, fixture=same_episode)
        await _verify_login_save_reservation_lock_order(
            factory,
            now=started_at + timedelta(minutes=1),
            cleanup_watch_ids=watch_ids,
        )
        result_order = await _seed_fixture(
            factory,
            prefix=f"result-order-{uuid4().hex[:8]}",
            now=started_at + timedelta(minutes=2),
        )
        watch_ids.append(result_order.watch_id)
        await _verify_result_transaction_lock_order(factory, fixture=result_order)
        late_result = await _seed_fixture(
            factory,
            prefix=f"late-result-{uuid4().hex[:8]}",
            now=started_at + timedelta(minutes=3),
        )
        watch_ids.append(late_result.watch_id)
        await _verify_late_result_fenced_after_credential_replacement(
            factory,
            fixture=late_result,
        )
    finally:
        if engine is not None:
            try:
                await _cleanup(factory, watch_ids=watch_ids)
            finally:
                await engine.dispose()


if __name__ == "__main__":
    _require_isolated_database()
    asyncio.run(verify())
    print("PostgreSQL 예약 episode·credential generation fencing 수용 검증 통과")
