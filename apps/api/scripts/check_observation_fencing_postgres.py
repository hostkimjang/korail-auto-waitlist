from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from multiprocessing import get_context
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from queue import Empty
from typing import Any
from uuid import uuid4

from check_execution_lease_fencing_postgres import (
    _engine_and_factory,
    _join_or_terminate,
    _wait_for_takeover_lock,
)
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from rail_waitlist.config import get_settings
from rail_waitlist.domain import (
    Provider,
    ProviderCircuitState,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.observations.contracts import SeatObservationResult
from rail_waitlist.observations.cycle_application import (
    finish_observation_cycle,
    latest_observation_fingerprint,
)
from rail_waitlist.observations.group_application import (
    ObservationGroupDependencies,
    ObservationTarget,
    apply_current_circuit_to_watch,
    defer_watch_group_observation,
    persist_observation_cycle,
    prepare_watch,
    process_watch_group_observation,
    provider_circuit_is_closed,
)
from rail_waitlist.outbox import add_outbox_event
from rail_waitlist.outbox_management.models import OutboxEvent
from rail_waitlist.provider_circuit.models import ProviderCircuit
from rail_waitlist.provider_execution.models import ProviderExecutionLease
from rail_waitlist.provider_execution_lease import (
    ExecutionLeaseGrant,
    ProviderExecutionLeaseService,
    lock_execution_lease_current,
)
from rail_waitlist.provider_registry.contracts import ProviderCapabilities
from rail_waitlist.reservations.attempt_policy import is_confirmed_absent_retry_source
from rail_waitlist.services import (
    apply_watch_transition,
    get_or_create_provider_circuit,
    record_seat_observation,
)
from rail_waitlist.watch_management.models import (
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)

_ISOLATED_OPT_IN = "POSTGRES_ACCEPTANCE_ISOLATED"
_ISOLATED_DATABASE_PREFIX = "rail_waitlist_acceptance_"
_PROCESS_TIMEOUT_SECONDS = 15
_DEADLOCK_ROUNDS = 8

ProcessMessage = dict[str, int | str | None]


@dataclass(frozen=True, slots=True)
class WatchFixture:
    watch_id: str
    candidate_id: str


@dataclass(frozen=True, slots=True)
class StaleFixtures:
    prepare: WatchFixture
    defer: WatchFixture
    persist: WatchFixture
    circuit: WatchFixture

    @property
    def watch_ids(self) -> list[str]:
        return [
            self.prepare.watch_id,
            self.defer.watch_id,
            self.persist.watch_id,
            self.circuit.watch_id,
        ]

    @property
    def candidate_ids(self) -> list[str]:
        return [
            self.prepare.candidate_id,
            self.defer.candidate_id,
            self.persist.candidate_id,
            self.circuit.candidate_id,
        ]


class DeterministicObservationAdapter:
    provider = Provider.SRT

    def __init__(self, observed_at: datetime) -> None:
        self.observed_at = observed_at
        self.observe_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=False,
        )

    async def observation_deferred_until(self) -> datetime | None:
        return None

    async def observe_seats(self, request: Any) -> list[SeatObservationResult]:
        self.observe_calls += 1
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=SeatObservationStatus.SOLD_OUT,
                source="postgres-acceptance",
                observed_at=self.observed_at,
                fresh_until=self.observed_at + timedelta(minutes=5),
            )
        ]


def _require_isolated_database_opt_in() -> None:
    if os.environ.get(_ISOLATED_OPT_IN, "").strip().lower() != "true":
        raise RuntimeError(
            "isolated PostgreSQL acceptance opt-in is required before database mutation"
        )
    database_url = get_settings().database_url
    database_name = (
        "" if database_url is None else (make_url(database_url).database or "").strip().lower()
    )
    if not database_name.startswith(_ISOLATED_DATABASE_PREFIX):
        raise RuntimeError(
            "isolated PostgreSQL acceptance requires a dedicated acceptance database name"
        )


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
    # Only a categorical role crosses the process boundary. Exception text, DSNs,
    # owner tokens, provider payloads, and credentials must not reach IPC or stdout.
    _put(messages, f"{role}_error")


async def _next_message(messages: Queue[ProcessMessage]) -> ProcessMessage:
    try:
        return await asyncio.to_thread(messages.get, True, _PROCESS_TIMEOUT_SECONDS)
    except Empty as error:
        raise AssertionError("observation acceptance process did not report in time") from error


def _require_kind(message: ProcessMessage, expected: str, *, parent_pid: int) -> None:
    kind = message.get("kind")
    if isinstance(kind, str) and kind.endswith("_error"):
        raise AssertionError(f"{kind} during observation acceptance")
    if kind != expected:
        raise AssertionError(f"expected {expected}, received {kind}")
    if message.get("pid") == parent_pid:
        raise AssertionError("observation acceptance did not use an independent process")


def _dependencies(
    factory: async_sessionmaker[AsyncSession],
    service: ProviderExecutionLeaseService,
    *,
    now: datetime,
    locked_lease_current=lock_execution_lease_current,
    reservation_counter: list[int] | None = None,
) -> ObservationGroupDependencies:
    async def reserve_winner(_target: ObservationTarget) -> None:
        if reservation_counter is not None:
            reservation_counter[0] += 1

    return ObservationGroupDependencies(
        session_factory=factory,
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        latest_observation_fingerprint=latest_observation_fingerprint,
        record_seat_observation=record_seat_observation,
        finish_observation_cycle=finish_observation_cycle,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        reserve_winner=reserve_winner,
        lease_is_current=service.is_current,
        lease_is_current_in_session=locked_lease_current,
        provider_call_errors=(RuntimeError, ValueError),
        now=lambda: now,
    )


def _target(fixture: WatchFixture, now: datetime) -> ObservationTarget:
    return ObservationTarget(
        watch_id=fixture.watch_id,
        candidate_id=fixture.candidate_id,
        provider=Provider.SRT,
        origin="서울",
        destination="부산",
        origin_node_id="ACCEPTANCE-SEOUL",
        destination_node_id="ACCEPTANCE-BUSAN",
        train_number=f"SRT-{fixture.watch_id[-6:]}",
        departure_at=now + timedelta(days=1),
        arrival_at=now + timedelta(days=1, hours=2),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        priority=1,
    )


def _result(now: datetime) -> SeatObservationResult:
    return SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.SOLD_OUT,
        source="postgres-acceptance",
        observed_at=now,
        fresh_until=now + timedelta(minutes=5),
    )


async def _create_watch_fixture(
    session: AsyncSession,
    *,
    prefix: str,
    now: datetime,
    status: WatchStatus,
) -> WatchFixture:
    watch_id = str(uuid4())
    candidate_id = str(uuid4())
    watch = Watch(
        id=watch_id,
        provider=Provider.SRT,
        origin="서울",
        origin_node_id="ACCEPTANCE-SEOUL",
        destination="부산",
        destination_node_id="ACCEPTANCE-BUSAN",
        travel_date=(now + timedelta(days=1)).date(),
        time_from=time(8),
        time_to=time(12),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        mode="official",
        reservation_policy=ReservationPolicy.NOTIFY_ONLY,
        status=status,
        dedupe_key=f"pg-accept-{prefix}-{uuid4().hex[:20]}",
        next_check_at=now - timedelta(seconds=1),
    )
    watch.candidates.append(
        WatchCandidate(
            id=candidate_id,
            train_number=f"SRT-{uuid4().hex[:8]}",
            departure_at=now + timedelta(days=1),
            arrival_at=now + timedelta(days=1, hours=2),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="active",
        )
    )
    session.add(watch)
    await session.flush()
    return WatchFixture(watch_id=watch_id, candidate_id=candidate_id)


async def _ensure_closed_circuit(session: AsyncSession) -> None:
    circuit = await get_or_create_provider_circuit(session, Provider.SRT, lock=True)
    circuit.state = ProviderCircuitState.CLOSED
    circuit.reason = None
    circuit.opened_at = None
    circuit.cooldown_until = None
    circuit.manual_resume_required = False


async def _seed_holder_and_stale_fixtures(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> tuple[WatchFixture, StaleFixtures]:
    async with factory.begin() as session:
        await _ensure_closed_circuit(session)
        holder = await _create_watch_fixture(
            session, prefix="holder", now=now, status=WatchStatus.SCHEDULED
        )
        stale = StaleFixtures(
            prepare=await _create_watch_fixture(
                session, prefix="stale-prepare", now=now, status=WatchStatus.SCHEDULED
            ),
            defer=await _create_watch_fixture(
                session, prefix="stale-defer", now=now, status=WatchStatus.SCHEDULED
            ),
            persist=await _create_watch_fixture(
                session, prefix="stale-persist", now=now, status=WatchStatus.WATCHING
            ),
            circuit=await _create_watch_fixture(
                session, prefix="stale-circuit", now=now, status=WatchStatus.SCHEDULED
            ),
        )
    return holder, stale


async def _application_holder(
    fixture: WatchFixture,
    grant: ExecutionLeaseGrant,
    *,
    now: datetime,
    application_name: str,
    commit_requested: Event,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database_opt_in()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        service = ProviderExecutionLeaseService(factory)
        signaled = False

        async def lock_then_pause(
            session: AsyncSession,
            current_grant: object,
            *,
            now: datetime,
        ) -> bool:
            nonlocal signaled
            if not isinstance(current_grant, ExecutionLeaseGrant):
                return False
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            locked = await lock_execution_lease_current(session, current_grant, now=now)
            if not isinstance(backend_pid, int):
                raise RuntimeError("holder connection did not report a backend PID")
            if not signaled:
                _put(
                    messages,
                    "holder_locked",
                    value=int(locked),
                    backend_pid=backend_pid,
                    application_name=application_name,
                )
                signaled = True
                if not locked:
                    return False
                if not await asyncio.to_thread(commit_requested.wait, _PROCESS_TIMEOUT_SECONDS):
                    raise TimeoutError("holder did not receive the commit request")
            return locked

        adapter = DeterministicObservationAdapter(now)
        targets = await prepare_watch(
            fixture.watch_id,
            now,
            adapter=adapter,
            lease_grant=grant,
            dependencies=_dependencies(
                factory,
                service,
                now=now,
                locked_lease_current=lock_then_pause,
            ),
        )
        _put(messages, "holder_committed", value=len(targets))
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "holder")
    finally:
        if engine is not None:
            await engine.dispose()


async def _takeover(
    scope: str,
    *,
    started_at: datetime,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database_opt_in()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        service = ProviderExecutionLeaseService(factory)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
        if not isinstance(backend_pid, int):
            raise RuntimeError("takeover connection did not report a backend PID")
        _put(
            messages,
            "takeover_ready",
            backend_pid=backend_pid,
            application_name=application_name,
        )
        grant = await service.acquire(
            Provider.SRT,
            scope,
            "observation-takeover-owner",
            now=started_at + timedelta(seconds=6),
            expires_at=started_at + timedelta(minutes=15),
        )
        _put(
            messages,
            "takeover_result",
            value=None if grant is None else grant.fencing_token,
            application_name=application_name,
        )
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "takeover")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_application_holder(
    fixture: WatchFixture,
    grant: ExecutionLeaseGrant,
    now: datetime,
    application_name: str,
    commit_requested: Event,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _application_holder(
            fixture,
            grant,
            now=now,
            application_name=application_name,
            commit_requested=commit_requested,
            messages=messages,
        )
    )


def _run_takeover(
    scope: str,
    started_at: datetime,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _takeover(
            scope,
            started_at=started_at,
            application_name=application_name,
            messages=messages,
        )
    )


async def _verify_application_holder_blocks_takeover(
    factory: async_sessionmaker[AsyncSession],
    service: ProviderExecutionLeaseService,
    *,
    fixture: WatchFixture,
    scope: str,
    started_at: datetime,
) -> tuple[ExecutionLeaseGrant, ExecutionLeaseGrant]:
    first = await service.acquire(
        Provider.SRT,
        scope,
        "observation-holder-owner",
        now=started_at,
        expires_at=started_at + timedelta(seconds=5),
    )
    if first is None:
        raise AssertionError("failed to acquire the observation holder lease")

    context = get_context("spawn")
    commit_requested = context.Event()
    messages: Queue[ProcessMessage] = context.Queue()
    holder_name = f"obs-holder-{uuid4().hex}"
    takeover_name = f"obs-takeover-{uuid4().hex}"
    holder = context.Process(
        target=_run_application_holder,
        args=(
            fixture,
            first,
            started_at + timedelta(seconds=1),
            holder_name,
            commit_requested,
            messages,
        ),
    )
    takeover = context.Process(
        target=_run_takeover,
        args=(scope, started_at, takeover_name, messages),
    )
    holder_started = False
    takeover_started = False
    parent_pid = os.getpid()
    try:
        holder.start()
        holder_started = True
        holder_message = await _next_message(messages)
        _require_kind(holder_message, "holder_locked", parent_pid=parent_pid)
        if holder_message.get("value") != 1:
            raise AssertionError("observation application did not lock the current lease")
        if holder_message.get("application_name") != holder_name:
            raise AssertionError("observation holder application identity did not match")
        holder_process_pid = holder_message.get("pid")
        holder_backend_pid = holder_message.get("backend_pid")
        if not isinstance(holder_process_pid, int) or not isinstance(holder_backend_pid, int):
            raise AssertionError("observation holder backend identity was missing")

        takeover.start()
        takeover_started = True
        readiness = await _next_message(messages)
        _require_kind(readiness, "takeover_ready", parent_pid=parent_pid)
        takeover_process_pid = readiness.get("pid")
        takeover_backend_pid = readiness.get("backend_pid")
        if not isinstance(takeover_process_pid, int) or not isinstance(takeover_backend_pid, int):
            raise AssertionError("takeover backend identity was missing")
        if holder_process_pid == takeover_process_pid:
            raise AssertionError("holder and takeover reused one OS process")
        if holder_backend_pid == takeover_backend_pid:
            raise AssertionError("holder and takeover reused one PostgreSQL backend")
        if readiness.get("application_name") != takeover_name:
            raise AssertionError("takeover application identity did not match")

        await _wait_for_takeover_lock(
            factory,
            application_name=takeover_name,
            backend_pid=takeover_backend_pid,
        )
        commit_requested.set()

        holder_done = False
        token: int | None = None
        while not holder_done or token is None:
            message = await _next_message(messages)
            kind = message.get("kind")
            if isinstance(kind, str) and kind.endswith("_error"):
                raise AssertionError(f"{kind} during observation acceptance")
            if kind == "holder_committed":
                if message.get("value") != 1:
                    raise AssertionError("holder prepare did not return its candidate")
                holder_done = True
            elif kind == "takeover_result":
                if message.get("application_name") != takeover_name:
                    raise AssertionError("takeover result identity did not match")
                value = message.get("value")
                token = value if isinstance(value, int) else None
            else:
                raise AssertionError(f"unexpected holder lifecycle event: {kind}")
        if token != first.fencing_token + 1:
            raise AssertionError("observation takeover did not advance fencing by one")
        second = ExecutionLeaseGrant(
            provider=Provider.SRT,
            account_scope=scope,
            owner_token="observation-takeover-owner",
            fencing_token=token,
            expires_at=started_at + timedelta(minutes=15),
        )
        return first, second
    finally:
        commit_requested.set()
        if takeover_started:
            await _join_or_terminate(takeover)
        if holder_started:
            await _join_or_terminate(holder)
        messages.close()
        messages.join_thread()


async def _snapshot(
    factory: async_sessionmaker[AsyncSession], fixtures: StaleFixtures
) -> dict[str, object]:
    async with factory() as session:
        watches = list(
            (
                await session.execute(
                    select(
                        Watch.id,
                        Watch.status,
                        Watch.next_check_at,
                        Watch.cooldown_until,
                        Watch.unchanged_runs,
                    )
                    .where(Watch.id.in_(fixtures.watch_ids))
                    .order_by(Watch.id)
                )
            ).all()
        )
        candidates = list(
            (
                await session.execute(
                    select(
                        WatchCandidate.id,
                        WatchCandidate.state,
                        WatchCandidate.operational_status,
                        WatchCandidate.operational_source,
                        WatchCandidate.operational_observed_at,
                        WatchCandidate.operational_fresh_until,
                    )
                    .where(WatchCandidate.id.in_(fixtures.candidate_ids))
                    .order_by(WatchCandidate.id)
                )
            ).all()
        )
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .where(SeatObservation.candidate_id.in_(fixtures.candidate_ids))
        )
        history_count = await session.scalar(
            select(func.count())
            .select_from(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id.in_(fixtures.watch_ids))
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id.in_(fixtures.watch_ids))
        )
        attempt_count = await session.scalar(
            select(func.count())
            .select_from(ReservationAttempt)
            .where(ReservationAttempt.candidate_id.in_(fixtures.candidate_ids))
        )
        circuit = await session.scalar(
            select(ProviderCircuit).where(ProviderCircuit.provider == Provider.SRT)
        )
        circuit_state = (
            None
            if circuit is None
            else (
                circuit.state,
                circuit.reason,
                circuit.opened_at,
                circuit.cooldown_until,
                circuit.manual_resume_required,
                circuit.generation,
                circuit.updated_at,
            )
        )
    return {
        "watches": watches,
        "candidates": candidates,
        "observation_count": observation_count,
        "history_count": history_count,
        "outbox_count": outbox_count,
        "attempt_count": attempt_count,
        "circuit": circuit_state,
    }


async def _run_stale_paths(
    fixtures: StaleFixtures,
    stale_grant: ExecutionLeaseGrant,
    *,
    now: datetime,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database_opt_in()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory()
        service = ProviderExecutionLeaseService(factory)
        reservations = [0]
        dependencies = _dependencies(
            factory,
            service,
            now=now,
            reservation_counter=reservations,
        )
        adapter = DeterministicObservationAdapter(now)

        prepared = await prepare_watch(
            fixtures.prepare.watch_id,
            now,
            adapter=adapter,
            lease_grant=stale_grant,
            dependencies=dependencies,
        )
        await defer_watch_group_observation(
            [fixtures.defer.watch_id],
            now + timedelta(minutes=10),
            now,
            lease_grant=stale_grant,
            prepared=False,
            dependencies=dependencies,
        )
        persist_target = _target(fixtures.persist, now)
        winner = await persist_observation_cycle(
            fixtures.persist.watch_id,
            [persist_target],
            {persist_target.cache_key: _result(now)},
            now,
            lease_grant=stale_grant,
            dependencies=dependencies,
        )
        await apply_current_circuit_to_watch(
            fixtures.circuit.watch_id,
            lease_grant=stale_grant,
            dependencies=dependencies,
        )
        await process_watch_group_observation(
            [fixtures.prepare.watch_id],
            now,
            provider=Provider.SRT,
            adapter=adapter,
            lease_grant=stale_grant,
            dependencies=dependencies,
        )
        if prepared or winner is not None:
            raise AssertionError("a stale observation path returned a writable result")
        if adapter.observe_calls or reservations[0]:
            raise AssertionError("a stale observation path reached provider or reservation work")
        _put(
            messages,
            "stale_paths_done",
            value=adapter.observe_calls + reservations[0],
        )
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "stale")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_stale_process(
    fixtures: StaleFixtures,
    stale_grant: ExecutionLeaseGrant,
    now: datetime,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _run_stale_paths(
            fixtures,
            stale_grant,
            now=now,
            messages=messages,
        )
    )


async def _run_stale_provider_circuit(
    stale_grant: ExecutionLeaseGrant,
    *,
    now: datetime,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database_opt_in()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory()
        service = ProviderExecutionLeaseService(factory)
        circuit_closed = await provider_circuit_is_closed(
            Provider.SRT,
            lease_grant=stale_grant,
            dependencies=_dependencies(factory, service, now=now),
        )
        if circuit_closed:
            raise AssertionError("stale provider-circuit check returned a writable result")
        _put(messages, "stale_circuit_done")
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "stale_circuit")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_stale_provider_circuit_process(
    stale_grant: ExecutionLeaseGrant,
    now: datetime,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _run_stale_provider_circuit(
            stale_grant,
            now=now,
            messages=messages,
        )
    )


async def _verify_stale_provider_circuit_does_not_create(
    factory: async_sessionmaker[AsyncSession],
    stale_grant: ExecutionLeaseGrant,
    *,
    now: datetime,
) -> None:
    async with factory.begin() as session:
        await session.execute(
            delete(ProviderCircuit).where(ProviderCircuit.provider == Provider.SRT)
        )

    context = get_context("spawn")
    messages: Queue[ProcessMessage] = context.Queue()
    process = context.Process(
        target=_run_stale_provider_circuit_process,
        args=(stale_grant, now, messages),
    )
    started = False
    try:
        process.start()
        started = True
        message = await _next_message(messages)
        _require_kind(message, "stale_circuit_done", parent_pid=os.getpid())
    finally:
        if started:
            await _join_or_terminate(process)
        messages.close()
        messages.join_thread()

    async with factory() as session:
        circuit_count = await session.scalar(
            select(func.count())
            .select_from(ProviderCircuit)
            .where(ProviderCircuit.provider == Provider.SRT)
        )
    if circuit_count != 0:
        raise AssertionError("stale provider-circuit check created persistent state")


async def _verify_stale_paths_write_nothing(
    factory: async_sessionmaker[AsyncSession],
    fixtures: StaleFixtures,
    stale_grant: ExecutionLeaseGrant,
    *,
    now: datetime,
) -> None:
    await _verify_stale_provider_circuit_does_not_create(
        factory,
        stale_grant,
        now=now,
    )
    async with factory.begin() as session:
        circuit = await get_or_create_provider_circuit(session, Provider.SRT, lock=True)
        circuit.state = ProviderCircuitState.OPEN
        circuit.reason = "postgres_acceptance"
        circuit.opened_at = now
        circuit.cooldown_until = now + timedelta(minutes=10)
        circuit.manual_resume_required = False

    before = await _snapshot(factory, fixtures)
    context = get_context("spawn")
    messages: Queue[ProcessMessage] = context.Queue()
    process = context.Process(
        target=_run_stale_process,
        args=(fixtures, stale_grant, now, messages),
    )
    started = False
    try:
        process.start()
        started = True
        message = await _next_message(messages)
        _require_kind(message, "stale_paths_done", parent_pid=os.getpid())
        if message.get("value") != 0:
            raise AssertionError("stale observation paths reached side effects")
    finally:
        if started:
            await _join_or_terminate(process)
        messages.close()
        messages.join_thread()
    after = await _snapshot(factory, fixtures)
    if after != before:
        raise AssertionError("stale observation paths changed persisted state")


async def _run_concurrent_group(
    watch_ids: list[str],
    grant: ExecutionLeaseGrant,
    *,
    now: datetime,
    start_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    _require_isolated_database_opt_in()
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        service = ProviderExecutionLeaseService(factory)
        adapter = DeterministicObservationAdapter(now)
        reservations = [0]
        checks = [0]

        async def counted_locked_check(
            session: AsyncSession,
            current_grant: object,
            *,
            now: datetime,
        ) -> bool:
            checks[0] += 1
            if not isinstance(current_grant, ExecutionLeaseGrant):
                return False
            return await lock_execution_lease_current(session, current_grant, now=now)

        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
        if not isinstance(backend_pid, int):
            raise RuntimeError("concurrent worker did not report a backend PID")
        _put(
            messages,
            "concurrent_ready",
            backend_pid=backend_pid,
            application_name=application_name,
        )
        if not await asyncio.to_thread(start_requested.wait, _PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("concurrent observation start was not released")
        await process_watch_group_observation(
            watch_ids,
            now,
            provider=Provider.SRT,
            adapter=adapter,
            lease_grant=grant,
            dependencies=_dependencies(
                factory,
                service,
                now=now,
                locked_lease_current=counted_locked_check,
                reservation_counter=reservations,
            ),
        )
        if checks[0] == 0:
            raise AssertionError("concurrent observation skipped the locked lease path")
        if reservations[0] != 0:
            raise AssertionError("sold-out concurrency fixture attempted a reservation")
        _put(
            messages,
            "concurrent_done",
            value=checks[0],
            application_name=application_name,
        )
    except Exception:  # noqa: BLE001 - child reports only a safe category.
        _put_error(messages, "concurrent")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_concurrent_process(
    watch_ids: list[str],
    grant: ExecutionLeaseGrant,
    now: datetime,
    start_requested: Event,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _run_concurrent_group(
            watch_ids,
            grant,
            now=now,
            start_requested=start_requested,
            application_name=application_name,
            messages=messages,
        )
    )


async def _delete_watch_fixtures(
    factory: async_sessionmaker[AsyncSession], watch_ids: list[str]
) -> None:
    async with factory.begin() as session:
        await session.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(watch_ids)))
        await session.execute(delete(Watch).where(Watch.id.in_(watch_ids)))


async def _verify_concurrent_lock_order(
    factory: async_sessionmaker[AsyncSession],
    grant: ExecutionLeaseGrant,
    *,
    now: datetime,
    cleanup_watch_ids: list[str],
) -> None:
    async with factory.begin() as session:
        await _ensure_closed_circuit(session)

    context = get_context("spawn")
    for round_number in range(_DEADLOCK_ROUNDS):
        async with factory.begin() as session:
            first = await _create_watch_fixture(
                session,
                prefix=f"concurrent-{round_number}-a",
                now=now,
                status=WatchStatus.SCHEDULED,
            )
            second = await _create_watch_fixture(
                session,
                prefix=f"concurrent-{round_number}-b",
                now=now,
                status=WatchStatus.SCHEDULED,
            )
        watch_ids = [first.watch_id, second.watch_id]
        cleanup_watch_ids.extend(watch_ids)
        start_requested = context.Event()
        messages: Queue[ProcessMessage] = context.Queue()
        names = [
            f"obs-order-a-{round_number}-{uuid4().hex[:12]}",
            f"obs-order-b-{round_number}-{uuid4().hex[:12]}",
        ]
        processes = [
            context.Process(
                target=_run_concurrent_process,
                args=(watch_ids, grant, now, start_requested, names[0], messages),
            ),
            context.Process(
                target=_run_concurrent_process,
                args=(list(reversed(watch_ids)), grant, now, start_requested, names[1], messages),
            ),
        ]
        started: list[Any] = []
        try:
            for process in processes:
                process.start()
                started.append(process)
            ready_names: set[str] = set()
            ready_process_pids: set[int] = set()
            ready_backend_pids: set[int] = set()
            while len(ready_names) < 2:
                message = await _next_message(messages)
                _require_kind(message, "concurrent_ready", parent_pid=os.getpid())
                reported_name = message.get("application_name")
                process_pid = message.get("pid")
                backend_pid = message.get("backend_pid")
                if (
                    not isinstance(reported_name, str)
                    or not isinstance(process_pid, int)
                    or not isinstance(backend_pid, int)
                ):
                    raise AssertionError("concurrent worker identity was missing")
                ready_names.add(reported_name)
                ready_process_pids.add(process_pid)
                ready_backend_pids.add(backend_pid)
            if ready_names != set(names):
                raise AssertionError("concurrent worker identities did not match")
            if len(ready_process_pids) != 2 or len(ready_backend_pids) != 2:
                raise AssertionError(
                    "concurrent workers did not use independent processes/backends"
                )
            start_requested.set()
            done_names: set[str] = set()
            while len(done_names) < 2:
                message = await _next_message(messages)
                _require_kind(message, "concurrent_done", parent_pid=os.getpid())
                reported_name = message.get("application_name")
                if not isinstance(reported_name, str) or not isinstance(message.get("value"), int):
                    raise AssertionError("concurrent completion evidence was incomplete")
                done_names.add(reported_name)
            if done_names != set(names):
                raise AssertionError("concurrent completion identities did not match")
        finally:
            start_requested.set()
            for process in started:
                await _join_or_terminate(process)
            messages.close()
            messages.join_thread()

        async with factory() as session:
            observations = list(
                (
                    await session.execute(
                        select(SeatObservation.candidate_id, func.count())
                        .where(
                            SeatObservation.candidate_id.in_(
                                [first.candidate_id, second.candidate_id]
                            )
                        )
                        .group_by(SeatObservation.candidate_id)
                    )
                ).all()
            )
            watches = list(
                (
                    await session.scalars(
                        select(Watch).where(Watch.id.in_(watch_ids)).order_by(Watch.id)
                    )
                ).all()
            )
            attempts = await session.scalar(
                select(func.count())
                .select_from(ReservationAttempt)
                .where(
                    ReservationAttempt.candidate_id.in_([first.candidate_id, second.candidate_id])
                )
            )
        if {candidate_id: count for candidate_id, count in observations} != {
            first.candidate_id: 1,
            second.candidate_id: 1,
        }:
            raise AssertionError(
                "concurrent observation did not persist exactly one result per candidate"
            )
        if {watch.status for watch in watches} != {WatchStatus.WATCHING}:
            raise AssertionError("concurrent observation left an invalid watch state")
        if any(watch.next_check_at is None for watch in watches):
            raise AssertionError("concurrent observation did not schedule the next cycle")
        if attempts != 0:
            raise AssertionError("concurrent sold-out observation created a reservation attempt")
        await _delete_watch_fixtures(factory, watch_ids)


async def _cleanup(
    factory: async_sessionmaker[AsyncSession],
    *,
    watch_ids: list[str],
    scope: str,
) -> None:
    async with factory.begin() as session:
        await session.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(watch_ids)))
        await session.execute(delete(Watch).where(Watch.id.in_(watch_ids)))
        await session.execute(
            delete(ProviderExecutionLease).where(
                ProviderExecutionLease.provider == Provider.SRT,
                ProviderExecutionLease.account_scope == scope,
            )
        )
        await session.execute(
            delete(ProviderCircuit).where(ProviderCircuit.provider == Provider.SRT)
        )


async def verify() -> None:
    _require_isolated_database_opt_in()
    engine: AsyncEngine | None = None
    holder: WatchFixture | None = None
    stale: StaleFixtures | None = None
    concurrency_watch_ids: list[str] = []
    scope = f"observation-fencing-acceptance:{uuid4()}"
    try:
        engine, factory = _engine_and_factory()
        service = ProviderExecutionLeaseService(factory)
        started_at = datetime.now(UTC)
        holder, stale = await _seed_holder_and_stale_fixtures(factory, started_at)
        first, second = await _verify_application_holder_blocks_takeover(
            factory,
            service,
            fixture=holder,
            scope=scope,
            started_at=started_at,
        )
        await _verify_stale_paths_write_nothing(
            factory,
            stale,
            first,
            now=started_at + timedelta(seconds=7),
        )
        await _verify_concurrent_lock_order(
            factory,
            second,
            now=started_at + timedelta(seconds=8),
            cleanup_watch_ids=concurrency_watch_ids,
        )
    finally:
        if engine is not None:
            watch_ids = (
                concurrency_watch_ids
                if holder is None or stale is None
                else [holder.watch_id, *stale.watch_ids, *concurrency_watch_ids]
            )
            try:
                await _cleanup(factory, watch_ids=watch_ids, scope=scope)
            finally:
                await engine.dispose()


if __name__ == "__main__":
    _require_isolated_database_opt_in()
    asyncio.run(verify())
    print("PostgreSQL 관찰 application fencing·잠금 순서 수용 검증 통과")
