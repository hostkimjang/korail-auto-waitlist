from __future__ import annotations

from rail_waitlist.seat_status_cooldown import RedisCooldownStore


class FakePipeline:
    def __init__(self, redis) -> None:
        self.redis = redis
        self.key = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get(self, key):
        self.key = key
        return self

    def ttl(self, key):
        self.key = key
        return self

    async def execute(self):
        return [self.redis.values.get(self.key), self.redis.ttls.get(self.key, -2)]


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self, transaction=False):
        return FakePipeline(self)

    async def set(self, key, value, *, ex, nx):
        self.values[key] = value
        self.ttls[key] = ex
        return True


async def test_redis_cooldown_is_visible_to_a_new_store_instance() -> None:
    redis = FakeRedis()
    first = RedisCooldownStore(redis)
    second = RedisCooldownStore(redis)

    await first.set("korail", "provider_access_restricted", 300)
    recovered = await second.get("korail")

    assert recovered is not None
    assert recovered.reason == "provider_access_restricted"
    assert recovered.retry_after_seconds == 300


async def test_unknown_redis_reason_is_ignored_fail_closed() -> None:
    redis = FakeRedis()
    redis.values["rail-waitlist:seat-status:cooldown:korail"] = "raw-provider-body"
    redis.ttls["rail-waitlist:seat-status:cooldown:korail"] = 30
    assert await RedisCooldownStore(redis).get("korail") is None
