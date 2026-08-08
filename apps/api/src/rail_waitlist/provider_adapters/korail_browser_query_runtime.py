from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..korail_sidecar.browser_contracts import BrowserSeatSearchRequest, BrowserSeatSearchResult
from ..korail_sidecar.client import _AdapterFailure
from ..seat_status_cooldown import CooldownStore
from ..timetable_management.schemas import SeatAvailabilityNotObservedReason

QueryKey = tuple[str, str, str, str, str, int]
QueryLoad = Callable[
    [QueryKey, BrowserSeatSearchRequest],
    Coroutine[Any, Any, BrowserSeatSearchResult],
]
ProviderSearch = Callable[[], Awaitable[BrowserSeatSearchResult]]
Monotonic = Callable[[], float]
CooldownStoreReader = Callable[[], CooldownStore]
SecondsReader = Callable[[], int]

SOURCE_FAILURE_COOLDOWN_MAX_SECONDS = 300


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: BrowserSeatSearchResult


@dataclass(frozen=True)
class _QueryCooldown:
    expires_at: float
    reason: SeatAvailabilityNotObservedReason


class _ProviderCooldown(RuntimeError):
    def __init__(self, reason: SeatAvailabilityNotObservedReason) -> None:
        self.reason = reason
        super().__init__(reason)


class KorailBrowserQueryRuntime:
    """Coordinate one API process's KORAIL browser queries and cooldown evidence."""

    def __init__(self) -> None:
        self._cache: dict[QueryKey, _CacheEntry] = {}
        self._inflight: dict[QueryKey, asyncio.Task[BrowserSeatSearchResult]] = {}
        self._state_lock = asyncio.Lock()
        self._provider_gate = asyncio.Semaphore(1)
        self._failure_count = 0
        self._query_failure_counts: dict[QueryKey, int] = {}
        self._query_cooldowns: dict[QueryKey, _QueryCooldown] = {}

    @property
    def query_cooldowns(self) -> dict[QueryKey, _QueryCooldown]:
        """Expose the legacy read seam without transferring state ownership."""

        return self._query_cooldowns

    async def search(
        self,
        request: BrowserSeatSearchRequest,
        *,
        load: QueryLoad,
        monotonic: Monotonic,
        cooldown_store: CooldownStoreReader,
    ) -> BrowserSeatSearchResult:
        key = request.cache_key()
        now = monotonic()
        async with self._state_lock:
            expired_query_keys = [
                query_key
                for query_key, cooldown in self._query_cooldowns.items()
                if cooldown.expires_at <= now
            ]
            for query_key in expired_query_keys:
                self._query_cooldowns.pop(query_key, None)
                self._query_failure_counts.pop(query_key, None)
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.result
            query_cooldown = self._query_cooldowns.get(key)
            if query_cooldown is not None:
                raise _ProviderCooldown(query_cooldown.reason)
            provider_cooldown = await cooldown_store().get("korail-browser")
            if (
                provider_cooldown is not None
                and provider_cooldown.reason == "provider_access_restricted"
            ):
                raise _ProviderCooldown(provider_cooldown.reason)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(load(key, request))
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def load(
        self,
        key: QueryKey,
        request: BrowserSeatSearchRequest,
        *,
        provider_search: ProviderSearch,
        monotonic: Monotonic,
        cache_ttl_seconds: SecondsReader,
    ) -> BrowserSeatSearchResult:
        current_task = asyncio.current_task()
        try:
            async with self._provider_gate:
                result = await provider_search()
            if (
                result.origin != request.origin
                or result.destination != request.destination
                or result.travel_date != request.travel_date
                or result.passenger_count != request.passenger_count
            ):
                raise _AdapterFailure("source_unavailable")
            async with self._state_lock:
                self._failure_count = 0
                self._query_failure_counts.pop(key, None)
                self._query_cooldowns.pop(key, None)
                self._cache[key] = _CacheEntry(
                    expires_at=monotonic() + cache_ttl_seconds(),
                    result=result,
                )
            return result
        finally:
            async with self._state_lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)

    async def open_cooldown(
        self,
        error: _AdapterFailure,
        key: QueryKey,
        *,
        monotonic: Monotonic,
        cooldown_store: CooldownStoreReader,
        rate_limit_seconds: SecondsReader,
        protection_seconds: SecondsReader,
    ) -> None:
        if error.protection:
            self._failure_count += 1
            duration = protection_seconds()
        elif error.rate_limited:
            self._failure_count += 1
            duration = rate_limit_seconds()
        else:
            # Ordinary source failures are query-local. Only explicit provider access
            # evidence may create the shared Redis hold used across service dates.
            async with self._state_lock:
                failures = self._query_failure_counts.get(key, 0) + 1
                self._query_failure_counts[key] = failures
                duration = min(
                    30 * (2 ** (failures - 1)),
                    SOURCE_FAILURE_COOLDOWN_MAX_SECONDS,
                )
                self._query_cooldowns[key] = _QueryCooldown(
                    expires_at=monotonic() + duration,
                    reason=error.reason,
                )
            return
        await cooldown_store().set("korail-browser", error.reason, duration)

    async def drain_pending_calls(self) -> None:
        """Drain shielded searches before the owning transport is closed."""

        while True:
            async with self._state_lock:
                tasks = tuple(self._inflight.values())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def observation_deferred_until(
        self,
        *,
        cooldown_store: CooldownStoreReader,
    ) -> datetime | None:
        cooldown = await cooldown_store().get("korail-browser")
        if cooldown is None or cooldown.reason != "provider_access_restricted":
            return None
        return datetime.now(UTC) + timedelta(seconds=max(1, cooldown.retry_after_seconds))
