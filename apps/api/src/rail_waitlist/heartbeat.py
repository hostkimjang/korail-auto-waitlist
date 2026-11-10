"""Durable worker/scheduler heartbeats so operations status never guesses liveness.

Worker and scheduler processes publish their own Redis-backed timestamps; the API
answers from that state and fail-closes to unknown when a heartbeat is missing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from celery.beat import PersistentScheduler
from celery.signals import task_postrun, task_prerun
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

from .config import get_settings

logger = logging.getLogger("rail_waitlist.heartbeat")

HEARTBEAT_SERVICES = ("worker", "scheduler")
HEARTBEAT_TTL_SECONDS = 120
HEARTBEAT_READ_TIMEOUT_SECONDS = 2.0
HEARTBEAT_FUTURE_TOLERANCE = timedelta(minutes=5)
_HEARTBEAT_KEYS = {
    "worker": "rail-waitlist:heartbeat:worker",
    "scheduler": "rail-waitlist:heartbeat:scheduler",
}


class RedisHeartbeatWriter:
    """Publishes one process's own liveness; a Redis outage must never stop tasks."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        return self._redis

    def mark(self, service: str, *, now: datetime | None = None) -> None:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            self._client().set(
                _HEARTBEAT_KEYS[service],
                timestamp.isoformat(),
                ex=HEARTBEAT_TTL_SECONDS,
            )
        except RedisError:
            logger.warning("heartbeat 기록 실패 service=%s", service)


@dataclass(frozen=True, slots=True)
class ServiceHeartbeats:
    worker: datetime | None = None
    scheduler: datetime | None = None


EMPTY_HEARTBEATS = ServiceHeartbeats()


def _parse_heartbeat(value: object, now: datetime) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    normalized = parsed.astimezone(UTC)
    if normalized > now + HEARTBEAT_FUTURE_TOLERANCE:
        return None
    return normalized


class RedisHeartbeatReader:
    """Reads heartbeat state for the summary route and fail-closes on any outage."""

    def __init__(self, redis: AsyncRedis) -> None:
        self._redis = redis

    async def read(self, *, now: datetime | None = None) -> ServiceHeartbeats:
        moment = now or datetime.now(UTC)
        try:
            values = await asyncio.wait_for(
                self._redis.mget([_HEARTBEAT_KEYS[service] for service in HEARTBEAT_SERVICES]),
                timeout=HEARTBEAT_READ_TIMEOUT_SECONDS,
            )
        except (RedisError, OSError, TimeoutError):
            return EMPTY_HEARTBEATS
        worker, scheduler = (_parse_heartbeat(value, moment) for value in (values[0], values[1]))
        return ServiceHeartbeats(worker=worker, scheduler=scheduler)


class HeartbeatPersistentScheduler(PersistentScheduler):
    """Celery beat scheduler that records one liveness timestamp per tick."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._heartbeat_writer = RedisHeartbeatWriter()

    def tick(self, *args: object, **kwargs: object) -> float:
        self._heartbeat_writer.mark("scheduler")
        return float(super().tick(*args, **kwargs))


_worker_heartbeat_writer = RedisHeartbeatWriter()


@task_prerun.connect  # type: ignore[untyped-decorator]
@task_postrun.connect  # type: ignore[untyped-decorator]
def _mark_worker_heartbeat(**_kwargs: object) -> None:
    _worker_heartbeat_writer.mark("worker")


__all__ = [
    "EMPTY_HEARTBEATS",
    "HEARTBEAT_SERVICES",
    "HEARTBEAT_TTL_SECONDS",
    "HeartbeatPersistentScheduler",
    "RedisHeartbeatReader",
    "RedisHeartbeatWriter",
    "ServiceHeartbeats",
]
