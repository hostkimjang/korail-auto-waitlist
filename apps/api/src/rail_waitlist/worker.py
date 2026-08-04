from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from .celery_app import celery_app
from .config import get_settings
from .database import SessionFactory, engine
from .domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from .korail_execution import korail_background_monitoring_enabled
from .metrics import WATCH_GROUPS, WORKER_RUNS
from .models import (
    ProviderExecutionLease,
    RailProviderAccount,
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
)
from .notification_management.delivery import deliver_pending_notifications
from .observations.due_pipeline_application import (
    DuePipelineDependencies,
    process_due_pipeline,
)
from .operational import decide_operational_expiry
from .provider_accounts import (
    update_provider_auth_status,
)
from .provider_execution_lease import (
    ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
    ExecutionLeaseGrant,
    ProviderExecutionLeaseService,
)
from .providers import ProviderUnavailable, RailProviderAdapter, get_execution_provider
from .reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .reservations.reconciliation_application import (
    ReconciliationDependencies,
    _reservation_reconciliation_due_clause,
)
from .reservations.reconciliation_application import (
    reconcile_reservation_attempt as run_reservation_reconciliation,
)
from .schemas import (
    RailProviderAuthStatus,
    ReservationRequest,
    ReservationResult,
    SeatObservationRequest,
    SeatObservationResult,
)
from .services import (
    ACTIONABLE_SEAT_STATUSES,
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
    SEAT_FOUND_STATUSES,
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
    request_hash,
)
from .srt_reservation import SRT_RESERVATION_SOURCE
from .watch_management.expiry_application import (
    WatchExpiryDependencies,
    expire_elapsed_watches,
)

OBSERVATION_WATCH_STATUSES = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }
)
OBSERVABLE_CANDIDATE_STATES = frozenset({"active", "observed", "seat_found"})
CONCLUSIVE_UNAVAILABLE_SEAT_STATUSES = frozenset(
    {
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }
)
RESERVATION_ATTEMPT_STALE_AFTER = timedelta(minutes=5)
PROVIDER_EXECUTION_LEASE_DURATION = timedelta(minutes=2)
NOT_AVAILABLE_RETRY_EPISODE_PREFIX = "not-available-retry:"
_EXTERNAL_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ReservationConfirmationEvaluation:
    result: ReservationResult
    confirmation: ReservationConfirmationResult | None

    def __iter__(self):
        yield self.result
        yield self.confirmation

    @property
    def outcome(self) -> ReservationOutcome:
        return self.result.outcome

    @property
    def source(self) -> str:
        return self.result.source

    @property
    def payment_deadline(self) -> datetime | None:
        return self.result.payment_deadline

    @property
    def official_handoff_url(self):
        return self.result.official_handoff_url


def _provider_auth_status_for_reservation_outcome(
    outcome: ReservationOutcome,
) -> RailProviderAuthStatus | None:
    """Return only authentication metadata supported by a conclusive outcome."""
    if outcome in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
        ReservationOutcome.NOT_AVAILABLE,
    }:
        return "authenticated"
    if outcome is ReservationOutcome.AUTH_REQUIRED:
        return "auth_required"
    if outcome is ReservationOutcome.PROVIDER_BLOCKED:
        return "provider_blocked"
    # UNKNOWN and FAILED include provider execution/result boundaries that do not
    # prove the saved account became invalid. Preserve the separately verified
    # account status; AUTH_REQUIRED and PROVIDER_BLOCKED are the conclusive signals.
    return None


async def _confirm_provider_reservation_result(
    adapter: RailProviderAdapter,
    target: _CandidateTarget,
    attempt_id: str,
    result: ReservationResult,
) -> _ReservationConfirmationEvaluation:
    """Require exact official hold evidence before exposing a payment-required state."""

    if target.provider not in _EXTERNAL_PROVIDERS or result.outcome not in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
        ReservationOutcome.UNKNOWN,
    }:
        return _ReservationConfirmationEvaluation(result, None)
    if result.credential_version is None:
        return _ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source=result.source,
                observed_at=result.observed_at,
            ),
            None,
        )
    if (
        target.provider is Provider.SRT
        and result.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and result.source == SRT_RESERVATION_SOURCE
        and result.official_handoff_url is not None
    ):
        # The SRT executor already exact-matched the returned official reservation's
        # trip, seat class and passenger count. A second full-list lookup is slower and
        # may become ambiguous when the account holds the same train more than once.
        confirmation = ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source=result.source,
            observed_at=result.observed_at,
            payment_deadline=result.payment_deadline,
            official_handoff_url=str(result.official_handoff_url),
        )
        return _ReservationConfirmationEvaluation(result, confirmation)
    confirmation_target = ReservationConfirmationTarget(
        attempt_id=attempt_id,
        candidate_id=target.candidate_id,
        provider=target.provider,
        train_number=target.train_number,
        origin=target.origin,
        destination=target.destination,
        departure_at=target.departure_at,
        arrival_at=target.arrival_at,
        seat_class=SeatClass(target.seat_class),
        passenger_count=target.passenger_count,
        credential_version=result.credential_version,
    )
    try:
        confirmation = await adapter.confirm_reservation(confirmation_target)
    except (ProviderUnavailable, RuntimeError, TypeError, ValueError):
        return _ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source=result.source,
                observed_at=datetime.now(timezone.utc),
                credential_version=result.credential_version,
            ),
            None,
        )
    if confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        return _ReservationConfirmationEvaluation(
            ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source=confirmation.source,
                observed_at=confirmation.observed_at,
                credential_version=result.credential_version,
                payment_deadline=confirmation.payment_deadline,
                official_handoff_url=confirmation.official_handoff_url,
            ),
            confirmation,
        )
    mapped_outcome = {
        ReservationConfirmationOutcome.AUTH_REQUIRED: ReservationOutcome.AUTH_REQUIRED,
        ReservationConfirmationOutcome.PROVIDER_BLOCKED: (ReservationOutcome.PROVIDER_BLOCKED),
    }.get(confirmation.outcome, ReservationOutcome.UNKNOWN)
    return _ReservationConfirmationEvaluation(
        ReservationResult(
            outcome=mapped_outcome,
            source=confirmation.source,
            observed_at=confirmation.observed_at,
            credential_version=result.credential_version,
        ),
        confirmation,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _close_execution_adapter(
    adapter: RailProviderAdapter,
    provider: Provider,
) -> None:
    try:
        await adapter.aclose()
    except Exception:
        # Cleanup diagnostics stay categorical; upstream details and credentials must
        # never be copied into worker logs.  Lease release is handled separately.
        LOGGER.warning("execution adapter cleanup failed provider=%s", provider.value)


async def _drain_execution_adapter(
    adapter: RailProviderAdapter,
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


@dataclass(frozen=True)
class _CandidateTarget:
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

    def reservation_request(
        self,
        idempotency_key: str,
        *,
        expected_credential_version: int | None = None,
    ) -> ReservationRequest:
        return ReservationRequest(
            **self.observation_request().model_dump(),
            candidate_id=self.candidate_id,
            idempotency_key=idempotency_key,
            expected_credential_version=expected_credential_version,
            arrival_at=self.arrival_at,
        )


async def _retryable_reservation_episode_key(
    session,
    candidate: WatchCandidate,
    current_observation: SeatObservation,
    provider: Provider,
) -> str | None:
    """Return a stable episode identity only when another provider call is safe.

    A continuously available seat is one episode. A provider-level NOT_AVAILABLE is
    conclusive evidence that no hold exists, so one later actionable observation may
    re-check that race without waiting for the background observer to sample the brief
    sold-out interval. That race retry cannot chain. Later retries still require a
    conclusive unavailable observation. AUTH_REQUIRED can be retried once for each
    newer successful account-verification generation. A confirmed unpaid hold that
    later disappears can also be retried only after a later conclusive unavailable
    observation proves a new availability edge.
    """
    latest_attempt = await session.scalar(
        select(ReservationAttempt)
        .where(ReservationAttempt.candidate_id == candidate.id)
        .order_by(ReservationAttempt.attempt_sequence.desc())
        .limit(1)
    )
    if latest_attempt is None:
        return f"availability:{current_observation.id}"
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
        if unavailable_observation is not None:
            return f"availability-after:{unavailable_observation.id}"
        if _as_utc(
            current_observation.observed_at
        ) <= finished_at or latest_attempt.episode_key.startswith(
            NOT_AVAILABLE_RETRY_EPISODE_PREFIX
        ):
            return None
        return f"{NOT_AVAILABLE_RETRY_EPISODE_PREFIX}{latest_attempt.id}"
    if latest_attempt.outcome in {
        ReservationOutcome.AUTH_REQUIRED,
        ReservationOutcome.PROVIDER_BLOCKED,
    }:
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
    if (
        is_confirmed_absent_retry_source(latest_attempt)
        and current_observation.observed_at > latest_attempt.confirmation_observed_at
    ):
        return f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}{latest_attempt.id}"
    if (
        latest_attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and latest_attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
        and latest_attempt.post_deadline_reconciled_at is not None
    ):
        hold_ended_at = _as_utc(latest_attempt.post_deadline_reconciled_at)
        unavailable_observation = await session.scalar(
            select(SeatObservation)
            .where(
                SeatObservation.candidate_id == candidate.id,
                SeatObservation.observed_at > hold_ended_at,
                SeatObservation.observed_at < current_observation.observed_at,
                SeatObservation.status.in_(CONCLUSIVE_UNAVAILABLE_SEAT_STATUSES),
            )
            .order_by(SeatObservation.observed_at, SeatObservation.id)
            .limit(1)
        )
        if unavailable_observation is None:
            return None
        return f"availability-after-hold:{unavailable_observation.id}"
    return None


async def _pause_unexecutable_watch(
    session,
    watch: Watch,
    *,
    reason: str,
    event_type: str,
) -> None:
    if watch.status == WatchStatus.SCHEDULED:
        await apply_watch_transition(
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
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.PAUSED,
            reason=reason,
        )
    watch.next_check_at = None
    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type=event_type,
        payload={"watch_id": watch.id, "status": watch.status.value, "reason": reason},
        dedupe_key=f"watch:{watch.id}:{event_type}:{reason}",
    )


async def _prepare_watch(
    watch_id: str,
    now: datetime,
    *,
    adapter: RailProviderAdapter | None = None,
) -> list[_CandidateTarget]:
    async with SessionFactory() as session:
        watch = await session.scalar(select(Watch).where(Watch.id == watch_id).with_for_update())
        if watch is None or watch.status not in OBSERVATION_WATCH_STATUSES:
            return []
        if watch.next_check_at is not None:
            due_at = watch.next_check_at
            if due_at.tzinfo is None or due_at.utcoffset() is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            if due_at > now:
                return []

        if watch.mode == "experimental":
            await _pause_unexecutable_watch(
                session,
                watch,
                reason="experimental_adapter_not_implemented",
                event_type="watch.experimental_noop",
            )
            await session.commit()
            return []

        execution_adapter = adapter or get_execution_provider(watch.provider)
        if execution_adapter.provider != watch.provider:
            raise RuntimeError("execution adapter provider does not match watch")
        capabilities = execution_adapter.capabilities()
        if not capabilities.seat_monitoring:
            await _pause_unexecutable_watch(
                session,
                watch,
                reason="seat_monitoring_capability_unavailable",
                event_type="watch.provider_capability_unavailable",
            )
            await session.commit()
            return []

        circuit = await get_or_create_provider_circuit(session, watch.provider, lock=True)
        if circuit.state != ProviderCircuitState.CLOSED:
            if watch.status == WatchStatus.SCHEDULED:
                await apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.WATCHING,
                    reason="worker_claimed_due_watch",
                )
            if circuit.state == ProviderCircuitState.OPEN and not circuit.manual_resume_required:
                watch.cooldown_until = circuit.cooldown_until
                await apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.COOLDOWN,
                    reason="provider_circuit_open",
                )
                watch.next_check_at = circuit.cooldown_until
            else:
                await apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.AUTH_REQUIRED,
                    reason="provider_circuit_manual_hold",
                )
                watch.next_check_at = None
            await session.commit()
            return []

        # A source-level hold can end independently of the provider circuit.  Once the
        # adapter preflight no longer defers this cycle, remove its stale display value.
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
            await apply_watch_transition(
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
            )
            await session.commit()
            return []

        if watch.status == WatchStatus.SCHEDULED:
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.WATCHING,
                reason="worker_claimed_due_watch",
            )
        # The watch-level timestamp prevents duplicate work within this cycle. External
        # providers are additionally serialized by the provider/account DB lease acquired
        # before this scheduled watch can transition to watching.
        watch.next_check_at = (
            min(deferred_retry_times)
            if deferred_retry_times and len(deferred_retry_times) == len(candidates)
            else now + timedelta(minutes=1)
        )
        targets = [
            _CandidateTarget(
                watch_id=watch.id,
                candidate_id=candidate.id,
                provider=watch.provider,
                origin=watch.origin,
                destination=watch.destination,
                origin_node_id=watch.origin_node_id,
                destination_node_id=watch.destination_node_id,
                train_number=candidate.train_number,
                departure_at=(
                    candidate.departure_at
                    if candidate.departure_at.tzinfo is not None
                    else candidate.departure_at.replace(tzinfo=timezone.utc)
                ),
                arrival_at=(
                    candidate.arrival_at
                    if candidate.arrival_at is None or candidate.arrival_at.tzinfo is not None
                    else candidate.arrival_at.replace(tzinfo=timezone.utc)
                ),
                seat_class=candidate.seat_class,
                passenger_count=watch.passenger_count,
                priority=candidate.priority,
            )
            for candidate in candidates
        ]
        await session.commit()
        return targets


async def _defer_watch_group_observation(
    watch_ids: list[str],
    deferred_until: datetime,
    now: datetime,
    lease_grant: ExecutionLeaseGrant | None = None,
    *,
    prepared: bool = False,
) -> None:
    """Move a due group past a shared source cooldown without recording an error row."""
    if deferred_until.tzinfo is None or deferred_until.utcoffset() is None:
        deferred_until = deferred_until.replace(tzinfo=timezone.utc)
    if deferred_until <= now:
        return
    latest_eligible_check = now + timedelta(minutes=1) if prepared else now
    async with SessionFactory() as session:
        if lease_grant is not None:
            checked_at = datetime.now(timezone.utc)
            lease = await session.scalar(
                select(ProviderExecutionLease)
                .where(
                    ProviderExecutionLease.provider == lease_grant.provider,
                    ProviderExecutionLease.account_scope == lease_grant.account_scope,
                )
                .with_for_update()
            )
            if lease is None or lease.owner_token != lease_grant.owner_token:
                return
            if lease.fencing_token != lease_grant.fencing_token or lease.expires_at is None:
                return
            lease_expiry = lease.expires_at
            if lease_expiry.tzinfo is None or lease_expiry.utcoffset() is None:
                lease_expiry = lease_expiry.replace(tzinfo=timezone.utc)
            if lease_expiry <= checked_at:
                return
        watches = list(
            (
                await session.scalars(
                    select(Watch)
                    .where(
                        Watch.id.in_(watch_ids),
                        Watch.status.in_(OBSERVATION_WATCH_STATUSES),
                        Watch.next_check_at.is_not(None),
                        Watch.next_check_at <= latest_eligible_check,
                    )
                    .with_for_update()
                )
            ).all()
        )
        for watch in watches:
            watch.cooldown_until = deferred_until
            watch.next_check_at = deferred_until
        await session.commit()


async def _arm_supported_provider_watches(
    provider: Provider,
    now: datetime,
    *,
    adapter: RailProviderAdapter | None = None,
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
    adapter: RailProviderAdapter | None = None,
) -> int:
    """Compatibility wrapper for focused SRT worker tests."""
    return await _arm_supported_provider_watches(Provider.SRT, now, adapter=adapter)


async def _watch_group_provider(watch_ids: list[str]) -> Provider | None:
    async with SessionFactory() as session:
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


async def _persist_observation_cycle(
    watch_id: str,
    targets: list[_CandidateTarget],
    results: dict[tuple[object, ...], SeatObservationResult],
    now: datetime,
) -> _CandidateTarget | None:
    async with SessionFactory() as session:
        watch = await session.scalar(select(Watch).where(Watch.id == watch_id).with_for_update())
        if watch is None or watch.status not in {
            WatchStatus.WATCHING,
            WatchStatus.OFFICIAL_WAITLIST,
            WatchStatus.SEAT_FOUND,
        }:
            return None
        previous_fingerprint = await latest_observation_fingerprint(session, watch)
        winner: _CandidateTarget | None = None
        observed_results: list[SeatObservationResult] = []
        observed_candidates: list[tuple[WatchCandidate, SeatObservationResult]] = []
        for target in sorted(targets, key=lambda item: item.priority):
            candidate = await session.get(WatchCandidate, target.candidate_id)
            result = results.get(target.cache_key)
            if candidate is None or result is None:
                continue
            observation = await record_seat_observation(
                session,
                watch,
                candidate,
                result,
                apply_status_transition=False,
            )
            observed_results.append(result)
            observed_candidates.append((candidate, result))
            if winner is None and result.status in ACTIONABLE_SEAT_STATUSES:
                episode_key = await _retryable_reservation_episode_key(
                    session,
                    candidate,
                    observation,
                    watch.provider,
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
            for candidate, result in observed_candidates:
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
            await apply_watch_transition(
                session,
                watch,
                summarized_status,
                reason=transition_reason,
            )
        await finish_observation_cycle(session, watch, previous_fingerprint, now)
        await session.commit()
        return winner


async def _provider_circuit_is_closed(provider: Provider) -> bool:
    async with SessionFactory() as session:
        circuit = await get_or_create_provider_circuit(session, provider)
        await session.commit()
        return circuit.state == ProviderCircuitState.CLOSED


async def _apply_current_circuit_to_watch(watch_id: str) -> None:
    async with SessionFactory() as session:
        watch = await session.scalar(select(Watch).where(Watch.id == watch_id).with_for_update())
        if watch is None or watch.status not in {
            WatchStatus.SCHEDULED,
            WatchStatus.WATCHING,
            WatchStatus.OFFICIAL_WAITLIST,
            WatchStatus.SEAT_FOUND,
        }:
            return
        circuit = await get_or_create_provider_circuit(session, watch.provider, lock=True)
        if circuit.state == ProviderCircuitState.CLOSED:
            return
        if watch.status == WatchStatus.SCHEDULED:
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.WATCHING,
                reason="worker_claimed_due_watch",
            )
        if circuit.state == ProviderCircuitState.OPEN and not circuit.manual_resume_required:
            watch.cooldown_until = circuit.cooldown_until
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.COOLDOWN,
                reason="provider_circuit_open_during_observation",
            )
            watch.next_check_at = circuit.cooldown_until
        else:
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.AUTH_REQUIRED,
                reason="provider_circuit_manual_hold_during_observation",
            )
            watch.next_check_at = None
        await session.commit()


async def _reserve_winner(adapter, target: _CandidateTarget) -> None:
    provider_credential_version: int | None = None
    async with SessionFactory() as session:
        if target.provider in {Provider.KORAIL, Provider.SRT}:
            # Provider-account writes always lock account -> watches. Keep the same
            # order here so a concurrent verified login cannot deadlock reservation.
            provider_credential_version = await session.scalar(
                select(RailProviderAccount.credential_version)
                .where(
                    RailProviderAccount.provider == target.provider,
                    RailProviderAccount.enabled.is_(True),
                    RailProviderAccount.last_auth_status == "authenticated",
                )
                .with_for_update()
            )
        watch = await session.scalar(
            select(Watch).where(Watch.id == target.watch_id).with_for_update()
        )
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.id == target.candidate_id)
            .with_for_update(of=WatchCandidate)
        )
        if (
            watch is None
            or candidate is None
            or watch.status != WatchStatus.SEAT_FOUND
            or watch.reservation_policy != ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        ):
            return
        circuit = await get_or_create_provider_circuit(session, watch.provider, lock=True)
        if circuit.state != ProviderCircuitState.CLOSED:
            if circuit.state == ProviderCircuitState.OPEN and not circuit.manual_resume_required:
                watch.cooldown_until = circuit.cooldown_until
                await apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.COOLDOWN,
                    reason="provider_circuit_open_before_reservation",
                )
                watch.next_check_at = circuit.cooldown_until
            else:
                await apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.AUTH_REQUIRED,
                    reason="provider_circuit_manual_hold_before_reservation",
                )
                watch.next_check_at = None
            await session.commit()
            return
        if (
            watch.provider in {Provider.KORAIL, Provider.SRT}
            and provider_credential_version is None
        ):
            # The account can become invalid after the user enabled automatic
            # reservation. Re-check it inside the locked reservation transaction
            # before claiming an episode attempt or calling the provider.
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.AUTH_REQUIRED,
                reason="provider_account_not_authenticated_before_reservation",
            )
            watch.next_check_at = None
            await session.commit()
            return
        if target.reservation_episode_key is None:
            return
        idempotency_key = (
            f"reserve:{target.candidate_id}:{request_hash(target.reservation_episode_key)[:32]}"
        )
        attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            idempotency_key,
            episode_key=target.reservation_episode_key,
            retry_authorized=True,
            credential_version=provider_credential_version,
        )
        attempt_id = attempt.id
        await session.commit()
    if not created:
        return

    try:
        result = await adapter.reserve_once(
            target.reservation_request(
                idempotency_key,
                expected_credential_version=provider_credential_version,
            )
        )
    except (ProviderUnavailable, RuntimeError, ValueError):
        result = ReservationResult(
            outcome=ReservationOutcome.FAILED,
            source="mock" if target.provider == Provider.MOCK else "authorized-provider",
            observed_at=datetime.now(timezone.utc),
        )
    result, confirmation = await _confirm_provider_reservation_result(
        adapter,
        target,
        attempt_id,
        result,
    )

    auth_status = (
        _provider_auth_status_for_reservation_outcome(result.outcome)
        if target.provider in {Provider.KORAIL, Provider.SRT}
        else None
    )
    async with SessionFactory() as session:
        if auth_status is not None and result.credential_version is not None:
            # Match provider-account -> watch lock order used by verified login saves.
            await session.scalar(
                select(RailProviderAccount.id)
                .where(RailProviderAccount.provider == target.provider)
                .with_for_update()
            )
        watch = await session.scalar(
            select(Watch).where(Watch.id == target.watch_id).with_for_update()
        )
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.id == target.candidate_id)
            .with_for_update(of=WatchCandidate)
        )
        attempt = await session.scalar(
            select(ReservationAttempt).where(ReservationAttempt.id == attempt_id).with_for_update()
        )
        if watch is None or candidate is None or attempt is None:
            return
        if (
            watch.status != WatchStatus.RESERVING
            or attempt.outcome != ReservationOutcome.PENDING
            or candidate.state != "reservation_attempted"
        ):
            if attempt.outcome == ReservationOutcome.PENDING:
                attempt.outcome = ReservationOutcome.UNKNOWN
                attempt.finished_at = datetime.now(timezone.utc)
                attempt.credential_version = result.credential_version
                if confirmation is not None:
                    record_reservation_confirmation(attempt, confirmation)
            if watch.status == WatchStatus.EXPIRED:
                candidate.state = "expired"
            elif candidate.state == "reservation_attempted":
                candidate.state = "failed"
            await add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.reservation_result_requires_manual_check",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": candidate.id,
                    "reason": "watch_state_changed_during_provider_call",
                },
                dedupe_key=f"reservation-result-fenced:{attempt.id}",
            )
            await session.commit()
            return
        if auth_status is not None and result.credential_version is not None:
            await update_provider_auth_status(
                session,
                watch.provider,
                auth_status,
                expected_credential_version=result.credential_version,
                commit=False,
            )
        await complete_reservation_attempt(
            session,
            watch,
            candidate,
            attempt,
            result,
            confirmation=confirmation,
        )
        await session.commit()


async def _process_watch_group(
    watch_ids: list[str],
    now: datetime,
    *,
    provider: Provider | None = None,
    adapter: RailProviderAdapter | None = None,
) -> None:
    provider = provider or await _watch_group_provider(watch_ids)
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
        if adapter.provider != provider:
            raise RuntimeError("execution adapter provider does not match watch group")
        deferred_until = await adapter.observation_deferred_until()
        if deferred_until is not None:
            if deferred_until.tzinfo is None or deferred_until.utcoffset() is None:
                deferred_until = deferred_until.replace(tzinfo=timezone.utc)
            if deferred_until > now:
                if lease_grant is not None and not await lease_service.is_current(
                    lease_grant, now=datetime.now(timezone.utc)
                ):
                    return
                await _defer_watch_group_observation(watch_ids, deferred_until, now, lease_grant)
                return

        prepared: dict[str, list[_CandidateTarget]] = {}
        for watch_id in watch_ids:
            if lease_grant is not None and not await lease_service.is_current(
                lease_grant, now=datetime.now(timezone.utc)
            ):
                return
            targets = await _prepare_watch(watch_id, now, adapter=adapter)
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
                if lease_grant is not None and not await lease_service.is_current(
                    lease_grant, now=datetime.now(timezone.utc)
                ):
                    return
                if not await _provider_circuit_is_closed(target.provider):
                    circuit_blocked = True
                    break
                try:
                    observations = await adapter.observe_seats(target.observation_request())
                    result = next(
                        (
                            item
                            for item in observations
                            if item.seat_class.value == target.seat_class
                        ),
                        None,
                    )
                    if result is None:
                        raise RuntimeError("provider returned no matching seat class")
                except (ProviderUnavailable, RuntimeError, ValueError):
                    observed_at = datetime.now(timezone.utc)
                    result = SeatObservationResult(
                        seat_class=target.seat_class,
                        status=SeatObservationStatus.ERROR,
                        source=(
                            "mock" if target.provider == Provider.MOCK else "authorized-provider"
                        ),
                        observed_at=observed_at,
                        fresh_until=observed_at,
                        error_category="provider_unavailable",
                    )
                if result.status == SeatObservationStatus.ERROR:
                    deferred_until = await adapter.observation_deferred_until()
                    if deferred_until is not None:
                        if deferred_until.tzinfo is None or deferred_until.utcoffset() is None:
                            deferred_until = deferred_until.replace(tzinfo=timezone.utc)
                        if deferred_until > now:
                            if lease_grant is not None and not await lease_service.is_current(
                                lease_grant, now=datetime.now(timezone.utc)
                            ):
                                return
                            await _defer_watch_group_observation(
                                watch_ids,
                                deferred_until,
                                now,
                                lease_grant,
                                prepared=True,
                            )
                            return
                results[target.cache_key] = result
            if circuit_blocked:
                break

        if not circuit_blocked:
            circuit_blocked = not await _provider_circuit_is_closed(first_target.provider)

        for watch_id, targets in prepared.items():
            if lease_grant is not None and not await lease_service.is_current(
                lease_grant, now=datetime.now(timezone.utc)
            ):
                return
            winner = await _persist_observation_cycle(watch_id, targets, results, now)
            if circuit_blocked:
                await _apply_current_circuit_to_watch(watch_id)
            elif winner is not None and capabilities.reservation_once:
                await _reserve_winner(adapter, winner)
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
    adapter: RailProviderAdapter | None = None,
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
