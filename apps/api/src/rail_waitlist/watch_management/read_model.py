from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import (
    Provider,
    ReservationPolicy,
    WatchStatus,
    reservation_result_reason_code_for_outcome,
)
from ..provider_account_management.models import RailProviderAccount
from ..provider_registry.application import get_execution_provider
from ..reservations.attempt_policy import (
    active_unresolved_unknown_attempt_ids,
    automatic_reservation_retry_fence_reason,
    exact_paid_reservation_attempt_id,
    is_unresolved_unknown_manual_rearm_source,
)
from ..reservations.domain import (
    reservation_attempt_manual_check_required,
    reservation_attempt_result_policy,
)
from ..reservations.manual_rearm_contracts import ManualReservationRearmReason
from ..reservations.payment_hold_application import payment_hold_end_reason
from ..reservations.progress_timing_policy import (
    normalize_reservation_terminal_time,
    persisted_reservation_progress_times,
)
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    effective_reservation_confirmation_diagnostic_code,
)
from .models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .schemas import WatchRead

ACTIVE_OBSERVATION_STATUSES = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }
)


@dataclass(frozen=True, slots=True)
class _ManualRearmWatchFence:
    exact_paid: bool = False
    unresolved_unknown_source_attempt_id: str | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def reservation_attempt_projection(
    reservation_policy: ReservationPolicy,
    attempt: ReservationAttempt,
    *,
    manual_rearm_reason: ManualReservationRearmReason | None = None,
) -> dict[str, object]:
    """Project an attempt without broadening the persisted retry policy."""
    result_policy = reservation_attempt_result_policy(attempt.outcome)
    hold_end_reason = payment_hold_end_reason(attempt)
    payment_hold_ended = hold_end_reason is not None
    confirmed_paid = attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    if confirmed_paid:
        manual_rearm_reason = None
    automatic_hold_retry = (
        payment_hold_ended and reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
    )
    progress_stages = attempt.progress_stages or []
    finished_at = attempt.finished_at
    if finished_at is not None:
        finished_at = normalize_reservation_terminal_time(
            finished_at,
            persisted_reservation_progress_times(progress_stages),
        )
    return {
        "outcome": attempt.outcome,
        "result_reason_code": (
            attempt.result_reason_code
            or reservation_result_reason_code_for_outcome(attempt.outcome)
        ),
        "confirmation_outcome": attempt.confirmation_outcome,
        "confirmation_diagnostic_code": effective_reservation_confirmation_diagnostic_code(
            attempt.confirmation_outcome,
            attempt.confirmation_diagnostic_code,
        ),
        "confirmation_observed_at": attempt.confirmation_observed_at,
        "reconciliation_attempt_count": attempt.reconciliation_attempt_count,
        "reconciliation_resolution": attempt.reconciliation_resolution,
        "next_reconcile_at": attempt.next_reconcile_at,
        "started_at": attempt.started_at,
        "finished_at": finished_at,
        "progress_stages": progress_stages,
        "reserved_seats": attempt.reserved_seats or [],
        "post_deadline_reconciled_at": attempt.post_deadline_reconciled_at,
        "payment_hold_end_reason": hold_end_reason,
        "automatic_reservation_retry_fence_reason": (
            automatic_reservation_retry_fence_reason(attempt)
        ),
        "retryable": automatic_hold_retry or (not payment_hold_ended and result_policy.retryable),
        "manual_check_required": reservation_attempt_manual_check_required(
            attempt.outcome,
            confirmation_outcome=attempt.confirmation_outcome,
            reconciliation_resolution=attempt.reconciliation_resolution,
        ),
        "manual_rearm_available": manual_rearm_reason is not None,
        "manual_rearm_reason": manual_rearm_reason,
        "retry_condition": (
            "new_availability_episode"
            if automatic_hold_retry
            else None
            if payment_hold_ended
            else result_policy.retry_condition
        ),
    }


async def _latest_observations_by_watch(
    session: AsyncSession, watch_ids: list[str]
) -> tuple[dict[str, SeatObservation], dict[str, datetime]]:
    if not watch_ids:
        return {}, {}
    ranked_observations = (
        select(
            SeatObservation.id.label("observation_id"),
            func.row_number()
            .over(
                partition_by=SeatObservation.candidate_id,
                order_by=(SeatObservation.observed_at.desc(), SeatObservation.id.desc()),
            )
            .label("observation_rank"),
        )
        .join(WatchCandidate, WatchCandidate.id == SeatObservation.candidate_id)
        .where(WatchCandidate.watch_id.in_(watch_ids))
        .subquery()
    )
    rows = (
        await session.execute(
            select(WatchCandidate.watch_id, SeatObservation)
            .join(SeatObservation, SeatObservation.candidate_id == WatchCandidate.id)
            .join(
                ranked_observations,
                ranked_observations.c.observation_id == SeatObservation.id,
            )
            .where(ranked_observations.c.observation_rank == 1)
        )
    ).all()
    latest_by_candidate: dict[str, SeatObservation] = {}
    latest_by_watch: dict[str, datetime] = {}
    for watch_id, observation in rows:
        latest_by_candidate[observation.candidate_id] = observation
        current_latest = latest_by_watch.get(watch_id)
        if current_latest is None or observation.observed_at > current_latest:
            latest_by_watch[watch_id] = observation.observed_at
    return latest_by_candidate, latest_by_watch


async def _latest_reservation_attempts_by_watch(
    session: AsyncSession, watch_ids: list[str]
) -> dict[str, ReservationAttempt]:
    if not watch_ids:
        return {}
    ranked_attempts = (
        select(
            ReservationAttempt.id.label("attempt_id"),
            func.row_number()
            .over(
                partition_by=ReservationAttempt.candidate_id,
                order_by=(
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.id.desc(),
                ),
            )
            .label("attempt_rank"),
        )
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(WatchCandidate.watch_id.in_(watch_ids))
        .subquery()
    )
    attempts = (
        await session.scalars(
            select(ReservationAttempt)
            .join(ranked_attempts, ranked_attempts.c.attempt_id == ReservationAttempt.id)
            .where(ranked_attempts.c.attempt_rank == 1)
        )
    ).all()
    return {attempt.candidate_id: attempt for attempt in attempts}


async def _latest_reservation_attempt_ids_by_watch(
    session: AsyncSession,
    watch_ids: list[str],
) -> dict[str, str]:
    if not watch_ids:
        return {}
    ranked_attempts = (
        select(
            WatchCandidate.watch_id.label("watch_id"),
            ReservationAttempt.id.label("attempt_id"),
            func.row_number()
            .over(
                partition_by=WatchCandidate.watch_id,
                order_by=(
                    ReservationAttempt.started_at.desc(),
                    ReservationAttempt.attempt_sequence.desc(),
                    ReservationAttempt.id.desc(),
                ),
            )
            .label("attempt_rank"),
        )
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(WatchCandidate.watch_id.in_(watch_ids))
        .subquery()
    )
    rows = (
        await session.execute(
            select(ranked_attempts.c.watch_id, ranked_attempts.c.attempt_id).where(
                ranked_attempts.c.attempt_rank == 1
            )
        )
    ).all()
    return {watch_id: attempt_id for watch_id, attempt_id in rows}


async def _manual_rearm_account_versions(
    session: AsyncSession,
    watches: list[Watch],
) -> dict[Provider, int]:
    requested = {watch.provider for watch in watches}.intersection({Provider.KORAIL, Provider.SRT})
    if not requested:
        return {}
    authenticated = (
        await session.execute(
            select(
                RailProviderAccount.provider,
                RailProviderAccount.credential_version,
            ).where(
                RailProviderAccount.provider.in_(requested),
                RailProviderAccount.enabled.is_(True),
                RailProviderAccount.last_auth_status == "authenticated",
            )
        )
    ).all()
    return {
        provider: credential_version
        for provider, credential_version in authenticated
        if get_execution_provider(provider).capabilities().reservation_once
    }


async def _manual_rearm_watch_fences(
    session: AsyncSession,
    watch_ids: list[str],
) -> dict[str, _ManualRearmWatchFence]:
    if not watch_ids:
        return {}
    rows = (
        await session.execute(
            select(WatchCandidate.watch_id, ReservationAttempt)
            .join(ReservationAttempt, ReservationAttempt.candidate_id == WatchCandidate.id)
            .where(WatchCandidate.watch_id.in_(watch_ids))
        )
    ).all()
    attempts_by_watch: dict[str, list[ReservationAttempt]] = {}
    for watch_id, attempt in rows:
        attempts_by_watch.setdefault(watch_id, []).append(attempt)
    fences: dict[str, _ManualRearmWatchFence] = {}
    for watch_id, attempts in attempts_by_watch.items():
        exact_paid = exact_paid_reservation_attempt_id(attempts) is not None
        unresolved_ids = active_unresolved_unknown_attempt_ids(attempts)
        fences[watch_id] = _ManualRearmWatchFence(
            exact_paid=exact_paid,
            unresolved_unknown_source_attempt_id=(
                next(iter(unresolved_ids)) if not exact_paid and len(unresolved_ids) == 1 else None
            ),
        )
    return fences


async def watch_read(
    session: AsyncSession,
    watch: Watch,
    *,
    last_checked_at: datetime | None = None,
    latest_observations: dict[str, SeatObservation] | None = None,
    latest_reservation_attempts: dict[str, ReservationAttempt] | None = None,
    latest_watch_reservation_attempt_ids: dict[str, str] | None = None,
    manual_rearm_watch_fences: dict[str, _ManualRearmWatchFence] | None = None,
    manual_rearm_account_versions: dict[Provider, int] | None = None,
    read_at: datetime | None = None,
) -> WatchRead:
    normalized_read_at = (read_at or datetime.now(UTC)).astimezone(UTC)
    if latest_observations is None:
        latest_observations, latest_by_watch = await _latest_observations_by_watch(
            session, [watch.id]
        )
        last_checked_at = latest_by_watch.get(watch.id)
    if latest_reservation_attempts is None:
        latest_reservation_attempts = await _latest_reservation_attempts_by_watch(
            session, [watch.id]
        )
    if latest_watch_reservation_attempt_ids is None:
        latest_watch_reservation_attempt_ids = await _latest_reservation_attempt_ids_by_watch(
            session, [watch.id]
        )
    if manual_rearm_watch_fences is None:
        manual_rearm_watch_fences = await _manual_rearm_watch_fences(session, [watch.id])
    if manual_rearm_account_versions is None:
        manual_rearm_account_versions = await _manual_rearm_account_versions(session, [watch])
    in_flight_until = watch.observation_in_flight_until
    if in_flight_until is not None:
        if in_flight_until.tzinfo is None or in_flight_until.utcoffset() is None:
            in_flight_until = in_flight_until.replace(tzinfo=UTC)
        else:
            in_flight_until = in_flight_until.astimezone(UTC)
    observation_execution_state = (
        "in_progress"
        if (
            watch.status in ACTIVE_OBSERVATION_STATUSES
            and in_flight_until is not None
            and in_flight_until > normalized_read_at
        )
        else "idle"
    )
    payload = WatchRead.model_validate(watch).model_dump()
    payload["observation_execution_state"] = observation_execution_state
    candidate_models = {candidate.id: candidate for candidate in watch.candidates}
    latest_watch_attempt_id = latest_watch_reservation_attempt_ids.get(watch.id)
    manual_rearm_fence = manual_rearm_watch_fences.get(watch.id, _ManualRearmWatchFence())
    for candidate in payload["candidates"]:
        latest = latest_observations.get(candidate["id"])
        if latest is not None:
            candidate["latest_observation"] = {
                "status": latest.status,
                "source": latest.source,
                "observed_at": latest.observed_at,
                "fresh_until": latest.fresh_until,
                "error_category": latest.error_category,
            }
        latest_attempt = latest_reservation_attempts.get(candidate["id"])
        if latest_attempt is not None:
            candidate_model = candidate_models[candidate["id"]]
            departure_at = (
                candidate["actual_departure_at"]
                or candidate["estimated_departure_at"]
                or candidate["scheduled_departure_at"]
            )
            if departure_at.tzinfo is None or departure_at.utcoffset() is None:
                departure_at = departure_at.replace(tzinfo=UTC)
            else:
                departure_at = departure_at.astimezone(UTC)
            account_credential_version = manual_rearm_account_versions.get(watch.provider)
            common_manual_rearm_gate = (
                not manual_rearm_fence.exact_paid
                and watch.status is WatchStatus.WATCHING
                and watch.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
                and account_credential_version is not None
                and departure_at > normalized_read_at
            )
            payment_manual_rearm_gate = (
                common_manual_rearm_gate and latest_watch_attempt_id == latest_attempt.id
            )
            unknown_manual_rearm_gate = (
                common_manual_rearm_gate
                and manual_rearm_fence.unresolved_unknown_source_attempt_id == latest_attempt.id
            )
            marker_matches_latest = (
                candidate_model.manual_rearm_source_attempt_id == latest_attempt.id
            )
            authorized_at = candidate_model.manual_rearm_authorized_at
            hold_ended_at = latest_attempt.post_deadline_reconciled_at
            payment_rearm_already_authorized = (
                marker_matches_latest
                and authorized_at is not None
                and hold_ended_at is not None
                and _as_utc(authorized_at) >= _as_utc(hold_ended_at)
            )
            manual_rearm_reason = None
            if (
                payment_manual_rearm_gate
                and payment_hold_end_reason(latest_attempt) is not None
                and not payment_rearm_already_authorized
            ):
                manual_rearm_reason = ManualReservationRearmReason.PAYMENT_HOLD_ENDED
            elif (
                unknown_manual_rearm_gate
                and not marker_matches_latest
                and latest_attempt.credential_version is not None
                and latest_attempt.credential_version == account_credential_version
                and is_unresolved_unknown_manual_rearm_source(latest_attempt)
            ):
                manual_rearm_reason = ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED
            candidate["latest_reservation_attempt"] = reservation_attempt_projection(
                watch.reservation_policy,
                latest_attempt,
                manual_rearm_reason=manual_rearm_reason,
            )
    return WatchRead.model_validate({**payload, "last_checked_at": last_checked_at})


async def watch_reads(session: AsyncSession, watches: list[Watch]) -> list[WatchRead]:
    if not watches:
        return []
    latest_observations, latest_by_watch = await _latest_observations_by_watch(
        session, [watch.id for watch in watches]
    )
    latest_reservation_attempts = await _latest_reservation_attempts_by_watch(
        session, [watch.id for watch in watches]
    )
    latest_watch_reservation_attempt_ids = await _latest_reservation_attempt_ids_by_watch(
        session, [watch.id for watch in watches]
    )
    manual_rearm_watch_fences = await _manual_rearm_watch_fences(
        session, [watch.id for watch in watches]
    )
    manual_rearm_account_versions = await _manual_rearm_account_versions(session, watches)
    read_at = datetime.now(UTC)
    return [
        await watch_read(
            session,
            watch,
            last_checked_at=latest_by_watch.get(watch.id),
            latest_observations=latest_observations,
            latest_reservation_attempts=latest_reservation_attempts,
            latest_watch_reservation_attempt_ids=latest_watch_reservation_attempt_ids,
            manual_rearm_watch_fences=manual_rearm_watch_fences,
            manual_rearm_account_versions=manual_rearm_account_versions,
            read_at=read_at,
        )
        for watch in watches
    ]
