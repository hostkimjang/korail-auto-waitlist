from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..auth import require_admin
from ..celery_app import celery_app
from ..database import get_session
from ..domain import TERMINAL_STATUSES, Provider, ReservationOutcome, WatchStatus
from ..idempotency.application import IdempotencyConflict
from ..provider_registry.application import get_timetable_provider
from ..reservations.attempt_result_application import ReservationAttemptAlreadyCompleted
from ..reservations.attempt_runtime import (
    begin_reservation_attempt,
    complete_reservation_attempt,
)
from ..reservations.contracts import ReservationResult
from ..reservations.domain import reservation_attempt_result_policy
from ..reservations.manual_rearm_application import (
    ManualReservationRearmNotFound,
    ManualReservationRearmRejected,
)
from ..reservations.manual_rearm_runtime import authorize_manual_reservation_rearm
from ..reservations.provider_confirmation.contracts import ReservationConfirmationOutcome
from ..reservations.reconciliation_policy import ReservationReconciliationResolution
from .application import should_enqueue_after_policy_update, should_enqueue_after_start
from .cancel_application import WatchCancellationInProgress
from .cancel_runtime import cancel_watch as cancel_watch_runtime
from .command_runtime import create_watch, update_watch
from .create_application import (
    WatchCreateForbidden,
    WatchCreateValidationError,
    WatchRegistrationEvidenceExpired,
)
from .lookup_application import WatchLookupNotFound
from .lookup_application import find_watch as find_watch_application
from .models import ReservationAttempt, Watch, WatchCandidate
from .read_model import watch_read, watch_reads
from .schemas import (
    ManualReservationRearmRequest,
    RegistrationEvidenceConflictDetail,
    WatchCreate,
    WatchRead,
    WatchUpdate,
)
from .transition_application import WatchTransitionRejected
from .transition_command_application import WatchTransitionCommandNotFound
from .transition_runtime import transition_watch as transition_watch_runtime
from .update_application import (
    WatchCommandConflict,
    WatchCommandNotFound,
    WatchCommandValidationError,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)]
LOGGER = logging.getLogger(__name__)
_PROCESS_WATCH_NOW_TASK = "rail_waitlist.worker.process_watch_now"
_LIVE_TERMINAL_RECONCILIATION_WINDOW = timedelta(hours=24)
_MANUAL_CHECK_RESERVATION_OUTCOMES = tuple(
    outcome
    for outcome in ReservationOutcome
    if reservation_attempt_result_policy(outcome).manual_check_required
)


def _live_watch_condition(read_at: datetime) -> ColumnElement[bool]:
    latest_watch_attempt_id = (
        select(ReservationAttempt.id)
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(WatchCandidate.watch_id == Watch.id)
        .order_by(
            ReservationAttempt.started_at.desc(),
            ReservationAttempt.attempt_sequence.desc(),
            ReservationAttempt.id.desc(),
        )
        .limit(1)
        .correlate(Watch)
        .scalar_subquery()
    )
    has_latest_attempt_requiring_manual_check = exists(
        select(ReservationAttempt.id).where(
            ReservationAttempt.id == latest_watch_attempt_id,
            ReservationAttempt.outcome.in_(_MANUAL_CHECK_RESERVATION_OUTCOMES),
            ReservationAttempt.confirmation_outcome.is_distinct_from(
                ReservationConfirmationOutcome.CONFIRMED_PAID
            ),
            ReservationAttempt.reconciliation_resolution.is_distinct_from(
                ReservationReconciliationResolution.CONFIRMED_ABSENT
            ),
        )
    )
    has_any_exact_paid_confirmation = exists(
        select(ReservationAttempt.id)
        .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
        .where(
            WatchCandidate.watch_id == Watch.id,
            ReservationAttempt.confirmation_outcome
            == ReservationConfirmationOutcome.CONFIRMED_PAID,
        )
        .correlate(Watch)
    )
    return or_(
        Watch.updated_at >= read_at - _LIVE_TERMINAL_RECONCILIATION_WINDOW,
        ~has_any_exact_paid_confirmation
        & or_(
            Watch.status.not_in(tuple(TERMINAL_STATUSES)),
            has_latest_attempt_requiring_manual_check,
        ),
    )


async def _find_watch_or_404(session: AsyncSession, watch_id: str) -> Watch:
    try:
        return await find_watch_application(session, watch_id)
    except WatchLookupNotFound as error:
        raise HTTPException(404, str(error)) from None


async def _create_watch_or_http_error(
    session: AsyncSession,
    data: WatchCreate,
    idempotency_key: str | None,
) -> Watch:
    try:
        return await create_watch(session, data, idempotency_key)
    except WatchCreateForbidden as error:
        raise HTTPException(403, str(error)) from None
    except (WatchCreateValidationError, WatchCommandValidationError) as error:
        raise HTTPException(422, str(error)) from None
    except WatchRegistrationEvidenceExpired as error:
        conflict = RegistrationEvidenceConflictDetail(
            reason="expired",
            message=str(error),
        )
        raise HTTPException(status_code=409, detail=conflict.model_dump()) from None
    except WatchCommandConflict as error:
        raise HTTPException(409, str(error)) from None


async def _update_watch_or_http_error(
    session: AsyncSession,
    watch: Watch,
    data: WatchUpdate,
) -> Watch:
    try:
        return await update_watch(session, watch, data)
    except WatchCommandNotFound as error:
        raise HTTPException(404, str(error)) from None
    except WatchCommandConflict as error:
        raise HTTPException(409, str(error)) from None
    except WatchCommandValidationError as error:
        raise HTTPException(422, str(error)) from None


async def _begin_reservation_attempt_or_409(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    idempotency_key: str,
) -> tuple[ReservationAttempt, bool]:
    try:
        return await begin_reservation_attempt(
            session,
            watch,
            candidate,
            idempotency_key,
        )
    except WatchTransitionRejected as error:
        raise HTTPException(409, str(error)) from None


async def _complete_reservation_attempt_or_409(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    result: ReservationResult,
) -> None:
    try:
        await complete_reservation_attempt(
            session,
            watch,
            candidate,
            attempt,
            result,
        )
    except ReservationAttemptAlreadyCompleted as error:
        raise HTTPException(409, "reservation attempt was already completed") from error
    except WatchTransitionRejected as error:
        raise HTTPException(409, str(error)) from None


def enqueue_immediate_watch_processing(watch_id: str) -> bool:
    """Wake the durable watch pipeline without coupling commit success to the broker."""
    try:
        celery_app.send_task(_PROCESS_WATCH_NOW_TASK, args=[watch_id], queue="rail")
    except Exception:  # noqa: BLE001 -- broker failures must not roll back a committed watch.
        LOGGER.warning("Immediate watch processing enqueue failed")
        return False
    return True


async def transition_watch(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
) -> Watch:
    """Translate the feature command's domain failures at the HTTP boundary."""
    try:
        return await transition_watch_runtime(
            session,
            watch,
            target,
            idempotency_key,
            reason=reason,
        )
    except WatchTransitionCommandNotFound as error:
        raise HTTPException(404, str(error)) from None
    except WatchTransitionRejected as error:
        raise HTTPException(409, str(error)) from None


@router.post("/watches", response_model=WatchRead, status_code=201)
async def watches_create(
    data: WatchCreate, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    try:
        watch = await _create_watch_or_http_error(session, data, idempotency_key)
    except IdempotencyConflict as error:
        raise HTTPException(409, str(error)) from None
    return await watch_read(session, watch)


@router.get("/watches", response_model=list[WatchRead])
async def watches_list(
    session: Session,
    watch_status: Annotated[WatchStatus | None, Query(alias="status")] = None,
    watch_view: Annotated[Literal["all", "live"], Query(alias="view")] = "all",
) -> list[WatchRead]:
    query = select(Watch).order_by(Watch.created_at.desc())
    if watch_status:
        query = query.where(Watch.status == watch_status)
    if watch_view == "live":
        query = query.where(_live_watch_condition(datetime.now(UTC)))
    return await watch_reads(session, list((await session.scalars(query)).all()))


@router.get("/watches/{watch_id}", response_model=WatchRead)
async def watches_get(watch_id: str, session: Session) -> WatchRead:
    return await watch_read(session, await _find_watch_or_404(session, watch_id))


@router.patch("/watches/{watch_id}", response_model=WatchRead)
async def watches_update(watch_id: str, data: WatchUpdate, session: Session) -> WatchRead:
    watch = await _find_watch_or_404(session, watch_id)
    updated = await _update_watch_or_http_error(session, watch, data)
    if await should_enqueue_after_policy_update(session, data.reservation_policy, updated):
        # 기존 reservation attempt fence는 그대로 둔 채, 이미 좌석을 찾은 작업만
        # scheduler와 동일한 safe one-time pipeline에 best-effort로 다시 태웁니다.
        enqueue_immediate_watch_processing(updated.id)
    return await watch_read(session, updated)


@router.delete("/watches/{watch_id}", status_code=204)
async def watches_delete(watch_id: str, session: Session) -> Response:
    watch = await _find_watch_or_404(session, watch_id)
    if watch.status not in {WatchStatus.DRAFT, WatchStatus.EXPIRED, WatchStatus.FAILED}:
        raise HTTPException(409, "cancel an active watch before deleting it")
    await session.delete(watch)
    await session.commit()
    return Response(status_code=204)


@router.post("/watches/{watch_id}/start", response_model=WatchRead)
async def watches_start(
    watch_id: str, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    watch = await _find_watch_or_404(session, watch_id)
    previous_status = watch.status
    try:
        started = await transition_watch(
            session,
            watch,
            WatchStatus.SCHEDULED,
            idempotency_key,
        )
    except IdempotencyConflict as error:
        raise HTTPException(409, str(error)) from None
    if await should_enqueue_after_start(session, previous_status, started):
        enqueue_immediate_watch_processing(started.id)
    return await watch_read(session, started)


@router.post("/watches/{watch_id}/pause", response_model=WatchRead)
async def watches_pause(
    watch_id: str, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    watch = await _find_watch_or_404(session, watch_id)
    try:
        paused = await transition_watch(session, watch, WatchStatus.PAUSED, idempotency_key)
    except IdempotencyConflict as error:
        raise HTTPException(409, str(error)) from None
    return await watch_read(
        session,
        paused,
    )


@router.post("/watches/{watch_id}/cancel", response_model=WatchRead)
async def watches_cancel(
    watch_id: str, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    watch = await _find_watch_or_404(session, watch_id)
    try:
        cancelled = await cancel_watch_runtime(session, watch, idempotency_key)
    except WatchTransitionCommandNotFound as error:
        raise HTTPException(404, str(error)) from None
    except IdempotencyConflict as error:
        raise HTTPException(409, str(error)) from None
    except (WatchCancellationInProgress, WatchTransitionRejected) as error:
        raise HTTPException(409, str(error)) from None
    return await watch_read(
        session,
        cancelled,
    )


@router.post("/watches/{watch_id}/reservation-rearm", response_model=WatchRead)
async def watches_reservation_rearm(
    watch_id: str,
    session: Session,
    data: Annotated[ManualReservationRearmRequest | None, Body()] = None,
    _idempotency_key: IdempotencyKey = None,
) -> WatchRead:
    """Authorize one retry after a bounded official-state confirmation."""
    try:
        result = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=data.reason if data is not None else None,
            official_reservation_state_confirmed=(
                data.official_reservation_state_confirmed is True if data is not None else False
            ),
        )
    except ManualReservationRearmNotFound as error:
        raise HTTPException(404, str(error)) from None
    except ManualReservationRearmRejected as error:
        raise HTTPException(409, str(error)) from None
    if result.created:
        enqueue_immediate_watch_processing(result.watch.id)
    return await watch_read(session, result.watch)


@router.post("/watches/{watch_id}/mock-transition", response_model=WatchRead)
async def watches_mock_transition(
    watch_id: str,
    target: WatchStatus,
    session: Session,
    payment_deadline: datetime | None = None,
) -> WatchRead:
    watch = await _find_watch_or_404(session, watch_id)
    if watch.provider != Provider.MOCK:
        raise HTTPException(403, "mock transition is only available for the mock provider")
    if target == WatchStatus.RESERVING:
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.watch_id == watch.id)
            .order_by(WatchCandidate.priority)
            .limit(1)
        )
        if candidate is None:
            raise HTTPException(409, "a persisted candidate is required for reservation")
        _, created = await _begin_reservation_attempt_or_409(
            session,
            watch,
            candidate,
            f"mock-debug:{candidate.id}",
        )
        if not created:
            await session.rollback()
            raise HTTPException(409, "reservation was already attempted")
        await session.commit()
        await session.refresh(watch)
        return await watch_read(session, watch)
    if target == WatchStatus.PAYMENT_REQUIRED:
        if payment_deadline is not None and (
            payment_deadline.tzinfo is None or payment_deadline.utcoffset() is None
        ):
            raise HTTPException(422, "payment_deadline must include a timezone")
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.watch_id == watch.id)
            .order_by(WatchCandidate.priority)
            .limit(1)
        )
        if candidate is None:
            raise HTTPException(409, "a persisted candidate is required for reservation")
        attempt, created = await _begin_reservation_attempt_or_409(
            session,
            watch,
            candidate,
            f"mock-debug:{candidate.id}",
        )
        if created:
            await session.commit()
        normalized_deadline = payment_deadline.astimezone(UTC) if payment_deadline else None
        await _complete_reservation_attempt_or_409(
            session,
            watch,
            candidate,
            attempt,
            ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source="mock",
                observed_at=datetime.now(UTC),
                payment_deadline=normalized_deadline,
                official_handoff_url=get_timetable_provider(Provider.MOCK).official_booking_url(),
            ),
        )
        await session.commit()
        await session.refresh(watch)
        return await watch_read(session, watch)
    return await watch_read(session, await transition_watch(session, watch, target))
