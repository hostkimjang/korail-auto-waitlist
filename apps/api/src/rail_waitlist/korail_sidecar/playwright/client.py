from __future__ import annotations

import ipaddress
import logging
import re
import time
from datetime import date
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from ...provider_registry.korail_search_url_policy import (
    validate_korail_general_search_url as validate_korail_general_search_url,
)
from ..browser_contracts import BrowserAdapterError as BrowserAdapterError
from ..browser_contracts import (
    BrowserProtectionDetected as BrowserProtectionDetected,
)
from ..browser_contracts import BrowserRateLimited as BrowserRateLimited
from ..browser_contracts import (
    BrowserSeatSearchRequest as BrowserSeatSearchRequest,
)
from ..browser_contracts import (
    BrowserSeatSearchResult as BrowserSeatSearchResult,
)
from ..browser_contracts import (
    BrowserSourceUnavailable as BrowserSourceUnavailable,
)
from ..browser_contracts import SeatStatus as SeatStatus
from ..browser_page_contracts import FULLSTACK_E2E_PAGE_URL, OFFICIAL_KORAIL_SEARCH_URL
from ..browser_protection import (
    GENERIC_PROTECTION_TRIGGERS as GENERIC_PROTECTION_TRIGGERS,
)
from ..browser_protection import is_rate_limit_response as is_rate_limit_response
from ..browser_protection import (
    protection_trigger_from_http_response as protection_trigger_from_http_response,
)
from ..browser_protection import (
    protection_trigger_from_text as protection_trigger_from_text,
)
from ..browser_service_availability import (
    BrowserProviderUnavailable,
    provider_unavailable_trigger_from_page,
)
from ..direct_cdp import DirectCdpLaunchError, open_direct_cdp_browser
from . import search_form as _search_form
from .result_reader import ROUTE_HEADING as ROUTE_HEADING
from .result_reader import _normalize_station as _normalize_station
from .result_reader import _normalize_train_number as _normalize_train_number
from .result_reader import read_result as _read_result_impl
from .result_reader import read_seat_status as _read_seat_status_impl
from .result_reader import seat_boxes as _seat_boxes_impl

if TYPE_CHECKING:
    from playwright.async_api import CDPSession, Locator, Page

logger = logging.getLogger("rail_waitlist.korail_browser_automation")

PROTECTION_SURFACE_SELECTOR = (
    '[role="alert"]:visible, dialog[open]:visible, [aria-modal="true"]:visible, '
    ".alert:visible, .error:visible, .popup:visible, .modal:visible"
)


async def probe_chromium() -> None:
    """Verify the production direct-launch/CDP-attach path without opening a network page."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise BrowserSourceUnavailable() from error

    try:
        async with async_playwright() as playwright:
            async with open_direct_cdp_browser(
                playwright.chromium,
                timeout_ms=10_000,
            ):
                pass
    except Exception as error:
        raise BrowserSourceUnavailable() from error


class PlaywrightKorailBrowserClient:
    """Attaches Playwright to a fresh direct-launched Chromium over loopback CDP."""

    def __init__(
        self,
        *,
        page_url: str = OFFICIAL_KORAIL_SEARCH_URL,
        timeout_seconds: float = 25,
        allow_test_loopback: bool = False,
        allow_fullstack_fixture: bool = False,
    ) -> None:
        self.page_url = page_url
        self.timeout_ms = int(timeout_seconds * 1000)
        self._validate_page_url(allow_test_loopback, allow_fullstack_fixture)

    def _validate_page_url(
        self,
        allow_test_loopback: bool,
        allow_fullstack_fixture: bool,
    ) -> None:
        parsed = urlsplit(self.page_url)
        official = (
            parsed.scheme == "https"
            and parsed.hostname == "www.korail.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path == "/ticket/search/general"
            and not parsed.query
            and not parsed.fragment
        )
        if official:
            return
        if allow_fullstack_fixture and self.page_url == FULLSTACK_E2E_PAGE_URL:
            return
        if allow_test_loopback and parsed.scheme == "http":
            try:
                if ipaddress.ip_address(parsed.hostname or "").is_loopback:
                    return
            except ValueError:
                if parsed.hostname == "localhost":
                    return
        raise ValueError("browser adapter page URL must be the official KORAIL HTTPS host")

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        stage = "browser_import"
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise BrowserSourceUnavailable(stage) from error

        try:
            stage = "browser_launch"
            async with async_playwright() as playwright:
                async with open_direct_cdp_browser(
                    playwright.chromium,
                    timeout_ms=self.timeout_ms,
                ) as browser:
                    # The process owns a fresh temporary profile. A fixed desktop
                    # viewport keeps KORAIL's date/time sliders in their supported PC
                    # layout; no User-Agent, header, locale, timezone, proxy, or
                    # fingerprint overrides are applied.
                    if len(browser.contexts) != 1:
                        raise BrowserSourceUnavailable("browser_context")
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.set_viewport_size({"width": 1440, "height": 1000})
                    blocked_responses: list[tuple[int, str]] = []
                    page.on(
                        "response",
                        lambda item: (
                            blocked_responses.append((item.status, item.request.resource_type))
                            if item.status in {403, 429}
                            else None
                        ),
                    )
                    stage = "load_page"
                    try:
                        response = await page.goto(
                            self.page_url,
                            wait_until="domcontentloaded",
                            timeout=self.timeout_ms,
                        )
                    except PlaywrightTimeoutError:
                        # A service-outage DOM can be complete even when a peripheral
                        # resource prevents the navigation signal from settling.
                        await self._assert_not_protected(page, blocked_responses, stage)
                        raise
                    if response is not None and response.status == 429:
                        raise BrowserRateLimited()
                    if response is not None and response.status == 403:
                        raise BrowserProtectionDetected("http_403_main", stage)
                    stage = "initial_protection_check"
                    await self._assert_not_protected(page, blocked_responses, stage)
                    stage = "submit_search"
                    await self._submit_search(page, request)
                    stage = "wait_result"
                    await self._wait_for_result(page, blocked_responses)
                    stage = "result_protection_check"
                    await self._assert_not_protected(page, blocked_responses, stage)
                    stage = "read_result"
                    return await self._read_result(page, request)
        except BrowserSourceUnavailable as error:
            if error.stage == "unspecified":
                raise BrowserSourceUnavailable(stage) from error
            raise
        except (BrowserAdapterError, BrowserProtectionDetected, BrowserRateLimited):
            raise
        except PlaywrightTimeoutError as error:
            raise BrowserSourceUnavailable(stage) from error
        except DirectCdpLaunchError as error:
            raise BrowserSourceUnavailable(stage) from error
        except Exception as error:
            raise BrowserSourceUnavailable(stage) from error

    async def _submit_search(
        self,
        page: Page,
        request: BrowserSeatSearchRequest,
    ) -> None:
        await _search_form.submit_search(self, page, request)

    async def _click_visible_control(
        self,
        page: Page,
        control: Locator,
        stage: str,
    ) -> None:
        """Send one physical CDP mouse press/hold/release to a verified visible control."""
        await _search_form.click_visible_control(self, page, control, stage)

    @staticmethod
    async def _release_and_detach_mouse(
        session: CDPSession,
        *,
        pressed: bool,
        x: float,
        y: float,
    ) -> None:
        await _search_form.release_and_detach_mouse(
            session,
            pressed=pressed,
            x=x,
            y=y,
        )

    async def _choose_station(self, page: Page, label_text: str, value: str) -> None:
        await _search_form.choose_station(self, page, label_text, value)

    async def _wait_for_unique_station_result(self, stations: Locator) -> Locator:
        """Wait for one stable async station result without accepting ambiguous matches."""
        return await _search_form.wait_for_unique_station_result(self, stations)

    async def _station_trigger(self, page: Page, label_text: str) -> Locator:
        return await _search_form.station_trigger(self, page, label_text)

    async def _station_value(
        self,
        page: Page,
        label_text: str,
        selector: str,
    ) -> str:
        return await _search_form.station_value(self, page, label_text, selector)

    async def _departure_input(self, page: Page) -> Locator:
        return await _search_form.departure_input(page)

    async def _passenger_value(self, page: Page) -> str:
        return await _search_form.passenger_value(page)

    async def _assert_pre_submit_identity(
        self,
        page: Page,
        request: BrowserSeatSearchRequest,
    ) -> None:
        await _search_form.assert_pre_submit_identity(self, page, request)

    async def _choose_departure(
        self,
        page: Page,
        travel_date: date,
        hour: int,
    ) -> None:
        await _search_form.choose_departure(self, page, travel_date, hour)

    async def _wait_for_unique_departure_dialog(self, page: Page) -> Locator:
        """Wait for one stable async date dialog without accepting ambiguous matches."""
        return await _search_form.wait_for_unique_departure_dialog(self, page)

    async def _move_calendar_to_month(
        self,
        dialog: Locator,
        current_date: date,
        travel_date: date,
    ) -> None:
        await _search_form.move_calendar_to_month(self, dialog, current_date, travel_date)

    async def _move_time_to_hour(
        self,
        time_slider: Locator,
        hour_link: Locator,
        target_hour: int,
    ) -> None:
        await _search_form.move_time_to_hour(self, time_slider, hour_link, target_hour)

    async def _active_time_hours(self, time_slider: Locator) -> list[int]:
        return await _search_form.active_time_hours(time_slider)

    async def _find_time_control(
        self,
        time_slider: Locator,
        direction: Literal["next", "prev"],
    ) -> Locator:
        return await _search_form.find_time_control(time_slider, direction)

    async def _find_date_link(self, dialog: Locator, travel_date: date) -> Locator:
        return await _search_form.find_date_link(self, dialog, travel_date)

    async def _wait_for_result(
        self,
        page: Page,
        blocked_responses: list[tuple[int, str]],
    ) -> None:
        deadline = time.monotonic() + (self.timeout_ms / 1000)
        while time.monotonic() < deadline:
            await self._assert_not_protected(page, blocked_responses, "wait_result")
            if await page.locator("li.tckList:visible").count() > 0:
                return
            body_text = await page.locator("body").inner_text()
            if re.search(r"조회\s*결과(?:가)?\s*(?:없|0건)", body_text):
                raise BrowserSourceUnavailable()
            await page.wait_for_timeout(100)
        raise BrowserSourceUnavailable()

    async def _assert_not_protected(
        self,
        page: Page,
        blocked_responses: list[tuple[int, str]],
        stage: str,
    ) -> None:
        if any(
            is_rate_limit_response(status, resource_type)
            for status, resource_type in blocked_responses
        ):
            raise BrowserRateLimited()
        for status, resource_type in blocked_responses:
            trigger = protection_trigger_from_http_response(status, resource_type)
            # A document 403 means the business page itself was denied.  Fonts,
            # analytics and other subresources can fail independently; they are not
            # evidence of a protected result when the visible result DOM is valid.
            if trigger == "http_403_main":
                raise BrowserProtectionDetected(trigger, stage)
        body_text = await page.locator("body").inner_text()
        result_rows_present = await page.locator("li.tckList:visible").count() > 0
        unavailable_trigger = provider_unavailable_trigger_from_page(
            page.url,
            body_text,
            has_result_rows=result_rows_present,
        )
        if unavailable_trigger is not None:
            raise BrowserProviderUnavailable(unavailable_trigger, stage)
        trigger = protection_trigger_from_text(body_text)
        if trigger is None:
            return
        if trigger not in GENERIC_PROTECTION_TRIGGERS:
            raise BrowserProtectionDetected(trigger, stage)

        surfaces = page.locator(PROTECTION_SURFACE_SELECTOR)
        for index in range(await surfaces.count()):
            surface_trigger = protection_trigger_from_text(await surfaces.nth(index).inner_text())
            if surface_trigger in GENERIC_PROTECTION_TRIGGERS:
                raise BrowserProtectionDetected(surface_trigger, stage)
        if not result_rows_present:
            raise BrowserProtectionDetected(trigger, stage)

    async def _read_result(
        self,
        page: Page,
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        return await _read_result_impl(self, page, request)

    async def _seat_boxes(self, row: Locator) -> tuple[Locator, Locator]:
        return await _seat_boxes_impl(row)

    async def _read_seat_status(self, box: Locator) -> SeatStatus:
        return await _read_seat_status_impl(box)
