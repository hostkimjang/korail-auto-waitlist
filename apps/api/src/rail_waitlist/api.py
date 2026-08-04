from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_admin
from .celery_app import celery_app
from .config import get_settings
from .database import SessionFactory, get_session
from .domain import Provider, ReservationOutcome, ReservationPolicy, WatchStatus
from .korail_browser_bridge import overlay_korail_browser_snapshots
from .korail_browser_seat_source import KorailBrowserTimetableUnavailable
from .models import (
    KorailBrowserSnapshotBatch,
    NotificationChannel,
    OutboxEvent,
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
)
from .official_page_confirmations import (
    overlay_official_page_confirmations,
    upsert_official_page_confirmations,
)
from .provider_accounts import has_authenticated_provider_account
from .providers import (
    OfficialTimetableAdapter,
    ProviderUnavailable,
    RouteValidationError,
    get_execution_provider,
    get_timetable_provider,
    list_capabilities,
)
from .schemas import (
    EventRead,
    KorailBrowserSnapshotRevision,
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
    OfficialPageSeatConfirmationCreate,
    OfficialPageSeatConfirmationRead,
    ProviderCapabilities,
    QueuedResponse,
    ReservationResult,
    SeatStatusRefreshRequest,
    SeatStatusSourceStatus,
    StationCatalog,
    TimetableItem,
    WatchCreate,
    WatchRead,
    WatchUpdate,
)
from .seat_status_cooldown import ProviderCooldown
from .services import (
    add_outbox_event,
    begin_reservation_attempt,
    complete_reservation_attempt,
    create_notification_channel,
    create_watch,
    find_watch,
    payment_hold_end_reason,
    reservation_attempt_result_policy,
    transition_watch,
    update_notification_channel,
    update_watch,
)
from .srt_live_timetable import map_srt_live_timetable
from .srt_provider_adapter import SrtProviderAdapterUnavailable
from .srt_seat_source import SrtLiveTimetableUnavailable
from .timetable_evidence import persist_timetable_seat_evidence
from .timetable_snapshot_cache import TimetableSnapshotKey
from .watch_registration_policy import apply_watch_registration_capability

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)]
LOGGER = logging.getLogger(__name__)
_PROCESS_WATCH_NOW_TASK = "rail_waitlist.worker.process_watch_now"


def _enqueue_immediate_watch_processing(watch_id: str) -> bool:
    """Best-effort wake-up; the 30-second scheduler remains the durable fallback."""
    try:
        celery_app.send_task(_PROCESS_WATCH_NOW_TASK, args=[watch_id], queue="rail")
    except Exception:  # noqa: BLE001 -- broker failures must not roll back a committed watch.
        LOGGER.warning("Immediate watch processing enqueue failed")
        return False
    return True


@router.get(
    "/korail-browser-snapshot-revision",
    response_model=KorailBrowserSnapshotRevision,
)
async def korail_browser_snapshot_revision(
    response: Response, session: Session
) -> KorailBrowserSnapshotRevision:
    response.headers["Cache-Control"] = "no-store"
    revision = await session.scalar(
        select(func.max(KorailBrowserSnapshotBatch.observed_at)).where(
            KorailBrowserSnapshotBatch.fresh_until > datetime.now(timezone.utc)
        )
    )
    if revision is not None:
        revision = (
            revision.replace(tzinfo=timezone.utc)
            if revision.tzinfo is None or revision.utcoffset() is None
            else revision.astimezone(timezone.utc)
        )
    return KorailBrowserSnapshotRevision(revision=revision)


@router.get("/providers", response_model=list[ProviderCapabilities])
async def providers() -> list[ProviderCapabilities]:
    return list_capabilities()


@router.get("/stations", response_model=StationCatalog)
async def stations(provider: Provider, request: Request) -> StationCatalog:
    try:
        adapter = get_timetable_provider(provider)
        if isinstance(adapter, OfficialTimetableAdapter):
            return await request.app.state.station_catalog_service.get_catalog(provider)
        return await adapter.stations()
    except ProviderUnavailable as error:
        raise HTTPException(503, str(error)) from None


@router.get("/timetables", response_model=list[TimetableItem])
async def timetables(
    request: Request,
    response: Response,
    session: Session,
    provider: Provider,
    origin: Annotated[str, Query(min_length=1, max_length=40)],
    destination: Annotated[str, Query(min_length=1, max_length=40)],
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: Annotated[int, Query(ge=1, le=9)] = 1,
    origin_node_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    destination_node_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
) -> list[TimetableItem]:
    response.headers["Cache-Control"] = "no-store"
    items = await _load_timetable_items(
        app=request.app,
        session=session,
        provider=provider,
        origin=origin,
        destination=destination,
        departure_from=departure_from,
        departure_to=departure_to,
        passenger_count=passenger_count,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
    )
    await _store_timetable_snapshot(
        request=request,
        provider=provider,
        origin=origin,
        destination=destination,
        departure_from=departure_from,
        departure_to=departure_to,
        passenger_count=passenger_count,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
        items=items,
    )
    return items


@router.post("/seat-status/refresh", response_model=list[TimetableItem])
async def refresh_seat_status(
    data: SeatStatusRefreshRequest,
    request: Request,
    response: Response,
    session: Session,
) -> list[TimetableItem]:
    """Fetch a fresh server-managed seat snapshot and return exact-matched timetable rows."""
    response.headers["Cache-Control"] = "no-store"
    items = await _load_timetable_items(
        app=request.app,
        session=session,
        provider=data.provider,
        origin=data.origin,
        destination=data.destination,
        departure_from=data.departure_from,
        departure_to=data.departure_to,
        passenger_count=data.passenger_count,
        origin_node_id=data.origin_node_id,
        destination_node_id=data.destination_node_id,
    )
    await _store_timetable_snapshot(
        request=request,
        provider=data.provider,
        origin=data.origin,
        destination=data.destination,
        departure_from=data.departure_from,
        departure_to=data.departure_to,
        passenger_count=data.passenger_count,
        origin_node_id=data.origin_node_id,
        destination_node_id=data.destination_node_id,
        items=items,
    )
    return items


@router.get("/timetable-snapshots", response_model=list[TimetableItem])
async def timetable_snapshot(
    request: Request,
    response: Response,
    provider: Provider,
    origin: Annotated[str, Query(min_length=1, max_length=40)],
    destination: Annotated[str, Query(min_length=1, max_length=40)],
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: Annotated[int, Query(ge=1, le=9)] = 1,
    origin_node_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    destination_node_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
) -> list[TimetableItem]:
    """Return a cached snapshot and only schedule bounded background revalidation."""
    response.headers["Cache-Control"] = "no-store"
    key = TimetableSnapshotKey.from_request(
        provider=provider,
        origin=origin,
        destination=destination,
        departure_from=departure_from,
        departure_to=departure_to,
        passenger_count=passenger_count,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
    )
    items = await request.app.state.timetable_snapshot_cache.get(key)
    if items is None:
        raise HTTPException(404, "timetable snapshot was not found")
    await request.app.state.timetable_snapshot_cache.refresh_if_due(
        key,
        lambda: _load_timetable_snapshot_in_background(
            request=request,
            provider=provider,
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
            origin_node_id=origin_node_id,
            destination_node_id=destination_node_id,
        ),
    )
    return items


@router.get("/seat-status/status", response_model=list[SeatStatusSourceStatus])
async def seat_status_sources(
    request: Request,
    response: Response,
) -> list[SeatStatusSourceStatus]:
    """Expose only the active seat-source cooldowns, not worker provider circuits."""
    response.headers["Cache-Control"] = "no-store"
    cooldown_store = request.app.state.seat_status_cooldown_store
    korail_cooldown, srt_cooldown = await asyncio.gather(
        cooldown_store.get("korail-browser"),
        cooldown_store.get("srt"),
    )
    return [
        _seat_status_source_status("korail", "korail_browser", korail_cooldown),
        _seat_status_source_status("srt", "srt_live", srt_cooldown),
    ]


def _seat_status_source_status(
    provider: Literal["korail", "srt"],
    source: Literal["korail_browser", "srt_live"],
    cooldown: ProviderCooldown | None,
) -> SeatStatusSourceStatus:
    if cooldown is None:
        return SeatStatusSourceStatus(
            provider=provider,
            source=source,
            state="ready",
        )
    return SeatStatusSourceStatus(
        provider=provider,
        source=source,
        state="cooldown",
        cause=cooldown.reason,
        retry_after_seconds=cooldown.retry_after_seconds,
    )


async def _load_timetable_items(
    *,
    app,
    session: AsyncSession,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: int,
    origin_node_id: str | None,
    destination_node_id: str | None,
) -> list[TimetableItem]:
    try:
        if provider == Provider.MOCK:
            items = await _load_adapter_timetable(
                app=app,
                provider=provider,
                origin=origin,
                destination=destination,
                departure_from=departure_from,
                departure_to=departure_to,
                origin_node_id=origin_node_id,
                destination_node_id=destination_node_id,
            )
            return items
        if provider not in {Provider.KORAIL, Provider.SRT}:
            raise HTTPException(400, "unsupported provider")

        try:
            items = await _load_live_timetable(
                app=app,
                provider=provider,
                origin=origin,
                destination=destination,
                departure_from=departure_from,
                departure_to=departure_to,
                passenger_count=passenger_count,
            )
        except (
            KorailBrowserTimetableUnavailable,
            SrtLiveTimetableUnavailable,
            SrtProviderAdapterUnavailable,
            ValueError,
        ):
            LOGGER.warning(
                "Official live timetable unavailable provider=%s; trying TAGO fallback",
                provider.value,
            )
            items = await _load_adapter_timetable(
                app=app,
                provider=provider,
                origin=origin,
                destination=destination,
                departure_from=departure_from,
                departure_to=departure_to,
                origin_node_id=origin_node_id,
                destination_node_id=destination_node_id,
            )

        has_station_nodes = origin_node_id is not None and destination_node_id is not None
        if has_station_nodes:
            items = await overlay_official_page_confirmations(
                session,
                items,
                provider=provider,
                origin_node_id=origin_node_id,
                destination_node_id=destination_node_id,
                passenger_count=passenger_count,
            )
        if provider == Provider.KORAIL:
            items = await overlay_korail_browser_snapshots(
                session,
                items,
                origin=origin,
                destination=destination,
                passenger_count=passenger_count,
            )
        execution_capabilities = get_execution_provider(provider).capabilities()
        items = apply_watch_registration_capability(
            items,
            seat_monitoring_enabled=execution_capabilities.seat_monitoring,
        )
        if has_station_nodes:
            items = await persist_timetable_seat_evidence(
                session,
                items,
                provider=provider,
                origin_node_id=origin_node_id,
                destination_node_id=destination_node_id,
                passenger_count=passenger_count,
            )
        await session.commit()
        return items
    except ProviderUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except RouteValidationError as error:
        raise HTTPException(422, str(error)) from None
    raise HTTPException(400, "unsupported provider")


async def _load_live_timetable(
    *,
    app,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: int,
) -> list[TimetableItem]:
    if provider == Provider.KORAIL:
        return await app.state.korail_browser_seat_source.search_timetable(
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
        )
    if provider == Provider.SRT:
        trains = await app.state.srt_seat_source.search_timetable(
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
        )
        return map_srt_live_timetable(trains)
    raise ValueError("provider does not expose a live timetable")


async def _load_adapter_timetable(
    *,
    app,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    origin_node_id: str | None,
    destination_node_id: str | None,
) -> list[TimetableItem]:
    adapter = get_timetable_provider(provider)
    if isinstance(adapter, OfficialTimetableAdapter):
        service = app.state.station_catalog_service
        await service.get_catalog(provider)
        adapter.tago_client = service.tago_client
    return await adapter.timetable(
        origin=origin,
        destination=destination,
        departure_from=departure_from,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
        departure_to=departure_to,
    )


async def _store_timetable_snapshot(
    *,
    request: Request,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: int,
    origin_node_id: str | None,
    destination_node_id: str | None,
    items: list[TimetableItem],
) -> None:
    key = TimetableSnapshotKey.from_request(
        provider=provider,
        origin=origin,
        destination=destination,
        departure_from=departure_from,
        departure_to=departure_to,
        passenger_count=passenger_count,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
    )
    await request.app.state.timetable_snapshot_cache.store(key, items)


async def _load_timetable_snapshot_in_background(
    *,
    request: Request,
    provider: Provider,
    origin: str,
    destination: str,
    departure_from: datetime,
    departure_to: datetime,
    passenger_count: int,
    origin_node_id: str | None,
    destination_node_id: str | None,
) -> list[TimetableItem]:
    """Use a fresh database session so cache refresh outlives the GET response safely."""
    session_factory = request.app.state.timetable_snapshot_session_factory
    async with session_factory() as session:
        return await _load_timetable_items(
            app=request.app,
            session=session,
            provider=provider,
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
            origin_node_id=origin_node_id,
            destination_node_id=destination_node_id,
        )


@router.post(
    "/seat-observations/official-page-confirmations",
    response_model=OfficialPageSeatConfirmationRead,
    status_code=201,
)
async def official_page_confirmation_create(
    data: OfficialPageSeatConfirmationCreate,
    response: Response,
    session: Session,
    idempotency_key: IdempotencyKey = None,
) -> OfficialPageSeatConfirmationRead:
    response.headers["Cache-Control"] = "no-store"
    try:
        confirmations, created_count, replayed = await upsert_official_page_confirmations(
            session,
            data,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    await session.commit()
    for confirmation in confirmations:
        await session.refresh(confirmation)
    first = confirmations[0]
    return OfficialPageSeatConfirmationRead(
        provider=first.provider,
        origin_node_id=first.origin_node_id,
        destination_node_id=first.destination_node_id,
        train_number=first.train_number,
        departure_at=first.departure_at,
        passenger_count=first.passenger_count,
        seat_classes=[
            {
                "id": confirmation.id,
                "seat_class": confirmation.seat_class,
                "status": confirmation.status.value,
            }
            for confirmation in confirmations
        ],
        source=first.source,
        observed_at=first.observed_at,
        fresh_until=first.fresh_until,
        created_count=created_count,
        replayed=replayed,
    )


async def _watch_read(
    session: AsyncSession,
    watch: Watch,
    *,
    last_checked_at: datetime | None = None,
    latest_observations: dict[str, SeatObservation] | None = None,
    latest_reservation_attempts: dict[str, ReservationAttempt] | None = None,
) -> WatchRead:
    if latest_observations is None:
        latest_observations, latest_by_watch = await _latest_observations_by_watch(
            session, [watch.id]
        )
        last_checked_at = latest_by_watch.get(watch.id)
    if latest_reservation_attempts is None:
        latest_reservation_attempts = await _latest_reservation_attempts_by_watch(
            session, [watch.id]
        )
    payload = WatchRead.model_validate(watch).model_dump()
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
            result_policy = reservation_attempt_result_policy(latest_attempt.outcome)
            hold_end_reason = payment_hold_end_reason(latest_attempt)
            payment_hold_ended = hold_end_reason is not None
            automatic_hold_retry = (
                payment_hold_ended
                and watch.reservation_policy
                is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
            )
            candidate["latest_reservation_attempt"] = {
                "outcome": latest_attempt.outcome,
                "confirmation_outcome": latest_attempt.confirmation_outcome,
                "started_at": latest_attempt.started_at,
                "finished_at": latest_attempt.finished_at,
                "post_deadline_reconciled_at": latest_attempt.post_deadline_reconciled_at,
                "payment_hold_end_reason": hold_end_reason,
                "retryable": (
                    automatic_hold_retry
                    or (not payment_hold_ended and result_policy.retryable)
                ),
                "manual_check_required": (
                    False if payment_hold_ended else result_policy.manual_check_required
                ),
                "retry_condition": (
                    "new_availability_episode"
                    if automatic_hold_retry
                    else None if payment_hold_ended else result_policy.retry_condition
                ),
            }
    return WatchRead.model_validate(
        {
            **payload,
            "last_checked_at": last_checked_at,
        }
    )


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


async def _watch_reads(session: AsyncSession, watches: list[Watch]) -> list[WatchRead]:
    if not watches:
        return []
    latest_observations, latest_by_watch = await _latest_observations_by_watch(
        session, [watch.id for watch in watches]
    )
    latest_reservation_attempts = await _latest_reservation_attempts_by_watch(
        session, [watch.id for watch in watches]
    )
    return [
        await _watch_read(
            session,
            watch,
            last_checked_at=latest_by_watch.get(watch.id),
            latest_observations=latest_observations,
            latest_reservation_attempts=latest_reservation_attempts,
        )
        for watch in watches
    ]


@router.post("/watches", response_model=WatchRead, status_code=201)
async def watches_create(
    data: WatchCreate, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    return await _watch_read(session, await create_watch(session, data, idempotency_key))


@router.get("/watches", response_model=list[WatchRead])
async def watches_list(
    session: Session,
    watch_status: Annotated[WatchStatus | None, Query(alias="status")] = None,
) -> list[WatchRead]:
    query = select(Watch).order_by(Watch.created_at.desc())
    if watch_status:
        query = query.where(Watch.status == watch_status)
    return await _watch_reads(session, list((await session.scalars(query)).all()))


@router.get("/watches/{watch_id}", response_model=WatchRead)
async def watches_get(watch_id: str, session: Session) -> WatchRead:
    return await _watch_read(session, await find_watch(session, watch_id))


@router.patch("/watches/{watch_id}", response_model=WatchRead)
async def watches_update(watch_id: str, data: WatchUpdate, session: Session) -> WatchRead:
    watch = await find_watch(session, watch_id)
    updated = await update_watch(session, watch, data)
    if (
        data.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and updated.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and updated.status is WatchStatus.SEAT_FOUND
        and updated.provider in {Provider.KORAIL, Provider.SRT}
        and await has_authenticated_provider_account(session, updated.provider)
        and get_execution_provider(updated.provider).capabilities().reservation_once
    ):
        # 기존 reservation attempt fence는 그대로 둔 채, 이미 좌석을 찾은 작업만
        # scheduler와 동일한 safe one-time pipeline에 best-effort로 다시 태웁니다.
        _enqueue_immediate_watch_processing(updated.id)
    return await _watch_read(session, updated)


@router.delete("/watches/{watch_id}", status_code=204)
async def watches_delete(watch_id: str, session: Session) -> Response:
    watch = await find_watch(session, watch_id)
    if watch.status not in {WatchStatus.DRAFT, WatchStatus.EXPIRED, WatchStatus.FAILED}:
        raise HTTPException(409, "cancel an active watch before deleting it")
    await session.delete(watch)
    await session.commit()
    return Response(status_code=204)


@router.post("/watches/{watch_id}/start", response_model=WatchRead)
async def watches_start(
    watch_id: str, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    watch = await find_watch(session, watch_id)
    previous_status = watch.status
    started = await transition_watch(
        session,
        watch,
        WatchStatus.SCHEDULED,
        idempotency_key,
    )
    if (
        previous_status is not WatchStatus.SCHEDULED
        and started.status is WatchStatus.SCHEDULED
        and started.next_check_at is not None
        and started.provider in {Provider.KORAIL, Provider.SRT}
        and started.reservation_policy
        is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and await has_authenticated_provider_account(session, started.provider)
        and get_execution_provider(started.provider).capabilities().reservation_once
    ):
        _enqueue_immediate_watch_processing(started.id)
    return await _watch_read(session, started)


@router.post("/watches/{watch_id}/pause", response_model=WatchRead)
async def watches_pause(
    watch_id: str, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    return await _watch_read(
        session,
        await transition_watch(
            session, await find_watch(session, watch_id), WatchStatus.PAUSED, idempotency_key
        ),
    )


@router.post("/watches/{watch_id}/cancel", response_model=WatchRead)
async def watches_cancel(
    watch_id: str, session: Session, idempotency_key: IdempotencyKey = None
) -> WatchRead:
    return await _watch_read(
        session,
        await transition_watch(
            session, await find_watch(session, watch_id), WatchStatus.EXPIRED, idempotency_key
        ),
    )


@router.post("/watches/{watch_id}/mock-transition", response_model=WatchRead)
async def watches_mock_transition(
    watch_id: str,
    target: WatchStatus,
    session: Session,
    payment_deadline: datetime | None = None,
) -> WatchRead:
    watch = await find_watch(session, watch_id)
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
        _, created = await begin_reservation_attempt(
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
        return await _watch_read(session, watch)
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
        attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            f"mock-debug:{candidate.id}",
        )
        if created:
            await session.commit()
        normalized_deadline = (
            payment_deadline.astimezone(timezone.utc)
            if payment_deadline
            else None
        )
        await complete_reservation_attempt(
            session,
            watch,
            candidate,
            attempt,
            ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source="mock",
                observed_at=datetime.now(timezone.utc),
                payment_deadline=normalized_deadline,
                official_handoff_url=get_timetable_provider(Provider.MOCK).official_booking_url(),
            ),
        )
        await session.commit()
        await session.refresh(watch)
        return await _watch_read(session, watch)
    return await _watch_read(session, await transition_watch(session, watch, target))


@router.post("/notifications/channels", response_model=NotificationChannelRead, status_code=201)
async def channels_create(
    data: NotificationChannelCreate, session: Session
) -> NotificationChannel:
    return await create_notification_channel(session, data)


@router.get("/notifications/web-push/public-key")
async def webpush_public_key() -> dict[str, str]:
    public_key = get_settings().webpush_public_key()
    if not public_key:
        raise HTTPException(503, "Web Push VAPID public key is not configured")
    return {"public_key": public_key}


@router.get("/notifications/channels", response_model=list[NotificationChannelRead])
async def channels_list(session: Session) -> list[NotificationChannel]:
    rows = await session.scalars(
        select(NotificationChannel).order_by(NotificationChannel.created_at)
    )
    return list(rows.all())


async def find_channel(session: AsyncSession, channel_id: str) -> NotificationChannel:
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "notification channel not found")
    return channel


@router.get("/notifications/channels/{channel_id}", response_model=NotificationChannelRead)
async def channels_get(channel_id: str, session: Session) -> NotificationChannel:
    return await find_channel(session, channel_id)


@router.patch("/notifications/channels/{channel_id}", response_model=NotificationChannelRead)
async def channels_update(
    channel_id: str, data: NotificationChannelUpdate, session: Session
) -> NotificationChannel:
    return await update_notification_channel(session, await find_channel(session, channel_id), data)


@router.delete("/notifications/channels/{channel_id}", status_code=204)
async def channels_delete(channel_id: str, session: Session) -> Response:
    await find_channel(session, channel_id)
    await session.execute(delete(NotificationChannel).where(NotificationChannel.id == channel_id))
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/notifications/channels/{channel_id}/test-send",
    response_model=QueuedResponse,
    status_code=202,
)
async def channels_test_send(channel_id: str, session: Session) -> QueuedResponse:
    channel = await find_channel(session, channel_id)
    if not channel.enabled:
        raise HTTPException(409, "notification channel is disabled")
    event = await add_outbox_event(
        session,
        aggregate_type="notification_channel",
        aggregate_id=channel.id,
        event_type="notification.test_requested",
        payload={"channel_id": channel.id, "message": "KORAIL·SRT 알림 테스트입니다."},
        dedupe_key=f"notification:{channel.id}:test:{datetime.now().isoformat()}",
    )
    await session.commit()
    return QueuedResponse(queued=True, event_id=event.id)


def event_wire(event: OutboxEvent) -> str:
    data = EventRead(
        id=event.id,
        event_type=event.event_type,
        aggregate_id=event.aggregate_id,
        payload=event.payload,
        created_at=event.created_at,
    ).model_dump_json()
    return f"id: {event.id}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/events")
async def events(
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    async def stream():
        cursor_time = None
        cursor_id = last_event_id
        if cursor_id:
            async with SessionFactory() as session:
                previous = await session.get(OutboxEvent, cursor_id)
                cursor_time = previous.created_at if previous else None
        while not await request.is_disconnected():
            async with SessionFactory() as session:
                query = (
                    select(OutboxEvent)
                    .order_by(OutboxEvent.created_at, OutboxEvent.id)
                    .limit(100)
                )
                if cursor_time is not None:
                    query = query.where(
                        (OutboxEvent.created_at > cursor_time)
                        | (
                            (OutboxEvent.created_at == cursor_time)
                            & (OutboxEvent.id > (cursor_id or ""))
                        )
                    )
                rows = list((await session.scalars(query)).all())
            if rows:
                for event in rows:
                    cursor_time, cursor_id = event.created_at, event.id
                    yield event_wire(event)
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(get_settings().sse_poll_seconds)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
