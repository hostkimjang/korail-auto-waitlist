from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..official_rail_identity import normalize_official_train_number
from ..timetable_management.schemas import (
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)
from .models import KorailBrowserSeatSnapshot, KorailBrowserSnapshotBatch
from .schemas import KORAIL_BROWSER_COMPANION_SOURCE

SOURCE = KORAIL_BROWSER_COMPANION_SOURCE
KOREA = ZoneInfo("Asia/Seoul")

__all__ = ("overlay_korail_browser_snapshots",)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _snapshot_key(
    train_number: str, departure_at: datetime, seat_class: object
) -> tuple[str, datetime, object]:
    return (
        normalize_official_train_number(train_number),
        _as_utc(departure_at).replace(microsecond=0),
        seat_class,
    )


async def overlay_korail_browser_snapshots(
    session: AsyncSession,
    items: list[TimetableItem],
    *,
    origin: str,
    destination: str,
    passenger_count: int,
    now: datetime | None = None,
) -> list[TimetableItem]:
    if not items:
        return items
    current = _as_utc(now or datetime.now(UTC))
    departures = [_as_utc(item.departure_at).replace(microsecond=0) for item in items]
    travel_dates = {item.departure_at.astimezone(KOREA).date() for item in items}
    rows = list(
        (
            await session.execute(
                select(KorailBrowserSeatSnapshot, KorailBrowserSnapshotBatch)
                .join(
                    KorailBrowserSnapshotBatch,
                    KorailBrowserSeatSnapshot.batch_id == KorailBrowserSnapshotBatch.id,
                )
                .where(
                    KorailBrowserSnapshotBatch.origin == origin.strip(),
                    KorailBrowserSnapshotBatch.destination == destination.strip(),
                    KorailBrowserSnapshotBatch.travel_date.in_(travel_dates),
                    KorailBrowserSnapshotBatch.passenger_count == passenger_count,
                    KorailBrowserSnapshotBatch.fresh_until > current,
                    KorailBrowserSeatSnapshot.departure_at >= min(departures),
                    KorailBrowserSeatSnapshot.departure_at <= max(departures),
                )
                .order_by(
                    KorailBrowserSnapshotBatch.observed_at.desc(),
                    KorailBrowserSeatSnapshot.created_at.desc(),
                    KorailBrowserSeatSnapshot.id.desc(),
                )
            )
        ).all()
    )
    snapshots: dict[
        tuple[str, datetime, object],
        tuple[KorailBrowserSeatSnapshot, KorailBrowserSnapshotBatch],
    ] = {}
    for snapshot, batch in rows:
        snapshots.setdefault(
            _snapshot_key(snapshot.train_number, snapshot.departure_at, snapshot.seat_class),
            (snapshot, batch),
        )

    result: list[TimetableItem] = []
    for item in items:
        if item.origin.strip() != origin.strip() or item.destination.strip() != destination.strip():
            result.append(item)
            continue
        seat_classes: list[SeatClassAvailability] = []
        for seat in item.seat_classes:
            matched = snapshots.get(
                _snapshot_key(item.train_number, item.departure_at, seat.seat_class)
            )
            if matched is None or seat.status != "unknown":
                seat_classes.append(seat)
                continue
            snapshot, batch = matched
            seat_classes.append(
                SeatClassAvailability.model_validate(
                    {
                        **seat.model_dump(),
                        "status": snapshot.status.value,
                        "provenance": SeatAvailabilityProvenance(
                            kind="official_page_browser_companion",
                            source=SOURCE,
                            observed_at=_as_utc(batch.observed_at),
                            fresh_until=_as_utc(batch.fresh_until),
                        ),
                        "actions": _seat_actions(snapshot.status.value, item.official_booking_url),
                    }
                )
            )
        result.append(item.model_copy(update={"seat_classes": seat_classes}))
    return result


def _seat_actions(status: str, official_booking_url: AnyHttpUrl) -> list[SeatAvailabilityAction]:
    if status == "standing_only":
        return [
            SeatAvailabilityAction(kind="official_check", url=official_booking_url),
            SeatAvailabilityAction(kind="add_to_watch"),
        ]
    if status in {"available", "limited", "standing_plus_seat"}:
        return [SeatAvailabilityAction(kind="official_check", url=official_booking_url)]
    if status == "sold_out":
        return [SeatAvailabilityAction(kind="add_to_watch")]
    if status == "waitlist_available":
        return [
            SeatAvailabilityAction(kind="official_waitlist", url=official_booking_url),
            SeatAvailabilityAction(kind="add_to_watch"),
        ]
    return []
