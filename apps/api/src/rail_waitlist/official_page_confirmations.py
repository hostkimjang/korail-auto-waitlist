from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import AnyHttpUrl
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import Provider, SeatObservationStatus
from .models import OfficialPageSeatConfirmation
from .schemas import (
    OFFICIAL_PAGE_CONFIRMATION_SOURCE,
    OfficialPageSeatConfirmationCreate,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
    normalize_official_train_number,
)
from .services import get_idempotent_resource, remember_idempotency, request_hash

CONFIRMATION_FRESHNESS = timedelta(minutes=5)
IDEMPOTENCY_SCOPE = "official-page-seat-confirmation.create"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _batch_rows(
    session: AsyncSession,
    batch_id: str,
    data: OfficialPageSeatConfirmationCreate,
) -> list[OfficialPageSeatConfirmation]:
    rows = list(
        (
            await session.scalars(
                select(OfficialPageSeatConfirmation).where(
                    OfficialPageSeatConfirmation.batch_id == batch_id
                )
            )
        ).all()
    )
    by_class = {row.seat_class: row for row in rows}
    return [by_class[item.seat_class] for item in data.seat_classes if item.seat_class in by_class]


async def upsert_official_page_confirmations(
    session: AsyncSession,
    data: OfficialPageSeatConfirmationCreate,
    *,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> tuple[list[OfficialPageSeatConfirmation], int, bool]:
    """Atomically upsert normalized seats; source and clock remain server-owned."""
    payload_hash = request_hash(data)
    replay_resource = await get_idempotent_resource(
        session, IDEMPOTENCY_SCOPE, idempotency_key, payload_hash
    )
    if replay_resource is not None:
        replayed = await _batch_rows(session, replay_resource, data)
        if len(replayed) != len(data.seat_classes):
            raise ValueError("idempotent confirmation batch is incomplete")
        return replayed, 0, True
    batch_id = str(uuid.uuid4())
    if idempotency_key:
        try:
            async with session.begin_nested():
                await remember_idempotency(
                    session,
                    IDEMPOTENCY_SCOPE,
                    idempotency_key,
                    batch_id,
                    payload_hash,
                )
                await session.flush()
        except IntegrityError:
            replay_resource = await get_idempotent_resource(
                session, IDEMPOTENCY_SCOPE, idempotency_key, payload_hash
            )
            if replay_resource is None:
                raise ValueError("idempotent confirmation batch is unavailable") from None
            replayed = await _batch_rows(session, replay_resource, data)
            if len(replayed) != len(data.seat_classes):
                raise ValueError("idempotent confirmation batch is incomplete") from None
            return replayed, 0, True

    observed_at = _as_utc(now or datetime.now(UTC))
    fresh_until = observed_at + CONFIRMATION_FRESHNESS
    results: list[OfficialPageSeatConfirmation] = []
    for item in data.seat_classes:
        row = OfficialPageSeatConfirmation(
            batch_id=batch_id,
            provider=data.provider,
            origin_node_id=data.origin_node_id,
            destination_node_id=data.destination_node_id,
            train_number=data.train_number,
            departure_at=data.departure_at,
            passenger_count=data.passenger_count,
            seat_class=item.seat_class,
            status=SeatObservationStatus(item.status),
            source=OFFICIAL_PAGE_CONFIRMATION_SOURCE,
            observed_at=observed_at,
            fresh_until=fresh_until,
        )
        session.add(row)
        results.append(row)

    await session.flush()
    return results, len(results), False


def _confirmation_key(
    train_number: str, departure_at: datetime, seat_class: object
) -> tuple[str, datetime, object]:
    return (
        normalize_official_train_number(train_number),
        _as_utc(departure_at).replace(microsecond=0),
        seat_class,
    )


async def overlay_official_page_confirmations(
    session: AsyncSession,
    items: list[TimetableItem],
    *,
    provider: Provider,
    origin_node_id: str,
    destination_node_id: str,
    passenger_count: int,
    now: datetime | None = None,
) -> list[TimetableItem]:
    if not items or provider not in {Provider.KORAIL, Provider.SRT}:
        return items
    current = _as_utc(now or datetime.now(UTC))
    departures = [_as_utc(item.departure_at).replace(microsecond=0) for item in items]
    rows = list(
        (
            await session.scalars(
                select(OfficialPageSeatConfirmation)
                .where(
                    OfficialPageSeatConfirmation.provider == provider,
                    OfficialPageSeatConfirmation.origin_node_id == origin_node_id,
                    OfficialPageSeatConfirmation.destination_node_id == destination_node_id,
                    OfficialPageSeatConfirmation.passenger_count == passenger_count,
                    OfficialPageSeatConfirmation.departure_at >= min(departures),
                    OfficialPageSeatConfirmation.departure_at <= max(departures),
                    OfficialPageSeatConfirmation.fresh_until > current,
                )
                .order_by(
                    OfficialPageSeatConfirmation.observed_at.desc(),
                    OfficialPageSeatConfirmation.created_at.desc(),
                    OfficialPageSeatConfirmation.id.desc(),
                )
            )
        ).all()
    )
    confirmations: dict[tuple[str, datetime, object], OfficialPageSeatConfirmation] = {}
    for row in rows:
        confirmations.setdefault(
            _confirmation_key(row.train_number, row.departure_at, row.seat_class), row
        )
    result: list[TimetableItem] = []
    for item in items:
        seat_classes: list[SeatClassAvailability] = []
        for seat in item.seat_classes:
            confirmation = confirmations.get(
                _confirmation_key(item.train_number, item.departure_at, seat.seat_class)
            )
            if confirmation is None or seat.status != "unknown":
                seat_classes.append(seat)
                continue
            seat_classes.append(
                SeatClassAvailability.model_validate(
                    {
                        **seat.model_dump(),
                        "status": confirmation.status.value,
                        "provenance": SeatAvailabilityProvenance(
                            kind="user_confirmed_official_page",
                            source=OFFICIAL_PAGE_CONFIRMATION_SOURCE,
                            observed_at=_as_utc(confirmation.observed_at),
                            fresh_until=_as_utc(confirmation.fresh_until),
                        ),
                        "actions": _seat_actions(
                            confirmation.status.value,
                            item.official_booking_url,
                        ),
                    }
                )
            )
        result.append(item.model_copy(update={"seat_classes": seat_classes}))
    return result


def _seat_actions(status: str, official_booking_url: AnyHttpUrl) -> list[SeatAvailabilityAction]:
    if status == "available":
        return [
            SeatAvailabilityAction(kind="official_check", url=official_booking_url),
            SeatAvailabilityAction(kind="add_to_watch"),
        ]
    if status == "sold_out":
        return [SeatAvailabilityAction(kind="add_to_watch")]
    if status == "waitlist_available":
        return [
            SeatAvailabilityAction(kind="official_waitlist", url=official_booking_url),
            SeatAvailabilityAction(kind="add_to_watch"),
        ]
    return [SeatAvailabilityAction(kind="official_check", url=official_booking_url)]
