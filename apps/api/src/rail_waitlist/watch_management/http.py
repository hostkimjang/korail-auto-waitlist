from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..celery_app import celery_app
from ..database import get_session
from ..domain import Provider, ReservationOutcome, WatchStatus
from ..idempotency.application import IdempotencyConflict
from ..provider_registry.application import get_timetable_provider
from ..reservations.attempt_result_application import ReservationAttemptAlreadyCompleted
from ..reservations.attempt_runtime import (
    begin_reservation_attempt,
    complete_reservation_attempt,
)
from ..reservations.contracts import ReservationResult
from .application import should_enqueue_after_policy_update, should_enqueue_after_start
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
from .schemas import RegistrationEvidenceConflictDetail, WatchCreate, WatchRead, WatchUpdate
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
) -> list[WatchRead]:
    query = select(Watch).order_by(Watch.created_at.desc())
    if watch_status:
        query = query.where(Watch.status == watch_status)
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
        cancelled = await transition_watch(session, watch, WatchStatus.EXPIRED, idempotency_key)
    except IdempotencyConflict as error:
        raise HTTPException(409, str(error)) from None
    return await watch_read(
        session,
        cancelled,
    )


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
