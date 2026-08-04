from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..domain import Provider
from ..providers import ProviderUnavailable, RouteValidationError
from ..schemas import SeatStatusRefreshRequest, TimetableItem
from ..timetable_snapshot_cache import TimetableSnapshotKey
from .application import TimetableApplication, UnsupportedTimetableProvider, load_timetable_items

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]


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
    items = await _load_items_for_http(
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
    items = await _load_items_for_http(
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


async def _load_items_for_http(
    *,
    app: TimetableApplication,
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
        return await load_timetable_items(
            app=app,
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
    except ProviderUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except RouteValidationError as error:
        raise HTTPException(422, str(error)) from None
    except UnsupportedTimetableProvider as error:
        raise HTTPException(400, str(error)) from None


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
        return await _load_items_for_http(
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
