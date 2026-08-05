from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .domain import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatObservationMode,
    SeatObservationStatus,
    WatchStatus,
)
from .models import (
    IdempotencyRecord,
    NotificationChannel,
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

RESERVATION_RECONCILIATION_MAX_ATTEMPTS = 3
RESERVATION_RECONCILIATION_INTERVAL = timedelta(seconds=30)
UNKNOWN_RECONCILIATION_MAX_ATTEMPTS = 6
CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX = "confirmed-absent-retry:"
MAX_FOCUSED_WATCHES_PER_PROVIDER = 3
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
    filters = [
        Watch.provider == provider,
        Watch.seat_observation_mode == SeatObservationMode.FOCUSED,
        Watch.status.not_in(list(TERMINAL_STATUSES)),
    ]
    if exclude_watch_id is not None:
        filters.append(Watch.id != exclude_watch_id)
    focused_ids = list(
        (
            await session.scalars(
                select(Watch.id)
                .where(*filters)
                .order_by(Watch.created_at, Watch.id)
                .limit(MAX_FOCUSED_WATCHES_PER_PROVIDER)
                .with_for_update()
            )
        ).all()
    )
    if len(focused_ids) >= MAX_FOCUSED_WATCHES_PER_PROVIDER:
        raise HTTPException(
            409,
            "focused observation allows up to 3 non-terminal watches per provider",
        )


def is_confirmed_absent_retry_source(attempt: ReservationAttempt) -> bool:
    """Return whether exact negative evidence can safely re-arm one attempt.

    Older PAYMENT_REQUIRED rows may predate persisted payment deadlines and the
    post-deadline marker.  They would otherwise remain fenced forever even after an
    official reservation-list read proved the hold absent.  Keep this compatibility
    path deliberately narrower than the normal expired-hold flow: a missing deadline,
    exact NOT_FOUND confirmation, and a non-retry episode are all required.
    """
    if (
        attempt.confirmation_outcome is not ReservationConfirmationOutcome.NOT_FOUND
        or attempt.confirmation_observed_at is None
        or attempt.episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
    ):
        return False
    if attempt.outcome is ReservationOutcome.UNKNOWN:
        return True
    return (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.payment_deadline is None
        and attempt.post_deadline_reconciled_at is None
    )


_RESERVATION_RETRY_EDGE_OBSERVATIONS = frozenset(
    {
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }
)


def request_hash(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def get_idempotent_resource(
    session: AsyncSession, scope: str, key: str | None, payload_hash: str
) -> str | None:
    if not key:
        return None
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
        )
    )
    if record is None:
        return None
    if record.request_hash != payload_hash:
        raise HTTPException(409, "Idempotency-Key was already used with a different request")
    return record.resource_id


async def remember_idempotency(
    session: AsyncSession, scope: str, key: str | None, resource_id: str, payload_hash: str
) -> None:
    if key:
        session.add(
            IdempotencyRecord(
                scope=scope, key=key, resource_id=resource_id, request_hash=payload_hash
            )
        )


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
    payload_hash = request_hash({"watch_id": watch.id, "target": target.value})
    scope = f"watch.transition.{target.value}"
    existing_id = await get_idempotent_resource(session, scope, idempotency_key, payload_hash)
    if existing_id:
        existing = await session.get(Watch, existing_id)
        if existing:
            return existing
    if watch.status == target:
        return watch
    if target not in ALLOWED_TRANSITIONS[watch.status]:
        raise HTTPException(409, f"cannot transition {watch.status.value} to {target.value}")
    previous = watch.status
    watch.status = target
    transition_at = datetime.now(UTC)
    watch.updated_at = transition_at
    if target == WatchStatus.SCHEDULED:
        watch.cooldown_until = None
        execution_capabilities = get_execution_provider(watch.provider).capabilities()
        watch.next_check_at = transition_at if execution_capabilities.seat_monitoring else None
    elif target in TERMINAL_STATUSES or target == WatchStatus.PAUSED:
        watch.next_check_at = None
    transition_reason = (reason or f"transition_to_{target.value}")[:160]
    session.add(
        WatchTransitionHistory(
            watch=watch,
            from_status=previous,
            to_status=target,
            reason=transition_reason,
            observation=observation,
        )
    )
    await remember_idempotency(session, scope, idempotency_key, watch.id, payload_hash)
    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.status_changed",
        payload={"watch_id": watch.id, "from": previous.value, "to": target.value},
        dedupe_key=f"watch:{watch.id}:transition:{previous.value}:{target.value}:{transition_at.isoformat()}",
    )
    await add_watch_notifications(
        session,
        watch,
        target,
        f"{previous.value}:{target.value}:{transition_at.isoformat()}",
        reason=transition_reason,
    )
    return watch


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
    normalized_episode_key = episode_key or f"manual:{idempotency_key}"
    existing = await session.scalar(
        select(ReservationAttempt).where(
            ReservationAttempt.candidate_id == candidate.id,
            ReservationAttempt.episode_key == normalized_episode_key,
        )
    )
    if existing is not None:
        return existing, False

    latest_attempt = await session.scalar(
        select(ReservationAttempt)
        .where(ReservationAttempt.candidate_id == candidate.id)
        .order_by(ReservationAttempt.attempt_sequence.desc())
        .limit(1)
    )
    confirmed_absent_retry_authorized = False
    if latest_attempt is not None and normalized_episode_key.startswith(
        CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX
    ):
        expected_episode_key = f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}{latest_attempt.id}"
        actionable_after_confirmation = None
        if (
            retry_authorized
            and normalized_episode_key == expected_episode_key
            and is_confirmed_absent_retry_source(latest_attempt)
        ):
            actionable_after_confirmation = await session.scalar(
                select(SeatObservation.id)
                .where(
                    SeatObservation.candidate_id == candidate.id,
                    SeatObservation.observed_at > latest_attempt.confirmation_observed_at,
                    SeatObservation.status.in_(ACTIONABLE_SEAT_STATUSES),
                )
                .order_by(SeatObservation.observed_at, SeatObservation.id)
                .limit(1)
            )
        confirmed_absent_retry_authorized = actionable_after_confirmation is not None
    payment_hold_ended = latest_attempt is not None and is_payment_hold_ended(latest_attempt)
    payment_hold_retry_edge_observed = False
    if payment_hold_ended and latest_attempt is not None:
        retry_edge = await session.scalar(
            select(SeatObservation.id)
            .where(
                SeatObservation.candidate_id == candidate.id,
                SeatObservation.observed_at > latest_attempt.post_deadline_reconciled_at,
                SeatObservation.status.in_(_RESERVATION_RETRY_EDGE_OBSERVATIONS),
            )
            .order_by(SeatObservation.observed_at, SeatObservation.id)
            .limit(1)
        )
        payment_hold_retry_edge_observed = retry_edge is not None
    if latest_attempt is not None and (
        not retry_authorized
        or latest_attempt.outcome
        not in {
            ReservationOutcome.NOT_AVAILABLE,
            ReservationOutcome.AUTH_REQUIRED,
            ReservationOutcome.PROVIDER_BLOCKED,
        }
        and not payment_hold_retry_edge_observed
        and not confirmed_absent_retry_authorized
    ):
        return latest_attempt, False

    latest_sequence = latest_attempt.attempt_sequence if latest_attempt is not None else 0

    attempt = ReservationAttempt(
        candidate_id=candidate.id,
        attempt_sequence=(latest_sequence or 0) + 1,
        episode_key=normalized_episode_key,
        idempotency_key=idempotency_key,
        outcome=ReservationOutcome.PENDING,
        credential_version=credential_version,
    )
    try:
        async with session.begin_nested():
            session.add(attempt)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(ReservationAttempt).where(
                ReservationAttempt.candidate_id == candidate.id,
                ReservationAttempt.episode_key == normalized_episode_key,
            )
        )
        if existing is None:
            raise
        return existing, False

    candidate.state = "reservation_attempted"
    watch.reservation_attempted = True
    if watch.status == WatchStatus.SEAT_FOUND:
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.RESERVING,
            reason="reservation_attempt_claimed",
        )
    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.reservation_attempted",
        payload={
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "attempt_sequence": attempt.attempt_sequence,
            "episode_key": attempt.episode_key,
            "outcome": ReservationOutcome.PENDING.value,
        },
        dedupe_key=f"reservation-attempt:{attempt.id}",
    )
    return attempt, True


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
    locked_watch = await session.scalar(
        select(Watch)
        .where(Watch.id == watch.id)
        .options(selectinload(Watch.candidates))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked_watch is None:
        raise HTTPException(404, "watch not found")
    watch = locked_watch
    values = data.model_dump(exclude_unset=True)
    previous_reservation_policy = watch.reservation_policy
    fully_editable_statuses = {WatchStatus.DRAFT, WatchStatus.PAUSED}
    policy_editable_statuses = {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
    }
    policy_only_active_update = watch.status not in fully_editable_statuses
    active_control_fields = {
        "reservation_policy",
        "seat_observation_mode",
        "focused_observation_interval_seconds",
    }
    if policy_only_active_update and (
        watch.status not in policy_editable_statuses
        or not values
        or not set(values).issubset(active_control_fields)
    ):
        raise HTTPException(
            409,
            "active watches only allow reservation_policy and observation policy updates",
        )
    if "notification_channel_ids" in values:
        await validate_channel_ids(session, values["notification_channel_ids"])
    if watch.candidates and not policy_only_active_update:
        if "seat_class" in values and any(
            candidate.seat_class != values["seat_class"] for candidate in watch.candidates
        ):
            raise HTTPException(422, "seat_class must remain consistent with persisted candidates")
        if "train_numbers" in values and set(values["train_numbers"]) != {
            candidate.train_number for candidate in watch.candidates
        }:
            raise HTTPException(
                422, "train_numbers must remain consistent with persisted candidates"
            )
    if not policy_only_active_update:
        next_time_from = values.get("time_from", watch.time_from)
        next_time_to = values.get("time_to", watch.time_to)
        if next_time_from >= next_time_to:
            raise HTTPException(422, "time_from must be earlier than time_to")
        seoul = ZoneInfo("Asia/Seoul")
        for candidate in watch.candidates:
            departure_at = candidate.departure_at
            if departure_at.tzinfo is None or departure_at.utcoffset() is None:
                departure_at = departure_at.replace(tzinfo=UTC)
            local_departure = departure_at.astimezone(seoul)
            local_time = local_departure.time().replace(tzinfo=None)
            if (
                local_departure.date() != watch.travel_date
                or not next_time_from <= local_time <= next_time_to
            ):
                raise HTTPException(
                    422, "time window must remain consistent with persisted candidates"
                )
    next_observation_mode = values.get("seat_observation_mode", watch.seat_observation_mode)
    if (
        next_observation_mode is SeatObservationMode.FOCUSED
        and watch.seat_observation_mode is not SeatObservationMode.FOCUSED
    ):
        await _ensure_focused_observation_capacity(
            session,
            watch.provider,
            exclude_watch_id=watch.id,
        )
    for field, value in values.items():
        setattr(watch, field, value)
    if set(values).intersection(
        {"seat_observation_mode", "focused_observation_interval_seconds"}
    ) and watch.status in {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }:
        watch.next_check_at = datetime.now(UTC)
    if (
        previous_reservation_policy is not ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and watch.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and watch.status is WatchStatus.SEAT_FOUND
    ):
        # The immediate task uses the same due-watch observation and reservation fences as
        # the scheduler. Arm this already-actionable watch in the same transaction as the
        # policy change; otherwise a future next_check_at makes the best-effort task a no-op.
        watch.next_check_at = datetime.now(UTC)
    if not policy_only_active_update:
        watch.dedupe_key = build_watch_dedupe_key(
            watch.provider,
            watch.origin,
            watch.destination,
            watch.travel_date,
            watch.time_from,
            watch.time_to,
            watch.seat_class,
            watch.passenger_count,
            watch.train_numbers,
            watch.origin_node_id,
            watch.destination_node_id,
        )
    await add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.updated",
        payload={"watch_id": watch.id},
        dedupe_key=f"watch:{watch.id}:updated:{datetime.now(UTC).isoformat()}",
    )
    await session.commit()
    await session.refresh(watch)
    return watch


async def validate_channel_ids(session: AsyncSession, channel_ids: list[str]) -> None:
    unique_ids = set(channel_ids)
    if not unique_ids:
        return
    found = set(
        (
            await session.scalars(
                select(NotificationChannel.id).where(
                    NotificationChannel.id.in_(unique_ids), NotificationChannel.enabled.is_(True)
                )
            )
        ).all()
    )
    if unique_ids - found:
        raise HTTPException(422, "notification channels must exist and be enabled")
