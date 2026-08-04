from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from redis.asyncio import Redis

from .config import Settings
from .korail_browser_seat_source import KorailBrowserSeatSource
from .schemas import SeatObservationRequest, SeatObservationResult
from .schemas import ReservationRequest, ReservationResult
from .provider_accounts import ProviderCredentials
from .reservation_confirmation import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .seat_status_cooldown import RedisCooldownStore


class KorailSeatObserver(Protocol):
    async def observation_deferred_until(self) -> datetime | None: ...

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]: ...

    async def drain_pending_calls(self) -> None: ...

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult: ...

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult: ...


@dataclass
class ManagedKorailSeatObserver:
    """Own one Chromium source and Redis client for a single Celery task loop."""

    source: KorailBrowserSeatSource
    redis: Redis

    async def observation_deferred_until(self) -> datetime | None:
        return await self.source.observation_deferred_until()

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        return await self.source.observe(
            request,
            origin=origin,
            destination=destination,
        )

    async def drain_pending_calls(self) -> None:
        await self.source.drain_pending_calls()

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult:
        return await self.source.reserve_once(request, credentials)

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return await self.source.confirm_reservation(target)

    async def aclose(self) -> None:
        await self.source.close()
        await self.redis.aclose()


@dataclass(frozen=True)
class KorailExecutionSourceConfig:
    redis_url: str
    adapter_url: str
    adapter_token: str | None
    cache_ttl_seconds: int
    timeout_seconds: float
    rate_limit_cooldown_seconds: int
    protection_cooldown_seconds: int
    allow_fullstack_test_url: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> KorailExecutionSourceConfig:
        return cls(
            redis_url=settings.redis_url,
            adapter_url=settings.korail_browser_adapter_url,
            adapter_token=settings.korail_browser_adapter_token,
            cache_ttl_seconds=settings.korail_browser_adapter_cache_ttl_seconds,
            timeout_seconds=settings.korail_browser_adapter_timeout_seconds,
            rate_limit_cooldown_seconds=settings.seat_status_rate_limit_cooldown_seconds,
            protection_cooldown_seconds=settings.seat_status_protection_cooldown_seconds,
            allow_fullstack_test_url=settings.environment == "test",
        )


def korail_background_monitoring_enabled(settings: Settings) -> bool:
    """Keep recurring Chromium traffic behind its own explicit opt-in."""

    return (
        settings.experimental_rail_enabled
        and settings.korail_browser_adapter_enabled
        and settings.korail_seat_monitoring_enabled
    )


def _source_for_config(config: KorailExecutionSourceConfig) -> ManagedKorailSeatObserver:
    redis = Redis.from_url(config.redis_url, decode_responses=True)
    source = KorailBrowserSeatSource(
        enabled=True,
        adapter_url=config.adapter_url,
        token=config.adapter_token,
        cache_ttl_seconds=config.cache_ttl_seconds,
        timeout_seconds=config.timeout_seconds,
        rate_limit_cooldown_seconds=config.rate_limit_cooldown_seconds,
        protection_cooldown_seconds=config.protection_cooldown_seconds,
        cooldown_store=RedisCooldownStore(redis),
        allow_fullstack_test_url=config.allow_fullstack_test_url,
    )
    return ManagedKorailSeatObserver(source=source, redis=redis)


def default_korail_execution_source(settings: Settings) -> ManagedKorailSeatObserver:
    """Create one source owned by the current Celery task event loop."""

    if not korail_background_monitoring_enabled(settings):
        raise RuntimeError("KORAIL background monitoring is not explicitly enabled")
    return _source_for_config(KorailExecutionSourceConfig.from_settings(settings))
