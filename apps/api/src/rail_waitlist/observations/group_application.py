from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatObservationStatus,
    WatchStatus,
)
from ..operational import decide_operational_expiry
from ..provider_account_management.models import RailProviderAccount
from ..provider_circuit.models import ProviderCircuit
from ..provider_contracts import ObservationProvider
from ..reservations.attempt_policy import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
    CONFIRMED_ABSENT_RETRY_OBSERVATIONS,
    active_unresolved_unknown_attempt_ids,
    exact_paid_reservation_attempt_id,
    manual_payment_hold_rearm_episode_key,
    manual_unknown_rearm_episode_key,
    official_seat_observation_source,
    payment_hold_retry_episode_key,
)
from ..reservations.payment_hold_retry_application import (
    active_watch_payment_hold_fence,
    conclusive_unavailable_after_hold,
)
from ..reservations.progress_timing_policy import (
    has_persisted_reservation_requested_progress,
)
from ..watch_management.models import (
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
)
from .contracts import SeatObservationRequest, SeatObservationResult
from .status_policy import ACTIONABLE_SEAT_STATUSES, SEAT_FOUND_STATUSES

OBSERVATION_WATCH_STATUSES = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }
)
OBSERVABLE_CANDIDATE_STATES = frozenset({"active", "observed", "seat_found"})
ACCOUNT_AUTH_OBSERVATION_BLOCK_STATUSES = frozenset({"provider_blocked"})
OBSERVATION_IN_FLIGHT_DURATION = timedelta(minutes=1)
CONCLUSIVE_UNAVAILABLE_SEAT_STATUSES = frozenset(
    {
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.STANDING_ONLY,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }
)


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


@dataclass(frozen=True)
class ObservationTarget:
    watch_id: str
    candidate_id: str
    provider: Provider
    origin: str
    destination: str
    origin_node_id: str
    destination_node_id: str
    train_number: str
    departure_at: datetime
    arrival_at: datetime | None
    seat_class: str
    passenger_count: int
    priority: int
    reservation_episode_key: str | None = None

    @property
    def cache_key(self) -> tuple[object, ...]:
        return (
            self.provider,
            self.origin,
            self.destination,
            self.origin_node_id,
            self.destination_node_id,
            self.train_number,
            self.departure_at,
            self.seat_class,
            self.passenger_count,
        )

    def observation_request(self) -> SeatObservationRequest:
        return SeatObservationRequest(
            provider=self.provider,
            origin=self.origin,
            destination=self.destination,
            origin_node_id=self.origin_node_id,
            destination_node_id=self.destination_node_id,
            train_number=self.train_number,
            departure_at=self.departure_at,
            seat_class=self.seat_class,
            passenger_count=self.passenger_count,
        )


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        *,
        reason: str | None = None,
        observation: SeatObservation | None = None,
    ) -> Watch: ...


class AddOutboxEvent(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object: ...


class GetOrCreateProviderCircuit(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        *,
        lock: bool = False,
    ) -> ProviderCircuit: ...


class LatestObservationFingerprint(Protocol):
    async def __call__(self, session: AsyncSession, watch: Watch) -> str | None: ...


class RecordSeatObservation(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        candidate: WatchCandidate,
        result: SeatObservationResult,
        *,
        apply_status_transition: bool = True,
    ) -> SeatObservation: ...


class FinishObservationCycle(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        previous_fingerprint: str | None,
        now: datetime,
    ) -> None: ...


class ReservationDelegate(Protocol):
    async def __call__(self, target: ObservationTarget) -> None: ...


class ConfirmedAbsentRetryPredicate(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


class UnresolvedUnknownManualRearmPredicate(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


class PaymentHoldEndedPredicate(Protocol):
    def __call__(self, attempt: ReservationAttempt) -> bool: ...


class UtcNow(Protocol):
    def __call__(self) -> datetime: ...


class LeaseCurrent(Protocol):
    async def __call__(self, grant: object, *, now: datetime) -> bool: ...


class LockedLeaseCurrent(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        grant: object,
        *,
        now: datetime,
    ) -> bool: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ObservationGroupDependencies:
    session_factory: AsyncSessionFactory
    apply_watch_transition: ApplyWatchTransition
    add_outbox_event: AddOutboxEvent
    get_or_create_provider_circuit: GetOrCreateProviderCircuit
    latest_observation_fingerprint: LatestObservationFingerprint
    record_seat_observation: RecordSeatObservation
    finish_observation_cycle: FinishObservationCycle
    is_confirmed_absent_retry_source: ConfirmedAbsentRetryPredicate
    is_unresolved_unknown_manual_rearm_source: UnresolvedUnknownManualRearmPredicate
    is_payment_hold_ended: PaymentHoldEndedPredicate
    reserve_winner: ReservationDelegate
    # Runtime composition owns the concrete grant; the application only passes its
    # opaque fencing token to the two lease-current ports.
    lease_is_current: LeaseCurrent
    lease_is_current_in_session: LockedLeaseCurrent
    provider_call_errors: tuple[type[Exception], ...]
    now: UtcNow = _utc_now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def retryable_reservation_episode_key(
    session: AsyncSession,
    candidate: WatchCandidate,
    current_observation: SeatObservation,
    provider: Provider,
    *,
    is_confirmed_absent_retry_source: ConfirmedAbsentRetryPredicate,
    is_unresolved_unknown_manual_rearm_source: UnresolvedUnknownManualRearmPredicate,
    is_payment_hold_ended: PaymentHoldEndedPredicate,
) -> str | None:
    """Return one stable provider-call fence for the current availability episode."""
    watch_attempts = list(
        (
            await session.scalars(
                select(ReservationAttempt)
                .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
                .where(WatchCandidate.watch_id == candidate.watch_id)
                .order_by(
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.id.desc(),
                )
            )
        ).all()
    )
    attempts_by_id = {attempt.id: attempt for attempt in watch_attempts}
    paid_attempt_id = exact_paid_reservation_attempt_id(watch_attempts)
    if paid_attempt_id is not None:
        return None
    unresolved_unknown_ids = active_unresolved_unknown_attempt_ids(watch_attempts)
    if len(unresolved_unknown_ids) > 1:
        return None
    unresolved_unknown_attempt = (
        attempts_by_id[next(iter(unresolved_unknown_ids))] if unresolved_unknown_ids else None
    )
    if (
        unresolved_unknown_attempt is not None
        and unresolved_unknown_attempt.candidate_id != candidate.id
    ):
        return None
    payment_hold_fence = await active_watch_payment_hold_fence(
        session,
        candidate.watch_id,
        is_payment_hold_ended=is_payment_hold_ended,
    )
    if unresolved_unknown_attempt is not None and payment_hold_fence is not None:
        return None
    if payment_hold_fence is not None:
        manual_rearm_authorized_at = candidate.manual_rearm_authorized_at
        if (
            current_observation.source == official_seat_observation_source(provider)
            and candidate.manual_rearm_source_attempt_id == payment_hold_fence.attempt.id
            and manual_rearm_authorized_at is not None
            and _as_utc(manual_rearm_authorized_at) >= payment_hold_fence.ended_at
            and _as_utc(current_observation.observed_at) > _as_utc(manual_rearm_authorized_at)
        ):
            return manual_payment_hold_rearm_episode_key(
                payment_hold_fence.attempt.id,
                candidate.id,
                current_observation.id,
            )
        unavailable_observation = await conclusive_unavailable_after_hold(
            session,
            payment_hold_fence,
            candidate.id,
            before=current_observation.observed_at,
        )
        if unavailable_observation is None:
            return None
        return payment_hold_retry_episode_key(
            payment_hold_fence.attempt.id,
            unavailable_observation.id,
        )
    latest_attempt = await session.scalar(
        select(ReservationAttempt)
        .where(ReservationAttempt.candidate_id == candidate.id)
        .order_by(ReservationAttempt.attempt_sequence.desc())
        .limit(1)
    )
    if latest_attempt is None:
        return f"availability:{current_observation.id}"
    manual_rearm_authorized_at = candidate.manual_rearm_authorized_at
    manual_rearm_source_attempt_id = candidate.manual_rearm_source_attempt_id
    if (
        manual_rearm_authorized_at is not None
        and manual_rearm_source_attempt_id == latest_attempt.id
        and latest_attempt.credential_version is not None
        and is_unresolved_unknown_manual_rearm_source(latest_attempt)
        and current_observation.source == official_seat_observation_source(provider)
        and current_observation.status in CONFIRMED_ABSENT_RETRY_OBSERVATIONS
        and _as_utc(current_observation.observed_at) > _as_utc(manual_rearm_authorized_at)
    ):
        current_credential_version = await session.scalar(
            select(RailProviderAccount.credential_version).where(
                RailProviderAccount.provider == provider,
                RailProviderAccount.enabled.is_(True),
                RailProviderAccount.last_auth_status == "authenticated",
            )
        )
        if (
            unresolved_unknown_attempt is not None
            and unresolved_unknown_attempt.id == latest_attempt.id
            and current_credential_version == latest_attempt.credential_version
        ):
            return manual_unknown_rearm_episode_key(
                latest_attempt.id,
                candidate.id,
                current_observation.id,
            )
    if unresolved_unknown_attempt is not None:
        return None
    if latest_attempt.outcome is ReservationOutcome.NOT_AVAILABLE:
        finished_at = _as_utc(latest_attempt.finished_at or latest_attempt.started_at)
        unavailable_observation = await session.scalar(
            select(SeatObservation)
            .where(
                SeatObservation.candidate_id == candidate.id,
                SeatObservation.observed_at > finished_at,
                SeatObservation.observed_at < current_observation.observed_at,
                SeatObservation.status.in_(CONCLUSIVE_UNAVAILABLE_SEAT_STATUSES),
            )
            .order_by(SeatObservation.observed_at, SeatObservation.id)
            .limit(1)
        )
        if unavailable_observation is None:
            return None
        return f"availability-after:{unavailable_observation.id}"
    if latest_attempt.outcome in {
        ReservationOutcome.AUTH_REQUIRED,
        ReservationOutcome.PROVIDER_BLOCKED,
    }:
        if has_persisted_reservation_requested_progress(latest_attempt.progress_stages):
            return None
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == provider,
                RailProviderAccount.enabled.is_(True),
                RailProviderAccount.last_auth_status == "authenticated",
            )
        )
        if account is None or account.last_authenticated_at is None:
            return None
        authenticated_at = _as_utc(account.last_authenticated_at)
        attempt_finished_at = _as_utc(latest_attempt.finished_at or latest_attempt.started_at)
        if authenticated_at <= attempt_finished_at:
            return None
        generation = int(authenticated_at.timestamp() * 1_000_000)
        return f"auth:{account.credential_version}:{generation}"
    confirmation_observed_at = latest_attempt.confirmation_observed_at
    safe_unknown_rediscovery = (
        latest_attempt.outcome is ReservationOutcome.UNKNOWN
        and current_observation.source == official_seat_observation_source(provider)
        and current_observation.status in CONFIRMED_ABSENT_RETRY_OBSERVATIONS
    )
    legacy_payment_hold_rediscovery = latest_attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    if (
        is_confirmed_absent_retry_source(latest_attempt)
        and confirmation_observed_at is not None
        and _as_utc(current_observation.observed_at) > _as_utc(confirmation_observed_at)
        and (safe_unknown_rediscovery or legacy_payment_hold_rediscovery)
    ):
        return f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}{latest_attempt.id}"
    return None


async def watch_group_provider(
    watch_ids: list[str], *, session_factory: AsyncSessionFactory
) -> Provider | None:
    async with session_factory() as session:
        providers = set(
            (
                await session.scalars(
                    select(Watch.provider).where(Watch.id.in_(watch_ids)).distinct()
                )
            ).all()
        )
    if len(providers) != 1:
        return None
    return providers.pop()


async def _lease_allows_external_work(
    lease_grant: object | None,
    *,
    dependencies: ObservationGroupDependencies,
) -> bool:
    return lease_grant is None or await dependencies.lease_is_current(
        lease_grant, now=dependencies.now()
    )


async def _lease_allows_locked_write(
    session: AsyncSession,
    lease_grant: object | None,
    *,
    dependencies: ObservationGroupDependencies,
) -> bool:
    return lease_grant is None or await dependencies.lease_is_current_in_session(
        session,
        lease_grant,
        now=dependencies.now(),
    )


async def _pause_unexecutable_watch(
    session: AsyncSession,
    watch: Watch,
    *,
    reason: str,
    event_type: str,
    dependencies: ObservationGroupDependencies,
) -> None:
    if watch.status == WatchStatus.SCHEDULED:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason="worker_claimed_due_watch",
        )
    if watch.status in {
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.PAUSED,
            reason=reason,
        )
    watch.next_check_at = None
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type=event_type,
        payload={"watch_id": watch.id, "status": watch.status.value, "reason": reason},
        dedupe_key=f"watch:{watch.id}:{event_type}:{reason}",
    )


async def _blocking_provider_account_auth_status(
    session: AsyncSession,
    provider: Provider,
) -> str | None:
    """Return a persisted account state that must stop external observation I/O.

    The provider runtime owns the bounded re-verification attempts. A watch worker must
    only consume that sanitized state; retrying the provider from every due watch would
    amplify an automation-protection response.
    """
    if provider not in {Provider.KORAIL, Provider.SRT}:
        return None
    auth_status = await session.scalar(
        select(RailProviderAccount.last_auth_status).where(
            RailProviderAccount.provider == provider,
            RailProviderAccount.enabled.is_(True),
            RailProviderAccount.last_auth_status.in_(ACCOUNT_AUTH_OBSERVATION_BLOCK_STATUSES),
        )
    )
    if isinstance(auth_status, str) and auth_status in ACCOUNT_AUTH_OBSERVATION_BLOCK_STATUSES:
        return auth_status
    return None


async def _defer_for_blocking_provider_account(
    session: AsyncSession,
    watch: Watch,
    *,
    dependencies: ObservationGroupDependencies,
) -> None:
    """Persist one account-wide observation hold without issuing provider I/O."""
    if watch.status == WatchStatus.SCHEDULED:
        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason="worker_claimed_due_watch",
        )
    await dependencies.apply_watch_transition(
        session,
        watch,
        WatchStatus.AUTH_REQUIRED,
        reason="provider_account_provider_blocked_before_observation",
    )
    # The provider session manager owns the 900-second protection backoff and the
    # generation-fenced recovery attempt. AUTH_REQUIRED is outside the due selector,
    # so storing a second watch-local retry clock would misrepresent the real gate.
    watch.cooldown_until = None
    watch.next_check_at = None


async def prepare_watch(
    watch_id: str,
    now: datetime,
    *,
    adapter: ObservationProvider,
    lease_grant: object | None,
    dependencies: ObservationGroupDependencies,
) -> list[ObservationTarget]:
    async with dependencies.session_factory() as session:
        try:
            if not await _lease_allows_locked_write(
                session, lease_grant, dependencies=dependencies
            ):
                return []
            watch = await session.scalar(
                select(Watch).where(Watch.id == watch_id).with_for_update()
            )
            if watch is None or watch.status not in OBSERVATION_WATCH_STATUSES:
                return []
            if watch.next_check_at is not None and _as_utc(watch.next_check_at) > now:
                return []
            if (
                watch.observation_in_flight_until is not None
                and _as_utc(watch.observation_in_flight_until) > now
            ):
                return []
            if watch.mode == "experimental":
                await _pause_unexecutable_watch(
                    session,
                    watch,
                    reason="experimental_adapter_not_implemented",
                    event_type="watch.experimental_noop",
                    dependencies=dependencies,
                )
                await session.commit()
                return []
            if adapter.provider != watch.provider:
                raise RuntimeError("execution adapter provider does not match watch")
            if not adapter.capabilities().seat_monitoring:
                await _pause_unexecutable_watch(
                    session,
                    watch,
                    reason="seat_monitoring_capability_unavailable",
                    event_type="watch.provider_capability_unavailable",
                    dependencies=dependencies,
                )
                await session.commit()
                return []
            account_auth_status = await _blocking_provider_account_auth_status(
                session,
                watch.provider,
            )
            if account_auth_status is not None:
                await _defer_for_blocking_provider_account(
                    session,
                    watch,
                    dependencies=dependencies,
                )
                await session.commit()
                return []
            circuit = await dependencies.get_or_create_provider_circuit(
                session, watch.provider, lock=True
            )
            if circuit.state != ProviderCircuitState.CLOSED:
                if watch.status == WatchStatus.SCHEDULED:
                    await dependencies.apply_watch_transition(
                        session,
                        watch,
                        WatchStatus.WATCHING,
                        reason="worker_claimed_due_watch",
                    )
                if (
                    circuit.state == ProviderCircuitState.OPEN
                    and not circuit.manual_resume_required
                ):
                    watch.cooldown_until = circuit.cooldown_until
                    await dependencies.apply_watch_transition(
                        session,
                        watch,
                        WatchStatus.COOLDOWN,
                        reason="provider_circuit_open",
                    )
                    watch.next_check_at = circuit.cooldown_until
                else:
                    await dependencies.apply_watch_transition(
                        session,
                        watch,
                        WatchStatus.AUTH_REQUIRED,
                        reason="provider_circuit_manual_hold",
                    )
                    watch.next_check_at = None
                await session.commit()
                return []
            watch.cooldown_until = None
            candidates = list(
                (
                    await session.scalars(
                        select(WatchCandidate)
                        .where(
                            WatchCandidate.watch_id == watch.id,
                            WatchCandidate.state.in_(OBSERVABLE_CANDIDATE_STATES),
                        )
                        .order_by(WatchCandidate.priority)
                    )
                ).all()
            )
            runnable_candidates: list[WatchCandidate] = []
            deferred_retry_times: list[datetime] = []
            for candidate in candidates:
                decision = decide_operational_expiry(candidate, now)
                if decision.expire:
                    candidate.state = "expired"
                else:
                    runnable_candidates.append(candidate)
                    if decision.retry_at is not None:
                        deferred_retry_times.append(decision.retry_at)
            if candidates and not runnable_candidates:
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.EXPIRED,
                    reason="all_candidates_operationally_terminal_or_horizon_elapsed",
                )
                await session.commit()
                return []
            candidates = runnable_candidates
            if not candidates or not watch.origin_node_id or not watch.destination_node_id:
                reason = "candidate_required" if not candidates else "station_identity_required"
                await _pause_unexecutable_watch(
                    session,
                    watch,
                    reason=reason,
                    event_type="watch.runtime_input_required",
                    dependencies=dependencies,
                )
                await session.commit()
                return []
            if watch.status == WatchStatus.SCHEDULED:
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.WATCHING,
                    reason="worker_claimed_due_watch",
                )
            if deferred_retry_times and len(deferred_retry_times) == len(candidates):
                watch.next_check_at = min(deferred_retry_times)
            watch.observation_in_flight_until = now + OBSERVATION_IN_FLIGHT_DURATION
            targets = [
                ObservationTarget(
                    watch_id=watch.id,
                    candidate_id=candidate.id,
                    provider=watch.provider,
                    origin=watch.origin,
                    destination=watch.destination,
                    origin_node_id=watch.origin_node_id,
                    destination_node_id=watch.destination_node_id,
                    train_number=candidate.train_number,
                    departure_at=_as_utc(candidate.departure_at),
                    arrival_at=(
                        _as_utc(candidate.arrival_at) if candidate.arrival_at is not None else None
                    ),
                    seat_class=candidate.seat_class,
                    passenger_count=watch.passenger_count,
                    priority=candidate.priority,
                )
                for candidate in candidates
            ]
            await session.commit()
            return targets
        except Exception:
            await session.rollback()
            raise


async def defer_watch_group_observation(
    watch_ids: list[str],
    deferred_until: datetime,
    now: datetime,
    *,
    lease_grant: object | None,
    prepared: bool,
    dependencies: ObservationGroupDependencies,
) -> None:
    deferred_until = _as_utc(deferred_until)
    if deferred_until <= now:
        return
    latest_eligible_check = None if prepared else now
    async with dependencies.session_factory() as session:
        try:
            if not await _lease_allows_locked_write(
                session, lease_grant, dependencies=dependencies
            ):
                return
            watches = list(
                (
                    await session.scalars(
                        _locked_deferred_watches_query(
                            watch_ids,
                            latest_eligible_check,
                        )
                    )
                ).all()
            )
            for watch in watches:
                watch.cooldown_until = deferred_until
                watch.next_check_at = deferred_until
                watch.observation_in_flight_until = None
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _locked_deferred_watches_query(watch_ids: list[str], latest_eligible_check: datetime | None):
    predicates = [
        Watch.id.in_(watch_ids),
        Watch.status.in_(OBSERVATION_WATCH_STATUSES),
    ]
    if latest_eligible_check is None:
        predicates.append(Watch.observation_in_flight_until.is_not(None))
    else:
        predicates.extend(
            [
                Watch.next_check_at.is_not(None),
                Watch.next_check_at <= latest_eligible_check,
            ]
        )
    return select(Watch).where(*predicates).order_by(Watch.id).with_for_update()


async def persist_observation_cycle(
    watch_id: str,
    targets: list[ObservationTarget],
    results: dict[tuple[object, ...], SeatObservationResult],
    now: datetime,
    *,
    lease_grant: object | None,
    dependencies: ObservationGroupDependencies,
) -> ObservationTarget | None:
    async with dependencies.session_factory() as session:
        try:
            if not await _lease_allows_locked_write(
                session, lease_grant, dependencies=dependencies
            ):
                return None
            watch = await session.scalar(
                select(Watch).where(Watch.id == watch_id).with_for_update()
            )
            if watch is None or watch.status not in {
                WatchStatus.WATCHING,
                WatchStatus.OFFICIAL_WAITLIST,
                WatchStatus.SEAT_FOUND,
            }:
                return None
            previous_fingerprint = await dependencies.latest_observation_fingerprint(session, watch)
            winner: ObservationTarget | None = None
            observed_results: list[SeatObservationResult] = []
            observed_candidates: list[
                tuple[WatchCandidate, SeatObservationResult, SeatObservation]
            ] = []
            for target in sorted(targets, key=lambda item: item.priority):
                candidate = await session.get(WatchCandidate, target.candidate_id)
                result = results.get(target.cache_key)
                if candidate is None or result is None:
                    continue
                observation = await dependencies.record_seat_observation(
                    session,
                    watch,
                    candidate,
                    result,
                    apply_status_transition=False,
                )
                observed_results.append(result)
                observed_candidates.append((candidate, result, observation))
                if winner is None and result.status in ACTIONABLE_SEAT_STATUSES:
                    episode_key = await retryable_reservation_episode_key(
                        session,
                        candidate,
                        observation,
                        watch.provider,
                        is_confirmed_absent_retry_source=(
                            dependencies.is_confirmed_absent_retry_source
                        ),
                        is_unresolved_unknown_manual_rearm_source=(
                            dependencies.is_unresolved_unknown_manual_rearm_source
                        ),
                        is_payment_hold_ended=dependencies.is_payment_hold_ended,
                    )
                    if episode_key is not None:
                        winner = replace(target, reservation_episode_key=episode_key)
            all_candidates_conclusively_unavailable = len(observed_results) == len(targets) and all(
                result.status in CONCLUSIVE_UNAVAILABLE_SEAT_STATUSES for result in observed_results
            )
            observed_statuses = {result.status for result in observed_results}
            actionable_observed = bool(observed_statuses.intersection(SEAT_FOUND_STATUSES))
            automatic_retry_fenced = (
                actionable_observed
                and watch.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
                and winner is None
            )
            if automatic_retry_fenced:
                for candidate, result, _observation in observed_candidates:
                    if result.status in ACTIONABLE_SEAT_STATUSES:
                        candidate.state = "observed"
            if actionable_observed and not automatic_retry_fenced:
                summarized_status = WatchStatus.SEAT_FOUND
            elif SeatObservationStatus.WAITLIST_AVAILABLE in observed_statuses:
                summarized_status = WatchStatus.OFFICIAL_WAITLIST
            elif all_candidates_conclusively_unavailable or automatic_retry_fenced:
                summarized_status = WatchStatus.WATCHING
            else:
                summarized_status = None
            if summarized_status is not None and watch.status != summarized_status:
                transition_reason = (
                    "automatic_reservation_retry_fenced"
                    if automatic_retry_fenced
                    else f"authorized_seat_observation_summary_{summarized_status.value}"
                )
                transition_observation = next(
                    (
                        observation
                        for _candidate, result, observation in observed_candidates
                        if (
                            summarized_status is WatchStatus.SEAT_FOUND
                            and result.status in SEAT_FOUND_STATUSES
                        )
                        or (
                            summarized_status is WatchStatus.OFFICIAL_WAITLIST
                            and result.status is SeatObservationStatus.WAITLIST_AVAILABLE
                        )
                        or summarized_status is WatchStatus.WATCHING
                    ),
                    None,
                )
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    summarized_status,
                    reason=transition_reason,
                    observation=transition_observation,
                )
            await dependencies.finish_observation_cycle(session, watch, previous_fingerprint, now)
            await session.commit()
            return winner
        except Exception:
            await session.rollback()
            raise


async def provider_circuit_is_closed(
    provider: Provider,
    *,
    lease_grant: object | None,
    dependencies: ObservationGroupDependencies,
) -> bool:
    async with dependencies.session_factory() as session:
        if not await _lease_allows_locked_write(session, lease_grant, dependencies=dependencies):
            return False
        circuit = await dependencies.get_or_create_provider_circuit(session, provider)
        await session.commit()
        return circuit.state == ProviderCircuitState.CLOSED


async def apply_current_circuit_to_watch(
    watch_id: str,
    *,
    lease_grant: object | None,
    dependencies: ObservationGroupDependencies,
) -> None:
    async with dependencies.session_factory() as session:
        try:
            if not await _lease_allows_locked_write(
                session, lease_grant, dependencies=dependencies
            ):
                return
            watch = await session.scalar(
                select(Watch).where(Watch.id == watch_id).with_for_update()
            )
            if watch is None or watch.status not in OBSERVATION_WATCH_STATUSES:
                return
            circuit = await dependencies.get_or_create_provider_circuit(
                session, watch.provider, lock=True
            )
            if circuit.state == ProviderCircuitState.CLOSED:
                return
            if watch.status == WatchStatus.SCHEDULED:
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.WATCHING,
                    reason="worker_claimed_due_watch",
                )
            if circuit.state == ProviderCircuitState.OPEN and not circuit.manual_resume_required:
                watch.cooldown_until = circuit.cooldown_until
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.COOLDOWN,
                    reason="provider_circuit_open_during_observation",
                )
                watch.next_check_at = circuit.cooldown_until
            else:
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.AUTH_REQUIRED,
                    reason="provider_circuit_manual_hold_during_observation",
                )
                watch.next_check_at = None
            watch.observation_in_flight_until = None
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def process_watch_group_observation(
    watch_ids: list[str],
    now: datetime,
    *,
    provider: Provider,
    adapter: ObservationProvider,
    lease_grant: object | None,
    dependencies: ObservationGroupDependencies,
) -> None:
    if adapter.provider != provider:
        raise RuntimeError("execution adapter provider does not match watch group")
    deferred_until = await adapter.observation_deferred_until()
    if deferred_until is not None and _as_utc(deferred_until) > now:
        if not await _lease_allows_external_work(lease_grant, dependencies=dependencies):
            return
        await defer_watch_group_observation(
            watch_ids,
            deferred_until,
            now,
            lease_grant=lease_grant,
            prepared=False,
            dependencies=dependencies,
        )
        return

    prepared: dict[str, list[ObservationTarget]] = {}
    for watch_id in watch_ids:
        if not await _lease_allows_external_work(lease_grant, dependencies=dependencies):
            return
        targets = await prepare_watch(
            watch_id,
            now,
            adapter=adapter,
            lease_grant=lease_grant,
            dependencies=dependencies,
        )
        if targets:
            prepared[watch_id] = targets
    if not prepared:
        return

    first_target = next(iter(next(iter(prepared.values()))))
    capabilities = adapter.capabilities()
    results: dict[tuple[object, ...], SeatObservationResult] = {}
    circuit_blocked = False
    for targets in prepared.values():
        for target in targets:
            if target.cache_key in results:
                continue
            if not await _lease_allows_external_work(lease_grant, dependencies=dependencies):
                return
            if not await provider_circuit_is_closed(
                target.provider,
                lease_grant=lease_grant,
                dependencies=dependencies,
            ):
                circuit_blocked = True
                break
            try:
                observations = await adapter.observe_seats(target.observation_request())
                result = next(
                    (item for item in observations if item.seat_class.value == target.seat_class),
                    None,
                )
                if result is None:
                    raise RuntimeError("provider returned no matching seat class")
            except dependencies.provider_call_errors:
                observed_at = dependencies.now()
                result = SeatObservationResult(
                    seat_class=target.seat_class,
                    status=SeatObservationStatus.ERROR,
                    source=("mock" if target.provider is Provider.MOCK else "authorized-provider"),
                    observed_at=observed_at,
                    fresh_until=observed_at,
                    error_category="provider_unavailable",
                )
            if result.status == SeatObservationStatus.ERROR:
                deferred_until = await adapter.observation_deferred_until()
                if deferred_until is not None and _as_utc(deferred_until) > now:
                    if not await _lease_allows_external_work(
                        lease_grant, dependencies=dependencies
                    ):
                        return
                    await defer_watch_group_observation(
                        list(prepared),
                        deferred_until,
                        now,
                        lease_grant=lease_grant,
                        prepared=True,
                        dependencies=dependencies,
                    )
                    return
            results[target.cache_key] = result
        if circuit_blocked:
            break

    if not circuit_blocked:
        circuit_blocked = not await provider_circuit_is_closed(
            first_target.provider,
            lease_grant=lease_grant,
            dependencies=dependencies,
        )
    for watch_id, targets in prepared.items():
        if not await _lease_allows_external_work(lease_grant, dependencies=dependencies):
            return
        winner = await persist_observation_cycle(
            watch_id,
            targets,
            results,
            now,
            lease_grant=lease_grant,
            dependencies=dependencies,
        )
        if circuit_blocked:
            await apply_current_circuit_to_watch(
                watch_id,
                lease_grant=lease_grant,
                dependencies=dependencies,
            )
        elif winner is not None and capabilities.reservation_once:
            if not await _lease_allows_external_work(lease_grant, dependencies=dependencies):
                return
            await dependencies.reserve_winner(winner)
