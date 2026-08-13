from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, cast

from redis.asyncio import Redis

from ..provider_adapters.srt_seat_source import SrtLiveSeatSource
from ..seat_status_cooldown import CooldownStore, RedisCooldownStore
from .application import SrtProviderSource
from .ports import EnvironmentReader, RedisResource


class BoundedNumberReader(Protocol):
    def __call__(
        self,
        name: str,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float: ...


class RedisFromUrl(Protocol):
    def __call__(self, url: str, *, decode_responses: bool) -> Redis: ...


class CooldownStoreFactory(Protocol):
    def __call__(self, redis: Redis) -> CooldownStore: ...


class SrtSourceFactory(Protocol):
    def __call__(
        self,
        *,
        enabled: bool,
        cache_ttl_seconds: int,
        timeout_seconds: float,
        rate_limit_cooldown_seconds: int,
        protection_cooldown_seconds: int,
        cooldown_store: CooldownStore,
    ) -> SrtProviderSource: ...


@dataclass(frozen=True)
class SrtRuntimeDependencies:
    getenv: EnvironmentReader
    redis_from_url: RedisFromUrl
    cooldown_store_factory: CooldownStoreFactory
    source_factory: SrtSourceFactory


def bounded_number(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
    *,
    getenv: EnvironmentReader,
) -> float:
    try:
        value = float(getenv(name, str(default)) or "")
    except ValueError as error:
        raise RuntimeError(f"{name} must be numeric") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def build_default_source(
    *,
    dependencies: SrtRuntimeDependencies,
    number_reader: BoundedNumberReader,
) -> tuple[SrtProviderSource, RedisResource]:
    redis = dependencies.redis_from_url(
        dependencies.getenv("REDIS_URL", "redis://redis:6379/0") or "",
        decode_responses=True,
    )
    source = dependencies.source_factory(
        enabled=True,
        cache_ttl_seconds=int(number_reader("SRT_SEAT_STATUS_CACHE_TTL_SECONDS", 1, 1, 300)),
        timeout_seconds=number_reader("SRT_SEAT_STATUS_TIMEOUT_SECONDS", 25, 3, 30),
        rate_limit_cooldown_seconds=int(
            number_reader("SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS", 300, 60, 86400)
        ),
        protection_cooldown_seconds=int(
            number_reader("SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS", 60, 60, 86400)
        ),
        cooldown_store=dependencies.cooldown_store_factory(redis),
    )
    return source, redis


def default_runtime_dependencies(
    *,
    getenv: EnvironmentReader | None = None,
    redis_from_url: RedisFromUrl | None = None,
    cooldown_store_factory: CooldownStoreFactory | None = None,
    source_factory: SrtSourceFactory | None = None,
) -> SrtRuntimeDependencies:
    return SrtRuntimeDependencies(
        getenv=_getenv if getenv is None else getenv,
        redis_from_url=_redis_from_url if redis_from_url is None else redis_from_url,
        cooldown_store_factory=(
            _cooldown_store if cooldown_store_factory is None else cooldown_store_factory
        ),
        source_factory=_source if source_factory is None else source_factory,
    )


def _getenv(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def _redis_from_url(url: str, *, decode_responses: bool) -> Redis:
    return cast(Redis, Redis.from_url(url, decode_responses=decode_responses))


def _cooldown_store(redis: Redis) -> CooldownStore:
    return RedisCooldownStore(redis)


def _source(
    *,
    enabled: bool,
    cache_ttl_seconds: int,
    timeout_seconds: float,
    rate_limit_cooldown_seconds: int,
    protection_cooldown_seconds: int,
    cooldown_store: CooldownStore,
) -> SrtProviderSource:
    return SrtLiveSeatSource(
        enabled=enabled,
        cache_ttl_seconds=cache_ttl_seconds,
        timeout_seconds=timeout_seconds,
        rate_limit_cooldown_seconds=rate_limit_cooldown_seconds,
        protection_cooldown_seconds=protection_cooldown_seconds,
        cooldown_store=cooldown_store,
    )
