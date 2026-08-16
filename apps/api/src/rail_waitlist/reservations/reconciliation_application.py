from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from ..domain import (
    Provider,
    ReservationOutcome,
    SeatClass,
    WatchStatus,
)
from ..provider_account_management.models import RailProviderAccount
from ..provider_call_context import bind_request_id
from ..provider_contracts import (
    ProviderLifecycle,
    ProviderUnavailable,
    ReconciliationExecutionProvider,
)
from ..provider_execution.contracts import AcquireExecutionLease, ExecutionLeaseGrant
from ..provider_execution.lease_application import lock_execution_lease_current
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from .contracts import POST_REQUEST_UNKNOWN_CORRELATION_REASON_CODES
from .provider_confirmation.contracts import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)
from .provider_confirmation.safety_policy import enforce_confirmation_target_safety
from .reconciliation_policy import (
    PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS,
    RESERVATION_RECONCILIATION_INTERVAL,
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
    payment_hold_reconciliation_retry_interval,
    unknown_reconciliation_retry_interval,
)
from .reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)
from .reconciliation_state_application import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_application,
)

EXTERNAL_RECONCILIATION_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})
logger = logging.getLogger(__name__)


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


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


def _confirmation_purpose(
    watch: Watch,
    attempt: ReservationAttempt,
) -> ReservationConfirmationPurpose:
    if (
        watch.status is WatchStatus.PAYMENT_REQUIRED
        and attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.confirmation_outcome
        not in {
            ReservationConfirmationOutcome.CONFIRMED_PAID,
        }
    ):
        return ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
    if attempt.outcome is ReservationOutcome.UNKNOWN:
        return ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
    return ReservationConfirmationPurpose.INITIAL


def _has_valid_reservation_requested_progress(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"stage", "occurred_at"}:
            continue
        if item.get("stage") != "reservation_requested":
            continue
        occurred_at = item.get("occurred_at")
        if not isinstance(occurred_at, str):
            continue
        try:
            parsed = datetime.fromisoformat(occurred_at)
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return True
    return False


def _persisted_confirmation_seats(
    value: object,
    *,
    max_count: int,
) -> tuple[ReservationConfirmationSeat, ...]:
    """Validate database JSON before it crosses the provider confirmation boundary."""

    if not isinstance(value, list) or len(value) > max_count:
        return ()
    seats: list[ReservationConfirmationSeat] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"car_number", "seat_number"}:
            return ()
        car_number = item.get("car_number")
        seat_number = item.get("seat_number")
        if not isinstance(car_number, str) or not isinstance(seat_number, str):
            return ()
        try:
            seats.append(
                ReservationConfirmationSeat(
                    car_number=car_number,
                    seat_number=seat_number,
                )
            )
        except ValueError:
            return ()
    seat_keys = tuple((seat.car_number, seat.seat_number) for seat in seats)
    if len(seat_keys) != len(set(seat_keys)):
        return ()
    return tuple(seats)


def _trusted_unknown_correlation_seats(
    attempt: ReservationAttempt,
    *,
    passenger_count: int,
) -> tuple[ReservationConfirmationSeat, ...]:
    """Return only durable, post-request, full-passenger UNKNOWN correlation."""

    persisted = _persisted_confirmation_seats(
        attempt.confirmation_correlation_seats,
        max_count=passenger_count,
    )
    if (
        attempt.outcome is ReservationOutcome.UNKNOWN
        and attempt.result_reason_code in POST_REQUEST_UNKNOWN_CORRELATION_REASON_CODES
        and _has_valid_reservation_requested_progress(attempt.progress_stages)
        and len(persisted) == passenger_count
    ):
        return persisted
    return ()


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
            Watch.status == WatchStatus.PAYMENT_REQUIRED,
            ReservationAttempt.outcome == ReservationOutcome.PAYMENT_REQUIRED,
            or_(
                ReservationAttempt.confirmation_outcome.is_(None),
                ReservationAttempt.confirmation_outcome.in_(
                    [
                        ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                        ReservationConfirmationOutcome.INCONCLUSIVE,
                        ReservationConfirmationOutcome.NOT_FOUND,
                        ReservationConfirmationOutcome.AUTH_REQUIRED,
                        ReservationConfirmationOutcome.PROVIDER_BLOCKED,
                    ]
                ),
            ),
            ReservationAttempt.reconciliation_attempt_count > 0,
            ReservationAttempt.reconciliation_attempt_count
            < PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS,
            or_(
                ReservationAttempt.next_reconcile_at <= now,
                and_(
                    ReservationAttempt.next_reconcile_at.is_(None),
                    ReservationAttempt.last_reconciled_at.is_not(None),
                    or_(
                        and_(
                            ReservationAttempt.reconciliation_attempt_count.in_([1, 2]),
                            ReservationAttempt.last_reconciled_at
                            <= now - RESERVATION_RECONCILIATION_INTERVAL,
                        ),
                        and_(
                            ReservationAttempt.reconciliation_attempt_count == 3,
                            ReservationAttempt.last_reconciled_at <= now - timedelta(minutes=2),
                        ),
                        and_(
                            ReservationAttempt.reconciliation_attempt_count == 4,
                            ReservationAttempt.last_reconciled_at <= now - timedelta(minutes=5),
                        ),
                        and_(
                            ReservationAttempt.reconciliation_attempt_count == 5,
                            ReservationAttempt.last_reconciled_at <= now - timedelta(minutes=10),
                        ),
                    ),
                ),
            ),
            Watch.payment_deadline.is_not(None),
            Watch.payment_deadline > now,
        ),
        and_(
            ReservationAttempt.outcome == ReservationOutcome.UNKNOWN,
            ReservationAttempt.confirmation_outcome.in_(
                [
                    ReservationConfirmationOutcome.INCONCLUSIVE,
                    ReservationConfirmationOutcome.NOT_FOUND,
                    ReservationConfirmationOutcome.AUTH_REQUIRED,
                    ReservationConfirmationOutcome.PROVIDER_BLOCKED,
                ]
            ),
            ReservationAttempt.reconciliation_attempt_count
            >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
            ReservationAttempt.reconciliation_attempt_count < UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
            or_(
                ReservationAttempt.next_reconcile_at <= now,
                and_(
                    ReservationAttempt.confirmation_outcome
                    == ReservationConfirmationOutcome.INCONCLUSIVE,
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
                ReservationAttempt.confirmation_outcome.is_(None),
                ReservationAttempt.confirmation_outcome.not_in(
                    [
                        ReservationConfirmationOutcome.CONFIRMED_PAID,
                    ]
                ),
            ),
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


def _watch_has_no_exact_paid_confirmation_clause() -> ColumnElement[bool]:
    paid_attempt = aliased(ReservationAttempt)
    paid_candidate = aliased(WatchCandidate)
    return ~exists(
        select(paid_attempt.id)
        .join(paid_candidate, paid_candidate.id == paid_attempt.candidate_id)
        .where(
            paid_candidate.watch_id == Watch.id,
            paid_attempt.confirmation_outcome == ReservationConfirmationOutcome.CONFIRMED_PAID,
        )
        .correlate(Watch)
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
    bounded_payment_confirmation_due = (
        watch.status is WatchStatus.PAYMENT_REQUIRED
        and attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.confirmation_outcome
        not in {
            ReservationConfirmationOutcome.CONFIRMED_PAID,
        }
        and 0 < attempt.reconciliation_attempt_count < PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS
        and (
            (attempt.next_reconcile_at is not None and _as_utc(attempt.next_reconcile_at) <= now)
            or (
                attempt.next_reconcile_at is None
                and attempt.last_reconciled_at is not None
                and (
                    retry_interval := payment_hold_reconciliation_retry_interval(
                        attempt.reconciliation_attempt_count
                    )
                )
                is not None
                and _as_utc(attempt.last_reconciled_at) + retry_interval <= now
            )
        )
        and watch.payment_deadline is not None
        and _as_utc(watch.payment_deadline) > now
    )
    if bounded_payment_confirmation_due:
        return True
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
            and attempt.confirmation_outcome
            not in {
                ReservationConfirmationOutcome.CONFIRMED_PAID,
            }
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
                    _watch_has_no_exact_paid_confirmation_clause(),
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
                RailProviderAccount.last_auth_status == "authenticated",
            )
        )
        if account_version != attempt.credential_version:
            return 0
        owner_watch_id = watch.id
        persisted_reserved_seats = _persisted_confirmation_seats(
            attempt.reserved_seats,
            max_count=watch.passenger_count,
        )
        trusted_correlation_seats = _trusted_unknown_correlation_seats(
            attempt,
            passenger_count=watch.passenger_count,
        )
        purpose = _confirmation_purpose(watch, attempt)
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
            purpose=purpose,
            reserved_seats=(
                persisted_reserved_seats
                if purpose is ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
                else ()
            ),
            confirmation_correlation_seats=(
                trusted_correlation_seats
                if purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
                else ()
            ),
        )
        reconciliation_attempt = attempt.reconciliation_attempt_count + 1

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
        with bind_request_id() as request_id:
            logger.info(
                "Reservation confirmation started event=reservation_confirmation_started "
                "phase=worker_reconciliation provider=%s purpose=%s attempt_id=%s request_id=%s "
                "reconciliation_attempt=%s",
                provider.value,
                target.purpose.value,
                attempt_id,
                request_id,
                reconciliation_attempt,
            )
            try:
                confirmation = await adapter.confirm_reservation(target)
            except ProviderUnavailable:
                confirmation = ReservationConfirmationResult(
                    provider=provider,
                    outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                    source="worker-reconciliation",
                    observed_at=dependencies.now(),
                    diagnostic_code=(
                        ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE
                    ),
                )
            except (RuntimeError, TypeError, ValueError):
                confirmation = ReservationConfirmationResult(
                    provider=provider,
                    outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                    source="worker-reconciliation",
                    observed_at=dependencies.now(),
                    diagnostic_code=(
                        ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE
                    ),
                )
            confirmation = enforce_confirmation_target_safety(target, confirmation)
            logger.info(
                "Reservation confirmation classified event=reservation_confirmation_classified "
                "phase=worker_reconciliation provider=%s purpose=%s outcome=%s "
                "confirmation_diagnostic_code=%s source=%s attempt_id=%s request_id=%s "
                "reconciliation_attempt=%s",
                provider.value,
                target.purpose.value,
                confirmation.outcome.value,
                (
                    confirmation.diagnostic_code.value
                    if confirmation.diagnostic_code is not None
                    else "none"
                ),
                confirmation.source,
                attempt_id,
                request_id,
                reconciliation_attempt,
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
                    RailProviderAccount.last_auth_status == "authenticated",
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
                auth_status = (
                    "auth_required"
                    if confirmation.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED
                    else "provider_blocked"
                    if confirmation.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED
                    else None
                )
                if auth_status is not None and dependencies.state_dependencies is not None:
                    updated = await dependencies.state_dependencies.update_provider_auth_status(
                        session,
                        provider,
                        auth_status,
                        expected_credential_version=target.credential_version,
                    )
                    if updated:
                        await session.commit()
                return 0
            paid_attempt_id = await session.scalar(
                select(ReservationAttempt.id)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .where(
                    WatchCandidate.watch_id == watch.id,
                    ReservationAttempt.confirmation_outcome
                    == ReservationConfirmationOutcome.CONFIRMED_PAID,
                )
                .limit(1)
                .with_for_update(of=ReservationAttempt)
            )
            reconciled_at = dependencies.now()
            if (
                paid_attempt_id is not None
                or attempt.credential_version != target.credential_version
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
            locked_reserved_seats = _persisted_confirmation_seats(
                attempt.reserved_seats,
                max_count=watch.passenger_count,
            )
            locked_trusted_correlation_seats = _trusted_unknown_correlation_seats(
                attempt,
                passenger_count=watch.passenger_count,
            )
            locked_purpose = _confirmation_purpose(watch, attempt)
            expected_target_seats = (
                locked_reserved_seats
                if locked_purpose is ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
                else ()
            )
            expected_correlation_seats = (
                locked_trusted_correlation_seats
                if locked_purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
                else ()
            )
            if (
                locked_purpose is not target.purpose
                or expected_target_seats != target.reserved_seats
                or expected_correlation_seats != target.confirmation_correlation_seats
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
            logger.info(
                "Reservation confirmation persisted event=reservation_confirmation_persisted "
                "phase=worker_reconciliation provider=%s purpose=%s outcome=%s "
                "confirmation_diagnostic_code=%s source=%s attempt_id=%s request_id=%s "
                "reconciliation_attempt=%s "
                "reconciliation_attempt_count=%s next_reconcile_at=%s",
                provider.value,
                target.purpose.value,
                confirmation.outcome.value,
                (
                    confirmation.diagnostic_code.value
                    if confirmation.diagnostic_code is not None
                    else "none"
                ),
                confirmation.source,
                attempt_id,
                request_id,
                reconciliation_attempt,
                attempt.reconciliation_attempt_count,
                (
                    attempt.next_reconcile_at.isoformat()
                    if attempt.next_reconcile_at is not None
                    else "none"
                ),
            )
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
