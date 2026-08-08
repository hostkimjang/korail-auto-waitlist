from __future__ import annotations

import re
from datetime import UTC, datetime
from datetime import time as clock_time
from typing import TYPE_CHECKING, Protocol

from ..browser_contracts import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
    BrowserTrainSnapshot,
    SeatStatus,
)
from ..search_result_policy import (
    parse_expected_delay_minutes,
    parse_official_train_type,
    parse_unambiguous_adult_fare,
    service_datetimes,
    status_from_seat_box,
    visible_departure_matches,
)

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

ROUTE_HEADING = re.compile(
    r"^(.+?)\s*→\s*(.+?)\s*\(\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*\)"
    r"(?:\s*소요시간\s*:\s*.+)?$"
)


class _ResultReaderHost(Protocol):
    async def _passenger_value(self, page: Page) -> str: ...

    async def _departure_input(self, page: Page) -> Locator: ...

    async def _seat_boxes(self, row: Locator) -> tuple[Locator, Locator]: ...

    async def _read_seat_status(self, box: Locator) -> SeatStatus: ...


async def read_result(
    host: _ResultReaderHost,
    page: Page,
    request: BrowserSeatSearchRequest,
) -> BrowserSeatSearchResult:
    if await host._passenger_value(page) != "총 1명":
        raise BrowserSourceUnavailable()
    date_input = await host._departure_input(page)
    visible_date = await date_input.input_value()
    if not visible_departure_matches(
        visible_date,
        request.travel_date,
        request.departure_from.hour,
    ):
        raise BrowserSourceUnavailable()

    trains: list[BrowserTrainSnapshot] = []
    rows = page.locator("li.tckList:visible")
    for index in range(await rows.count()):
        row = rows.nth(index)
        title_boxes = row.locator(".tck_inner .tit_box:visible")
        if await title_boxes.count() != 1:
            raise BrowserSourceUnavailable()
        title_box = title_boxes.first
        train_type = parse_official_train_type(await title_box.inner_text())
        if train_type is None:
            # The official page mixes KTX with conventional trains. TAGO's KORAIL
            # result set used by this overlay is KTX-only, so unrelated row shapes
            # must not invalidate otherwise exact KTX observations.
            continue
        train_numbers = row.locator(".tck_inner .tit_box .num:visible")
        route_boxes = row.locator(".tck_inner .data_box.right:visible")
        if await train_numbers.count() != 1 or await route_boxes.count() != 1:
            raise BrowserSourceUnavailable()
        train_number = _normalize_train_number(await train_numbers.first.inner_text())
        route_text = " ".join((await route_boxes.first.inner_text()).split())
        route = ROUTE_HEADING.match(route_text)
        if route is None:
            raise BrowserSourceUnavailable()
        origin = _normalize_station(route.group(1))
        destination = _normalize_station(route.group(2))
        if origin != request.origin or destination != request.destination:
            raise BrowserSourceUnavailable()
        departure_time = clock_time.fromisoformat(route.group(3))
        if not request.departure_from <= departure_time <= request.departure_to:
            continue
        arrival_time = clock_time.fromisoformat(route.group(4))
        standard_box, first_box = await host._seat_boxes(row)
        standard = await host._read_seat_status(standard_box)
        first = await host._read_seat_status(first_box)
        # KORAIL renders domestic service times in KST; keep that explicit on the wire.
        departure_at, arrival_at = service_datetimes(
            request.travel_date,
            departure_time,
            arrival_time,
        )
        trains.append(
            BrowserTrainSnapshot(
                train_number=train_number,
                train_type=train_type,
                departure_at=departure_at,
                arrival_at=arrival_at,
                adult_fare=parse_unambiguous_adult_fare(await standard_box.inner_text()),
                standard=standard,
                first=first,
                expected_delay_minutes=parse_expected_delay_minutes(await row.inner_text()),
            )
        )
    if not trains:
        raise BrowserSourceUnavailable()
    return BrowserSeatSearchResult(
        origin=request.origin,
        destination=request.destination,
        travel_date=request.travel_date,
        passenger_count=1,
        observed_at=datetime.now(UTC),
        trains=trains,
    )


async def seat_boxes(row: Locator) -> tuple[Locator, Locator]:
    boxes = row.locator(".tck_inner .price_box:visible")
    if await boxes.count() != 2:
        raise BrowserSourceUnavailable()
    standard = row.locator(".tck_inner .price_box.gen:visible").first
    first = row.locator(".tck_inner .price_box.spe:visible").first
    if await standard.count() == 1 and await first.count() == 1:
        return standard, first
    texts = [" ".join((await boxes.nth(index).inner_text()).split()) for index in range(2)]
    standard_index = next(
        (index for index, text in enumerate(texts) if "일반실" in text),
        None,
    )
    first_index = next(
        (index for index, text in enumerate(texts) if "특실" in text),
        None,
    )
    if standard_index is None or first_index is None or standard_index == first_index:
        # The current official result DOM does not label these columns with gen/spe
        # classes. Its fixed result-table order is 일반실 then 특실.
        return boxes.nth(0), boxes.nth(1)
    return boxes.nth(standard_index), boxes.nth(first_index)


async def read_seat_status(box: Locator) -> SeatStatus:
    text = " ".join((await box.inner_text()).split())
    classes = set((await box.get_attribute("class") or "").casefold().split())
    nested = box.locator(".sold_out, .sold_out_soon")
    for index in range(await nested.count()):
        classes.update((await nested.nth(index).get_attribute("class") or "").split())
    status = status_from_seat_box(text, classes)
    if status is None:
        raise BrowserSourceUnavailable()
    return status


def _normalize_station(value: str) -> str:
    return " ".join(value.split()).removesuffix("역")


def _normalize_train_number(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z-]", "", " ".join(value.split()))
    if not normalized or len(normalized) > 40:
        raise BrowserSourceUnavailable()
    digits = "".join(character for character in normalized if character.isdigit())
    return digits.lstrip("0") or "0"
