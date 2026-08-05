from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
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
from .outbox import add_outbox_event as add_outbox_event
from .policy import build_watch_dedupe_key
from .provider_registry.application import get_execution_provider, get_timetable_provider
from .reservation_confirmation import (
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
from .reservations.attempt_result_application import (
    ReservationAttemptAlreadyCompleted,
    ReservationAttemptResultDependencies,
)
from .reservations.attempt_result_application import (
    complete_reservation_attempt as complete_reservation_attempt_application,
)
from .reservations.attempt_result_application import (
    record_reservation_confirmation as record_reservation_confirmation,
)
from .reservations.domain import ReservationAttemptResultPolicy as ReservationAttemptResultPolicy
from .reservations.domain import (
    reservation_attempt_result_policy as reservation_attempt_result_policy,
)
from .reservations.payment_hold_application import _utc_instant as _utc_instant
from .reservations.payment_hold_application import (
    is_payment_hold_ended as is_payment_hold_ended,
)
from .reservations.payment_hold_application import (
    payment_hold_end_reason as payment_hold_end_reason,
)
from .reservations.reconciliation_policy import (
    RESERVATION_RECONCILIATION_INTERVAL as RESERVATION_RECONCILIATION_INTERVAL,
)
from .reservations.reconciliation_policy import (
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS as RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
)
from .reservations.reconciliation_policy import (
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS as UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
)
from .reservations.reconciliation_policy import (
    unknown_reconciliation_retry_interval as unknown_reconciliation_retry_interval,
)
from .reservations.reconciliation_state_application import (
    ReservationReconciliationNotEligible,
    ReservationReconciliationStateDependencies,
)
from .reservations.reconciliation_state_application import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_application,
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
    dependencies = ReservationAttemptResultDependencies(
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        now=lambda: datetime.now(UTC),
        result_policy=reservation_attempt_result_policy,
        record_reservation_confirmation=record_reservation_confirmation,
    )
    try:
        await complete_reservation_attempt_application(
            session,
            watch,
            candidate,
            attempt,
            result,
            confirmation,
            dependencies=dependencies,
        )
    except ReservationAttemptAlreadyCompleted as error:
        raise HTTPException(409, "reservation attempt was already completed") from error


async def apply_reservation_reconciliation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime,
) -> None:
    dependencies = ReservationReconciliationStateDependencies(
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        record_reservation_confirmation=record_reservation_confirmation,
        utc_instant=_utc_instant,
    )
    try:
        await apply_reservation_reconciliation_application(
            session,
            watch,
            candidate,
            attempt,
            confirmation,
            reconciled_at=reconciled_at,
            dependencies=dependencies,
        )
    except ReservationReconciliationNotEligible as error:
        raise HTTPException(
            409,
            "reservation attempt is not eligible for reconciliation",
        ) from error


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
