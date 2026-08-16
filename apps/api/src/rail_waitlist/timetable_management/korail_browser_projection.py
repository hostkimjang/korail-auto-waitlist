from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl

from ..domain import Provider, SeatClass
from ..korail_sidecar.browser_contracts import (
    SOURCE_NAME,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
    SeatStatus,
)
from ..korail_sidecar.browser_page_contracts import OFFICIAL_KORAIL_SEARCH_URL
from .schemas import (
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityNotObservedReason,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)

KOREA = ZoneInfo("Asia/Seoul")


class SeatClassProjector(Protocol):
    def __call__(
        self,
        seat_class: SeatClass | str,
        status: SeatStatus,
        observed_at: datetime,
        official_url: AnyHttpUrl | str,
        *,
        fare: int | None = None,
    ) -> SeatClassAvailability: ...


class OverlayItemProjector(Protocol):
    def __call__(
        self,
        item: TimetableItem,
        snapshot: BrowserTrainSnapshot,
        observed_at: datetime,
        *,
        official_search_url: str | None,
        seat_class_projector: SeatClassProjector,
    ) -> TimetableItem: ...


class NotObservedMarker(Protocol):
    def __call__(
        self,
        items: list[TimetableItem],
        reason: SeatAvailabilityNotObservedReason,
    ) -> list[TimetableItem]: ...


def normalize_train_number(value: object) -> str:
    """Preserve the browser source's permissive train-identity normalization."""

    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.lstrip("0") or "0"


def _seat_class(
    seat_class: SeatClass | str,
    status: SeatStatus,
    observed_at: datetime,
    official_url: AnyHttpUrl | str,
    *,
    fare: int | None = None,
) -> SeatClassAvailability:
    actions: list[SeatAvailabilityAction] = []
    if status in {"available", "limited", "standing_plus_seat", "standing_only"}:
        normalized_url = AnyHttpUrl(str(official_url))
        actions.extend(
            [
                SeatAvailabilityAction(kind="official_check", url=normalized_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        )
    elif status == "waitlist_available":
        normalized_url = AnyHttpUrl(str(official_url))
        actions.extend(
            [
                SeatAvailabilityAction(kind="official_waitlist", url=normalized_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        )
    elif status == "sold_out":
        actions.append(SeatAvailabilityAction(kind="add_to_watch"))
    return SeatClassAvailability(
        seat_class=SeatClass(seat_class),
        status=status,
        provenance=SeatAvailabilityProvenance(
            kind="official_provider",
            source=SOURCE_NAME,
            observed_at=observed_at.astimezone(UTC),
        ),
        fare=fare,
        actions=actions,
    )


def overlay_item(
    item: TimetableItem,
    snapshot: BrowserTrainSnapshot,
    observed_at: datetime,
    *,
    official_search_url: str | None = None,
    seat_class_projector: SeatClassProjector = _seat_class,
) -> TimetableItem:
    official_url = item.official_booking_url
    seats = [
        seat_class_projector(
            SeatClass.STANDARD,
            snapshot.standard,
            observed_at,
            official_url,
        ),
        seat_class_projector(
            SeatClass.FIRST,
            snapshot.first,
            observed_at,
            official_url,
        ),
    ]
    return item.model_copy(
        update={"seat_classes": seats, "official_search_url": official_search_url}
    )


def mark_not_observed(
    items: list[TimetableItem],
    reason: SeatAvailabilityNotObservedReason,
) -> list[TimetableItem]:
    marked: list[TimetableItem] = []
    for item in items:
        seats: list[SeatClassAvailability] = []
        for seat in item.seat_classes:
            if seat.status == "unknown" and seat.provenance.kind == "not_observed":
                seats.append(
                    seat.model_copy(
                        update={
                            "provenance": SeatAvailabilityProvenance(
                                kind="not_observed", reason=reason
                            ),
                            "actions": [],
                        }
                    )
                )
            else:
                seats.append(seat)
        marked.append(item.model_copy(update={"seat_classes": seats}))
    return marked


def project_overlay_items(
    items: list[TimetableItem],
    result: BrowserSeatSearchResult,
    *,
    train_number_normalizer: Callable[[object], str] = normalize_train_number,
    seat_class_projector: SeatClassProjector = _seat_class,
    item_projector: OverlayItemProjector = overlay_item,
    not_observed_marker: NotObservedMarker = mark_not_observed,
    timezone: ZoneInfo = KOREA,
) -> list[TimetableItem]:
    """Overlay exact browser snapshots without provider I/O or fallback widening."""
    by_identity = {
        (
            train_number_normalizer(snapshot.train_number),
            snapshot.departure_at.astimezone(timezone).strftime("%Y%m%d%H%M%S"),
        ): snapshot
        for snapshot in result.trains
    }
    overlaid: list[TimetableItem] = []
    for item in items:
        local_departure = item.departure_at.astimezone(timezone)
        snapshot = by_identity.get(
            (
                train_number_normalizer(item.train_number),
                local_departure.strftime("%Y%m%d%H%M%S"),
            )
        )
        if snapshot is None:
            overlaid.extend(not_observed_marker([item], "no_exact_match"))
            continue
        overlaid.append(
            item_projector(
                item,
                snapshot,
                result.observed_at,
                official_search_url=result.official_search_url,
                seat_class_projector=seat_class_projector,
            )
        )
    return overlaid


def project_primary_timetable(
    result: BrowserSeatSearchResult,
    *,
    departure_from: datetime,
    departure_to: datetime,
    train_number_normalizer: Callable[[object], str] = normalize_train_number,
    seat_class_projector: SeatClassProjector = _seat_class,
) -> list[TimetableItem]:
    """Map one validated official-browser result without performing provider I/O."""

    official_url = AnyHttpUrl(OFFICIAL_KORAIL_SEARCH_URL)
    official_search_url = (
        None if result.official_search_url is None else AnyHttpUrl(result.official_search_url)
    )
    items: list[TimetableItem] = []
    for snapshot in result.trains:
        local_departure = snapshot.departure_at.astimezone(KOREA)
        if local_departure < departure_from or local_departure > departure_to:
            continue
        seats = [
            seat_class_projector(
                SeatClass.STANDARD,
                snapshot.standard,
                result.observed_at,
                official_url,
                fare=snapshot.adult_fare,
            ),
            seat_class_projector(
                SeatClass.FIRST,
                snapshot.first,
                result.observed_at,
                official_url,
            ),
        ]
        items.append(
            TimetableItem(
                provider=Provider.KORAIL,
                train_number=train_number_normalizer(snapshot.train_number),
                train_type=snapshot.train_type,
                origin=result.origin,
                destination=result.destination,
                departure_at=snapshot.departure_at,
                arrival_at=snapshot.arrival_at,
                adult_fare=snapshot.adult_fare,
                timetable_source="official_provider",
                timetable_retrieved_at=result.observed_at,
                availability=SeatAvailability(
                    status=snapshot.standard,
                    source=SOURCE_NAME,
                    observed_at=result.observed_at,
                ),
                seat_classes=seats,
                official_booking_url=official_url,
                official_search_url=official_search_url,
            )
        )
    return items
