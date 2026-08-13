from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .timetable_management.schemas import SeatAvailabilityNotObservedReason

KORAIL_BROWSER_COOLDOWN_KEY = "korail-browser"
KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY = "korail-browser-outage"
SRT_COOLDOWN_KEY = "srt"


@dataclass(frozen=True)
class ProviderCooldown:
    reason: SeatAvailabilityNotObservedReason
    retry_after_seconds: int


class CooldownStore(Protocol):
    async def get(self, provider: str) -> ProviderCooldown | None: ...

    async def set(
        self,
        provider: str,
        reason: SeatAvailabilityNotObservedReason,
        duration_seconds: int,
    ) -> None: ...


class MemoryCooldownStore:
    def __init__(self, monotonic=time.monotonic) -> None:
        self._monotonic = monotonic
        self._records: dict[str, tuple[float, SeatAvailabilityNotObservedReason]] = {}

    async def get(self, provider: str) -> ProviderCooldown | None:
        record = self._records.get(provider)
        if record is None:
            return None
        expires_at, reason = record
        remaining = int(expires_at - self._monotonic())
        if remaining <= 0:
            self._records.pop(provider, None)
            return None
        return ProviderCooldown(reason=reason, retry_after_seconds=remaining)

    async def set(
        self,
        provider: str,
        reason: SeatAvailabilityNotObservedReason,
        duration_seconds: int,
    ) -> None:
        self._records[provider] = (self._monotonic() + duration_seconds, reason)


class RedisCooldownStore:
    """Redis-backed provider hold with an in-process fallback for Redis outages."""

    def __init__(self, redis: Redis, fallback: CooldownStore | None = None) -> None:
        self._redis = redis
        self._fallback = fallback or MemoryCooldownStore()

    @staticmethod
    def _key(provider: str) -> str:
        return f"rail-waitlist:seat-status:cooldown:{provider}"

    async def get(self, provider: str) -> ProviderCooldown | None:
        try:
            async with self._redis.pipeline(transaction=False) as pipeline:
                pipeline.get(self._key(provider))
                pipeline.ttl(self._key(provider))
                raw_reason, ttl = await pipeline.execute()
            if raw_reason is None or int(ttl) <= 0:
                return None
            reason = str(raw_reason)
            if reason not in {"provider_access_restricted", "source_unavailable"}:
                return None
            return ProviderCooldown(
                reason=reason,
                retry_after_seconds=int(ttl),
            )
        except RedisError:
            return await self._fallback.get(provider)

    async def set(
        self,
        provider: str,
        reason: SeatAvailabilityNotObservedReason,
        duration_seconds: int,
    ) -> None:
        await self._fallback.set(provider, reason, duration_seconds)
        try:
            await self._redis.set(self._key(provider), reason, ex=duration_seconds, nx=False)
        except RedisError:
            return
