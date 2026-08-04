from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .celery_app import celery_app
from .config import get_settings
from .database import SessionFactory, engine
from .domain import (
    Provider,
    ReservationOutcome,
    WatchStatus,
)
from .korail_execution import korail_background_monitoring_enabled
from .metrics import WATCH_GROUPS, WORKER_RUNS
from .models import ReservationAttempt, Watch, WatchCandidate
from .notification_management.delivery import deliver_pending_notifications
from .observations.due_pipeline_application import (
    DuePipelineDependencies,
    process_due_pipeline,
)
from .observations.group_application import (
    ObservationGroupDependencies,
    ObservationTarget,
    process_watch_group_observation,
    provider_circuit_is_closed,
    watch_group_provider,
)
from .provider_accounts import update_provider_auth_status
from .provider_contracts import ExecutionProvider, ProviderLifecycle, ProviderUnavailable
from .provider_execution_lease import (
    ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
    ExecutionLeaseGrant,
    ProviderExecutionLeaseService,
    lock_execution_lease_current,
)
from .providers import get_execution_provider
from .reservations.execution_application import (
    ReservationExecutionDependencies,
    ReservationExecutionTarget,
    execute_reservation,
)
from .reservations.reconciliation_application import (
    ReconciliationDependencies,
    _reservation_reconciliation_due_clause,
)
from .reservations.reconciliation_application import (
    reconcile_reservation_attempt as run_reservation_reconciliation,
)
from .schemas import RailProviderAuthStatus
from .services import (
    add_outbox_event,
    apply_watch_transition,
    begin_reservation_attempt,
    complete_reservation_attempt,
    finish_observation_cycle,
    get_or_create_provider_circuit,
    is_confirmed_absent_retry_source,
    latest_observation_fingerprint,
    record_reservation_confirmation,
    record_seat_observation,
)
from .srt_reservation import SRT_RESERVATION_SOURCE
from .watch_management.expiry_application import (
    WatchExpiryDependencies,
    expire_elapsed_watches,
)

RESERVATION_ATTEMPT_STALE_AFTER = timedelta(minutes=5)
PROVIDER_EXECUTION_LEASE_DURATION = timedelta(minutes=2)
_EXTERNAL_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})
LOGGER = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _close_execution_adapter(
    adapter: ProviderLifecycle,
    provider: Provider,
) -> None:
    try:
        await adapter.aclose()
    except Exception:
        # Cleanup diagnostics stay categorical; upstream details and credentials must
        # never be copied into worker logs.  Lease release is handled separately.
        LOGGER.warning("execution adapter cleanup failed provider=%s", provider.value)


async def _drain_execution_adapter(
    adapter: ProviderLifecycle,
    provider: Provider,
) -> None:
    try:
        await adapter.drain_pending_calls()
    except Exception:
        # The lease still has to be released if a provider drain reports a cleanup
        # failure. Keep diagnostics categorical for the same reason as aclose().
        LOGGER.warning("execution adapter drain failed provider=%s", provider.value)


async def _run_isolated(operation: Awaitable[int]) -> int:
    """Celery의 작업별 event loop가 닫히기 전에 asyncpg 연결 풀도 함께 정리한다."""
    try:
        return await operation
    finally:
        await engine.dispose()


def _watch_expiry_dependencies() -> WatchExpiryDependencies:
    return WatchExpiryDependencies(apply_watch_transition=apply_watch_transition)


async def _expire_elapsed_watches(session, now: datetime) -> int:
    return await expire_elapsed_watches(
        session,
        now,
        dependencies=_watch_expiry_dependencies(),
    )


async def _recover_stale_reservation_attempts(session, now: datetime) -> int:
    """Fence abandoned provider calls whose hold result can no longer be proven."""
    rows = list(
        (
            await session.execute(
                select(ReservationAttempt, WatchCandidate, Watch)
                .join(
                    WatchCandidate,
                    WatchCandidate.id == ReservationAttempt.candidate_id,
                )
                .join(Watch, Watch.id == WatchCandidate.watch_id)
                .where(
                    ReservationAttempt.outcome == ReservationOutcome.PENDING,
                    ReservationAttempt.started_at <= now - RESERVATION_ATTEMPT_STALE_AFTER,
                )
                # registration_evidence is a nullable joined relationship. Lock
                # only the required rows so PostgreSQL does not try to lock the
                # nullable side of that LEFT OUTER JOIN.
                .with_for_update(
                    of=(ReservationAttempt, WatchCandidate, Watch),
                    skip_locked=True,
                )
            )
        ).all()
    )
    for attempt, candidate, watch in rows:
        attempt.outcome = ReservationOutcome.UNKNOWN
        attempt.finished_at = now
        if watch.status == WatchStatus.RESERVING:
            # A process restart/timeout leaves the provider result unknown. Resume
            # observation without claiming an authentication failure. The completed
            # UNKNOWN remains a durable ambiguous-result fence for this candidate.
            candidate.state = "observed"
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.WATCHING,
                reason="stale_reservation_attempt_requires_manual_check",
            )
            if watch.next_check_at is None:
                watch.next_check_at = now
        elif watch.status == WatchStatus.EXPIRED:
            candidate.state = "expired"
        elif candidate.state == "reservation_attempted":
            candidate.state = "observed"
        await add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.reservation_attempt_recovery_required",
            payload={
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "reason": "reservation_attempt_result_unknown_after_restart",
            },
            dedupe_key=f"reservation-attempt-recovery:{attempt.id}",
        )
    if rows:
        await session.commit()
    return len(rows)


async def _provider_circuit_is_closed(provider: Provider) -> bool:
    """Compatibility adapter for reservation reconciliation."""
    return await provider_circuit_is_closed(
        provider,
        lease_grant=None,
        dependencies=_observation_group_dependencies(),
    )


async def _arm_supported_provider_watches(
    provider: Provider,
    now: datetime,
    *,
    adapter: ExecutionProvider | None = None,
) -> int:
    """Activate pre-existing official watches after an execution adapter becomes effective."""
    if provider not in _EXTERNAL_PROVIDERS:
        return 0
    execution_adapter = adapter or get_execution_provider(provider)
    if not execution_adapter.capabilities().seat_monitoring:
        return 0
    async with SessionFactory() as session:
        watches = list(
            (
                await session.scalars(
                    select(Watch)
                    .where(
                        Watch.provider == provider,
                        Watch.mode == "official",
                        Watch.status.in_(
                            [
                                WatchStatus.SCHEDULED,
                                WatchStatus.OFFICIAL_WAITLIST,
                                WatchStatus.SEAT_FOUND,
                            ]
                        ),
                        Watch.next_check_at.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for watch in watches:
            watch.next_check_at = now
        if watches:
            await session.commit()
        return len(watches)


async def _arm_supported_srt_watches(
    now: datetime,
    *,
    adapter: ExecutionProvider | None = None,
) -> int:
    """Compatibility wrapper for focused SRT worker tests."""
    return await _arm_supported_provider_watches(Provider.SRT, now, adapter=adapter)


def _execution_lease_service() -> ProviderExecutionLeaseService:
    # SessionFactory is replaceable in isolated tests, so construct this lazily.
    return ProviderExecutionLeaseService(SessionFactory)


async def _acquire_execution_lease(
    provider: Provider,
    now: datetime,
) -> tuple[ProviderExecutionLeaseService, ExecutionLeaseGrant | None]:
    service = _execution_lease_service()
    grant = await service.acquire(
        provider,
        ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        uuid4().hex,
        now=now,
        expires_at=now + PROVIDER_EXECUTION_LEASE_DURATION,
    )
    return service, grant


async def _update_provider_auth_status_in_reservation_transaction(
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


def _reservation_execution_dependencies() -> ReservationExecutionDependencies:
    return ReservationExecutionDependencies(
        session_factory=SessionFactory,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        apply_watch_transition=apply_watch_transition,
        begin_reservation_attempt=begin_reservation_attempt,
        add_outbox_event=add_outbox_event,
        complete_reservation_attempt=complete_reservation_attempt,
        record_reservation_confirmation=record_reservation_confirmation,
        update_provider_auth_status=_update_provider_auth_status_in_reservation_transaction,
        provider_call_errors=(ProviderUnavailable, RuntimeError, ValueError),
        srt_exact_reservation_source=SRT_RESERVATION_SOURCE,
    )


async def _reserve_winner(adapter: ExecutionProvider, target: ObservationTarget) -> None:
    """Compatibility wiring for worker and focused integration tests."""
    await execute_reservation(
        adapter,
        ReservationExecutionTarget(
            watch_id=target.watch_id,
            candidate_id=target.candidate_id,
            provider=target.provider,
            origin=target.origin,
            destination=target.destination,
            origin_node_id=target.origin_node_id,
            destination_node_id=target.destination_node_id,
            train_number=target.train_number,
            departure_at=target.departure_at,
            arrival_at=target.arrival_at,
            seat_class=target.seat_class,
            passenger_count=target.passenger_count,
            reservation_episode_key=target.reservation_episode_key,
        ),
        dependencies=_reservation_execution_dependencies(),
    )


def _observation_group_dependencies(
    lease_service: ProviderExecutionLeaseService | None = None,
    adapter: ExecutionProvider | None = None,
) -> ObservationGroupDependencies:
    async def lease_is_current(grant: object, *, now: datetime) -> bool:
        if lease_service is None:
            return True
        return await lease_service.is_current(grant, now=now)

    async def reserve_winner(target: ObservationTarget) -> None:
        if adapter is None:
            raise RuntimeError("reservation adapter is unavailable")
        await _reserve_winner(adapter, target)

    return ObservationGroupDependencies(
        session_factory=SessionFactory,
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        latest_observation_fingerprint=latest_observation_fingerprint,
        record_seat_observation=record_seat_observation,
        finish_observation_cycle=finish_observation_cycle,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        reserve_winner=reserve_winner,
        lease_is_current=lease_is_current,
        lease_is_current_in_session=lock_execution_lease_current,
        provider_call_errors=(ProviderUnavailable, RuntimeError, ValueError),
    )


async def _process_watch_group(
    watch_ids: list[str],
    now: datetime,
    *,
    provider: Provider | None = None,
    adapter: ExecutionProvider | None = None,
) -> None:
    provider = provider or await watch_group_provider(
        watch_ids,
        session_factory=SessionFactory,
    )
    if provider is None:
        return
    owns_adapter = adapter is None
    lease_service: ProviderExecutionLeaseService | None = None
    lease_grant: ExecutionLeaseGrant | None = None
    if provider in _EXTERNAL_PROVIDERS:
        lease_service, lease_grant = await _acquire_execution_lease(provider, now)
        if lease_grant is None:
            return

    try:
        if adapter is None:
            adapter = get_execution_provider(provider)
        await process_watch_group_observation(
            watch_ids,
            now,
            provider=provider,
            adapter=adapter,
            lease_grant=lease_grant,
            dependencies=_observation_group_dependencies(lease_service, adapter),
        )
    finally:
        try:
            if adapter is not None:
                await _drain_execution_adapter(adapter, provider)
        finally:
            try:
                if owns_adapter and adapter is not None:
                    await _close_execution_adapter(adapter, provider)
            finally:
                if lease_grant is not None:
                    await lease_service.release(
                        lease_grant,
                        now=datetime.now(timezone.utc),
                    )


def _due_pipeline_dependencies() -> DuePipelineDependencies:
    return DuePipelineDependencies(
        session_factory=SessionFactory,
        get_execution_provider=get_execution_provider,
        arm_provider_watches=_arm_supported_provider_watches,
        expire_elapsed_watches=_expire_elapsed_watches,
        recover_stale_reservation_attempts=_recover_stale_reservation_attempts,
        process_watch_group=_process_watch_group,
        reconcile_reservation_attempt=_reconcile_reservation_attempt,
        close_execution_adapter=_close_execution_adapter,
        reservation_reconciliation_due_clause=_reservation_reconciliation_due_clause,
    )


async def _process_due_watches() -> int:
    providers_to_arm = [Provider.SRT]
    if korail_background_monitoring_enabled(get_settings()):
        providers_to_arm.append(Provider.KORAIL)
    group_count = await process_due_pipeline(
        providers_to_arm,
        dependencies=_due_pipeline_dependencies(),
    )
    WATCH_GROUPS.inc(group_count)
    return group_count


async def _process_watch_now(watch_id: str) -> int:
    """Process one newly started watch through the normal lease and reservation fences."""
    await _process_watch_group([watch_id], datetime.now(timezone.utc))
    return 1


def _reconciliation_dependencies() -> ReconciliationDependencies:
    return ReconciliationDependencies(
        session_factory=SessionFactory,
        acquire_execution_lease=_acquire_execution_lease,
        get_execution_provider=get_execution_provider,
        drain_execution_adapter=_drain_execution_adapter,
        close_execution_adapter=_close_execution_adapter,
        provider_circuit_is_closed=_provider_circuit_is_closed,
    )


async def _reconcile_reservation_attempt(
    attempt_id: str,
    *,
    adapter: ExecutionProvider | None = None,
) -> int:
    return await run_reservation_reconciliation(
        attempt_id,
        dependencies=_reconciliation_dependencies(),
        adapter=adapter,
    )


@celery_app.task(name="rail_waitlist.worker.process_due_watches")
def process_due_watches() -> int:
    try:
        result = asyncio.run(_run_isolated(_process_due_watches()))
    except Exception:
        WORKER_RUNS.labels("process_due_watches", "failed").inc()
        raise
    WORKER_RUNS.labels("process_due_watches", "succeeded").inc()
    return result


@celery_app.task(name="rail_waitlist.worker.process_watch_now")
def process_watch_now(watch_id: str) -> int:
    try:
        result = asyncio.run(_run_isolated(_process_watch_now(watch_id)))
    except Exception:
        WORKER_RUNS.labels("process_watch_now", "failed").inc()
        raise
    WORKER_RUNS.labels("process_watch_now", "succeeded").inc()
    return result


@celery_app.task(name="rail_waitlist.worker.reconcile_reservation_attempt")
def reconcile_reservation_attempt(attempt_id: str) -> int:
    try:
        result = asyncio.run(_run_isolated(_reconcile_reservation_attempt(attempt_id)))
    except Exception:
        WORKER_RUNS.labels("reconcile_reservation_attempt", "failed").inc()
        raise
    WORKER_RUNS.labels("reconcile_reservation_attempt", "succeeded").inc()
    return result


@celery_app.task(name="rail_waitlist.worker.deliver_outbox")
def deliver_outbox() -> int:
    try:
        result = asyncio.run(_run_isolated(deliver_pending_notifications()))
    except Exception:
        WORKER_RUNS.labels("deliver_outbox", "failed").inc()
        raise
    WORKER_RUNS.labels("deliver_outbox", "succeeded").inc()
    return result
