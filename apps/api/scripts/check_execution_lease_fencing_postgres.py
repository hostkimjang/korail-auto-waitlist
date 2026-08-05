from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from queue import Empty
from typing import Literal
from uuid import uuid4

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rail_waitlist.config import get_settings
from rail_waitlist.domain import Provider
from rail_waitlist.models import ProviderExecutionLease
from rail_waitlist.provider_execution_lease import (
    ExecutionLeaseGrant,
    ProviderExecutionLeaseService,
    lock_execution_lease_current,
)

_BLOCKED_WINDOW_SECONDS = 0.25
_PROCESS_TIMEOUT_SECONDS = 15
_PROCESS_SHUTDOWN_SECONDS = 5

ProcessMessageKind = Literal[
    "holder_locked",
    "holder_committed",
    "holder_error",
    "takeover_ready",
    "takeover_result",
    "takeover_error",
]
ProcessMessage = tuple[ProcessMessageKind, int, int | None, str | None]


def _engine_and_factory(
    *, application_name: str | None = None
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("database URL is not configured")
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
        raise RuntimeError("execution lease contention check requires PostgreSQL")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _verify_single_process(
    service: ProviderExecutionLeaseService,
    factory: async_sessionmaker[AsyncSession],
    *,
    scope: str,
    started_at: datetime,
) -> None:
    """Keep the original two-session PostgreSQL row-lock acceptance check."""

    first = await service.acquire(
        Provider.SRT,
        scope,
        "fencing-check-first-owner",
        now=started_at,
        expires_at=started_at + timedelta(seconds=5),
    )
    if first is None:
        raise AssertionError("failed to acquire the first verification lease")

    takeover: asyncio.Task[ExecutionLeaseGrant | None] | None = None
    try:
        async with factory() as guarded_session:
            if not await lock_execution_lease_current(
                guarded_session,
                first,
                now=started_at + timedelta(seconds=1),
            ):
                raise AssertionError("current lease was rejected before the guarded commit")

            takeover = asyncio.create_task(
                service.acquire(
                    Provider.SRT,
                    scope,
                    "fencing-check-second-owner",
                    now=started_at + timedelta(seconds=6),
                    expires_at=started_at + timedelta(seconds=30),
                )
            )
            try:
                await asyncio.wait_for(asyncio.shield(takeover), timeout=_BLOCKED_WINDOW_SECONDS)
            except TimeoutError:
                pass
            else:
                raise AssertionError("new lease epoch was not blocked by the guarded row lock")
            await guarded_session.commit()
            second = await asyncio.wait_for(takeover, timeout=_PROCESS_TIMEOUT_SECONDS)
    finally:
        if takeover is not None and not takeover.done():
            takeover.cancel()
            with suppress(asyncio.CancelledError):
                await takeover

    if second is None or second.fencing_token != first.fencing_token + 1:
        raise AssertionError("takeover did not advance the fencing token")
    async with factory() as session:
        if await lock_execution_lease_current(
            session, first, now=started_at + timedelta(seconds=7)
        ):
            raise AssertionError("stale lease epoch remained current after takeover")
        if not await lock_execution_lease_current(
            session, second, now=started_at + timedelta(seconds=7)
        ):
            raise AssertionError("new lease epoch was not current after takeover")
        await session.rollback()


def _send_error(messages: Queue[ProcessMessage], role: Literal["holder", "takeover"]) -> None:
    # Error categories are enough for the parent assertion. Do not return a DSN,
    # credential, owner token, or provider exception text through IPC or stdout.
    messages.put((f"{role}_error", os.getpid(), None, None))


async def _hold_lease_lock(
    grant: ExecutionLeaseGrant,
    *,
    now: datetime,
    commit_requested: Event,
    messages: Queue[ProcessMessage],
) -> None:
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory()
        async with factory() as session:
            locked = await lock_execution_lease_current(session, grant, now=now)
            messages.put(("holder_locked", os.getpid(), int(locked), None))
            if not locked:
                return
            if not await asyncio.to_thread(commit_requested.wait, _PROCESS_TIMEOUT_SECONDS):
                raise TimeoutError("holder process did not receive the commit request")
            await session.commit()
            messages.put(("holder_committed", os.getpid(), None, None))
    except Exception:  # noqa: BLE001 - child reports only a safe categorical failure.
        _send_error(messages, "holder")
    finally:
        if engine is not None:
            await engine.dispose()


async def _attempt_takeover(
    scope: str,
    *,
    started_at: datetime,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory(application_name=application_name)
        service = ProviderExecutionLeaseService(factory)
        async with factory() as session:
            backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.rollback()
        if not isinstance(backend_pid, int):
            raise RuntimeError("takeover connection did not report a PostgreSQL backend PID")
        # pool_size=1 makes the immediately following acquire reuse this identified backend.
        # The parent still waits for pg_stat_activity/pg_locks evidence before committing.
        messages.put(("takeover_ready", os.getpid(), backend_pid, application_name))
        grant = await service.acquire(
            Provider.SRT,
            scope,
            "fencing-check-process-second-owner",
            now=started_at + timedelta(seconds=6),
            expires_at=started_at + timedelta(seconds=30),
        )
        messages.put(
            (
                "takeover_result",
                os.getpid(),
                None if grant is None else grant.fencing_token,
                application_name,
            )
        )
    except Exception:  # noqa: BLE001 - child reports only a safe categorical failure.
        _send_error(messages, "takeover")
    finally:
        if engine is not None:
            await engine.dispose()


def _run_holder_process(
    grant: ExecutionLeaseGrant,
    now: datetime,
    commit_requested: Event,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _hold_lease_lock(
            grant,
            now=now,
            commit_requested=commit_requested,
            messages=messages,
        )
    )


def _run_takeover_process(
    scope: str,
    started_at: datetime,
    application_name: str,
    messages: Queue[ProcessMessage],
) -> None:
    asyncio.run(
        _attempt_takeover(
            scope,
            started_at=started_at,
            application_name=application_name,
            messages=messages,
        )
    )


async def _next_message(
    messages: Queue[ProcessMessage], *, timeout_seconds: float
) -> ProcessMessage:
    try:
        return await asyncio.to_thread(messages.get, True, timeout_seconds)
    except Empty as error:
        raise AssertionError(
            "process acceptance did not report the expected lifecycle event"
        ) from error


def _require_message(
    message: ProcessMessage,
    expected_kind: ProcessMessageKind,
    *,
    parent_pid: int,
) -> int:
    kind, pid, value, _ = message
    if kind.endswith("_error"):
        raise AssertionError(f"{kind} during process acceptance")
    if kind != expected_kind:
        raise AssertionError(f"expected {expected_kind}, received {kind}")
    if pid == parent_pid:
        raise AssertionError("process acceptance did not use an independent child process")
    if expected_kind == "holder_locked" and value != 1:
        raise AssertionError("holder process did not lock the current lease")
    return pid


async def _wait_for_takeover_lock(
    factory: async_sessionmaker[AsyncSession],
    *,
    application_name: str,
    backend_pid: int,
) -> None:
    deadline = asyncio.get_running_loop().time() + _PROCESS_TIMEOUT_SECONDS
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
    raise AssertionError("takeover backend did not enter the expected PostgreSQL lock wait")


async def _join_or_terminate(process) -> None:
    await asyncio.to_thread(process.join, _PROCESS_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.terminate()
        await asyncio.to_thread(process.join, _PROCESS_SHUTDOWN_SECONDS)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join, _PROCESS_SHUTDOWN_SECONDS)
    if process.is_alive():
        raise AssertionError(
            "process acceptance child did not stop within the bounded cleanup window"
        )
    if process.exitcode != 0:
        raise AssertionError("process acceptance child exited unsuccessfully")


async def _verify_process_isolation(
    service: ProviderExecutionLeaseService,
    factory: async_sessionmaker[AsyncSession],
    *,
    scope: str,
    started_at: datetime,
) -> None:
    """Prove a guarded PostgreSQL lease lock blocks takeover across OS processes."""

    first = await service.acquire(
        Provider.SRT,
        scope,
        "fencing-check-process-first-owner",
        now=started_at,
        expires_at=started_at + timedelta(seconds=5),
    )
    if first is None:
        raise AssertionError("failed to acquire the first process verification lease")

    context = get_context("spawn")
    commit_requested = context.Event()
    messages: Queue[ProcessMessage] = context.Queue()
    holder = context.Process(
        target=_run_holder_process,
        args=(first, started_at + timedelta(seconds=1), commit_requested, messages),
    )
    takeover = None
    holder_started = False
    takeover_started = False
    parent_pid = os.getpid()
    application_name = f"rail-waitlist-fencing-{uuid4().hex}"
    try:
        holder.start()
        holder_started = True
        holder_pid = _require_message(
            await _next_message(messages, timeout_seconds=_PROCESS_TIMEOUT_SECONDS),
            "holder_locked",
            parent_pid=parent_pid,
        )

        takeover = context.Process(
            target=_run_takeover_process,
            args=(scope, started_at, application_name, messages),
        )
        takeover.start()
        takeover_started = True
        readiness = await _next_message(messages, timeout_seconds=_PROCESS_TIMEOUT_SECONDS)
        takeover_pid = _require_message(
            readiness,
            "takeover_ready",
            parent_pid=parent_pid,
        )
        if holder_pid == takeover_pid:
            raise AssertionError("holder and takeover must run in separate processes")
        _, _, backend_pid, reported_application_name = readiness
        if not isinstance(backend_pid, int) or reported_application_name != application_name:
            raise AssertionError("takeover connection readiness identity did not match")

        await _wait_for_takeover_lock(
            factory,
            application_name=application_name,
            backend_pid=backend_pid,
        )

        commit_requested.set()
        holder_committed = False
        token: int | None = None
        while not holder_committed or token is None:
            message = await _next_message(messages, timeout_seconds=_PROCESS_TIMEOUT_SECONDS)
            kind, pid, value, reported_application_name = message
            if kind.endswith("_error"):
                raise AssertionError(f"{kind} during process acceptance")
            if pid == parent_pid:
                raise AssertionError("process acceptance did not use an independent child process")
            if kind == "holder_committed":
                holder_committed = True
            elif kind == "takeover_result":
                if reported_application_name != application_name:
                    raise AssertionError("takeover result application identity did not match")
                token = value
            else:
                raise AssertionError(f"unexpected process lifecycle event after commit ({kind})")
        if not isinstance(token, int) or token != first.fencing_token + 1:
            raise AssertionError("process takeover did not advance the fencing token")
        second = ExecutionLeaseGrant(
            provider=Provider.SRT,
            account_scope=scope,
            owner_token="fencing-check-process-second-owner",
            fencing_token=token,
            expires_at=started_at + timedelta(seconds=30),
        )
        async with factory() as session:
            if await lock_execution_lease_current(
                session, first, now=started_at + timedelta(seconds=7)
            ):
                raise AssertionError("process acceptance allowed a stale lease epoch")
            if not await lock_execution_lease_current(
                session, second, now=started_at + timedelta(seconds=7)
            ):
                raise AssertionError("process acceptance rejected the takeover lease epoch")
            await session.rollback()
    finally:
        commit_requested.set()
        if takeover is not None and takeover_started:
            await _join_or_terminate(takeover)
        if holder_started:
            await _join_or_terminate(holder)
        messages.close()
        messages.join_thread()


async def verify() -> None:
    """Run independent-session and independent-process PostgreSQL fencing checks."""

    engine: AsyncEngine | None = None
    try:
        engine, factory = _engine_and_factory()
        service = ProviderExecutionLeaseService(factory)
        started_at = datetime.now(UTC)
        single_process_scope = f"execution-lease-verification:{uuid4()}"
        process_scope = f"execution-lease-process-verification:{uuid4()}"
        try:
            await _verify_single_process(
                service,
                factory,
                scope=single_process_scope,
                started_at=started_at,
            )
            await _verify_process_isolation(
                service,
                factory,
                scope=process_scope,
                started_at=started_at + timedelta(minutes=1),
            )
        finally:
            async with factory.begin() as session:
                await session.execute(
                    delete(ProviderExecutionLease).where(
                        ProviderExecutionLease.provider == Provider.SRT,
                        ProviderExecutionLease.account_scope.in_(
                            [single_process_scope, process_scope]
                        ),
                    )
                )
    finally:
        if engine is not None:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
    print("PostgreSQL 실행 임대 fencing 경합 검증 통과")
