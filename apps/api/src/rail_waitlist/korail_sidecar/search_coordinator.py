from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .browser_contracts import (
    AdapterErrorReason,
    BrowserAdapterError,
    BrowserClient,
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
)
from .browser_service_availability import (
    BrowserProviderUnavailable,
    ProviderUnavailableTrigger,
)

logger = logging.getLogger("rail_waitlist.korail_browser_automation")


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: BrowserSeatSearchResult


@dataclass(frozen=True)
class _Cooldown:
    reason: AdapterErrorReason
    expires_at: float
    provider_unavailable_trigger: ProviderUnavailableTrigger | None = None


class KorailBrowserAutomation:
    """Serialize browser work and collapse identical user-triggered searches."""

    def __init__(
        self,
        client: BrowserClient,
        *,
        cache_ttl_seconds: int = 1,
        rate_limit_cooldown_seconds: int = 300,
        protection_cooldown_seconds: int = 60,
        provider_unavailable_cooldown_seconds: int = 300,
        shutdown_drain_timeout_seconds: float = 70,
        shutdown_cancel_timeout_seconds: float = 10,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self._protection_cooldown_seconds = protection_cooldown_seconds
        self._provider_unavailable_cooldown_seconds = provider_unavailable_cooldown_seconds
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
        self._failure_backoffs: dict[tuple[str, str, str, str, str, int], _Cooldown] = {}
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
            if self._cooldown is not None:
                if self._cooldown.expires_at > now:
                    logger.info(
                        "KORAIL 운영사 조회를 생략합니다 "
                        "event=provider_query_skipped reason=provider_cooldown "
                        "outcome=%s remaining_seconds=%s",
                        self._cooldown.reason,
                        max(0, round(self._cooldown.expires_at - now)),
                    )
                    if self._cooldown.reason == "provider_access_restricted":
                        raise BrowserProtectionDetected()
                    if self._cooldown.reason == "rate_limited":
                        raise BrowserRateLimited()
                    if self._cooldown.provider_unavailable_trigger is not None:
                        raise BrowserProviderUnavailable(
                            self._cooldown.provider_unavailable_trigger,
                            "provider_cooldown",
                            retry_after_seconds=max(
                                1,
                                round(self._cooldown.expires_at - now),
                            ),
                        )
                    raise BrowserAdapterError(self._cooldown.reason)
                self._cooldown = None
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                return cached.result
            failure_backoff = self._failure_backoffs.get(key)
            if failure_backoff is not None and failure_backoff.expires_at > now:
                logger.info(
                    "KORAIL 운영사 조회를 생략합니다 "
                    "event=provider_query_skipped reason=query_backoff "
                    "outcome=source_unavailable remaining_seconds=%s",
                    max(0, round(failure_backoff.expires_at - now)),
                )
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
            drain_task = asyncio.create_task(self._drain_task_snapshot(tasks))
            while not drain_task.done():
                try:
                    await asyncio.shield(drain_task)
                except asyncio.CancelledError as error:
                    # Never abandon a Chromium process owned by an in-flight search.
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
        started_at = self._monotonic()
        query_started = False
        provider_unavailable_cooldown_opened = False
        try:
            async with self._browser_gate:
                queued_cooldown = await self._active_provider_cooldown()
                if queued_cooldown is not None:
                    now = self._monotonic()
                    logger.info(
                        "KORAIL 운영사 조회를 생략합니다 "
                        "event=provider_query_skipped reason=queued_provider_cooldown "
                        "outcome=%s remaining_seconds=%s",
                        (
                            "provider_unavailable"
                            if queued_cooldown.provider_unavailable_trigger is not None
                            else queued_cooldown.reason
                        ),
                        max(0, round(queued_cooldown.expires_at - now)),
                    )
                    if queued_cooldown.provider_unavailable_trigger is not None:
                        raise BrowserProviderUnavailable(
                            queued_cooldown.provider_unavailable_trigger,
                            "provider_cooldown",
                            retry_after_seconds=max(
                                1,
                                round(queued_cooldown.expires_at - now),
                            ),
                        )
                    if queued_cooldown.reason == "provider_access_restricted":
                        raise BrowserProtectionDetected()
                    if queued_cooldown.reason == "rate_limited":
                        raise BrowserRateLimited()
                started_at = self._monotonic()
                query_started = True
                logger.info("KORAIL 운영사 조회를 시작합니다 event=provider_query_started")
                try:
                    result = await self._client.search(request)
                except BrowserProviderUnavailable as error:
                    # Publish the provider-wide hold before releasing the browser gate.
                    # Otherwise a different queued query can slip into Chromium between
                    # the first outage response and the outer exception handler.
                    await self._open_cooldown(
                        "source_unavailable",
                        self._provider_unavailable_cooldown_seconds,
                        provider_unavailable_trigger=error.trigger,
                    )
                    provider_unavailable_cooldown_opened = True
                    raise error.with_retry_after(
                        self._provider_unavailable_cooldown_seconds
                    ) from None
            async with self._state_lock:
                self._failure_counts.pop(key, None)
                self._failure_backoffs.pop(key, None)
                self._cache[key] = _CacheEntry(
                    expires_at=self._monotonic() + self._cache_ttl_seconds,
                    result=result,
                )
            logger.info(
                "KORAIL 운영사 조회를 마쳤습니다 "
                "event=provider_query_completed outcome=success "
                "train_count=%s elapsed_ms=%s",
                len(result.trains),
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            return result
        except asyncio.CancelledError:
            if query_started:
                logger.info(
                    "KORAIL 운영사 조회를 중단합니다 "
                    "event=provider_query_completed outcome=cancelled elapsed_ms=%s",
                    max(0, round((self._monotonic() - started_at) * 1000)),
                )
            raise
        except BrowserRateLimited:
            if not query_started:
                raise
            await self._open_cooldown("rate_limited", self._rate_limit_cooldown_seconds)
            logger.warning(
                "KORAIL 운영사 조회를 중단합니다 "
                "event=provider_query_completed outcome=rate_limited "
                "cooldown_seconds=%s elapsed_ms=%s",
                self._rate_limit_cooldown_seconds,
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            raise
        except BrowserProtectionDetected as error:
            if not query_started:
                raise
            await self._open_cooldown(
                "provider_access_restricted", self._protection_cooldown_seconds
            )
            logger.warning(
                "KORAIL 운영사 조회를 중단합니다 "
                "event=provider_query_completed outcome=provider_access_restricted "
                "stage=%s trigger=%s "
                "cooldown_seconds=%s elapsed_ms=%s",
                error.stage,
                error.trigger,
                self._protection_cooldown_seconds,
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            raise
        except BrowserProviderUnavailable as error:
            if not query_started:
                raise
            if not provider_unavailable_cooldown_opened:
                await self._open_cooldown(
                    "source_unavailable",
                    self._provider_unavailable_cooldown_seconds,
                    provider_unavailable_trigger=error.trigger,
                )
            logger.warning(
                "KORAIL 운영사 조회를 중단합니다 "
                "event=provider_query_completed outcome=provider_unavailable "
                "stage=%s trigger=%s cooldown_seconds=%s elapsed_ms=%s",
                error.stage,
                error.trigger,
                self._provider_unavailable_cooldown_seconds,
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            if error.retry_after_seconds is None:
                raise error.with_retry_after(self._provider_unavailable_cooldown_seconds) from None
            raise
        except BrowserSourceUnavailable as error:
            backoff_seconds = await self._open_failure_backoff(key)
            logger.warning(
                "KORAIL 운영사 조회를 중단합니다 "
                "event=provider_query_completed outcome=source_unavailable "
                "stage=%s backoff_seconds=%s elapsed_ms=%s",
                error.stage,
                backoff_seconds,
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            raise
        except BrowserAdapterError as error:
            backoff_seconds = await self._open_failure_backoff(key)
            logger.warning(
                "KORAIL 운영사 조회를 중단합니다 "
                "event=provider_query_completed outcome=%s "
                "backoff_seconds=%s elapsed_ms=%s",
                error.reason,
                backoff_seconds,
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            raise
        except Exception as error:
            backoff_seconds = await self._open_failure_backoff(key)
            logger.warning(
                "KORAIL 운영사 조회를 중단합니다 "
                "event=provider_query_completed outcome=source_unavailable "
                "stage=unexpected_backend_error "
                "backoff_seconds=%s elapsed_ms=%s",
                backoff_seconds,
                max(0, round((self._monotonic() - started_at) * 1000)),
            )
            raise BrowserSourceUnavailable() from error
        finally:
            async with self._state_lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)

    async def _open_cooldown(
        self,
        reason: AdapterErrorReason,
        seconds: int,
        *,
        provider_unavailable_trigger: ProviderUnavailableTrigger | None = None,
    ) -> None:
        async with self._state_lock:
            self._cooldown = _Cooldown(
                reason,
                self._monotonic() + seconds,
                provider_unavailable_trigger,
            )

    async def _active_provider_cooldown(self) -> _Cooldown | None:
        async with self._state_lock:
            now = self._monotonic()
            if self._cooldown is None:
                return None
            if self._cooldown.expires_at <= now:
                self._cooldown = None
                return None
            return self._cooldown

    async def _open_failure_backoff(
        self,
        key: tuple[str, str, str, str, str, int],
    ) -> int:
        async with self._state_lock:
            failure_count = self._failure_counts.get(key, 0) + 1
            self._failure_counts[key] = failure_count
            seconds = min(30 * (1 << (failure_count - 1)), 300)
            # DOM/source failures can be specific to one exact query. Only explicit
            # rate-limit, access-restriction, and service-outage evidence is global.
            self._failure_backoffs[key] = _Cooldown(
                "source_unavailable",
                self._monotonic() + seconds,
            )
            return seconds
