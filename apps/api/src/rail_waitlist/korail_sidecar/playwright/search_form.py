from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date
from typing import TYPE_CHECKING, Literal, Protocol

from ..browser_contracts import BrowserSeatSearchRequest, BrowserSourceUnavailable
from ..search_result_policy import visible_departure_matches
from .result_reader import _normalize_station

if TYPE_CHECKING:
    from playwright.async_api import CDPSession, Locator, Page

logger = logging.getLogger("rail_waitlist.korail_browser_automation")


class _SearchFormHost(Protocol):
    timeout_ms: int

    async def _choose_station(self, page: Page, label_text: str, value: str) -> None: ...

    async def _choose_departure(self, page: Page, travel_date: date, hour: int) -> None: ...

    async def _assert_pre_submit_identity(
        self,
        page: Page,
        request: BrowserSeatSearchRequest,
    ) -> None: ...

    async def _click_visible_control(
        self,
        page: Page,
        control: Locator,
        stage: str,
    ) -> None: ...

    @staticmethod
    async def _release_and_detach_mouse(
        session: CDPSession,
        *,
        pressed: bool,
        x: float,
        y: float,
    ) -> None: ...

    async def _station_trigger(self, page: Page, label_text: str) -> Locator: ...

    async def _wait_for_unique_station_result(self, stations: Locator) -> Locator: ...

    async def _station_value(
        self,
        page: Page,
        label_text: str,
        selector: str,
    ) -> str: ...

    async def _departure_input(self, page: Page) -> Locator: ...

    async def _passenger_value(self, page: Page) -> str: ...

    async def _wait_for_unique_departure_dialog(self, page: Page) -> Locator: ...

    async def _move_calendar_to_month(
        self,
        dialog: Locator,
        current_date: date,
        travel_date: date,
    ) -> None: ...

    async def _move_time_to_hour(
        self,
        time_slider: Locator,
        hour_link: Locator,
        target_hour: int,
    ) -> None: ...

    async def _active_time_hours(self, time_slider: Locator) -> list[int]: ...

    async def _find_time_control(
        self,
        time_slider: Locator,
        direction: Literal["next", "prev"],
    ) -> Locator: ...

    async def _find_date_link(self, dialog: Locator, travel_date: date) -> Locator: ...


async def submit_search(
    host: _SearchFormHost,
    page: Page,
    request: BrowserSeatSearchRequest,
) -> None:
    try:
        await host._choose_station(page, "출발역", request.origin)
    except BrowserSourceUnavailable as error:
        if error.stage == "unspecified":
            raise BrowserSourceUnavailable("choose_origin") from error
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("choose_origin") from error
    try:
        await host._choose_station(page, "도착역", request.destination)
    except BrowserSourceUnavailable as error:
        if error.stage == "unspecified":
            raise BrowserSourceUnavailable("choose_destination") from error
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("choose_destination") from error
    try:
        await host._choose_departure(page, request.travel_date, request.departure_from.hour)
    except BrowserSourceUnavailable as error:
        if error.stage == "unspecified":
            raise BrowserSourceUnavailable("choose_departure") from error
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("choose_departure") from error
    await host._assert_pre_submit_identity(page, request)
    buttons = page.get_by_role("button", name=re.compile(r"(?:열차\s*)?조회|조회하기"))
    if await buttons.count() != 1:
        raise BrowserSourceUnavailable("submit_button")
    button = buttons.first
    if not await button.is_visible() or not await button.is_enabled():
        raise BrowserSourceUnavailable("submit_button")
    await host._click_visible_control(page, button, "submit_button_click")


async def click_visible_control(
    host: _SearchFormHost,
    page: Page,
    control: Locator,
    stage: str,
) -> None:
    """Send one physical CDP mouse press/hold/release to a verified visible control."""
    try:
        if not await control.is_visible() or not await control.is_enabled():
            raise BrowserSourceUnavailable(stage)
        await control.scroll_into_view_if_needed()
        box = await control.bounding_box()
        if box is None or box["width"] <= 0 or box["height"] <= 0:
            raise BrowserSourceUnavailable(stage)
        x = box["x"] + (box["width"] / 2)
        y = box["y"] + (box["height"] / 2)
        session = await page.context.new_cdp_session(page)
        pressed = False
        body_error: BaseException | None = None
        try:
            await session.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": y, "buttons": 0},
            )
            await session.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 1,
                    "clickCount": 1,
                },
            )
            pressed = True
            await asyncio.sleep(0.1)
        except BaseException as error:
            body_error = error
            raise
        finally:
            cleanup_cancelled: asyncio.CancelledError | None = None
            cleanup_error: BaseException | None = None
            cleanup_task = asyncio.create_task(
                host._release_and_detach_mouse(
                    session,
                    pressed=pressed,
                    x=x,
                    y=y,
                )
            )
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    # Repeated cancellation is delivered after the release and
                    # detach sequence finishes, never to the owned cleanup task.
                    if cleanup_cancelled is None:
                        cleanup_cancelled = error
            try:
                cleanup_task.result()
            except BaseException as error:
                cleanup_error = error
            if body_error is None:
                if cleanup_cancelled is not None:
                    raise cleanup_cancelled
                if cleanup_error is not None:
                    raise cleanup_error
    except BrowserSourceUnavailable:
        raise
    except Exception as error:
        raise BrowserSourceUnavailable(stage) from error


async def release_and_detach_mouse(
    session: CDPSession,
    *,
    pressed: bool,
    x: float,
    y: float,
) -> None:
    try:
        if pressed:
            await asyncio.wait_for(
                session.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "buttons": 0,
                        "clickCount": 1,
                    },
                ),
                timeout=2,
            )
    finally:
        await asyncio.wait_for(session.detach(), timeout=1)


async def choose_station(
    host: _SearchFormHost,
    page: Page,
    label_text: str,
    value: str,
) -> None:
    trigger = await host._station_trigger(page, label_text)
    if not await trigger.is_visible() or not await trigger.is_enabled():
        raise BrowserSourceUnavailable("station_trigger")
    await trigger.click()
    dialogs = page.get_by_role("dialog").filter(has_text="기차역 조회")
    if await dialogs.count() != 1:
        raise BrowserSourceUnavailable("station_dialog")
    dialog = dialogs.first
    await dialog.wait_for(state="visible", timeout=host.timeout_ms)
    searches = dialog.get_by_role("textbox", name="역명을 입력해주세요", exact=True)
    if await searches.count() == 0:
        searches = dialog.get_by_placeholder(re.compile(r"역\s*이름\s*또는\s*초성\s*검색"))
    if await searches.count() != 1:
        raise BrowserSourceUnavailable("station_search_input")
    search = searches.first
    try:
        await search.wait_for(state="visible", timeout=host.timeout_ms)
    except Exception as error:
        raise BrowserSourceUnavailable("station_search_input") from error
    if not await search.is_enabled():
        raise BrowserSourceUnavailable("station_search_input")
    await search.fill(value)
    result_containers = dialog.locator(".sch_form .sch_wrap")
    if await result_containers.count() != 1:
        raise BrowserSourceUnavailable("station_result")
    stations = result_containers.first.get_by_role("link", name=value, exact=True)
    station = await host._wait_for_unique_station_result(stations)
    await station.click()
    await dialog.wait_for(state="hidden", timeout=host.timeout_ms)


async def wait_for_unique_station_result(
    host: _SearchFormHost,
    stations: Locator,
) -> Locator:
    """Wait for one stable async station result without accepting ambiguous matches."""
    deadline = time.monotonic() + (host.timeout_ms / 1000)
    stable_since: float | None = None
    while True:
        now = time.monotonic()
        count = await stations.count()
        if count > 1:
            raise BrowserSourceUnavailable("station_result")
        if count == 1:
            station = stations.first
            if await station.is_visible() and await station.is_enabled():
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= 0.1:
                    if await stations.count() == 1:
                        return station
            else:
                stable_since = None
        else:
            stable_since = None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BrowserSourceUnavailable("station_result")
        await asyncio.sleep(min(0.05, remaining))


async def station_trigger(
    host: _SearchFormHost,
    page: Page,
    label_text: str,
) -> Locator:
    direct = page.get_by_role("link", name=f"{label_text} 선택", exact=True)
    direct_count = await direct.count()
    if direct_count == 1:
        return direct.first
    if direct_count > 1:
        raise BrowserSourceUnavailable("station_trigger")

    labels = page.get_by_text(label_text, exact=True)
    if await labels.count() != 1:
        raise BrowserSourceUnavailable("station_trigger")
    label = labels.first
    try:
        await label.wait_for(state="visible", timeout=host.timeout_ms)
    except Exception as error:
        raise BrowserSourceUnavailable("station_trigger") from error
    container = label
    for _ in range(4):
        container = container.locator("..")
        links = container.get_by_role("link")
        buttons = container.get_by_role("button")
        link_count = await links.count()
        button_count = await buttons.count()
        if link_count + button_count == 1:
            return links.first if link_count == 1 else buttons.first
        if link_count + button_count > 1:
            raise BrowserSourceUnavailable("station_trigger")
    raise BrowserSourceUnavailable("station_trigger")


async def station_value(
    host: _SearchFormHost,
    page: Page,
    label_text: str,
    selector: str,
) -> str:
    displays = page.locator(selector)
    count = await displays.count()
    if count == 1:
        display = displays.first
        if not await display.is_visible():
            raise BrowserSourceUnavailable("station_current_value")
        return _normalize_station(await display.input_value())
    if count > 1:
        raise BrowserSourceUnavailable("station_current_value")
    trigger = await host._station_trigger(page, label_text)
    if not await trigger.is_visible():
        raise BrowserSourceUnavailable("station_current_value")
    return _normalize_station(await trigger.inner_text())


async def departure_input(page: Page) -> Locator:
    inputs = page.get_by_role("textbox", name="출발일", exact=True)
    count = await inputs.count()
    if count == 1:
        return inputs.first
    if count > 1:
        raise BrowserSourceUnavailable("departure_current_date")
    inputs = page.locator("#startDate, #labelday")
    if await inputs.count() != 1:
        raise BrowserSourceUnavailable("departure_current_date")
    return inputs.first


async def passenger_value(page: Page) -> str:
    inputs = page.get_by_role("textbox", name="인원 선택", exact=True)
    count = await inputs.count()
    if count == 1:
        input_control = inputs.first
        if not await input_control.is_visible():
            raise BrowserSourceUnavailable("passenger_current_count")
        return " ".join((await input_control.input_value()).split())
    if count > 1:
        raise BrowserSourceUnavailable("passenger_current_count")

    # Older official-page variants expose the selected passenger count through
    # a disabled display input without an accessible name.
    inputs = page.locator("input#passenger, input#labelple")
    count = await inputs.count()
    if count == 1:
        input_control = inputs.first
        if not await input_control.is_visible():
            raise BrowserSourceUnavailable("passenger_current_count")
        return " ".join((await input_control.input_value()).split())
    if count > 1:
        raise BrowserSourceUnavailable("passenger_current_count")

    # `/ticket/search/general` renders the current passenger summary as the
    # popup trigger itself rather than an input. Only the one-person contract
    # is supported by this adapter, so use the exact accessible name.
    links = page.get_by_role("link", name="총 1명", exact=True)
    if await links.count() != 1 or not await links.first.is_visible():
        raise BrowserSourceUnavailable("passenger_current_count")
    return " ".join((await links.first.inner_text()).split())


async def assert_pre_submit_identity(
    host: _SearchFormHost,
    page: Page,
    request: BrowserSeatSearchRequest,
) -> None:
    try:
        origin = await host._station_value(
            page,
            "출발역",
            "input[name='txtGoStart']:visible, #labelstart:visible",
        )
        destination = await host._station_value(
            page,
            "도착역",
            "input[name='txtGoEnd']:visible, #labelend:visible",
        )
        date_input = await host._departure_input(page)
        passenger_value = await host._passenger_value(page)
        if not await date_input.is_visible():
            raise BrowserSourceUnavailable("pre_submit_identity_check")
        origin_matches = origin == request.origin
        destination_matches = destination == request.destination
        departure_matches = visible_departure_matches(
            await date_input.input_value(),
            request.travel_date,
            request.departure_from.hour,
        )
        passenger_matches = passenger_value == "총 1명"
        if not all(
            [
                origin_matches,
                destination_matches,
                departure_matches,
                passenger_matches,
            ]
        ):
            # Log only comparison outcomes. Route names and form values stay out
            # of persistent logs while operators can identify the stale control.
            logger.warning(
                "KORAIL pre-submit identity mismatch "
                "origin=%s destination=%s departure=%s passenger=%s",
                origin_matches,
                destination_matches,
                departure_matches,
                passenger_matches,
            )
            raise BrowserSourceUnavailable("pre_submit_identity_check")
    except BrowserSourceUnavailable:
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("pre_submit_identity_check") from error


async def choose_departure(
    host: _SearchFormHost,
    page: Page,
    travel_date: date,
    hour: int,
) -> None:
    try:
        stage = "departure_current_date"
        date_input = await host._departure_input(page)
        await date_input.wait_for(state="visible", timeout=host.timeout_ms)
        visible_date_match = re.match(
            r"^\s*(\d{4}-\d{2}-\d{2})",
            await date_input.input_value(),
        )
        if visible_date_match is None:
            raise BrowserSourceUnavailable(stage)
        current_date = date.fromisoformat(visible_date_match.group(1))

        stage = "departure_trigger_find"
        triggers = page.get_by_role("link", name="출발일 선택", exact=True)
        trigger: Locator
        if await triggers.count() == 1:
            trigger = triggers.first
        elif await triggers.count() > 1:
            raise BrowserSourceUnavailable(stage)
        else:
            labels = page.get_by_text("출발일", exact=True)
            if await labels.count() != 1:
                raise BrowserSourceUnavailable(stage)
            label = labels.first
            container = label
            candidate_trigger: Locator | None = None
            for _ in range(4):
                container = container.locator("..")
                links = container.get_by_role("link")
                if await links.count() == 1:
                    candidate_trigger = links.first
                    break
            if candidate_trigger is None:
                raise BrowserSourceUnavailable(stage)
            trigger = candidate_trigger
        if not await trigger.is_visible() or not await trigger.is_enabled():
            raise BrowserSourceUnavailable(stage)

        stage = "departure_trigger_click"
        await trigger.click(timeout=host.timeout_ms)
        stage = "departure_dialog_open"
        dialog = await host._wait_for_unique_departure_dialog(page)

        stage = "departure_month_navigate"
        await host._move_calendar_to_month(dialog, current_date, travel_date)
        stage = "departure_day_find"
        day = await host._find_date_link(dialog, travel_date)
        stage = "departure_day_click"
        await day.click(timeout=host.timeout_ms)

        stage = "departure_hour_find"
        time_sliders = dialog.locator(".slideWrap")
        if await time_sliders.count() != 1:
            raise BrowserSourceUnavailable(stage)
        time_slider = time_sliders.first
        hour_links = time_slider.locator("a").filter(has_text=re.compile(rf"^\s*{hour:02d}시\s*$"))
        await hour_links.first.wait_for(state="visible", timeout=host.timeout_ms)
        if await hour_links.count() != 1:
            raise BrowserSourceUnavailable(stage)
        hour_link = hour_links.first
        await host._move_time_to_hour(time_slider, hour_link, hour)
        stage = "departure_hour_click"
        await hour_link.click(timeout=host.timeout_ms)

        stage = "departure_apply_find"
        apply_buttons = dialog.get_by_role("button", name="적용", exact=True)
        if await apply_buttons.count() != 1:
            raise BrowserSourceUnavailable(stage)
        apply_button = apply_buttons.first
        if not await apply_button.is_visible() or not await apply_button.is_enabled():
            raise BrowserSourceUnavailable(stage)
        stage = "departure_apply_click"
        await apply_button.click(timeout=host.timeout_ms)
        stage = "departure_dialog_close"
        await dialog.wait_for(state="hidden", timeout=host.timeout_ms)
    except BrowserSourceUnavailable:
        raise
    except Exception as error:
        raise BrowserSourceUnavailable(stage) from error


async def wait_for_unique_departure_dialog(
    host: _SearchFormHost,
    page: Page,
) -> Locator:
    """Wait for one stable async date dialog without accepting ambiguous matches."""
    dialogs = page.get_by_role("dialog").filter(has_text="날짜 선택")
    deadline = time.monotonic() + (host.timeout_ms / 1000)
    stable_since: float | None = None
    while True:
        now = time.monotonic()
        count = await dialogs.count()
        if count > 1:
            raise BrowserSourceUnavailable("departure_dialog_open")
        if count == 1:
            dialog = dialogs.first
            if await dialog.is_visible():
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= 0.1:
                    if await dialogs.count() == 1 and await dialog.is_visible():
                        return dialog
            else:
                stable_since = None
        else:
            stable_since = None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BrowserSourceUnavailable("departure_dialog_open")
        await asyncio.sleep(min(0.05, remaining))


async def move_calendar_to_month(
    host: _SearchFormHost,
    dialog: Locator,
    current_date: date,
    travel_date: date,
) -> None:
    month_delta = (
        (travel_date.year - current_date.year) * 12 + travel_date.month - current_date.month
    )
    if abs(month_delta) > 24:
        raise BrowserSourceUnavailable("departure_month_unavailable")
    if month_delta == 0:
        return

    button_name = "Next" if month_delta > 0 else "Previous"
    buttons = dialog.get_by_role("button", name=button_name, exact=True)
    if await buttons.count() < 1:
        raise BrowserSourceUnavailable("departure_month_navigate")
    calendar_button = buttons.first
    if not await calendar_button.is_visible() or not await calendar_button.is_enabled():
        raise BrowserSourceUnavailable("departure_month_navigate")
    for _ in range(abs(month_delta)):
        await calendar_button.click(timeout=host.timeout_ms)


async def move_time_to_hour(
    host: _SearchFormHost,
    time_slider: Locator,
    hour_link: Locator,
    target_hour: int,
) -> None:
    for _ in range(24):
        if (
            await hour_link.is_visible()
            and await hour_link.is_enabled()
            and await hour_link.get_attribute("aria-disabled") != "true"
        ):
            return
        active_hours = await host._active_time_hours(time_slider)
        if not active_hours:
            raise BrowserSourceUnavailable("departure_hour_unavailable")
        direction: Literal["next", "prev"]
        if target_hour > max(active_hours):
            direction = "next"
        elif target_hour < min(active_hours):
            direction = "prev"
        else:
            raise BrowserSourceUnavailable("departure_hour_unavailable")
        control = await host._find_time_control(time_slider, direction)
        control_visible = await control.is_visible()
        control_enabled = await control.is_enabled()
        if not control_visible or not control_enabled:
            logger.warning(
                "KORAIL departure time slider control inactive "
                "direction=%s visible=%s enabled=%s active_hours=%s",
                direction,
                control_visible,
                control_enabled,
                active_hours,
            )
            raise BrowserSourceUnavailable("departure_hour_unavailable")
        await control.click(timeout=host.timeout_ms)
        await asyncio.sleep(0.5)
    if (
        await hour_link.is_visible()
        and await hour_link.is_enabled()
        and await hour_link.get_attribute("aria-disabled") != "true"
    ):
        return
    raise BrowserSourceUnavailable("departure_hour_unavailable")


async def active_time_hours(time_slider: Locator) -> list[int]:
    active: list[int] = []
    links = time_slider.locator("a")
    for index in range(await links.count()):
        link = links.nth(index)
        label = re.fullmatch(r"\s*(\d{2})시\s*", await link.inner_text())
        if label is None or await link.get_attribute("aria-disabled") == "true":
            continue
        if await link.is_visible() and await link.is_enabled():
            active.append(int(label.group(1)))
    return active


async def find_time_control(
    time_slider: Locator,
    direction: Literal["next", "prev"],
) -> Locator:
    selector = f".slick-{direction}:visible"
    container = time_slider
    for depth in range(5):
        candidates = container.locator(selector)
        active_controls = []
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            classes = set((await candidate.get_attribute("class") or "").split())
            if await candidate.is_enabled() and "slick-disabled" not in classes:
                active_controls.append(candidate)
        if len(active_controls) == 1:
            return active_controls[0]
        if len(active_controls) > 1:
            logger.warning(
                "KORAIL departure time slider control ambiguous direction=%s depth=%s count=%s",
                direction,
                depth,
                len(active_controls),
            )
            raise BrowserSourceUnavailable("departure_hour_navigate")
        container = container.locator("..")
    logger.warning(
        "KORAIL departure time slider control missing direction=%s links=%s",
        direction,
        await time_slider.locator("a").count(),
    )
    raise BrowserSourceUnavailable("departure_hour_navigate")


async def find_date_link(
    host: _SearchFormHost,
    dialog: Locator,
    travel_date: date,
) -> Locator:
    headings = dialog.locator(".datepicker p").filter(
        has_text=re.compile(
            rf"^\s*{travel_date.year}(?:\.\s*{travel_date.month:02d}\.|년\s*"
            rf"{travel_date.month}월)\s*$"
        )
    )
    try:
        await headings.first.wait_for(state="visible", timeout=host.timeout_ms)
    except Exception as error:
        raise BrowserSourceUnavailable("departure_month_find") from error
    if await headings.count() != 1:
        raise BrowserSourceUnavailable("departure_month_find")

    month = headings.first.locator("..")
    day_cells = month.locator("td").filter(
        has_text=re.compile(rf"^\s*{travel_date.day}\s*(?:오늘|출발일)?\s*$")
    )
    if await day_cells.count() != 1:
        raise BrowserSourceUnavailable("departure_day_find")
    day_cell = day_cells.first
    links = day_cell.locator("a")
    if await links.count() != 1:
        raise BrowserSourceUnavailable("departure_day_find")
    link = links.first
    deadline = time.monotonic() + min(host.timeout_ms / 1000, 5)
    while time.monotonic() < deadline:
        cell_classes = set((await day_cell.get_attribute("class") or "").split())
        if (
            "disabled" not in cell_classes
            and await link.get_attribute("aria-disabled") != "true"
            and await link.is_visible()
            and await link.is_enabled()
        ):
            return link
        await asyncio.sleep(0.05)
    raise BrowserSourceUnavailable("departure_day_unavailable")
