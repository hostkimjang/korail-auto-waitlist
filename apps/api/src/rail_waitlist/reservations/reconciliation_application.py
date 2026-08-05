from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationOutcome, SeatClass, WatchStatus
from ..models import RailProviderAccount, ReservationAttempt, Watch, WatchCandidate
from ..provider_contracts import (
    ProviderLifecycle,
    ProviderUnavailable,
    ReconciliationExecutionProvider,
)
from ..provider_execution_lease import ExecutionLeaseGrant, lock_execution_lease_current
from ..reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .reconciliation_policy import (
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
    unknown_reconciliation_retry_interval,
)
from .reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)
from .reconciliation_state_application import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_application,
)

EXTERNAL_RECONCILIATION_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


class ReconciliationLeaseService(Protocol):
    async def is_current(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool: ...

    async def release(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool: ...


AcquireExecutionLease = Callable[
    [Provider, datetime],
    Awaitable[tuple[ReconciliationLeaseService, ExecutionLeaseGrant | None]],
]
ProviderGetter = Callable[[Provider], ReconciliationExecutionProvider]
AdapterLifecycle = Callable[[ProviderLifecycle, Provider], Awaitable[None]]
ProviderCircuitCheck = Callable[[Provider], Awaitable[bool]]


class ApplyReconciliation(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        candidate: WatchCandidate,
        attempt: ReservationAttempt,
        confirmation: ReservationConfirmationResult,
        *,
        reconciled_at: datetime,
    ) -> None: ...


class LockedLeaseCheck(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        grant: ExecutionLeaseGrant,
        *,
        now: datetime,
    ) -> bool: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReconciliationDependencies:
    session_factory: AsyncSessionFactory
    acquire_execution_lease: AcquireExecutionLease
    get_execution_provider: ProviderGetter
    drain_execution_adapter: AdapterLifecycle
    close_execution_adapter: AdapterLifecycle
    provider_circuit_is_closed: ProviderCircuitCheck
    lease_is_current_in_session: LockedLeaseCheck = lock_execution_lease_current
    state_dependencies: ReservationReconciliationStateDependencies | None = None
    apply_reconciliation: ApplyReconciliation | None = None
    now: Callable[[], datetime] = _now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reservation_reconciliation_due_clause(now: datetime):
    """Select bounded initial checks and legacy/stale payment holds needing refresh."""

    return or_(
        and_(
            ReservationAttempt.reconciliation_attempt_count
            < RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
            or_(
                and_(
                    ReservationAttempt.reconciliation_attempt_count == 0,
                    ReservationAttempt.next_reconcile_at.is_(None),
                ),
                ReservationAttempt.next_reconcile_at <= now,
                and_(
                    Watch.status == WatchStatus.PAYMENT_REQUIRED,
                    ReservationAttempt.reconciliation_attempt_count > 0,
                    ReservationAttempt.next_reconcile_at.is_(None),
                    or_(
                        Watch.payment_deadline.is_(None),
                        Watch.payment_deadline <= now,
                    ),
                ),
            ),
        ),
        and_(
            ReservationAttempt.outcome == ReservationOutcome.UNKNOWN,
            ReservationAttempt.confirmation_outcome == ReservationConfirmationOutcome.INCONCLUSIVE,
            ReservationAttempt.reconciliation_attempt_count
            >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
            ReservationAttempt.reconciliation_attempt_count < UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
            or_(
                ReservationAttempt.next_reconcile_at <= now,
                and_(
                    ReservationAttempt.next_reconcile_at.is_(None),
                    ReservationAttempt.last_reconciled_at.is_not(None),
                    or_(
                        and_(
                            ReservationAttempt.reconciliation_attempt_count == 3,
                            ReservationAttempt.last_reconciled_at <= now - timedelta(minutes=5),
                        ),
                        and_(
                            ReservationAttempt.reconciliation_attempt_count == 4,
                            ReservationAttempt.last_reconciled_at <= now - timedelta(minutes=15),
                        ),
                        and_(
                            ReservationAttempt.reconciliation_attempt_count == 5,
                            ReservationAttempt.last_reconciled_at <= now - timedelta(minutes=60),
                        ),
                    ),
                ),
            ),
        ),
        and_(
            Watch.status == WatchStatus.PAYMENT_REQUIRED,
            ReservationAttempt.reconciliation_attempt_count
            >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
            ReservationAttempt.outcome == ReservationOutcome.PAYMENT_REQUIRED,
            or_(
                ReservationAttempt.post_deadline_reconciled_at.is_(None),
                and_(
                    ReservationAttempt.reconciliation_attempt_count
                    == RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
                    ReservationAttempt.post_deadline_reconciled_at.is_not(None),
                    ReservationAttempt.confirmation_outcome
                    == ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                    ReservationAttempt.payment_deadline.is_not(None),
                    ReservationAttempt.payment_deadline
                    <= ReservationAttempt.post_deadline_reconciled_at,
                ),
            ),
            Watch.payment_deadline.is_not(None),
            Watch.payment_deadline <= now,
            or_(
                ReservationAttempt.next_reconcile_at.is_(None),
                ReservationAttempt.next_reconcile_at <= now,
            ),
        ),
    )


def _reservation_reconciliation_is_due(
    attempt: ReservationAttempt,
    watch: Watch,
    now: datetime,
) -> bool:
    if attempt.outcome is ReservationOutcome.UNKNOWN:
        if attempt.reconciliation_attempt_count >= UNKNOWN_RECONCILIATION_MAX_ATTEMPTS:
            return False
        if attempt.reconciliation_attempt_count == 0 and attempt.next_reconcile_at is None:
            return True
        if attempt.next_reconcile_at is not None:
            return _as_utc(attempt.next_reconcile_at) <= now
        retry_interval = unknown_reconciliation_retry_interval(attempt.reconciliation_attempt_count)
        return (
            attempt.confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
            and retry_interval is not None
            and attempt.last_reconciled_at is not None
            and _as_utc(attempt.last_reconciled_at) + retry_interval <= now
        )
    if attempt.reconciliation_attempt_count >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS:
        legacy_expired_hold_cleanup_due = (
            attempt.reconciliation_attempt_count == RESERVATION_RECONCILIATION_MAX_ATTEMPTS
            and attempt.post_deadline_reconciled_at is not None
            and attempt.confirmation_outcome
            is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
            and attempt.payment_deadline is not None
            and _as_utc(attempt.payment_deadline) <= _as_utc(attempt.post_deadline_reconciled_at)
        )
        return (
            watch.status is WatchStatus.PAYMENT_REQUIRED
            and attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
            and (attempt.post_deadline_reconciled_at is None or legacy_expired_hold_cleanup_due)
            and watch.payment_deadline is not None
            and _as_utc(watch.payment_deadline) <= now
            and (attempt.next_reconcile_at is None or _as_utc(attempt.next_reconcile_at) <= now)
        )
    if attempt.reconciliation_attempt_count == 0 and attempt.next_reconcile_at is None:
        return True
    if attempt.next_reconcile_at is not None:
        return _as_utc(attempt.next_reconcile_at) <= now
    return (
        watch.status is WatchStatus.PAYMENT_REQUIRED
        and 0 < attempt.reconciliation_attempt_count < RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and (watch.payment_deadline is None or _as_utc(watch.payment_deadline) <= now)
    )


async def reconcile_reservation_attempt(
    attempt_id: str,
    *,
    dependencies: ReconciliationDependencies,
    adapter: ReconciliationExecutionProvider | None = None,
) -> int:
    """Run one due read-only confirmation without ever replaying reservation."""

    now = dependencies.now()
    async with dependencies.session_factory() as session:
        row = (
            await session.execute(
                select(ReservationAttempt, WatchCandidate, Watch)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .join(Watch, Watch.id == WatchCandidate.watch_id)
                .where(
                    ReservationAttempt.id == attempt_id,
                    ReservationAttempt.outcome.in_(
                        [
                            ReservationOutcome.PAYMENT_REQUIRED,
                            ReservationOutcome.UNKNOWN,
                        ]
                    ),
                    ReservationAttempt.credential_version.is_not(None),
                    _reservation_reconciliation_due_clause(now),
                    Watch.provider.in_(EXTERNAL_RECONCILIATION_PROVIDERS),
                )
            )
        ).one_or_none()
        if row is None:
            return 0
        attempt, candidate, watch = row
        account_version = await session.scalar(
            select(RailProviderAccount.credential_version).where(
                RailProviderAccount.provider == watch.provider,
                RailProviderAccount.enabled.is_(True),
            )
        )
        if account_version != attempt.credential_version:
            return 0
        owner_watch_id = watch.id
        target = ReservationConfirmationTarget(
            attempt_id=attempt.id,
            candidate_id=candidate.id,
            provider=watch.provider,
            train_number=candidate.train_number,
            origin=watch.origin,
            destination=watch.destination,
            departure_at=_as_utc(candidate.departure_at),
            arrival_at=(
                _as_utc(candidate.arrival_at) if candidate.arrival_at is not None else None
            ),
            seat_class=SeatClass(candidate.seat_class),
            passenger_count=watch.passenger_count,
            credential_version=attempt.credential_version,
        )

    provider = target.provider
    if not await dependencies.provider_circuit_is_closed(provider):
        return 0
    owns_adapter = adapter is None
    lease_service, lease_grant = await dependencies.acquire_execution_lease(
        provider, dependencies.now()
    )
    if lease_grant is None:
        return 0
    try:
        if adapter is None:
            adapter = dependencies.get_execution_provider(provider)
        if adapter.provider != provider:
            raise RuntimeError("execution adapter provider does not match reservation attempt")
        if not adapter.capabilities().reservation_once:
            return 0
        try:
            confirmation = await adapter.confirm_reservation(target)
        except (ProviderUnavailable, RuntimeError, TypeError, ValueError):
            confirmation = ReservationConfirmationResult(
                provider=provider,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                source="worker-reconciliation",
                observed_at=dependencies.now(),
            )
        if not await lease_service.is_current(
            lease_grant,
            now=dependencies.now(),
        ):
            return 0
        async with dependencies.session_factory() as session:
            # Keep the global lease -> account -> watch -> candidate -> attempt order.
            # The same transaction holds the lease row through the state commit.
            if not await dependencies.lease_is_current_in_session(
                session,
                lease_grant,
                now=dependencies.now(),
            ):
                return 0
            account = await session.scalar(
                select(RailProviderAccount)
                .where(
                    RailProviderAccount.provider == provider,
                    RailProviderAccount.enabled.is_(True),
                )
                .with_for_update()
            )
            if account is None or account.credential_version != target.credential_version:
                return 0
            watch = await session.scalar(
                select(Watch).where(Watch.id == owner_watch_id).with_for_update()
            )
            candidate = await session.scalar(
                select(WatchCandidate)
                .where(WatchCandidate.id == target.candidate_id)
                .with_for_update(of=WatchCandidate)
            )
            attempt = await session.scalar(
                select(ReservationAttempt)
                .where(ReservationAttempt.id == attempt_id)
                .with_for_update()
            )
            if watch is None or candidate is None or attempt is None:
                return 0
            reconciled_at = dependencies.now()
            if (
                attempt.credential_version != target.credential_version
                or not _reservation_reconciliation_is_due(
                    attempt,
                    watch,
                    reconciled_at,
                )
                or attempt.outcome
                not in {
                    ReservationOutcome.PAYMENT_REQUIRED,
                    ReservationOutcome.UNKNOWN,
                }
            ):
                return 0
            if (
                attempt.candidate_id != candidate.id
                or candidate.watch_id != watch.id
                or watch.provider != provider
            ):
                return 0
            if not await dependencies.lease_is_current_in_session(
                session,
                lease_grant,
                now=dependencies.now(),
            ):
                return 0
            if dependencies.apply_reconciliation is not None:
                await dependencies.apply_reconciliation(
                    session,
                    watch,
                    candidate,
                    attempt,
                    confirmation,
                    reconciled_at=reconciled_at,
                )
            else:
                state_dependencies = dependencies.state_dependencies
                if state_dependencies is None:
                    raise RuntimeError("reservation reconciliation state dependencies are missing")
                await apply_reservation_reconciliation_application(
                    session,
                    watch,
                    candidate,
                    attempt,
                    confirmation,
                    reconciled_at=reconciled_at,
                    dependencies=state_dependencies,
                )
            await session.commit()
        return 1
    finally:
        try:
            if adapter is not None:
                await dependencies.drain_execution_adapter(adapter, provider)
        finally:
            try:
                if owns_adapter and adapter is not None:
                    await dependencies.close_execution_adapter(adapter, provider)
            finally:
                await lease_service.release(
                    lease_grant,
                    now=dependencies.now(),
                )
