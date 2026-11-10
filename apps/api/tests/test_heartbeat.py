from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rail_waitlist import heartbeat
from rail_waitlist.heartbeat import (
    EMPTY_HEARTBEATS,
    HEARTBEAT_TTL_SECONDS,
    HeartbeatPersistentScheduler,
    RedisHeartbeatReader,
    RedisHeartbeatWriter,
    ServiceHeartbeats,
)
from rail_waitlist.operations import _service_heartbeat_state


class FakeSyncRedis:
    def __init__(self, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise heartbeat.RedisError("down")
        self.values[key] = value
        self.ttls[key] = ex


class FakeAsyncRedis:
    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.values = values or {}
        self.fail = fail

    async def mget(self, keys):
        if self.fail:
            raise heartbeat.RedisError("down")
        return [self.values.get(key) for key in keys]


def test_writer_stamps_both_services_with_bounded_ttl():
    redis = FakeSyncRedis()
    writer = RedisHeartbeatWriter(redis)
    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

    writer.mark("worker", now=now)
    writer.mark("scheduler", now=now)

    assert redis.values["rail-waitlist:heartbeat:worker"] == now.isoformat()
    assert redis.values["rail-waitlist:heartbeat:scheduler"] == now.isoformat()
    assert redis.ttls["rail-waitlist:heartbeat:worker"] == HEARTBEAT_TTL_SECONDS


def test_writer_never_raises_when_redis_is_down():
    writer = RedisHeartbeatWriter(FakeSyncRedis(fail=True))
    writer.mark("worker")


async def test_reader_round_trips_writer_output():
    redis = FakeSyncRedis()
    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    RedisHeartbeatWriter(redis).mark("worker", now=now)
    RedisHeartbeatWriter(redis).mark("scheduler", now=now + timedelta(seconds=1))

    async def async_mget(keys):
        return [redis.values.get(key) for key in keys]

    reader = RedisHeartbeatReader(FakeAsyncRedis())
    reader._redis.mget = async_mget  # type: ignore[method-assign]
    heartbeats = await reader.read(now=now + timedelta(seconds=2))

    assert heartbeats.worker == now
    assert heartbeats.scheduler == now + timedelta(seconds=1)


async def test_reader_fail_closes_on_redis_outage_and_bad_values():
    broken = await RedisHeartbeatReader(FakeAsyncRedis(fail=True)).read()
    assert broken == EMPTY_HEARTBEATS

    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    corrupted = FakeAsyncRedis(
        {
            "rail-waitlist:heartbeat:worker": "not-a-time",
            "rail-waitlist:heartbeat:scheduler": (now + timedelta(minutes=30)).isoformat(),
        }
    )
    heartbeats = await RedisHeartbeatReader(corrupted).read(now=now)
    assert heartbeats == EMPTY_HEARTBEATS


def test_heartbeat_state_maps_fresh_stale_and_missing():
    now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
    fresh = now - timedelta(seconds=30)
    stale = now - timedelta(seconds=HEARTBEAT_TTL_SECONDS + 1)

    healthy = _service_heartbeat_state("worker", ServiceHeartbeats(worker=fresh), now)
    assert (healthy.status, healthy.evidence, healthy.observed_at) == (
        "healthy",
        "redis_heartbeat",
        fresh,
    )

    delayed = _service_heartbeat_state("scheduler", ServiceHeartbeats(scheduler=stale), now)
    assert (delayed.status, delayed.evidence, delayed.observed_at) == (
        "stale",
        "redis_heartbeat_stale",
        stale,
    )

    missing = _service_heartbeat_state("worker", EMPTY_HEARTBEATS, now)
    assert (missing.status, missing.evidence, missing.observed_at) == (
        "unknown",
        "redis_heartbeat_missing",
        None,
    )


def test_scheduler_tick_marks_before_delegating(monkeypatch):
    scheduler = object.__new__(HeartbeatPersistentScheduler)
    marks: list[str] = []
    scheduler._heartbeat_writer = type(
        "Writer", (), {"mark": lambda _self, service: marks.append(service)}
    )()
    calls: list[str] = []
    monkeypatch.setattr(
        heartbeat.PersistentScheduler,
        "tick",
        lambda _self, *args, **kwargs: calls.append("tick") or 0.5,
    )

    assert scheduler.tick() == 0.5
    assert marks == ["scheduler"]
    assert calls == ["tick"]


def test_worker_task_signals_publish_worker_heartbeat(monkeypatch):
    marks: list[str] = []
    monkeypatch.setattr(
        heartbeat._worker_heartbeat_writer,
        "mark",
        lambda service, *, now=None: marks.append(service),
    )

    heartbeat._mark_worker_heartbeat(task=None, signal=None)

    assert marks == ["worker"]
