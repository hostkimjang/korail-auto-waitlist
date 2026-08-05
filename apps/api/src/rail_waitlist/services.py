from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from .idempotency.application import (
    get_idempotent_resource as get_idempotent_resource,
)
from .idempotency.application import remember_idempotency as remember_idempotency
from .idempotency.application import request_hash as request_hash
from .models import (
    ProviderCircuit,
    ReservationAttempt,
    SeatObservation,
    TimetableSeatEvidence,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from .notification_management.watch_transition_application import (
    add_watch_notifications as add_watch_notifications,
)
from .observations.cycle_application import (
    finish_observation_cycle as finish_observation_cycle,
)
from .observations.cycle_application import (
    latest_observation_fingerprint as latest_observation_fingerprint,
)
from .observations.operational_projection_application import (
    OperationalProjectionCandidate as OperationalProjectionCandidate,
)
from .observations.operational_projection_application import (
    apply_operational_projection as apply_operational_projection,
)
from .outbox import add_outbox_event
from .policy import build_watch_dedupe_key
from .provider_registry.application import get_execution_provider, get_timetable_provider
from .reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from .reservations.attempt_claim_application import (
    ReservationAttemptClaimDependencies,
)
from .reservations.attempt_claim_application import (
    begin_reservation_attempt as begin_reservation_attempt_application,
)
from .reservations.attempt_policy import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX as CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
)
from .reservations.attempt_policy import (
    is_confirmed_absent_retry_source as is_confirmed_absent_retry_source,
)
from .reservations.domain import ReservationAttemptResultPolicy as ReservationAttemptResultPolicy
from .reservations.domain import reservation_attempt_result_policy
from .reservations.payment_hold_application import _utc_instant as _utc_instant
from .reservations.payment_hold_application import (
    is_payment_hold_ended as is_payment_hold_ended,
)
from .reservations.payment_hold_application import (
    payment_hold_end_reason as payment_hold_end_reason,
)
from .schemas import (
    RegistrationEvidenceConflictDetail,
    ReservationResult,
    SeatObservationResult,
    WatchCreate,
    WatchUpdate,
    normalize_official_train_number,
)
from .ui_preferences.application import (
    update_admin_ui_preferences as update_admin_ui_preferences,
)
from .watch_management.transition_application import (
    WatchTransitionDependencies,
    WatchTransitionRejected,
)
from .watch_management.transition_application import (
    apply_watch_transition as apply_watch_transition_application,
)
from .watch_management.transition_policy import (
    build_watch_transition_identity,
    decide_watch_transition,
)
from .watch_management.update_application import (
    MAX_FOCUSED_WATCHES_PER_PROVIDER as MAX_FOCUSED_WATCHES_PER_PROVIDER,
)
from .watch_management.update_application import (
    WatchCommandConflict,
    WatchCommandNotFound,
    WatchCommandValidationError,
    WatchUpdateDependencies,
)
from .watch_management.update_application import (
    ensure_focused_observation_capacity as ensure_focused_observation_capacity_application,
)
from .watch_management.update_application import (
    update_watch as update_watch_application,
)
from .watch_management.update_application import (
    validate_channel_ids as validate_channel_ids_application,
)

RESERVATION_RECONCILIATION_MAX_ATTEMPTS = 3
RESERVATION_RECONCILIATION_INTERVAL = timedelta(seconds=30)
UNKNOWN_RECONCILIATION_MAX_ATTEMPTS = 6
_UNKNOWN_INCONCLUSIVE_RECONCILIATION_INTERVALS = {
    1: RESERVATION_RECONCILIATION_INTERVAL,
    2: RESERVATION_RECONCILIATION_INTERVAL,
    3: timedelta(minutes=5),
    4: timedelta(minutes=15),
    5: timedelta(minutes=60),
}


def unknown_reconciliation_retry_interval(completed_attempt_count: int) -> timedelta | None:
    return _UNKNOWN_INCONCLUSIVE_RECONCILIATION_INTERVALS.get(completed_attempt_count)


async def _ensure_focused_observation_capacity(
    session: AsyncSession,
    provider: Provider,
    *,
    exclude_watch_id: str | None = None,
) -> None:
    try:
        await ensure_focused_observation_capacity_application(
            session,
            provider,
            exclude_watch_id=exclude_watch_id,
        )
    except WatchCommandConflict as error:
        raise HTTPException(409, str(error)) from None


async def create_watch(
    session: AsyncSession, data: WatchCreate, idempotency_key: str | None = None
) -> Watch:
    payload_hash = request_hash(data)
    existing_id = await get_idempotent_resource(
        session, "watch.create", idempotency_key, payload_hash
    )
    if existing_id:
        existing = await session.get(Watch, existing_id)
        if existing:
            return existing

    if data.seat_observation_mode is SeatObservationMode.FOCUSED:
        await _ensure_focused_observation_capacity(session, data.provider)

    if data.mode == "experimental":
        from .config import get_settings

        if not get_settings().experimental_rail_enabled:
            raise HTTPException(403, "experimental rail mode is disabled")

    await validate_channel_ids(session, data.notification_channel_ids)

    registration_evidence: dict[str, TimetableSeatEvidence] = {}
    if data.provider in {Provider.KORAIL, Provider.SRT}:
        if any(candidate.registration_evidence_id is None for candidate in data.candidates):
            raise HTTPException(422, "official watch candidates require registration evidence")
        evidence_ids = {
            candidate.registration_evidence_id
            for candidate in data.candidates
            if candidate.registration_evidence_id is not None
        }
        rows = list(
            (
                await session.scalars(
                    select(TimetableSeatEvidence).where(TimetableSeatEvidence.id.in_(evidence_ids))
                )
            ).all()
        )
        registration_evidence = {row.id: row for row in rows}
        now = datetime.now(UTC)
        for candidate in data.candidates:
            evidence_id = candidate.registration_evidence_id
            evidence = registration_evidence.get(evidence_id or "")
            if evidence is None:
                raise HTTPException(422, "registration evidence was not found")
            if (
                not evidence.registration_allowed
                or evidence.status == SeatObservationStatus.UNKNOWN
                or evidence.provenance_kind == "not_observed"
            ):
                raise HTTPException(
                    422,
                    "registration evidence is not eligible for watch creation",
                )
            departure_at = candidate.departure_at.astimezone(UTC).replace(microsecond=0)
            evidence_departure = evidence.departure_at
            if evidence_departure.tzinfo is None or evidence_departure.utcoffset() is None:
                evidence_departure = evidence_departure.replace(tzinfo=UTC)
            valid_until = evidence.registration_valid_until
            if valid_until.tzinfo is None or valid_until.utcoffset() is None:
                valid_until = valid_until.replace(tzinfo=UTC)
            exact_match = (
                evidence.provider == data.provider
                and evidence.origin_node_id == data.origin_node_id
                and evidence.destination_node_id == data.destination_node_id
                and evidence.canonical_train_number
                == normalize_official_train_number(candidate.train_number)
                and evidence_departure.astimezone(UTC).replace(microsecond=0) == departure_at
                and evidence.passenger_count == data.passenger_count
                and evidence.seat_class == candidate.seat_class
            )
            if not exact_match:
                raise HTTPException(422, "registration evidence does not match the watch candidate")
            if valid_until <= now:
                conflict = RegistrationEvidenceConflictDetail(
                    reason="expired",
                    message="좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
                )
                raise HTTPException(status_code=409, detail=conflict.model_dump())
    elif any(candidate.registration_evidence_id is not None for candidate in data.candidates):
        raise HTTPException(422, "registration evidence is only valid for official watches")

    dedupe_key = build_watch_dedupe_key(
        data.provider,
        data.origin,
        data.destination,
        data.travel_date,
        data.time_from,
        data.time_to,
        data.seat_class,
        data.passenger_count,
        data.train_numbers,
        data.origin_node_id,
        data.destination_node_id,
    )
    watch_values = data.model_dump(exclude={"candidates"})
    watch = Watch(
        **watch_values,
        status=WatchStatus.DRAFT,
        dedupe_key=dedupe_key,
        official_booking_url=get_timetable_provider(data.provider).official_booking_url(),
    )
    watch.candidates = []
    for candidate in data.candidates:
        candidate_values = candidate.model_dump()
        candidate_values["departure_at"] = candidate.departure_at.astimezone(UTC)
        candidate_values["scheduled_departure_at"] = candidate.departure_at.astimezone(UTC)
        if candidate.arrival_at is not None:
            candidate_values["arrival_at"] = candidate.arrival_at.astimezone(UTC)
        persisted_candidate = WatchCandidate(**candidate_values)
        if candidate.registration_evidence_id is not None:
            persisted_candidate.registration_evidence = registration_evidence[
                candidate.registration_evidence_id
            ]
        watch.candidates.append(persisted_candidate)
    session.add(watch)
    await session.flush()
    try:
        await remember_idempotency(session, "watch.create", idempotency_key, watch.id, payload_hash)
        await add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.created",
            payload={"watch_id": watch.id, "status": watch.status.value},
            dedupe_key=f"watch:{watch.id}:created",
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            existing_id = await get_idempotent_resource(
                session, "watch.create", idempotency_key, payload_hash
            )
            if existing_id:
                existing = await session.get(Watch, existing_id)
                if existing is not None:
                    return existing
        raise
    await session.refresh(watch)
    return watch


async def find_watch(session: AsyncSession, watch_id: str) -> Watch:
    watch = await session.get(Watch, watch_id)
    if watch is None:
        raise HTTPException(404, "watch not found")
    return watch


async def apply_watch_transition(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
    observation: SeatObservation | None = None,
) -> Watch:
    """Apply a transition and its durable audit/outbox records without committing."""
    dependencies = WatchTransitionDependencies(
        request_hash=request_hash,
        get_idempotent_resource=get_idempotent_resource,
        decide_watch_transition=decide_watch_transition,
        get_execution_provider=get_execution_provider,
        build_watch_transition_identity=build_watch_transition_identity,
        remember_idempotency=remember_idempotency,
        add_outbox_event=add_outbox_event,
        add_watch_notifications=add_watch_notifications,
        now=lambda: datetime.now(UTC),
    )
    try:
        return await apply_watch_transition_application(
            session,
            watch,
            target,
            idempotency_key,
            reason=reason,
            observation=observation,
            dependencies=dependencies,
        )
    except WatchTransitionRejected as error:
        raise HTTPException(409, str(error)) from None


async def transition_watch(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
) -> Watch:
    locked_watch = await session.scalar(
        select(Watch)
        .where(Watch.id == watch.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked_watch is None:
        raise HTTPException(404, "watch not found")
    result = await apply_watch_transition(
        session,
        locked_watch,
        target,
        idempotency_key,
        reason=reason,
    )
    await session.commit()
    await session.refresh(result)
    return result


async def resume_watches_after_verified_provider_login(
    session: AsyncSession,
    provider: Provider,
    authenticated_at: datetime,
) -> list[str]:
    """Resume only authentication-stalled watches after a verified provider login.

    A completed ambiguous reservation attempt remains a durable fence. A conclusive
    AUTH_REQUIRED attempt can be armed for one later attempt only after this newer
    provider-account verification generation is persisted. A preflight account check
    can also stop a watch before an attempt is created; that path resumes monitoring
    after verification without creating, deleting, or re-arming an attempt fence.
    """
    watch_ids = list(
        (
            await session.scalars(
                select(Watch.id).where(
                    Watch.provider == provider,
                    Watch.status == WatchStatus.AUTH_REQUIRED,
                )
            )
        ).all()
    )
    resumed: list[str] = []
    for watch_id in watch_ids:
        watch = await session.scalar(
            select(Watch)
            .where(
                Watch.id == watch_id,
                Watch.status == WatchStatus.AUTH_REQUIRED,
            )
            .with_for_update()
        )
        if watch is None:
            continue
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch.id)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(1)
        )
        if latest_transition is None:
            continue

        transition_at = latest_transition.created_at
        if transition_at.tzinfo is None or transition_at.utcoffset() is None:
            transition_at = transition_at.replace(tzinfo=UTC)
        verified_at = authenticated_at
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            verified_at = verified_at.replace(tzinfo=UTC)
        auth_failure_reverified = (
            latest_transition.reason
            in {"reservation_auth_required", "reservation_provider_blocked"}
            and transition_at <= verified_at
        )
        preflight_auth_reverified = (
            latest_transition.reason == "provider_account_not_authenticated_before_reservation"
            and transition_at <= verified_at
        )
        non_auth_unknown = latest_transition.reason == "reservation_unknown"
        if not (auth_failure_reverified or preflight_auth_reverified or non_auth_unknown):
            continue

        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(WatchCandidate.watch_id == watch.id)
                )
            ).all()
        )
        for candidate in candidates:
            if candidate.state != "failed":
                continue
            attempt = await session.scalar(
                select(ReservationAttempt)
                .where(ReservationAttempt.candidate_id == candidate.id)
                .order_by(ReservationAttempt.attempt_sequence.desc())
                .limit(1)
            )
            if auth_failure_reverified:
                if attempt is not None and attempt.outcome in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }:
                    candidate.state = "observed"
            elif (
                non_auth_unknown
                and attempt is not None
                and attempt.outcome is ReservationOutcome.UNKNOWN
            ):
                candidate.state = "observed"
            # ``provider_account_not_authenticated_before_reservation`` is recorded
            # before ``begin_reservation_attempt``. Keep its candidate state intact:
            # there is no completed attempt to clear and the next observation decides
            # whether the still-unclaimed initial episode can be attempted.

        await apply_watch_transition(
            session,
            watch,
            WatchStatus.SCHEDULED,
            reason=(
                (
                    "provider_login_reverified_after_provider_block"
                    if latest_transition.reason == "reservation_provider_blocked"
                    else "provider_login_reverified"
                )
                if auth_failure_reverified
                else (
                    "provider_login_reverified_before_reservation"
                    if preflight_auth_reverified
                    else "reservation_unknown_monitoring_resumed"
                )
            ),
        )
        resumed.append(watch.id)
    return resumed


SEAT_FOUND_STATUSES = frozenset(
    {
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
        SeatObservationStatus.STANDING_PLUS_SEAT,
    }
)
ACTIONABLE_SEAT_STATUSES = SEAT_FOUND_STATUSES | {SeatObservationStatus.WAITLIST_AVAILABLE}


async def get_or_create_provider_circuit(
    session: AsyncSession, provider: Provider, *, lock: bool = False
) -> ProviderCircuit:
    query = select(ProviderCircuit).where(ProviderCircuit.provider == provider)
    if lock:
        query = query.with_for_update()
    circuit = await session.scalar(query)
    if circuit is not None:
        return circuit

    circuit = ProviderCircuit(
        provider=provider,
        state=ProviderCircuitState.CLOSED,
        generation=0,
        manual_resume_required=False,
    )
    try:
        async with session.begin_nested():
            session.add(circuit)
            await session.flush()
    except IntegrityError:
        circuit = await session.scalar(query)
        if circuit is None:
            raise
    return circuit


async def record_seat_observation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    result: SeatObservationResult,
    *,
    apply_status_transition: bool = True,
) -> SeatObservation:
    """Persist only the normalized observation and related atomic state/outbox changes."""
    apply_operational_projection(candidate, result)
    observation = SeatObservation(
        candidate=candidate,
        status=result.status,
        source=result.source,
        observed_at=result.observed_at,
        fresh_until=result.fresh_until,
        error_category=result.error_category,
    )
    session.add(observation)
    await session.flush()

    is_actionable = result.status in ACTIONABLE_SEAT_STATUSES
    candidate.state = "seat_found" if is_actionable else "observed"
    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.seat_observed",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "status": result.status.value,
            "source": result.source,
            "observed_at": result.observed_at.isoformat(),
            "fresh_until": result.fresh_until.isoformat(),
        },
        dedupe_key=f"seat-observation:{observation.id}",
    )
    if (
        apply_status_transition
        and result.status == SeatObservationStatus.WAITLIST_AVAILABLE
        and (watch.status == WatchStatus.WATCHING)
    ):
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.OFFICIAL_WAITLIST,
            reason="authorized_seat_observation_waitlist_available",
            observation=observation,
        )
    elif (
        apply_status_transition
        and result.status in SEAT_FOUND_STATUSES
        and watch.status == WatchStatus.WATCHING
    ):
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.SEAT_FOUND,
            reason="authorized_seat_observation_actionable",
            observation=observation,
        )
    return observation


async def begin_reservation_attempt(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    idempotency_key: str,
    *,
    episode_key: str | None = None,
    retry_authorized: bool = False,
    credential_version: int | None = None,
) -> tuple[ReservationAttempt, bool]:
    dependencies = ReservationAttemptClaimDependencies(
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        is_payment_hold_ended=is_payment_hold_ended,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        actionable_seat_statuses=ACTIONABLE_SEAT_STATUSES,
    )
    return await begin_reservation_attempt_application(
        session,
        watch,
        candidate,
        idempotency_key,
        episode_key=episode_key,
        retry_authorized=retry_authorized,
        credential_version=credential_version,
        dependencies=dependencies,
    )


async def complete_reservation_attempt(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    result: ReservationResult,
    confirmation: ReservationConfirmationResult | None = None,
) -> None:
    if attempt.outcome != ReservationOutcome.PENDING:
        raise HTTPException(409, "reservation attempt was already completed")
    attempt.outcome = result.outcome
    if result.credential_version is not None:
        attempt.credential_version = result.credential_version
    if confirmation is not None:
        if confirmation.provider != watch.provider:
            raise ValueError("reservation confirmation provider does not match watch")
        record_reservation_confirmation(attempt, confirmation)
    completed_at = datetime.now(UTC)
    attempt.finished_at = max(result.observed_at, completed_at)
    attempt.payment_deadline = result.payment_deadline
    attempt.official_handoff_url = (
        str(result.official_handoff_url) if result.official_handoff_url is not None else None
    )

    successful_hold = result.outcome in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
    }
    if (
        successful_hold
        and result.payment_deadline is not None
        and result.payment_deadline <= completed_at
    ):
        attempt.outcome = ReservationOutcome.UNKNOWN
        attempt.payment_deadline = None
        attempt.official_handoff_url = None
        # The provider reported a successful hold whose deadline was already unusable.
        # That is an ambiguous reservation result, not evidence that authentication
        # failed. Keep observing, while the ambiguous attempt remains a durable
        # fence. A later availability edge cannot make this result safe to replay.
        candidate.state = "observed"
        if watch.status == WatchStatus.RESERVING:
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.WATCHING,
                reason="reservation_result_deadline_already_elapsed",
            )
        await add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type="watch.reservation_result_requires_manual_check",
            payload={
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "reason": "payment_deadline_already_elapsed",
            },
            dedupe_key=f"reservation-result-expired-deadline:{attempt.id}",
        )
        return
    if successful_hold:
        candidate.state = "payment_required"
        watch.payment_deadline = result.payment_deadline
        if result.official_handoff_url is None:
            raise RuntimeError("successful reservation result requires an official handoff URL")
        watch.official_booking_url = str(result.official_handoff_url)
        if watch.status == WatchStatus.RESERVING:
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.PAYMENT_REQUIRED,
                reason="reservation_requires_user_payment",
            )
        lower_candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(
                        WatchCandidate.watch_id == watch.id,
                        WatchCandidate.priority > candidate.priority,
                        WatchCandidate.state.in_(["active", "observed", "seat_found"]),
                    )
                )
            ).all()
        )
        for lower in lower_candidates:
            lower.state = "suppressed_by_priority"
            lower.suppressed_by_candidate_id = candidate.id
            await add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.candidate_suppressed",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": lower.id,
                    "suppressed_by_candidate_id": candidate.id,
                    "reason": "higher_priority_payment_required",
                },
                dedupe_key=f"candidate-suppressed:{lower.id}:{candidate.id}",
            )
    else:
        monitoring_resumed = result.outcome in {
            ReservationOutcome.NOT_AVAILABLE,
            ReservationOutcome.UNKNOWN,
            ReservationOutcome.FAILED,
        }
        if monitoring_resumed:
            # Availability can disappear, or the provider can require a manual action
            # or fail without proving that monitoring itself is unsafe. Keep observing
            # until departure. Only NOT_AVAILABLE proves there is no hold and may be
            # re-armed after a later sold-out -> actionable availability edge.
            candidate.state = "observed"
            target = WatchStatus.WATCHING
        else:
            candidate.state = "failed"
            target = (
                WatchStatus.AUTH_REQUIRED
                if result.outcome
                in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }
                else WatchStatus.FAILED
            )
        if watch.status == WatchStatus.RESERVING:
            transition_reason = (
                "reservation_failed_monitoring_resumed"
                if result.outcome is ReservationOutcome.FAILED
                else f"reservation_{result.outcome.value}"
            )
            await apply_watch_transition(
                session,
                watch,
                target,
                reason=transition_reason,
            )

        if result.outcome is ReservationOutcome.FAILED:
            await add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.reservation_failed_monitoring_resumed",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": candidate.id,
                    "outcome": result.outcome.value,
                    "reason": "reservation_failed_monitoring_resumed",
                    "monitoring_resumed": True,
                },
                dedupe_key=f"reservation-failed-monitoring-resumed:{attempt.id}",
            )

    result_policy = reservation_attempt_result_policy(result.outcome)
    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_result",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "attempt_started_at": attempt.started_at.isoformat(),
            "attempt_finished_at": (
                attempt.finished_at.isoformat() if attempt.finished_at is not None else None
            ),
            "outcome": result.outcome.value,
            "payment_deadline": (
                result.payment_deadline.isoformat() if result.payment_deadline is not None else None
            ),
            "monitoring_resumed": result.outcome
            in {
                ReservationOutcome.NOT_AVAILABLE,
                ReservationOutcome.UNKNOWN,
                ReservationOutcome.FAILED,
            },
            "retryable": result_policy.retryable,
            "manual_check_required": result_policy.manual_check_required,
            "retry_condition": result_policy.retry_condition,
            **(
                {
                    "progress_stages": [
                        {
                            "stage": progress.stage,
                            "occurred_at": progress.occurred_at.isoformat(),
                        }
                        for progress in result.progress_stages
                    ]
                }
                if result.progress_stages
                else {}
            ),
        },
        dedupe_key=f"reservation-result:{attempt.id}",
    )


def record_reservation_confirmation(
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime | None = None,
) -> None:
    """Persist normalized confirmation evidence without provider transport material."""

    attempt.confirmation_outcome = confirmation.outcome
    attempt.confirmation_source = confirmation.source
    attempt.confirmation_observed_at = confirmation.observed_at
    if reconciled_at is not None:
        if reconciled_at.tzinfo is None or reconciled_at.utcoffset() is None:
            raise ValueError("reconciled_at must include a timezone")
        attempt.last_reconciled_at = max(reconciled_at, confirmation.observed_at)


async def apply_reservation_reconciliation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime,
) -> None:
    """Apply one bounded read-only confirmation while preserving the no-retry fence.

    Ambiguous lookup results remain fenced. An exact official-list NOT_FOUND for an
    UNKNOWN attempt may authorize one separately fenced retry after a later actionable
    observation. A positive exact match may restore the payment handoff that the
    original worker could not durably prove.
    """

    if attempt.outcome not in {
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.UNKNOWN,
    }:
        raise HTTPException(409, "reservation attempt is not eligible for reconciliation")
    if confirmation.provider != watch.provider:
        raise ValueError("reservation confirmation provider does not match watch")
    payment_deadline = watch.payment_deadline
    if payment_deadline is not None and (
        payment_deadline.tzinfo is None or payment_deadline.utcoffset() is None
    ):
        payment_deadline = payment_deadline.replace(tzinfo=UTC)
    legacy_expired_hold_cleanup_read = (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.reconciliation_attempt_count == RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and attempt.post_deadline_reconciled_at is not None
        and attempt.confirmation_outcome
        is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and attempt.payment_deadline is not None
        and _utc_instant(attempt.payment_deadline)
        <= _utc_instant(attempt.post_deadline_reconciled_at)
    )
    post_deadline_final_read = (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and watch.status is WatchStatus.PAYMENT_REQUIRED
        and payment_deadline is not None
        and payment_deadline <= reconciled_at
        and attempt.reconciliation_attempt_count >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and (attempt.post_deadline_reconciled_at is None or legacy_expired_hold_cleanup_read)
    )
    record_reservation_confirmation(
        attempt,
        confirmation,
        reconciled_at=reconciled_at,
    )
    confirmed_hold_has_usable_deadline = (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is not None
        and confirmation.payment_deadline > reconciled_at
    )
    reconciliation_attempt_limit = (
        UNKNOWN_RECONCILIATION_MAX_ATTEMPTS
        if attempt.outcome is ReservationOutcome.UNKNOWN
        else RESERVATION_RECONCILIATION_MAX_ATTEMPTS
    )
    if post_deadline_final_read:
        if confirmed_hold_has_usable_deadline:
            attempt.post_deadline_reconciled_at = None
        else:
            attempt.post_deadline_reconciled_at = reconciled_at
            if legacy_expired_hold_cleanup_read:
                attempt.reconciliation_attempt_count += 1
    else:
        attempt.reconciliation_attempt_count += 1
        if attempt.reconciliation_attempt_count > reconciliation_attempt_limit:
            raise RuntimeError("reservation reconciliation attempt limit exceeded")
    confirmed_absent_unknown = (
        attempt.outcome is ReservationOutcome.UNKNOWN
        and confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
    )
    terminal_confirmation = (
        confirmed_hold_has_usable_deadline
        or confirmed_absent_unknown
        or confirmation.outcome
        in {
            ReservationConfirmationOutcome.AUTH_REQUIRED,
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        }
    )
    if (
        attempt.outcome is ReservationOutcome.UNKNOWN
        and confirmation.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    ):
        retry_interval = unknown_reconciliation_retry_interval(attempt.reconciliation_attempt_count)
        attempt.next_reconcile_at = (
            attempt.last_reconciled_at + retry_interval if retry_interval is not None else None
        )
    elif (
        not terminal_confirmation
        and attempt.reconciliation_attempt_count < RESERVATION_RECONCILIATION_MAX_ATTEMPTS
    ):
        reconciliation_anchor = attempt.last_reconciled_at
        if reconciliation_anchor is None:
            raise RuntimeError("reconciliation must persist a reconciliation timestamp")
        attempt.next_reconcile_at = reconciliation_anchor + RESERVATION_RECONCILIATION_INTERVAL
    elif (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is not None
        and confirmation.payment_deadline <= reconciled_at
        and attempt.reconciliation_attempt_count >= RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        and attempt.post_deadline_reconciled_at is None
    ):
        attempt.next_reconcile_at = reconciled_at + RESERVATION_RECONCILIATION_INTERVAL
    else:
        attempt.next_reconcile_at = None
    expired_confirmed_hold = (
        confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        and confirmation.payment_deadline is not None
        and confirmation.payment_deadline <= reconciled_at
    )
    payment_hold_ended_confirmation = (
        post_deadline_final_read
        and attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and watch.status is WatchStatus.PAYMENT_REQUIRED
        and (
            confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
            or expired_confirmed_hold
        )
    )
    if payment_hold_ended_confirmation:
        if expired_confirmed_hold:
            attempt.payment_deadline = confirmation.payment_deadline
        # The official unpaid hold is either absent or retained only as a row whose
        # own provider deadline has elapsed. Neither is an actionable payment handoff.
        candidate.state = (
            "expired" if watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY else "observed"
        )
        candidate.suppressed_by_candidate_id = None
        suppressed_candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(
                        WatchCandidate.watch_id == watch.id,
                        WatchCandidate.state == "suppressed_by_priority",
                        WatchCandidate.suppressed_by_candidate_id == candidate.id,
                    )
                )
            ).all()
        )
        for suppressed in suppressed_candidates:
            suppressed.state = (
                "expired"
                if watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY
                else "observed"
            )
            suppressed.suppressed_by_candidate_id = None
        watch.payment_deadline = None
        watch.official_booking_url = None
        watch.next_check_at = reconciled_at
        terminal_one_off = watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.EXPIRED if terminal_one_off else WatchStatus.WATCHING,
            reason=(
                "confirmed_payment_hold_no_longer_actionable_one_off_expired"
                if terminal_one_off
                else "confirmed_payment_hold_no_longer_actionable_monitoring_resumed"
            ),
        )
        await add_outbox_event(
            session,
            aggregate_type="watch",
            aggregate_id=watch.id,
            event_type=(
                "watch.payment_hold_ended_one_off_expired"
                if terminal_one_off
                else "watch.payment_hold_ended_monitoring_resumed"
            ),
            payload={
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "terminal": True,
                "status": (
                    WatchStatus.EXPIRED.value if terminal_one_off else WatchStatus.WATCHING.value
                ),
                "from": WatchStatus.PAYMENT_REQUIRED.value,
                "to": (
                    WatchStatus.EXPIRED.value if terminal_one_off else WatchStatus.WATCHING.value
                ),
                "reason": (
                    "confirmed_payment_deadline_elapsed"
                    if expired_confirmed_hold
                    else "confirmed_payment_hold_no_longer_present"
                ),
                "message": (
                    "임시 예약이 결제기한 안에 결제되지 않아 취소되었습니다."
                    if expired_confirmed_hold
                    else "공식 예약 목록에서 미결제 보류가 종료된 것을 확인했습니다."
                ),
                "payment_deadline": (
                    confirmation.payment_deadline.isoformat()
                    if expired_confirmed_hold and confirmation.payment_deadline is not None
                    else None
                ),
                "automatic_reservation_retry": not terminal_one_off,
                "retry_condition": ("new_availability_episode" if not terminal_one_off else None),
            },
            dedupe_key=f"payment-hold-ended:{attempt.id}",
        )
        return
    if confirmation.outcome is not ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        return
    if confirmation.official_handoff_url is None:
        raise RuntimeError("confirmed reservation requires an official handoff URL")
    if confirmation.payment_deadline is not None and confirmation.payment_deadline <= reconciled_at:
        # Preserve the latest official evidence but do not surface an unusable hold.
        return

    attempt.outcome = ReservationOutcome.PAYMENT_REQUIRED
    attempt.payment_deadline = confirmation.payment_deadline
    attempt.official_handoff_url = confirmation.official_handoff_url
    if (
        watch.status
        in {
            WatchStatus.WATCHING,
            WatchStatus.OFFICIAL_WAITLIST,
            WatchStatus.SEAT_FOUND,
            WatchStatus.RESERVING,
            WatchStatus.PAYMENT_REQUIRED,
        }
        and candidate.state != "expired"
    ):
        candidate.state = "payment_required"
        watch.payment_deadline = confirmation.payment_deadline
        watch.official_booking_url = confirmation.official_handoff_url
        if watch.status != WatchStatus.PAYMENT_REQUIRED:
            await apply_watch_transition(
                session,
                watch,
                WatchStatus.PAYMENT_REQUIRED,
                reason="reservation_reconciliation_confirmed_payment_required",
            )
        lower_candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(
                        WatchCandidate.watch_id == watch.id,
                        WatchCandidate.priority > candidate.priority,
                        WatchCandidate.state.in_(["active", "observed", "seat_found"]),
                    )
                )
            ).all()
        )
        for lower in lower_candidates:
            lower.state = "suppressed_by_priority"
            lower.suppressed_by_candidate_id = candidate.id

    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_reconciled",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "confirmation_outcome": confirmation.outcome.value,
            "payment_deadline": (
                confirmation.payment_deadline.isoformat()
                if confirmation.payment_deadline is not None
                else None
            ),
            "retryable": False,
        },
        dedupe_key=(f"reservation-reconciled:{attempt.id}:{confirmation.observed_at.isoformat()}"),
    )


async def update_watch(session: AsyncSession, watch: Watch, data: WatchUpdate) -> Watch:
    dependencies = WatchUpdateDependencies(
        build_watch_dedupe_key=build_watch_dedupe_key,
        add_outbox_event=add_outbox_event,
        now=lambda: datetime.now(UTC),
        validate_channel_ids=validate_channel_ids,
        ensure_focused_observation_capacity=_ensure_focused_observation_capacity,
    )
    try:
        return await update_watch_application(
            session,
            watch,
            data,
            dependencies=dependencies,
        )
    except WatchCommandNotFound as error:
        raise HTTPException(404, str(error)) from None
    except WatchCommandConflict as error:
        raise HTTPException(409, str(error)) from None
    except WatchCommandValidationError as error:
        raise HTTPException(422, str(error)) from None


async def validate_channel_ids(session: AsyncSession, channel_ids: list[str]) -> None:
    try:
        await validate_channel_ids_application(session, channel_ids)
    except WatchCommandValidationError as error:
        raise HTTPException(422, str(error)) from None
