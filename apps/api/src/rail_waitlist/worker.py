from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime, timezone
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from .celery_app import celery_app
from .config import get_settings
from .database import SessionFactory, engine
from .domain import (
    Provider,
)
from .metrics import WATCH_GROUPS, WORKER_RUNS
from .notification_management.delivery import deliver_pending_notifications
from .observations.contracts import SeatObservationResult
from .observations.cycle_application import (
    finish_observation_cycle,
    latest_observation_fingerprint,
)
from .observations.due_pipeline_application import (
    DuePipelineDependencies,
    process_due_pipeline,
)
from .observations.due_provider_policy import (
    select_provider_arm_targets as select_provider_arm_targets_policy,
)
from .observations.due_runtime import DueSweepRuntimeDependencies
from .observations.due_runtime import process_due_watches as process_due_watches_runtime
from .observations.group_application import (
    LockedLeaseCurrent,
    ObservationGroupDependencies,
    ObservationTarget,
    process_watch_group_observation,
    provider_circuit_is_closed,
    watch_group_provider,
)
from .observations.group_runtime import WatchGroupRuntimeDependencies
from .observations.group_runtime import (
    process_watch_group_runtime as process_watch_group_runtime_application,
)
from .observations.operational_projection_application import apply_operational_projection
from .observations.recording_application import ObservationRecordingDependencies
from .observations.recording_application import (
    record_seat_observation as record_seat_observation_application,
)
from .outbox import add_outbox_event
from .provider_account_management.application import update_provider_auth_status
from .provider_account_management.reservation_runtime import (
    update_provider_auth_status_in_reservation_transaction,
)
from .provider_account_management.schemas import RailProviderAuthStatus
from .provider_adapters.korail_execution import korail_background_monitoring_enabled
from .provider_circuit.application import get_or_create_provider_circuit
from .provider_contracts import ExecutionProvider, ProviderLifecycle, ProviderUnavailable
from .provider_execution.contracts import ExecutionLeaseGrant, ExecutionLeaseService
from .provider_execution.lease_application import (
    ANONYMOUS_PUBLIC_ACCOUNT_SCOPE as ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
)
from .provider_execution.lease_application import (
    PROVIDER_EXECUTION_LEASE_DURATION as PROVIDER_EXECUTION_LEASE_DURATION,
)
from .provider_execution.lease_application import (
    ExecutionLeaseAcquisitionDependencies,
    ProviderExecutionLeaseService,
    acquire_anonymous_public_execution_lease,
    lock_execution_lease_current,
)
from .provider_execution.lifecycle_runtime import (
    close_execution_adapter_safely,
    drain_execution_adapter_safely,
)
from .provider_registry.application import get_execution_provider
from .reservations.attempt_policy import (
    is_confirmed_absent_retry_source,
    is_unresolved_unknown_manual_rearm_source,
)
from .reservations.attempt_result_application import record_reservation_confirmation
from .reservations.attempt_runtime import (
    begin_reservation_attempt,
    complete_reservation_attempt,
)
from .reservations.execution_application import (
    ReservationExecutionDependencies,
)
from .reservations.execution_runtime import (
    ReservationWinnerTarget,
)
from .reservations.execution_runtime import (
    reserve_observation_winner as reserve_observation_winner_application,
)
from .reservations.payment_hold_application import _utc_instant, is_payment_hold_ended
from .reservations.provider_confirmation.contracts import ReservationConfirmationResult
from .reservations.reconciliation_application import (
    ReconciliationDependencies,
    _reservation_reconciliation_due_clause,
)
from .reservations.reconciliation_application import (
    reconcile_reservation_attempt as run_reservation_reconciliation,
)
from .reservations.reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)
from .reservations.reconciliation_state_runtime import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_runtime,
)
from .reservations.reconciliation_state_runtime import (
    reservation_reconciliation_state_dependencies,
)
from .reservations.stale_attempt_recovery_application import (
    RESERVATION_ATTEMPT_STALE_AFTER,
    StaleReservationAttemptRecoveryDependencies,
    recover_stale_reservation_attempts,
)
from .srt_sidecar.reservation import SRT_RESERVATION_SOURCE
from .watch_management.arming_application import (
    WatchArmingDependencies,
)
from .watch_management.arming_application import (
    arm_supported_provider_watches as arm_supported_provider_watches_application,
)
from .watch_management.expiry_application import (
    WatchExpiryDependencies,
    expire_elapsed_watches,
)
from .watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .watch_management.transition_runtime import apply_watch_transition
from .worker_task_runtime import run_task_isolated

LOGGER = logging.getLogger(__name__)


async def _close_execution_adapter(
    adapter: ProviderLifecycle,
    provider: Provider,
) -> None:
    await close_execution_adapter_safely(adapter, provider, logger=LOGGER)


async def _drain_execution_adapter(
    adapter: ProviderLifecycle,
    provider: Provider,
) -> None:
    await drain_execution_adapter_safely(adapter, provider, logger=LOGGER)


async def _run_isolated(operation: Awaitable[int]) -> int:
    """Celery의 작업별 event loop가 닫히기 전에 asyncpg 연결 풀도 함께 정리한다."""
    return await run_task_isolated(operation, dispose_engine=engine.dispose)


def _watch_expiry_dependencies() -> WatchExpiryDependencies:
    return WatchExpiryDependencies(apply_watch_transition=apply_watch_transition)


async def _expire_elapsed_watches(session: AsyncSession, now: datetime) -> int:
    return await expire_elapsed_watches(
        session,
        now,
        dependencies=_watch_expiry_dependencies(),
    )


async def _recover_stale_reservation_attempts(session: AsyncSession, now: datetime) -> int:
    return await recover_stale_reservation_attempts(
        session,
        now,
        stale_after=RESERVATION_ATTEMPT_STALE_AFTER,
        dependencies=StaleReservationAttemptRecoveryDependencies(
            apply_watch_transition=apply_watch_transition,
            add_outbox_event=add_outbox_event,
        ),
    )


async def _recover_stale_reservation_attempts_independently() -> int:
    async with SessionFactory() as session:
        return await _recover_stale_reservation_attempts(session, datetime.now(timezone.utc))


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
    """Compose the canonical arming UoW from replaceable worker dependencies."""
    return await arm_supported_provider_watches_application(
        provider,
        now,
        adapter=adapter,
        dependencies=WatchArmingDependencies(
            session_factory=SessionFactory,
            get_execution_provider=get_execution_provider,
        ),
    )


async def _arm_supported_srt_watches(
    now: datetime,
    *,
    adapter: ExecutionProvider | None = None,
) -> int:
    """Compatibility wrapper for focused SRT worker tests."""
    return await _arm_supported_provider_watches(Provider.SRT, now, adapter=adapter)


async def _acquire_execution_lease(
    provider: Provider,
    now: datetime,
) -> tuple[ProviderExecutionLeaseService, ExecutionLeaseGrant | None]:
    return await acquire_anonymous_public_execution_lease(
        provider,
        now,
        dependencies=ExecutionLeaseAcquisitionDependencies(session_factory=SessionFactory),
    )


async def _update_provider_auth_status_in_reservation_transaction(
    session: AsyncSession,
    provider: Provider,
    status: RailProviderAuthStatus,
    *,
    expected_credential_version: int,
) -> None:
    await update_provider_auth_status_in_reservation_transaction(
        session,
        provider,
        status,
        expected_credential_version=expected_credential_version,
        persist_auth_status=update_provider_auth_status,
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
    await reserve_observation_winner_application(
        adapter,
        cast(ReservationWinnerTarget, target),
        dependencies=_reservation_execution_dependencies(),
    )


def _observation_group_dependencies(
    lease_service: ExecutionLeaseService | None = None,
    adapter: ExecutionProvider | None = None,
) -> ObservationGroupDependencies:
    async def lease_is_current(grant: object, *, now: datetime) -> bool:
        if lease_service is None:
            return True
        if not isinstance(grant, ExecutionLeaseGrant):
            return False
        return await lease_service.is_current(grant, now=now)

    async def reserve_winner(target: ObservationTarget) -> None:
        if adapter is None:
            raise RuntimeError("reservation adapter is unavailable")
        await _reserve_winner(adapter, target)

    async def record_seat_observation(
        session: AsyncSession,
        watch: Watch,
        candidate: WatchCandidate,
        result: SeatObservationResult,
        *,
        apply_status_transition: bool = True,
    ) -> SeatObservation:
        return await record_seat_observation_application(
            session,
            watch,
            candidate,
            result,
            apply_status_transition=apply_status_transition,
            dependencies=ObservationRecordingDependencies(
                apply_operational_projection=apply_operational_projection,
                add_outbox_event=add_outbox_event,
                apply_watch_transition=apply_watch_transition,
            ),
        )

    return ObservationGroupDependencies(
        session_factory=SessionFactory,
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        latest_observation_fingerprint=latest_observation_fingerprint,
        record_seat_observation=record_seat_observation,
        finish_observation_cycle=finish_observation_cycle,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
        is_payment_hold_ended=is_payment_hold_ended,
        reserve_winner=reserve_winner,
        lease_is_current=lease_is_current,
        lease_is_current_in_session=cast(
            LockedLeaseCurrent,
            lock_execution_lease_current,
        ),
        provider_call_errors=(ProviderUnavailable, RuntimeError, ValueError),
    )


async def _process_watch_group(
    watch_ids: list[str],
    now: datetime,
    *,
    provider: Provider | None = None,
    adapter: ExecutionProvider | None = None,
) -> None:
    await process_watch_group_runtime_application(
        watch_ids,
        now,
        provider=provider,
        adapter=adapter,
        dependencies=WatchGroupRuntimeDependencies(
            session_factory=SessionFactory,
            watch_group_provider=watch_group_provider,
            acquire_execution_lease=_acquire_execution_lease,
            get_execution_provider=get_execution_provider,
            observation_group_dependencies=_observation_group_dependencies,
            process_watch_group_observation=process_watch_group_observation,
            drain_execution_adapter=_drain_execution_adapter,
            close_execution_adapter=_close_execution_adapter,
        ),
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


def _due_sweep_runtime_dependencies() -> DueSweepRuntimeDependencies:
    return DueSweepRuntimeDependencies(
        korail_background_enabled=lambda: korail_background_monitoring_enabled(get_settings()),
        select_provider_arm_targets=select_provider_arm_targets_policy,
        process_due_pipeline=process_due_pipeline,
        due_pipeline_dependencies=_due_pipeline_dependencies,
        record_group_count=WATCH_GROUPS.inc,
    )


async def _process_due_watches() -> int:
    return await process_due_watches_runtime(
        dependencies=_due_sweep_runtime_dependencies(),
    )


async def _process_watch_now(watch_id: str) -> int:
    """Process one newly started watch through the normal lease and reservation fences."""
    await _process_watch_group([watch_id], datetime.now(timezone.utc))
    return 1


def _reconciliation_state_dependencies() -> ReservationReconciliationStateDependencies:
    return reservation_reconciliation_state_dependencies(
        apply_watch_transition_override=apply_watch_transition,
        add_outbox_event_override=add_outbox_event,
        record_reservation_confirmation_override=record_reservation_confirmation,
        utc_instant_override=_utc_instant,
    )


async def _apply_reservation_reconciliation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime,
) -> None:
    await apply_reservation_reconciliation_runtime(
        session,
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=reconciled_at,
        dependencies=_reconciliation_state_dependencies(),
    )


def _reconciliation_dependencies() -> ReconciliationDependencies:
    return ReconciliationDependencies(
        session_factory=SessionFactory,
        acquire_execution_lease=_acquire_execution_lease,
        get_execution_provider=get_execution_provider,
        drain_execution_adapter=_drain_execution_adapter,
        close_execution_adapter=_close_execution_adapter,
        provider_circuit_is_closed=_provider_circuit_is_closed,
        apply_reconciliation=_apply_reservation_reconciliation,
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


@celery_app.task(name="rail_waitlist.worker.process_due_watches")  # type: ignore[untyped-decorator]
def process_due_watches() -> int:
    try:
        result = asyncio.run(_run_isolated(_process_due_watches()))
    except Exception:
        WORKER_RUNS.labels("process_due_watches", "failed").inc()
        raise
    WORKER_RUNS.labels("process_due_watches", "succeeded").inc()
    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    name="rail_waitlist.worker.recover_stale_reservation_attempts"
)
def recover_abandoned_reservations() -> int:
    """Recover orphaned claims even while the single rail worker is occupied."""
    try:
        result = asyncio.run(_run_isolated(_recover_stale_reservation_attempts_independently()))
    except Exception:
        WORKER_RUNS.labels("recover_stale_reservation_attempts", "failed").inc()
        raise
    WORKER_RUNS.labels("recover_stale_reservation_attempts", "succeeded").inc()
    return result


@celery_app.task(name="rail_waitlist.worker.process_watch_now")  # type: ignore[untyped-decorator]
def process_watch_now(watch_id: str) -> int:
    try:
        result = asyncio.run(_run_isolated(_process_watch_now(watch_id)))
    except Exception:
        WORKER_RUNS.labels("process_watch_now", "failed").inc()
        raise
    WORKER_RUNS.labels("process_watch_now", "succeeded").inc()
    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    name="rail_waitlist.worker.reconcile_reservation_attempt"
)
def reconcile_reservation_attempt(attempt_id: str) -> int:
    try:
        result = asyncio.run(_run_isolated(_reconcile_reservation_attempt(attempt_id)))
    except Exception:
        WORKER_RUNS.labels("reconcile_reservation_attempt", "failed").inc()
        raise
    WORKER_RUNS.labels("reconcile_reservation_attempt", "succeeded").inc()
    return result


@celery_app.task(name="rail_waitlist.worker.deliver_outbox")  # type: ignore[untyped-decorator]
def deliver_outbox() -> int:
    try:
        result = asyncio.run(_run_isolated(deliver_pending_notifications()))
    except Exception:
        WORKER_RUNS.labels("deliver_outbox", "failed").inc()
        raise
    WORKER_RUNS.labels("deliver_outbox", "succeeded").inc()
    return result
