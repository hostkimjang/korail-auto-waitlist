"""Own bounded, read-only KORAIL HTTP replay leases.

The manager keeps captured provider material inside an HTTP client owned by the
sidecar process.  It knows neither browser lifecycle nor authenticated session
state; the Pydoll facade decides when a detached search session may be retired.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Protocol

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
)
from ..browser_protection import normalize_replay_protection_trigger
from ..http_replay import (
    HttpReplayInvalidCapture,
    HttpReplayInvalidResponse,
    HttpReplayLeaseInvalid,
    HttpReplayProtectionDetected,
    HttpReplayRateLimited,
    HttpReplaySessionInvalid,
    HttpReplaySourceUnavailable,
    KorailHttpReplayPlan,
)

logger = logging.getLogger("rail_waitlist.korail_pydoll_http_replay")

DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE = 4
_RouteKey = tuple[str, str]
Cleanup = Callable[[Awaitable[object]], Awaitable[None]]


class KorailHttpReplayCaptureSession(Protocol):
    """The secret-free capture surface exposed by one read-only browser search."""

    async def begin_http_replay_capture(self) -> None: ...

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> KorailHttpReplayPlan: ...


class KorailHttpReplaySearchClient(Protocol):
    """One disposable HTTP replay client whose captured material never escapes."""

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult: ...

    async def close(self) -> None: ...


class KorailHttpReplayClientFactory(Protocol):
    def __call__(
        self,
        plan: KorailHttpReplayPlan,
        *,
        timeout_seconds: float,
        lease_is_current: Callable[[], bool],
    ) -> KorailHttpReplaySearchClient: ...


@dataclass
class _ActiveHttpReplayLease:
    client: KorailHttpReplaySearchClient
    lease_id: object
    origin: str
    destination: str
    created_at: float
    captured_request_count: int
    searches_started: int = 0


class PydollHttpReplayManager:
    """Cache detached read-only replay plans by exact route within bounded reuse limits."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        reuse_ttl_seconds: float,
        reuse_max_searches: int,
        route_cache_size: int = DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE,
        monotonic: Callable[[], float],
        client_factory: KorailHttpReplayClientFactory,
        cleanup: Cleanup,
        event_logger: logging.Logger | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._reuse_ttl_seconds = reuse_ttl_seconds
        self._reuse_max_searches = reuse_max_searches
        self._route_cache_size = route_cache_size
        self._monotonic = monotonic
        self._client_factory = client_factory
        self._cleanup = cleanup
        self._logger = event_logger or logger
        self._active_leases: OrderedDict[_RouteKey, _ActiveHttpReplayLease] = OrderedDict()

    @property
    def active_leases(self) -> Mapping[_RouteKey, object]:
        """Expose lease presence for compatibility inspection without mutation access."""

        return MappingProxyType(self._active_leases)

    @staticmethod
    def route_key(request: BrowserSeatSearchRequest) -> _RouteKey:
        return request.origin, request.destination

    async def try_search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult | None:
        route_key = self.route_key(request)
        lease = self._active_leases.get(route_key)
        if lease is None:
            return None
        now = self._monotonic()
        retirement_reason: str | None = None
        if now - lease.created_at >= self._reuse_ttl_seconds:
            retirement_reason = "ttl_expired"
        elif lease.searches_started >= self._reuse_max_searches:
            retirement_reason = "search_limit"
        if retirement_reason is not None:
            self._logger.info(
                "KORAIL HTTP replay event=lease_retired reason=%s recovery=cold_init",
                retirement_reason,
            )
            await self.discard(route_key)
            return None
        self._active_leases.move_to_end(route_key)
        lease.searches_started += 1
        try:
            result = await lease.client.search(request)
            self._logger.info(
                "KORAIL HTTP replay event=search_succeeded lease_search_index=%d",
                lease.searches_started,
            )
            return result
        except HttpReplayProtectionDetected as error:
            await self.discard(route_key)
            raise BrowserProtectionDetected(
                normalize_replay_protection_trigger(error.trigger), "http_replay"
            ) from None
        except HttpReplayRateLimited:
            await self.discard(route_key)
            raise BrowserRateLimited() from None
        except HttpReplaySessionInvalid:
            self._logger.info(
                "KORAIL HTTP replay event=cold_reinit source=http_replay reason=session_invalid"
            )
            await self.discard(route_key)
            return None
        except (HttpReplayInvalidCapture, HttpReplayInvalidResponse, HttpReplayLeaseInvalid):
            await self.discard(route_key)
            raise BrowserSourceUnavailable("http_replay") from None
        except HttpReplaySourceUnavailable:
            await self.discard(route_key)
            raise BrowserSourceUnavailable("http_replay") from None

    async def begin_capture(self, session: KorailHttpReplayCaptureSession) -> bool:
        if not self._reuse_enabled:
            return False
        begin = getattr(session, "begin_http_replay_capture", None)
        if begin is None:
            return False
        try:
            await begin()
        except asyncio.CancelledError:
            raise
        except HttpReplayInvalidCapture as error:
            self._logger.warning(
                "KORAIL HTTP replay event=capture_unavailable stage=capture_start "
                "reason=%s capture_stage=%s",
                error.reason,
                error.stage,
            )
            return False
        except HttpReplaySessionInvalid as error:
            self._logger.warning(
                "KORAIL HTTP replay event=capture_unavailable stage=capture_start reason=%s",
                error.reason,
            )
            return False
        except Exception:  # noqa: BLE001 -- optional Pydoll capture exceptions are unstable.
            self._logger.warning("KORAIL HTTP replay capture unavailable stage=capture_start")
            return False
        return True

    async def install_capture(
        self,
        *,
        session: KorailHttpReplayCaptureSession,
        request: BrowserSeatSearchRequest,
        created_at: float,
        searches_started: int,
    ) -> bool:
        export = getattr(session, "export_http_replay_plan", None)
        if export is None:
            return False
        try:
            plan = await export(
                origin=request.origin,
                destination=request.destination,
                captured_date=request.travel_date,
            )
        except asyncio.CancelledError:
            raise
        except HttpReplayInvalidCapture as error:
            self._logger.warning(
                "KORAIL HTTP replay event=capture_unavailable stage=capture_export "
                "reason=%s capture_stage=%s",
                error.reason,
                error.stage,
            )
            return False
        except HttpReplaySessionInvalid as error:
            self._logger.warning(
                "KORAIL HTTP replay event=capture_unavailable stage=capture_export reason=%s",
                error.reason,
            )
            return False
        except Exception:  # noqa: BLE001 -- optional Pydoll capture exceptions are unstable.
            self._logger.warning("KORAIL HTTP replay capture unavailable stage=capture_export")
            return False

        route_key = self.route_key(request)
        lease_id = object()

        def lease_is_current() -> bool:
            active = self._active_leases.get(route_key)
            return bool(
                active is not None
                and active.lease_id is lease_id
                and self._monotonic() - active.created_at < self._reuse_ttl_seconds
                and active.searches_started <= self._reuse_max_searches
            )

        try:
            client = self._client_factory(
                plan,
                timeout_seconds=self._timeout_seconds,
                lease_is_current=lease_is_current,
            )
        except Exception:  # noqa: BLE001 -- optional replay setup must not hide UI results.
            self._logger.warning("KORAIL HTTP replay capture unavailable stage=client_init")
            return False
        await self.discard(route_key)
        self._active_leases[route_key] = _ActiveHttpReplayLease(
            client=client,
            lease_id=lease_id,
            origin=request.origin,
            destination=request.destination,
            created_at=created_at,
            captured_request_count=plan.captured_request_count,
            searches_started=searches_started,
        )
        self._active_leases.move_to_end(route_key)
        return True

    async def finalize_install(self, request: BrowserSeatSearchRequest) -> None:
        """Prune only after the facade has safely retired its detached browser session."""

        route_key = self.route_key(request)
        active = self._active_leases.get(route_key)
        if active is None:
            return
        while len(self._active_leases) > self._route_cache_size:
            oldest_route = next(iter(self._active_leases))
            self._logger.info("KORAIL HTTP replay event=lease_retired reason=route_cache_capacity")
            await self.discard(oldest_route)
        self._logger.info(
            "KORAIL HTTP replay event=lease_created captured_requests=%d "
            "ttl_seconds=%g max_searches=%d",
            active.captured_request_count,
            self._reuse_ttl_seconds,
            self._reuse_max_searches,
        )

    async def discard(self, route_key: _RouteKey | None = None) -> None:
        if route_key is not None:
            active = self._active_leases.pop(route_key, None)
            if active is not None:
                await self._cleanup(active.client.close())
            return

        active_leases = tuple(self._active_leases.values())
        self._active_leases.clear()
        if not active_leases:
            return

        async def close_all() -> None:
            results = await asyncio.gather(
                *(lease.client.close() for lease in active_leases),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

        await self._cleanup(close_all())

    @property
    def _reuse_enabled(self) -> bool:
        return self._reuse_ttl_seconds > 0 and self._reuse_max_searches > 1
