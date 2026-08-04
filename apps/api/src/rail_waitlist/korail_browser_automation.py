from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Literal, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .korail_direct_cdp import DirectCdpLaunchError, open_direct_cdp_browser
from .korail_search_bootstrap import validate_korail_general_search_url

logger = logging.getLogger(__name__)

SeatStatus = Literal[
    "available",
    "limited",
    "standing_plus_seat",
    "sold_out",
    "waitlist_available",
    "not_offered",
]
KorailTrainType = Literal["KTX", "KTX-산천", "KTX-청룡"]
AdapterErrorReason = Literal[
    "provider_access_restricted",
    "rate_limited",
    "source_unavailable",
    "passenger_count_not_supported",
]
ProtectionTrigger = Literal[
    "http_403_main",
    "http_403_subresource",
    "marker_code_8002",
    "marker_code_8003",
    "marker_code_1405",
    "marker_macro_err1",
    "marker_captcha",
    "marker_netfunnel",
    "marker_abnormal_access",
    "marker_unauthorized_tool",
]

SOURCE_NAME = "korail-official-page-browser"
OFFICIAL_KORAIL_SEARCH_URL = "https://www.korail.com/ticket/search/general"
FULLSTACK_E2E_PAGE_URL = (
    "http://e2e-korail-page:8080/korail_browser_page.html"
)
PROTECTION_MARKERS: tuple[tuple[ProtectionTrigger, re.Pattern[str]], ...] = (
    ("marker_code_8002", re.compile(r"code\s*:?\s*-?\s*8002", re.IGNORECASE)),
    ("marker_code_8003", re.compile(r"code\s*:?\s*-?\s*8003", re.IGNORECASE)),
    ("marker_code_1405", re.compile(r"code\s*:?\s*-?\s*1405", re.IGNORECASE)),
    ("marker_macro_err1", re.compile(r"macro_err1", re.IGNORECASE)),
    ("marker_captcha", re.compile(r"captcha", re.IGNORECASE)),
    ("marker_netfunnel", re.compile(r"netfunnel", re.IGNORECASE)),
    (
        "marker_abnormal_access",
        re.compile(r"비정상\s*접근", re.IGNORECASE),
    ),
    ("marker_unauthorized_tool", re.compile(r"미허가\s*도구", re.IGNORECASE)),
)
GENERIC_PROTECTION_TRIGGERS = frozenset(
    {"marker_abnormal_access", "marker_unauthorized_tool"}
)
PROTECTION_SURFACE_SELECTOR = (
    '[role="alert"]:visible, dialog[open]:visible, [aria-modal="true"]:visible, '
    ".alert:visible, .error:visible, .popup:visible, .modal:visible"
)
RATE_LIMIT_RESOURCE_TYPES = frozenset({"document", "fetch", "xhr"})
ROUTE_HEADING = re.compile(
    r"^(.+?)\s*→\s*(.+?)\s*\(\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*\)"
    r"(?:\s*소요시간\s*:\s*.+)?$"
)
DELAY_ESTIMATE_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*분\s*지연\s*예상")
ADULT_FARE_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*)\s*원(?!\w)")
OFFICIAL_TRAIN_TYPE_PATTERN = re.compile(
    r"^(KTX(?:\s*[-–—]?\s*(?:산천|청룡))?)(?:\s+(?:[A-Za-z]\s*)?0*\d+)?$",
    re.IGNORECASE,
)
KST = ZoneInfo("Asia/Seoul")


def parse_expected_delay_minutes(value: str) -> int | None:
    values = {int(item) for item in DELAY_ESTIMATE_PATTERN.findall(value)}
    if len(values) != 1:
        return None
    delay = values.pop()
    return delay if delay > 0 else None


def parse_unambiguous_adult_fare(value: str) -> int | None:
    matches = ADULT_FARE_PATTERN.findall(" ".join(value.split()))
    if len(matches) != 1:
        return None
    fare = int(matches[0].replace(",", ""))
    return fare if fare > 0 else None


def parse_official_train_type(value: str) -> KorailTrainType | None:
    match = OFFICIAL_TRAIN_TYPE_PATTERN.fullmatch(" ".join(value.split()))
    if match is None:
        return None
    train_type = match.group(1).replace("–", "-").replace("—", "-")
    if "산천" in train_type:
        return "KTX-산천"
    if "청룡" in train_type:
        return "KTX-청룡"
    return "KTX"


def service_datetimes(
    travel_date: date,
    departure_time: clock_time,
    arrival_time: clock_time,
) -> tuple[datetime, datetime]:
    departure_at = datetime.combine(travel_date, departure_time, tzinfo=KST)
    arrival_at = datetime.combine(travel_date, arrival_time, tzinfo=KST)
    if arrival_at <= departure_at:
        arrival_at += timedelta(days=1)
    return departure_at, arrival_at


class AdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserSeatSearchRequest(AdapterModel):
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    travel_date: date
    departure_from: clock_time
    departure_to: clock_time
    passenger_count: int = Field(default=1, ge=1, le=9)

    @field_validator("origin", "destination")
    @classmethod
    def normalize_station(cls, value: str) -> str:
        normalized = " ".join(value.split()).removesuffix("역")
        if not normalized:
            raise ValueError("station cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_route_and_window(self) -> BrowserSeatSearchRequest:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.departure_from >= self.departure_to:
            raise ValueError("departure_from must be earlier than departure_to")
        return self

    def cache_key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.origin,
            self.destination,
            self.travel_date.isoformat(),
            self.departure_from.isoformat(),
            self.departure_to.isoformat(),
            self.passenger_count,
        )


class BrowserTrainSnapshot(AdapterModel):
    train_number: str = Field(min_length=1, max_length=40)
    train_type: KorailTrainType
    departure_at: datetime
    arrival_at: datetime
    adult_fare: int | None = Field(default=None, ge=0)
    standard: SeatStatus
    first: SeatStatus
    expected_delay_minutes: int | None = Field(default=None, ge=1, le=999)

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def require_aware_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("schedule datetimes must include a timezone")
        return value

    @model_validator(mode="after")
    def require_arrival_after_departure(self) -> BrowserTrainSnapshot:
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


class BrowserSeatSearchResult(AdapterModel):
    source: Literal["korail-official-page-browser"] = SOURCE_NAME
    origin: str = Field(min_length=1, max_length=40)
    destination: str = Field(min_length=1, max_length=40)
    travel_date: date
    passenger_count: Literal[1]
    observed_at: datetime
    official_search_url: str | None = Field(default=None, max_length=2048)
    # A successful official response can legitimately contain no trains, especially
    # after the final departure of the current service day.  Keep that distinct from
    # malformed/protection responses, which are rejected by the transport parser.
    trains: list[BrowserTrainSnapshot] = Field(max_length=100)

    @field_validator("observed_at")
    @classmethod
    def require_aware_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @field_validator("official_search_url")
    @classmethod
    def require_strict_official_search_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_korail_general_search_url(value)


class BrowserAdapterError(RuntimeError):
    def __init__(self, reason: AdapterErrorReason) -> None:
        self.reason = reason
        super().__init__(reason)


class BrowserProtectionDetected(BrowserAdapterError):
    def __init__(
        self,
        trigger: ProtectionTrigger = "marker_abnormal_access",
        stage: str = "unspecified",
    ) -> None:
        self.trigger = trigger
        self.stage = stage
        super().__init__("provider_access_restricted")


class BrowserRateLimited(BrowserAdapterError):
    def __init__(self) -> None:
        super().__init__("rate_limited")


class BrowserSourceUnavailable(BrowserAdapterError):
    def __init__(self, stage: str = "unspecified") -> None:
        self.stage = stage
        super().__init__("source_unavailable")


class BrowserClient(Protocol):
    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult: ...


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


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: BrowserSeatSearchResult


@dataclass(frozen=True)
class _Cooldown:
    reason: AdapterErrorReason
    expires_at: float


class KorailBrowserAutomation:
    """Serializes browser work and collapses identical user-triggered searches."""

    def __init__(
        self,
        client: BrowserClient,
        *,
        cache_ttl_seconds: int = 1,
        rate_limit_cooldown_seconds: int = 1800,
        protection_cooldown_seconds: int = 300,
        shutdown_drain_timeout_seconds: float = 70,
        shutdown_cancel_timeout_seconds: float = 10,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self._protection_cooldown_seconds = protection_cooldown_seconds
        self._shutdown_drain_timeout_seconds = shutdown_drain_timeout_seconds
        self._shutdown_cancel_timeout_seconds = shutdown_cancel_timeout_seconds
        self._monotonic = monotonic
        self._cache: dict[tuple[str, str, str, str, str, int], _CacheEntry] = {}
        self._inflight: dict[
            tuple[str, str, str, str, str, int], asyncio.Task[BrowserSeatSearchResult]
        ] = {}
        self._state_lock = asyncio.Lock()
        self._browser_gate = asyncio.Semaphore(1)
        self._cooldown: _Cooldown | None = None
        self._failure_backoffs: dict[
            tuple[str, str, str, str, str, int], _Cooldown
        ] = {}
        self._failure_counts: dict[tuple[str, str, str, str, str, int], int] = {}

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        if request.passenger_count != 1:
            raise BrowserAdapterError("passenger_count_not_supported")
        key = request.cache_key()
        now = self._monotonic()
        async with self._state_lock:
            expired_backoff_keys = [
                failed_key
                for failed_key, backoff in self._failure_backoffs.items()
                if backoff.expires_at <= now
            ]
            for failed_key in expired_backoff_keys:
                self._failure_backoffs.pop(failed_key, None)
                self._failure_counts.pop(failed_key, None)
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.result
            if self._cooldown is not None:
                if self._cooldown.expires_at > now:
                    if self._cooldown.reason == "provider_access_restricted":
                        raise BrowserProtectionDetected()
                    if self._cooldown.reason == "rate_limited":
                        raise BrowserRateLimited()
                    raise BrowserAdapterError(self._cooldown.reason)
                self._cooldown = None
            failure_backoff = self._failure_backoffs.get(key)
            if failure_backoff is not None:
                if failure_backoff.expires_at > now:
                    raise BrowserSourceUnavailable("query_backoff")
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._load(key, request))
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def drain_pending_calls(self) -> bool:
        """Wait for owned browser searches to finish before sidecar shutdown completes."""
        pending_cancellation: asyncio.CancelledError | None = None
        while True:
            async with self._state_lock:
                tasks = tuple(self._inflight.values())
            if not tasks:
                if pending_cancellation is not None:
                    raise pending_cancellation
                return True
            drain_task = asyncio.create_task(
                self._drain_task_snapshot(tasks)
            )
            while not drain_task.done():
                try:
                    await asyncio.shield(drain_task)
                except asyncio.CancelledError as error:
                    # Request disconnect and shutdown cancellation must not abandon an
                    # owned Chromium process. Preserve the first cancellation and keep
                    # shielding until each browser context has run its bounded cleanup.
                    if pending_cancellation is None:
                        pending_cancellation = error
            completed = drain_task.result()
            if not completed:
                if pending_cancellation is not None:
                    raise pending_cancellation
                return False

    async def close(self) -> None:
        """Drain owned work and close an optional long-lived browser client."""
        drained = await self.drain_pending_calls()
        if not drained:
            logger.error("KORAIL browser client close skipped because owned search did not drain")
            return
        close_client = getattr(self._client, "close", None)
        if close_client is not None:
            await close_client()

    async def _drain_task_snapshot(
        self,
        tasks: tuple[asyncio.Task[BrowserSeatSearchResult], ...],
    ) -> bool:
        _, pending = await asyncio.wait(
            tasks,
            timeout=self._shutdown_drain_timeout_seconds,
        )
        if not pending:
            return True
        for task in pending:
            task.cancel()
        _, still_pending = await asyncio.wait(
            pending,
            timeout=self._shutdown_cancel_timeout_seconds,
        )
        if still_pending:
            logger.error(
                "KORAIL browser shutdown drain incomplete pending=%s",
                len(still_pending),
            )
            return False
        return True

    async def _load(
        self,
        key: tuple[str, str, str, str, str, int],
        request: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        current_task = asyncio.current_task()
        try:
            async with self._browser_gate:
                result = await self._client.search(request)
            async with self._state_lock:
                self._failure_counts.pop(key, None)
                self._failure_backoffs.pop(key, None)
                self._cache[key] = _CacheEntry(
                    expires_at=self._monotonic() + self._cache_ttl_seconds,
                    result=result,
                )
            return result
        except BrowserRateLimited:
            await self._open_cooldown("rate_limited", self._rate_limit_cooldown_seconds)
            raise
        except BrowserProtectionDetected:
            await self._open_cooldown(
                "provider_access_restricted", self._protection_cooldown_seconds
            )
            raise
        except BrowserAdapterError:
            await self._open_failure_backoff(key)
            raise
        except Exception as error:
            await self._open_failure_backoff(key)
            raise BrowserSourceUnavailable() from error
        finally:
            async with self._state_lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)

    async def _open_cooldown(self, reason: AdapterErrorReason, seconds: int) -> None:
        async with self._state_lock:
            self._cooldown = _Cooldown(reason, self._monotonic() + seconds)

    async def _open_failure_backoff(
        self,
        key: tuple[str, str, str, str, str, int],
    ) -> None:
        async with self._state_lock:
            failure_count = self._failure_counts.get(key, 0) + 1
            self._failure_counts[key] = failure_count
            seconds = min(30 * (2 ** (failure_count - 1)), 300)
            # DOM/source failures can be specific to a stale service date or one
            # exact query.  Keep only rate limits and access restrictions global.
            self._failure_backoffs[key] = _Cooldown(
                "source_unavailable",
                self._monotonic() + seconds,
            )


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
                    response = await page.goto(
                        self.page_url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
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

    async def _submit_search(self, page, request: BrowserSeatSearchRequest) -> None:
        try:
            await self._choose_station(page, "출발역", request.origin)
        except BrowserSourceUnavailable as error:
            if error.stage == "unspecified":
                raise BrowserSourceUnavailable("choose_origin") from error
            raise
        except Exception as error:
            raise BrowserSourceUnavailable("choose_origin") from error
        try:
            await self._choose_station(page, "도착역", request.destination)
        except BrowserSourceUnavailable as error:
            if error.stage == "unspecified":
                raise BrowserSourceUnavailable("choose_destination") from error
            raise
        except Exception as error:
            raise BrowserSourceUnavailable("choose_destination") from error
        try:
            await self._choose_departure(
                page, request.travel_date, request.departure_from.hour
            )
        except BrowserSourceUnavailable as error:
            if error.stage == "unspecified":
                raise BrowserSourceUnavailable("choose_departure") from error
            raise
        except Exception as error:
            raise BrowserSourceUnavailable("choose_departure") from error
        await self._assert_pre_submit_identity(page, request)
        buttons = page.get_by_role("button", name=re.compile(r"(?:열차\s*)?조회|조회하기"))
        if await buttons.count() != 1:
            raise BrowserSourceUnavailable("submit_button")
        button = buttons.first
        if not await button.is_visible() or not await button.is_enabled():
            raise BrowserSourceUnavailable("submit_button")
        await self._click_visible_control(page, button, "submit_button_click")

    async def _click_visible_control(self, page, control, stage: str) -> None:
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
                    self._release_and_detach_mouse(
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

    @staticmethod
    async def _release_and_detach_mouse(
        session,
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

    async def _choose_station(self, page, label_text: str, value: str) -> None:
        trigger = await self._station_trigger(page, label_text)
        if not await trigger.is_visible() or not await trigger.is_enabled():
            raise BrowserSourceUnavailable("station_trigger")
        await trigger.click()
        dialogs = page.get_by_role("dialog").filter(has_text="기차역 조회")
        if await dialogs.count() != 1:
            raise BrowserSourceUnavailable("station_dialog")
        dialog = dialogs.first
        await dialog.wait_for(state="visible", timeout=self.timeout_ms)
        searches = dialog.get_by_role("textbox", name="역명을 입력해주세요", exact=True)
        if await searches.count() == 0:
            searches = dialog.get_by_placeholder(
                re.compile(r"역\s*이름\s*또는\s*초성\s*검색")
            )
        if await searches.count() != 1:
            raise BrowserSourceUnavailable("station_search_input")
        search = searches.first
        try:
            await search.wait_for(state="visible", timeout=self.timeout_ms)
        except Exception as error:
            raise BrowserSourceUnavailable("station_search_input") from error
        if not await search.is_enabled():
            raise BrowserSourceUnavailable("station_search_input")
        await search.fill(value)
        result_containers = dialog.locator(".sch_form .sch_wrap")
        if await result_containers.count() != 1:
            raise BrowserSourceUnavailable("station_result")
        stations = result_containers.first.get_by_role("link", name=value, exact=True)
        station = await self._wait_for_unique_station_result(stations)
        await station.click()
        await dialog.wait_for(state="hidden", timeout=self.timeout_ms)

    async def _wait_for_unique_station_result(self, stations):
        """Wait for one stable async station result without accepting ambiguous matches."""
        deadline = time.monotonic() + (self.timeout_ms / 1000)
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

    async def _station_trigger(self, page, label_text: str):
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
            await label.wait_for(state="visible", timeout=self.timeout_ms)
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

    async def _station_value(self, page, label_text: str, selector: str) -> str:
        displays = page.locator(selector)
        count = await displays.count()
        if count == 1:
            display = displays.first
            if not await display.is_visible():
                raise BrowserSourceUnavailable("station_current_value")
            return _normalize_station(await display.input_value())
        if count > 1:
            raise BrowserSourceUnavailable("station_current_value")
        trigger = await self._station_trigger(page, label_text)
        if not await trigger.is_visible():
            raise BrowserSourceUnavailable("station_current_value")
        return _normalize_station(await trigger.inner_text())

    async def _departure_input(self, page):
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

    async def _passenger_value(self, page) -> str:
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

    async def _assert_pre_submit_identity(
        self, page, request: BrowserSeatSearchRequest
    ) -> None:
        try:
            origin = await self._station_value(
                page,
                "출발역",
                "input[name='txtGoStart']:visible, #labelstart:visible",
            )
            destination = await self._station_value(
                page,
                "도착역",
                "input[name='txtGoEnd']:visible, #labelend:visible",
            )
            date_input = await self._departure_input(page)
            passenger_value = await self._passenger_value(page)
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

    async def _choose_departure(self, page, travel_date: date, hour: int) -> None:
        try:
            stage = "departure_current_date"
            date_input = await self._departure_input(page)
            await date_input.wait_for(state="visible", timeout=self.timeout_ms)
            visible_date_match = re.match(
                r"^\s*(\d{4}-\d{2}-\d{2})",
                await date_input.input_value(),
            )
            if visible_date_match is None:
                raise BrowserSourceUnavailable(stage)
            current_date = date.fromisoformat(visible_date_match.group(1))

            stage = "departure_trigger_find"
            triggers = page.get_by_role("link", name="출발일 선택", exact=True)
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
                trigger = None
                for _ in range(4):
                    container = container.locator("..")
                    links = container.get_by_role("link")
                    if await links.count() == 1:
                        trigger = links.first
                        break
                if trigger is None:
                    raise BrowserSourceUnavailable(stage)
            if not await trigger.is_visible() or not await trigger.is_enabled():
                raise BrowserSourceUnavailable(stage)

            stage = "departure_trigger_click"
            await trigger.click(timeout=self.timeout_ms)
            stage = "departure_dialog_open"
            dialog = await self._wait_for_unique_departure_dialog(page)

            stage = "departure_month_navigate"
            await self._move_calendar_to_month(dialog, current_date, travel_date)
            stage = "departure_day_find"
            day = await self._find_date_link(dialog, travel_date)
            stage = "departure_day_click"
            await day.click(timeout=self.timeout_ms)

            stage = "departure_hour_find"
            time_sliders = dialog.locator(".slideWrap")
            if await time_sliders.count() != 1:
                raise BrowserSourceUnavailable(stage)
            time_slider = time_sliders.first
            hour_links = time_slider.locator("a").filter(
                has_text=re.compile(rf"^\s*{hour:02d}시\s*$")
            )
            await hour_links.first.wait_for(state="visible", timeout=self.timeout_ms)
            if await hour_links.count() != 1:
                raise BrowserSourceUnavailable(stage)
            hour_link = hour_links.first
            await self._move_time_to_hour(time_slider, hour_link, hour)
            stage = "departure_hour_click"
            await hour_link.click(timeout=self.timeout_ms)

            stage = "departure_apply_find"
            apply_buttons = dialog.get_by_role("button", name="적용", exact=True)
            if await apply_buttons.count() != 1:
                raise BrowserSourceUnavailable(stage)
            apply_button = apply_buttons.first
            if (
                not await apply_button.is_visible()
                or not await apply_button.is_enabled()
            ):
                raise BrowserSourceUnavailable(stage)
            stage = "departure_apply_click"
            await apply_button.click(timeout=self.timeout_ms)
            stage = "departure_dialog_close"
            await dialog.wait_for(state="hidden", timeout=self.timeout_ms)
        except BrowserSourceUnavailable:
            raise
        except Exception as error:
            raise BrowserSourceUnavailable(stage) from error

    async def _wait_for_unique_departure_dialog(self, page):
        """Wait for one stable async date dialog without accepting ambiguous matches."""
        dialogs = page.get_by_role("dialog").filter(has_text="날짜 선택")
        deadline = time.monotonic() + (self.timeout_ms / 1000)
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

    async def _move_calendar_to_month(
        self,
        dialog,
        current_date: date,
        travel_date: date,
    ) -> None:
        month_delta = (
            (travel_date.year - current_date.year) * 12
            + travel_date.month
            - current_date.month
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
            await calendar_button.click(timeout=self.timeout_ms)

    async def _move_time_to_hour(self, time_slider, hour_link, target_hour: int) -> None:
        for _ in range(24):
            if (
                await hour_link.is_visible()
                and await hour_link.is_enabled()
                and await hour_link.get_attribute("aria-disabled") != "true"
            ):
                return
            active_hours = await self._active_time_hours(time_slider)
            if not active_hours:
                raise BrowserSourceUnavailable("departure_hour_unavailable")
            if target_hour > max(active_hours):
                direction = "next"
            elif target_hour < min(active_hours):
                direction = "prev"
            else:
                raise BrowserSourceUnavailable("departure_hour_unavailable")
            control = await self._find_time_control(time_slider, direction)
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
            await control.click(timeout=self.timeout_ms)
            await asyncio.sleep(0.5)
        if (
            await hour_link.is_visible()
            and await hour_link.is_enabled()
            and await hour_link.get_attribute("aria-disabled") != "true"
        ):
            return
        raise BrowserSourceUnavailable("departure_hour_unavailable")

    async def _active_time_hours(self, time_slider) -> list[int]:
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

    async def _find_time_control(self, time_slider, direction: Literal["next", "prev"]):
        selector = f".slick-{direction}:visible"
        container = time_slider
        for depth in range(5):
            candidates = container.locator(selector)
            active_controls = []
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                classes = set(
                    (await candidate.get_attribute("class") or "").split()
                )
                if await candidate.is_enabled() and "slick-disabled" not in classes:
                    active_controls.append(candidate)
            if len(active_controls) == 1:
                return active_controls[0]
            if len(active_controls) > 1:
                logger.warning(
                    "KORAIL departure time slider control ambiguous "
                    "direction=%s depth=%s count=%s",
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

    async def _find_date_link(self, dialog, travel_date: date):
        headings = dialog.locator(".datepicker p").filter(
            has_text=re.compile(
                rf"^\s*{travel_date.year}(?:\.\s*{travel_date.month:02d}\.|년\s*"
                rf"{travel_date.month}월)\s*$"
            )
        )
        try:
            await headings.first.wait_for(state="visible", timeout=self.timeout_ms)
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
        deadline = time.monotonic() + min(self.timeout_ms / 1000, 5)
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

    async def _wait_for_result(self, page, blocked_responses: list[tuple[int, str]]) -> None:
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
        page,
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
        trigger = protection_trigger_from_text(body_text)
        if trigger is None:
            return
        if trigger not in GENERIC_PROTECTION_TRIGGERS:
            raise BrowserProtectionDetected(trigger, stage)

        surfaces = page.locator(PROTECTION_SURFACE_SELECTOR)
        for index in range(await surfaces.count()):
            surface_trigger = protection_trigger_from_text(
                await surfaces.nth(index).inner_text()
            )
            if surface_trigger in GENERIC_PROTECTION_TRIGGERS:
                raise BrowserProtectionDetected(surface_trigger, stage)
        if await page.locator("li.tckList:visible").count() == 0:
            raise BrowserProtectionDetected(trigger, stage)

    async def _read_result(
        self, page, request: BrowserSeatSearchRequest
    ) -> BrowserSeatSearchResult:
        if await self._passenger_value(page) != "총 1명":
            raise BrowserSourceUnavailable()
        date_input = await self._departure_input(page)
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
                # The official page mixes KTX with conventional trains.  TAGO's
                # KORAIL result set used by this overlay is KTX-only, so unrelated
                # row shapes must not invalidate otherwise exact KTX observations.
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
            standard_box, first_box = await self._seat_boxes(row)
            standard = await self._read_seat_status(standard_box)
            first = await self._read_seat_status(first_box)
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
                    adult_fare=parse_unambiguous_adult_fare(
                        await standard_box.inner_text()
                    ),
                    standard=standard,
                    first=first,
                    expected_delay_minutes=parse_expected_delay_minutes(
                        await row.inner_text()
                    ),
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

    async def _seat_boxes(self, row):
        boxes = row.locator(".tck_inner .price_box:visible")
        if await boxes.count() != 2:
            raise BrowserSourceUnavailable()
        standard = row.locator(".tck_inner .price_box.gen:visible").first
        first = row.locator(".tck_inner .price_box.spe:visible").first
        if await standard.count() == 1 and await first.count() == 1:
            return standard, first
        texts = [" ".join((await boxes.nth(index).inner_text()).split()) for index in range(2)]
        standard_index = next((index for index, text in enumerate(texts) if "일반실" in text), None)
        first_index = next((index for index, text in enumerate(texts) if "특실" in text), None)
        if standard_index is None or first_index is None or standard_index == first_index:
            # The current official result DOM does not label these columns with
            # gen/spe classes. Its fixed result-table order is 일반실 then 특실.
            return boxes.nth(0), boxes.nth(1)
        return boxes.nth(standard_index), boxes.nth(first_index)

    async def _read_seat_status(self, box) -> SeatStatus:
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


def is_supported_korail_train_kind(value: str) -> bool:
    return parse_official_train_type(value) is not None


def _normalize_train_number(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z-]", "", " ".join(value.split()))
    if not normalized or len(normalized) > 40:
        raise BrowserSourceUnavailable()
    digits = "".join(character for character in normalized if character.isdigit())
    return digits.lstrip("0") or "0"


def visible_departure_matches(value: str, travel_date: date, hour: int) -> bool:
    normalized = " ".join(value.split())
    iso_date = travel_date.isoformat()
    if normalized == iso_date:
        # The deterministic fixture keeps the minimal date-only representation.
        return True
    pattern = rf"{re.escape(iso_date)}\([월화수목금토일]\)\s+{hour:02d}:\d{{2}}"
    return re.fullmatch(pattern, normalized) is not None


def status_from_seat_box(text: str, classes: set[str]) -> SeatStatus | None:
    normalized = " ".join(text.split()).casefold()
    classes = {item.casefold() for item in classes}
    if re.search(r"예약\s*대기", normalized):
        return "waitlist_available"
    if "sold_out_soon" in classes or re.search(r"매진\s*임박", normalized):
        return "limited"
    if "sold_out" in classes or re.search(r"매진", normalized):
        return "sold_out"
    if re.search(r"입석\s*\+\s*(?:좌석|예매)", normalized):
        return "standing_plus_seat"
    if not normalized or re.fullmatch(r"(?:일반실|특실)?\s*[-–—]\s*", normalized):
        return "not_offered"
    if re.search(r"(?:좌석\s*)?(?:없음|없습니다)|해당\s*없음|미운행|미운영", normalized):
        return "not_offered"
    if re.search(r"(?:예매|예약)\s*불가", normalized):
        return None
    if re.search(r"\d{1,3}(?:,\d{3})*\s*원|(?:예매|예약)\s*가능", normalized):
        return "available"
    return None


def protection_trigger_from_http_response(
    status: int, resource_type: str
) -> ProtectionTrigger | None:
    if status != 403:
        return None
    if resource_type == "document":
        return "http_403_main"
    return "http_403_subresource"


def is_rate_limit_response(status: int, resource_type: str) -> bool:
    return status == 429 and resource_type in RATE_LIMIT_RESOURCE_TYPES


def protection_trigger_from_text(value: str) -> ProtectionTrigger | None:
    for trigger, pattern in PROTECTION_MARKERS:
        if pattern.search(value):
            return trigger
    return None
