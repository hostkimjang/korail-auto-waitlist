from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from redis.asyncio import Redis

from .config import Settings
from .fullstack_srt_fixture import fullstack_srt_client_factory
from .schemas import SeatObservationRequest, SeatObservationResult
from .seat_status_cooldown import RedisCooldownStore
from .srt_provider_adapter import SrtProviderAdapterClient
from .srt_seat_source import SrtLiveSeatSource


class SrtSeatObserver(Protocol):
    async def observation_deferred_until(self) -> datetime | None: ...

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]: ...

    async def drain_pending_calls(self) -> None: ...


@dataclass
class ManagedSrtSeatObserver:
    """Own one source and Redis client for a single worker task event loop."""

    source: SrtLiveSeatSource
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

    async def aclose(self) -> None:
        await self.drain_pending_calls()
        await self.redis.aclose()


@dataclass(frozen=True)
class SrtExecutionSourceConfig:
    redis_url: str
    cache_ttl_seconds: int
    timeout_seconds: float
    rate_limit_cooldown_seconds: int
    protection_cooldown_seconds: int
    fixture_url: str | None

    @classmethod
    def from_settings(cls, settings: Settings) -> SrtExecutionSourceConfig:
        return cls(
            redis_url=settings.redis_url,
            cache_ttl_seconds=settings.srt_seat_status_cache_ttl_seconds,
            timeout_seconds=settings.srt_seat_status_timeout_seconds,
            rate_limit_cooldown_seconds=settings.seat_status_rate_limit_cooldown_seconds,
            protection_cooldown_seconds=settings.seat_status_protection_cooldown_seconds,
            fixture_url=settings.srt_fullstack_fixture_url,
        )


def srt_background_monitoring_enabled(settings: Settings) -> bool:
    """Require an explicit experimental opt-in in addition to request-time seat lookup."""

    return (
        settings.experimental_rail_enabled
        and settings.srt_seat_status_enabled
        and settings.srt_seat_monitoring_enabled
    )


def _source_for_config(config: SrtExecutionSourceConfig) -> ManagedSrtSeatObserver:
    redis = Redis.from_url(config.redis_url, decode_responses=True)
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=config.cache_ttl_seconds,
        timeout_seconds=config.timeout_seconds,
        rate_limit_cooldown_seconds=config.rate_limit_cooldown_seconds,
        protection_cooldown_seconds=config.protection_cooldown_seconds,
        cooldown_store=RedisCooldownStore(redis),
        **(
            {
                "client_factory": fullstack_srt_client_factory(config.fixture_url),
                "source_name": "fullstack-srt-fixture",
            }
            if config.fixture_url is not None
            else {}
        ),
    )
    return ManagedSrtSeatObserver(source=source, redis=redis)


def default_srt_execution_source(settings: Settings) -> SrtSeatObserver:
    """Create one source owned by the current Celery task event loop."""

    if not srt_background_monitoring_enabled(settings):
        raise RuntimeError("SRT background monitoring is not explicitly enabled")
    if settings.srt_provider_adapter_enabled:
        return SrtProviderAdapterClient(
            settings.srt_provider_adapter_url,
            settings.srt_provider_adapter_timeout_seconds,
            settings.srt_provider_adapter_token,
        )
    return _source_for_config(SrtExecutionSourceConfig.from_settings(settings))
