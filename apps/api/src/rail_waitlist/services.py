from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    Provider,
    WatchStatus,
)
from .idempotency.application import (
    get_idempotent_resource as get_idempotent_resource,
)
from .idempotency.application import remember_idempotency as remember_idempotency
from .idempotency.application import request_hash as request_hash
from .notification_management.watch_transition_application import (
    add_watch_notifications as add_watch_notifications,
)
from .observations.contracts import SeatObservationResult
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
from .observations.recording_application import ObservationRecordingDependencies
from .observations.recording_application import (
    record_seat_observation as record_seat_observation_application,
)
from .observations.status_policy import (
    ACTIONABLE_SEAT_STATUSES as ACTIONABLE_SEAT_STATUSES,
)
from .observations.status_policy import SEAT_FOUND_STATUSES as SEAT_FOUND_STATUSES
from .outbox import add_outbox_event as add_outbox_event
from .policy import build_watch_dedupe_key
from .provider_account_management.auth_recovery_application import (
    ProviderAuthRecoveryDependencies,
)
from .provider_account_management.auth_recovery_application import (
    resume_watches_after_verified_provider_login as resume_provider_login_watches_application,
)
from .provider_circuit.application import (
    get_or_create_provider_circuit as get_or_create_provider_circuit_application,
)
from .provider_circuit.models import ProviderCircuit
from .provider_registry.application import get_execution_provider, get_timetable_provider
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
from .reservations.contracts import ReservationResult
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
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
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
)
from .reservations.reconciliation_state_application import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_application,
)
from .reservations.reconciliation_state_runtime import (
    reservation_reconciliation_state_dependencies,
)
from .ui_preferences.application import (
    update_admin_ui_preferences as update_admin_ui_preferences,
)
from .watch_management.create_application import (
    WatchCreateDependencies,
    WatchCreateForbidden,
    WatchCreateValidationError,
    WatchRegistrationEvidenceExpired,
)
from .watch_management.create_application import create_watch as create_watch_application
from .watch_management.lookup_application import WatchLookupNotFound
from .watch_management.lookup_application import find_watch as find_watch_application
from .watch_management.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from .watch_management.schemas import RegistrationEvidenceConflictDetail, WatchCreate, WatchUpdate
from .watch_management.transition_application import (
    WatchTransitionDependencies,
    WatchTransitionRejected,
)
from .watch_management.transition_application import (
    apply_watch_transition as apply_watch_transition_application,
)
from .watch_management.transition_command_application import (
    WatchTransitionCommandDependencies,
    WatchTransitionCommandNotFound,
)
from .watch_management.transition_command_application import (
    transition_watch as transition_watch_application,
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


def _experimental_rail_enabled() -> bool:
    from .config import get_settings

    return get_settings().experimental_rail_enabled


async def create_watch(
    session: AsyncSession, data: WatchCreate, idempotency_key: str | None = None
) -> Watch:
    dependencies = WatchCreateDependencies(
        request_hash=request_hash,
        get_idempotent_resource=get_idempotent_resource,
        ensure_focused_observation_capacity=_ensure_focused_observation_capacity,
        experimental_rail_enabled=_experimental_rail_enabled,
        validate_channel_ids=validate_channel_ids,
        build_watch_dedupe_key=build_watch_dedupe_key,
        official_booking_url_for_provider=lambda provider: get_timetable_provider(
            provider
        ).official_booking_url(),
        remember_idempotency=remember_idempotency,
        add_outbox_event=add_outbox_event,
        now=lambda: datetime.now(UTC),
    )
    try:
        return await create_watch_application(
            session,
            data,
            idempotency_key,
            dependencies=dependencies,
        )
    except WatchCreateForbidden as error:
        raise HTTPException(403, str(error)) from None
    except WatchCreateValidationError as error:
        raise HTTPException(422, str(error)) from None
    except WatchRegistrationEvidenceExpired as error:
        conflict = RegistrationEvidenceConflictDetail(
            reason="expired",
            message=str(error),
        )
        raise HTTPException(status_code=409, detail=conflict.model_dump()) from None


async def find_watch(session: AsyncSession, watch_id: str) -> Watch:
    try:
        return await find_watch_application(session, watch_id)
    except WatchLookupNotFound as error:
        raise HTTPException(404, str(error)) from None


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
    try:
        return await transition_watch_application(
            session,
            watch,
            target,
            idempotency_key,
            reason=reason,
            dependencies=WatchTransitionCommandDependencies(
                apply_watch_transition=apply_watch_transition,
            ),
        )
    except WatchTransitionCommandNotFound as error:
        raise HTTPException(404, str(error)) from None


async def resume_watches_after_verified_provider_login(
    session: AsyncSession,
    provider: Provider,
    authenticated_at: datetime,
    *,
    credential_version: int | None = None,
) -> list[str]:
    dependencies = ProviderAuthRecoveryDependencies(
        apply_watch_transition=apply_watch_transition,
    )
    return await resume_provider_login_watches_application(
        session,
        provider,
        authenticated_at,
        credential_version=credential_version,
        dependencies=dependencies,
    )


async def get_or_create_provider_circuit(
    session: AsyncSession, provider: Provider, *, lock: bool = False
) -> ProviderCircuit:
    return await get_or_create_provider_circuit_application(
        session,
        provider,
        lock=lock,
    )


async def record_seat_observation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    result: SeatObservationResult,
    *,
    apply_status_transition: bool = True,
) -> SeatObservation:
    dependencies = ObservationRecordingDependencies(
        apply_operational_projection=apply_operational_projection,
        add_outbox_event=add_outbox_event,
        apply_watch_transition=apply_watch_transition,
    )
    return await record_seat_observation_application(
        session,
        watch,
        candidate,
        result,
        apply_status_transition=apply_status_transition,
        dependencies=dependencies,
    )


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
    dependencies = reservation_reconciliation_state_dependencies(
        apply_watch_transition_override=apply_watch_transition,
        add_outbox_event_override=add_outbox_event,
        record_reservation_confirmation_override=record_reservation_confirmation,
        utc_instant_override=_utc_instant,
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
