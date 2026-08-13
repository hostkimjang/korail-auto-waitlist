from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..korail_sidecar.browser_contracts import BrowserSeatSearchRequest, BrowserSeatSearchResult
from ..korail_sidecar.client import _AdapterFailure
from ..provider_call_context import (
    bind_request_deadline_at,
    bind_request_id,
    current_request_deadline,
    current_request_id,
    new_log_id,
)
from ..seat_status_cooldown import (
    KORAIL_BROWSER_COOLDOWN_KEY,
    KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY,
    CooldownStore,
)
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
CooldownObserver = Callable[[_AdapterFailure, QueryKey], Awaitable[None]]

SOURCE_FAILURE_COOLDOWN_MAX_SECONDS = 300
MAIN_QUERY_CLEANUP_GRACE_SECONDS = 1.0
logger = logging.getLogger("rail_waitlist.korail_browser_query_runtime")


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: BrowserSeatSearchResult


@dataclass(frozen=True)
class _QueryCooldown:
    expires_at: float
    reason: SeatAvailabilityNotObservedReason


@dataclass
class _InflightQuery:
    task: asyncio.Task[BrowserSeatSearchResult]
    shared_request_id: str
    deadline: float
    waiters: dict[str, str] = field(default_factory=dict)
    provider_started: bool = False


class _ProviderCooldown(RuntimeError):
    def __init__(self, reason: SeatAvailabilityNotObservedReason) -> None:
        self.reason = reason
        super().__init__(reason)


class KorailBrowserQueryRuntime:
    """Coordinate one API process's KORAIL browser queries and cooldown evidence."""

    def __init__(self) -> None:
        self._cache: dict[QueryKey, _CacheEntry] = {}
        self._inflight: dict[QueryKey, _InflightQuery] = {}
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
        timeout_seconds: float,
    ) -> BrowserSeatSearchResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        key = request.cache_key()
        now = monotonic()
        request_id = current_request_id() or new_log_id()
        waiter_token = new_log_id()
        inherited_deadline = current_request_deadline()
        caller_deadline = now + timeout_seconds
        if inherited_deadline is not None:
            caller_deadline = min(caller_deadline, inherited_deadline)
        async with self._state_lock:
            expired_query_keys = [
                query_key
                for query_key, cooldown in self._query_cooldowns.items()
                if cooldown.expires_at <= now
            ]
            for query_key in expired_query_keys:
                self._query_cooldowns.pop(query_key, None)
                self._query_failure_counts.pop(query_key, None)
            outage_cooldown = await cooldown_store().get(KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY)
            if outage_cooldown is not None:
                raise _ProviderCooldown("source_unavailable")
            provider_cooldown = await cooldown_store().get(KORAIL_BROWSER_COOLDOWN_KEY)
            if (
                provider_cooldown is not None
                and provider_cooldown.reason == "provider_access_restricted"
            ):
                raise _ProviderCooldown(provider_cooldown.reason)
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.result
            query_cooldown = self._query_cooldowns.get(key)
            if query_cooldown is not None:
                raise _ProviderCooldown(query_cooldown.reason)
            inflight = self._inflight.get(key)
            if inflight is not None and inflight.task.done():
                self._inflight.pop(key, None)
                inflight = None
            if inflight is None:
                remaining_budget = max(0.0, caller_deadline - monotonic())
                cleanup_grace = min(
                    MAIN_QUERY_CLEANUP_GRACE_SECONDS,
                    remaining_budget * 0.2,
                )
                provider_deadline = caller_deadline - cleanup_grace
                with (
                    bind_request_id(request_id),
                    bind_request_deadline_at(provider_deadline) as deadline,
                ):
                    task = asyncio.create_task(load(key, request))
                task.add_done_callback(self._consume_task_terminal)
                inflight = _InflightQuery(
                    task=task,
                    shared_request_id=request_id,
                    deadline=deadline,
                )
                self._inflight[key] = inflight
                logger.info(
                    "KORAIL main query task created event=main_query_created request_id=%s",
                    request_id,
                )
            else:
                logger.info(
                    "KORAIL main singleflight joined "
                    "event=main_query_singleflight_join request_id=%s shared_request_id=%s",
                    request_id,
                    inflight.shared_request_id,
                )
            inflight.waiters[waiter_token] = request_id
        waiter_released = False
        try:
            remaining = caller_deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                return await asyncio.shield(inflight.task)
        except TimeoutError:
            logger.info(
                "KORAIL main query waiter deadline ended "
                "event=main_query_waiter_deadline request_id=%s shared_request_id=%s",
                request_id,
                inflight.shared_request_id,
            )
            await self._release_waiter(key, inflight, waiter_token)
            waiter_released = True
            raise _AdapterFailure("source_unavailable", deadline_exceeded=True) from None
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                logger.info(
                    "KORAIL main query caller cancelled "
                    "event=main_query_waiter_cancelled request_id=%s shared_request_id=%s "
                    "upstream_still_running=%s",
                    request_id,
                    inflight.shared_request_id,
                    str(not inflight.task.done()).lower(),
                )
                raise
            if inflight.provider_started:
                raise
            await self._release_waiter(key, inflight, waiter_token)
            waiter_released = True
            remaining = caller_deadline - monotonic()
            if remaining <= 0:
                raise _AdapterFailure("source_unavailable", deadline_exceeded=True) from None
            with bind_request_id(request_id):
                return await self.search(
                    request,
                    load=load,
                    monotonic=monotonic,
                    cooldown_store=cooldown_store,
                    timeout_seconds=remaining,
                )
        finally:
            if not waiter_released:
                await self._release_waiter(key, inflight, waiter_token)

    @staticmethod
    def _consume_task_terminal(task: asyncio.Task[BrowserSeatSearchResult]) -> None:
        if not task.cancelled():
            task.exception()

    async def _release_waiter(
        self,
        key: QueryKey,
        inflight: _InflightQuery,
        waiter_token: str,
    ) -> None:
        async with self._state_lock:
            inflight.waiters.pop(waiter_token, None)
            if (
                self._inflight.get(key) is inflight
                and not inflight.waiters
                and not inflight.provider_started
                and not inflight.task.done()
                and not inflight.task.cancelling()
            ):
                inflight.task.cancel()

    async def load(
        self,
        key: QueryKey,
        request: BrowserSeatSearchRequest,
        *,
        provider_search: ProviderSearch,
        observe_cooldown: CooldownObserver,
        cooldown_store: CooldownStoreReader,
        monotonic: Monotonic,
        cache_ttl_seconds: SecondsReader,
    ) -> BrowserSeatSearchResult:
        current_task = asyncio.current_task()
        provider_started = False
        try:
            deadline = current_request_deadline()
            if deadline is None:
                raise RuntimeError("KORAIL query task is missing its request deadline")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _AdapterFailure("source_unavailable", deadline_exceeded=True)
            try:
                async with asyncio.timeout(remaining):
                    async with self._provider_gate:
                        outage = await cooldown_store().get(KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY)
                        if outage is not None:
                            raise _ProviderCooldown("source_unavailable")
                        if monotonic() >= deadline:
                            raise TimeoutError
                        async with self._state_lock:
                            inflight = self._inflight.get(key)
                            if inflight is None or not inflight.waiters:
                                raise asyncio.CancelledError
                            inflight.provider_started = True
                        provider_started = True
                        try:
                            result = await provider_search()
                            if monotonic() >= deadline:
                                raise TimeoutError
                        except _AdapterFailure as error:
                            if error.cooldown_scope == "provider":
                                await observe_cooldown(error, key)
                            raise
            except TimeoutError:
                logger.info(
                    "KORAIL main query deadline ended "
                    "event=main_query_deadline outcome=deadline_exceeded query_started=%s",
                    str(provider_started).lower(),
                )
                raise _AdapterFailure("source_unavailable", deadline_exceeded=True) from None
            if (
                result.origin != request.origin
                or result.destination != request.destination
                or result.travel_date != request.travel_date
                or result.passenger_count != request.passenger_count
            ):
                raise _AdapterFailure("source_unavailable")
            async with self._state_lock:
                inflight = self._inflight.get(key)
                if monotonic() >= deadline or inflight is None or not inflight.waiters:
                    logger.info(
                        "KORAIL main query deadline ended "
                        "event=main_query_deadline outcome=deadline_exceeded "
                        "query_started=%s",
                        str(provider_started).lower(),
                    )
                    raise _AdapterFailure(
                        "source_unavailable",
                        deadline_exceeded=True,
                    )
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
                inflight = self._inflight.get(key)
                if inflight is not None and inflight.task is current_task:
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
        if error.deadline_exceeded:
            return
        if error.cooldown_scope == "provider":
            duration = error.retry_after_seconds or SOURCE_FAILURE_COOLDOWN_MAX_SECONDS
            await cooldown_store().set(
                KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY,
                "source_unavailable",
                duration,
            )
            return
        if error.protection:
            self._failure_count += 1
            duration = protection_seconds()
        elif error.rate_limited:
            self._failure_count += 1
            duration = rate_limit_seconds()
        else:
            # Ordinary source failures are query-local. Protection/rate-limit evidence
            # and explicit service-outage pages use separate shared provider holds.
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
        await cooldown_store().set(KORAIL_BROWSER_COOLDOWN_KEY, error.reason, duration)

    async def drain_pending_calls(self) -> None:
        """Drain shielded searches before the owning transport is closed."""

        while True:
            async with self._state_lock:
                tasks = tuple(inflight.task for inflight in self._inflight.values())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def observation_deferred_until(
        self,
        *,
        cooldown_store: CooldownStoreReader,
    ) -> datetime | None:
        outage, cooldown = await asyncio.gather(
            cooldown_store().get(KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY),
            cooldown_store().get(KORAIL_BROWSER_COOLDOWN_KEY),
        )
        candidates = [outage]
        if cooldown is not None and cooldown.reason == "provider_access_restricted":
            candidates.append(cooldown)
        active = max(
            (candidate for candidate in candidates if candidate is not None),
            key=lambda candidate: candidate.retry_after_seconds,
            default=None,
        )
        if active is None:
            return None
        return datetime.now(UTC) + timedelta(seconds=max(1, active.retry_after_seconds))
