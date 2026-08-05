from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum

from ..korail_browser_automation import (
    FULLSTACK_E2E_PAGE_URL,
    OFFICIAL_KORAIL_SEARCH_URL,
    BrowserClient,
    KorailBrowserAutomation,
    PlaywrightKorailBrowserClient,
    probe_chromium,
)
from ..korail_search_bootstrap import KorailStationIdentityResolver

# Preserve the operational logger namespace while the compatibility facade still owns HTTP.
logger = logging.getLogger("rail_waitlist.korail_browser_adapter_service")


class ReadinessGate:
    """Cache a successful probe while allowing bounded recovery after startup failure."""

    def __init__(
        self,
        probe: Callable[[], Awaitable[None]],
        *,
        retry_interval_seconds: float,
        probe_timeout_seconds: float,
    ) -> None:
        self._probe = probe
        self._retry_interval_seconds = retry_interval_seconds
        self._probe_timeout_seconds = probe_timeout_seconds
        self._lock = asyncio.Lock()
        self._last_attempt_at: float | None = None
        self.ready = False

    async def probe_if_due(self, *, force: bool = False) -> bool:
        if self.ready:
            return True
        async with self._lock:
            if self.ready:
                return True
            now = time.monotonic()
            if (
                not force
                and self._last_attempt_at is not None
                and now - self._last_attempt_at < self._retry_interval_seconds
            ):
                return False
            self._last_attempt_at = now
            try:
                await asyncio.wait_for(self._probe(), timeout=self._probe_timeout_seconds)
            except Exception:  # noqa: BLE001 -- optional browser backends expose unstable errors.
                logger.error("Chromium readiness probe failed; sidecar remains not ready")
                return False
            self.ready = True
            return True


class KorailBrowserEngine(StrEnum):
    PLAYWRIGHT_DIRECT_CDP = "playwright_direct_cdp"
    PYDOLL = "pydoll"


def browser_engine_setting() -> KorailBrowserEngine:
    raw_value = os.getenv("KORAIL_BROWSER_ENGINE", KorailBrowserEngine.PYDOLL.value)
    try:
        return KorailBrowserEngine(raw_value.strip().lower())
    except ValueError as error:
        allowed = ", ".join(engine.value for engine in KorailBrowserEngine)
        raise RuntimeError(f"KORAIL_BROWSER_ENGINE must be one of: {allowed}") from error


def integer_setting(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def float_setting(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be numeric") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def build_browser_client(
    engine: KorailBrowserEngine,
    *,
    page_url: str,
    timeout_seconds: float,
    allow_fullstack_fixture: bool,
) -> BrowserClient:
    if engine is KorailBrowserEngine.PLAYWRIGHT_DIRECT_CDP:
        return PlaywrightKorailBrowserClient(
            page_url=page_url,
            timeout_seconds=timeout_seconds,
            allow_fullstack_fixture=allow_fullstack_fixture,
        )

    from ..korail_pydoll_browser import PydollKorailBrowserClient

    return PydollKorailBrowserClient(
        page_url=page_url,
        timeout_seconds=timeout_seconds,
        allow_fullstack_fixture=allow_fullstack_fixture,
        station_identity_resolver=(
            None
            if allow_fullstack_fixture
            else KorailStationIdentityResolver(
                url=os.getenv(
                    "KORAIL_STATION_DATA_URL",
                    "https://www.korail.com/public/st_info/station_data.json",
                )
            )
        ),
        session_reuse_ttl_seconds=integer_setting(
            "KORAIL_BROWSER_SESSION_REUSE_TTL_SECONDS", 1800, minimum=30, maximum=1800
        ),
        session_reuse_max_searches=integer_setting(
            "KORAIL_BROWSER_SESSION_REUSE_MAX_SEARCHES", 100, minimum=2, maximum=100
        ),
    )


def readiness_probe_for_engine(
    engine: KorailBrowserEngine,
) -> Callable[[], Awaitable[None]]:
    if engine is KorailBrowserEngine.PLAYWRIGHT_DIRECT_CDP:
        return probe_chromium

    from ..korail_pydoll_browser import probe_pydoll_chromium

    return probe_pydoll_chromium


def build_automation(
    engine: KorailBrowserEngine | None = None,
    *,
    browser_client: BrowserClient | None = None,
) -> KorailBrowserAutomation:
    selected_engine = engine or browser_engine_setting()
    page_url = os.getenv("KORAIL_BROWSER_PAGE_URL", OFFICIAL_KORAIL_SEARCH_URL)
    allow_fullstack_fixture = (
        os.getenv("ENVIRONMENT", "").strip().lower() == "test"
        and page_url == FULLSTACK_E2E_PAGE_URL
    )
    client = browser_client or build_browser_client(
        selected_engine,
        page_url=page_url,
        timeout_seconds=float_setting(
            "KORAIL_BROWSER_ACTION_TIMEOUT_SECONDS", 25, minimum=5, maximum=60
        ),
        allow_fullstack_fixture=allow_fullstack_fixture,
    )
    return KorailBrowserAutomation(
        client,
        cache_ttl_seconds=integer_setting(
            "KORAIL_BROWSER_CACHE_TTL_SECONDS", 1, minimum=1, maximum=300
        ),
        rate_limit_cooldown_seconds=integer_setting(
            "SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS", 1800, minimum=60, maximum=86400
        ),
        protection_cooldown_seconds=integer_setting(
            "SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS", 300, minimum=300, maximum=86400
        ),
    )
