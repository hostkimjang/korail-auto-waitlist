from __future__ import annotations

import asyncio

import pytest

from rail_waitlist.korail_sidecar.provider_cooldown import (
    KEY,
    LOGIN_SUBMISSION_KEY,
    MemoryProviderCooldown,
    ProviderCooldownDeferred,
    RedisProviderCooldown,
)
from rail_waitlist.korail_sidecar.runtime import build_provider_cooldown


def test_production_factory_requires_persistent_configuration(monkeypatch) -> None:
    monkeypatch.delenv("KORAIL_PROVIDER_COOLDOWN_REDIS_URL", raising=False)
    with pytest.raises(RuntimeError, match="configuration is required"):
        build_provider_cooldown()


@pytest.mark.parametrize("reason", ["unknown", None, 1, {}])
def test_deferred_rejects_unclosed_reason(reason) -> None:
    with pytest.raises(ValueError):
        ProviderCooldownDeferred(reason, 300)


@pytest.mark.parametrize("seconds", [True, 0, 86401, 1.0, "300"])
def test_deferred_rejects_non_strict_interval(seconds) -> None:
    with pytest.raises(ValueError):
        ProviderCooldownDeferred("provider_unavailable", seconds)


async def test_observed_login_sequence_only_grows_on_http_5xx_and_success_keeps_hold() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0])
    for duration in (300, 600, 900, 900):
        await hold.login_failed(500, "http_error")
        with pytest.raises(ProviderCooldownDeferred) as caught:
            await hold.check()
        assert caught.value.retry_after_seconds == duration
        now[0] += duration
    for status, failure in [(401, "http_error"), (None, "timeout"), (500, "network_error")]:
        await hold.login_failed(status, failure)
    await hold.check()
    await hold.query_failed()
    await hold.authentication_succeeded()
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.check()
    assert caught.value.retry_after_seconds == 300
    now[0] += 300
    await hold.login_failed(503, "http_error")
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.check()
    assert caught.value.retry_after_seconds == 300


async def test_protection_max_deadline_is_not_shortened_by_queries_or_login() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0])
    await hold.protection_detected(900)
    now[0] += 10
    await asyncio.gather(hold.query_failed(300), hold.login_failed(500, "http_error"))
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.check()
    assert (caught.value.reason, caught.value.retry_after_seconds) == (
        "provider_access_restricted",
        890,
    )
    now[0] += 890
    await hold.check()


async def test_protection_hold_uses_configured_seconds_without_a_fifteen_minute_floor() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0], protection_seconds=90)
    await hold.protection_detected()
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.check()
    assert (caught.value.reason, caught.value.retry_after_seconds) == (
        "provider_access_restricted",
        90,
    )
    now[0] += 90
    await hold.check()

    # An explicit caller interval may be shorter than the default but never below 60s.
    await hold.protection_detected(5)
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.check()
    assert caught.value.retry_after_seconds == 60


@pytest.mark.parametrize("seconds", [59, 86401, True, 60.0])
def test_protection_setting_rejects_out_of_range_values(seconds) -> None:
    with pytest.raises(ValueError):
        MemoryProviderCooldown(protection_seconds=seconds)


class _RedisFixture:
    """Shared closed-state transport fake; never opens a Redis connection."""

    def __init__(self) -> None:
        self.now = 1_700_000_000
        self.deadline = 0
        self.reason = "provider_unavailable"
        self.count = 0
        self.failed = False
        self.row = None
        self.operations = []
        self.closed = 0

    async def execute_command(
        self, command, script, number, key, operation, seconds, minimum_count, reason
    ):
        assert command == "EVAL"
        seconds, minimum_count = int(seconds), int(minimum_count)
        assert number == 1 and key == KEY
        assert "redis.call('TIME')" in script and "candidate > deadline" in script
        assert "redis.call('PEXPIRE', KEYS[1], 86400000)" in script
        self.operations.append(operation)
        if self.failed:
            raise ConnectionError("fixture transport unavailable")
        if self.row is not None:
            return self.row
        if operation == "login":
            self.count = min(3, max(self.count + 1, minimum_count))
            seconds = (300, 600, 900)[self.count - 1]
        elif operation == "authenticated":
            self.count = 0
        elif operation == "merge":
            self.count = max(self.count, minimum_count)
        if operation in {"login", "query", "protection", "merge"}:
            candidate = self.now + seconds
            if candidate > self.deadline:
                self.deadline = candidate
                self.reason = (
                    "provider_access_restricted"
                    if operation == "protection"
                    else "provider_unavailable"
                )
                if operation == "merge":
                    self.reason = reason
        return [self.reason, max(0, self.deadline - self.now), self.count]

    async def aclose(self):
        self.closed += 1


async def test_new_redis_owner_reads_persisted_hold_and_login_sequence() -> None:
    redis = _RedisFixture()
    await RedisProviderCooldown(redis).login_failed(500, "http_error")
    redis.now += 300
    recovered = RedisProviderCooldown(redis)
    await recovered.check()
    await recovered.login_failed(500, "http_error")
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await RedisProviderCooldown(redis).check()
    assert caught.value.retry_after_seconds == 600
    assert redis.operations == ["login", "read", "login", "read"]


async def test_redis_protection_hold_persists_the_configured_interval() -> None:
    redis = _RedisFixture()
    await RedisProviderCooldown(redis, protection_seconds=120).protection_detected()
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await RedisProviderCooldown(redis).check()
    assert (caught.value.reason, caught.value.retry_after_seconds) == (
        "provider_access_restricted",
        120,
    )


async def test_redis_read_failure_defers_without_observation_or_writes() -> None:
    redis = _RedisFixture()
    redis.failed = True
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await RedisProviderCooldown(redis).check()
    assert caught.value.reason == "cooldown_store_unavailable"
    assert redis.operations == ["read"]


async def test_login_submission_interval_paces_only_inside_the_window() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0], login_submission_min_interval_seconds=1800)
    await hold.admit_login_submission()
    await hold.record_login_submission()
    now[0] += 600
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.admit_login_submission()
    assert (caught.value.reason, caught.value.retry_after_seconds) == (
        "provider_access_restricted",
        1200,
    )
    # The pacing never opens the shared admission hold used by searches.
    await hold.check()
    now[0] += 1200
    await hold.admit_login_submission()


async def test_login_submission_interval_zero_never_defers() -> None:
    hold = MemoryProviderCooldown()
    await hold.record_login_submission()
    await hold.admit_login_submission()


@pytest.mark.parametrize("seconds", [-1, 7201, True, 1800.0])
def test_login_submission_interval_setting_rejects_out_of_range_values(seconds) -> None:
    with pytest.raises(ValueError):
        MemoryProviderCooldown(login_submission_min_interval_seconds=seconds)


class _LoginLedgerFixture:
    """Closed fake of the login-submission script; never opens a Redis connection."""

    def __init__(self) -> None:
        self.now_ms = 1_700_000_000_000
        self.submitted_at_ms: int | None = None
        self.failed = False
        self.row: object = None
        self.operations: list[str] = []

    async def execute_command(self, command, script, number, key, operation):
        assert command == "EVAL"
        assert number == 1 and key == LOGIN_SUBMISSION_KEY
        assert "redis.call('TIME')" in script and "submitted_at_ms" in script
        self.operations.append(operation)
        if self.failed:
            raise ConnectionError("fixture transport unavailable")
        if self.row is not None:
            return self.row
        if operation == "record":
            self.submitted_at_ms = self.now_ms
            return 0
        assert operation == "read"
        if self.submitted_at_ms is None:
            return -1
        return max(0, self.now_ms - self.submitted_at_ms)

    async def aclose(self) -> None:
        return None


async def test_redis_login_submission_survives_an_adapter_restart() -> None:
    redis = _LoginLedgerFixture()
    await RedisProviderCooldown(
        redis, login_submission_min_interval_seconds=1800
    ).record_login_submission()
    redis.now_ms += 1_770_500
    restarted = RedisProviderCooldown(redis, login_submission_min_interval_seconds=1800)
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await restarted.admit_login_submission()
    assert (caught.value.reason, caught.value.retry_after_seconds) == (
        "provider_access_restricted",
        30,
    )
    redis.now_ms += 29_500
    await restarted.admit_login_submission()
    assert redis.operations == ["record", "read", "read"]


async def test_redis_login_submission_without_history_admits() -> None:
    redis = _LoginLedgerFixture()
    await RedisProviderCooldown(
        redis, login_submission_min_interval_seconds=1800
    ).admit_login_submission()
    assert redis.operations == ["read"]


async def test_redis_login_submission_disabled_interval_skips_the_store() -> None:
    redis = _LoginLedgerFixture()
    redis.failed = True
    await RedisProviderCooldown(redis).admit_login_submission()
    assert redis.operations == []


@pytest.mark.parametrize("row", [None, -2, True, "0", [0]])
async def test_redis_login_submission_unknown_history_fails_closed(row) -> None:
    redis = _LoginLedgerFixture()
    if row is None:
        redis.failed = True
    else:
        redis.row = row
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await RedisProviderCooldown(
            redis, login_submission_min_interval_seconds=1800
        ).admit_login_submission()
    assert caught.value.reason == "cooldown_store_unavailable"
    assert caught.value.retry_after_seconds >= 60


async def test_redis_login_submission_record_failure_still_paces_this_process() -> None:
    redis = _LoginLedgerFixture()
    hold = RedisProviderCooldown(redis, login_submission_min_interval_seconds=1800)
    redis.failed = True
    await hold.record_login_submission()
    redis.failed = False
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.admit_login_submission()
    assert caught.value.reason == "provider_access_restricted"
    assert 1790 <= caught.value.retry_after_seconds <= 1800


def test_production_factory_reads_the_login_submission_interval(monkeypatch) -> None:
    monkeypatch.setenv("KORAIL_PROVIDER_COOLDOWN_REDIS_URL", "redis://fixture.invalid:6379/0")
    monkeypatch.delenv("KORAIL_LOGIN_SUBMISSION_MIN_INTERVAL_SECONDS", raising=False)
    assert build_provider_cooldown()._fallback.login_submission_interval_seconds == 1800
    monkeypatch.setenv("KORAIL_LOGIN_SUBMISSION_MIN_INTERVAL_SECONDS", "0")
    assert build_provider_cooldown()._fallback.login_submission_interval_seconds == 0
    monkeypatch.setenv("KORAIL_LOGIN_SUBMISSION_MIN_INTERVAL_SECONDS", "7201")
    with pytest.raises(RuntimeError, match="between 0 and 7200"):
        build_provider_cooldown()


async def test_failed_observation_write_preserves_real_failure_and_blocks_next_admission() -> None:
    redis = _RedisFixture()
    hold = RedisProviderCooldown(redis)
    redis.failed = True
    await hold.login_failed(500, "http_error")
    redis.failed = False
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await hold.check()
    assert caught.value.reason == "provider_unavailable"
    assert caught.value.retry_after_seconds >= 299
    assert redis.operations == ["login", "merge", "read"]
    assert redis.count == 1
    with pytest.raises(ProviderCooldownDeferred):
        await RedisProviderCooldown(redis).check()


async def test_redis_client_close_preserves_borrowed_owner() -> None:
    redis = _RedisFixture()
    await RedisProviderCooldown(redis).close()
    assert redis.closed == 0
    await RedisProviderCooldown(redis, owns_client=True).close()
    assert redis.closed == 1


@pytest.mark.parametrize(
    "row",
    [
        [],
        ["unknown", 300, 0],
        ["provider_unavailable", True, 0],
        ["provider_unavailable", 300, 4],
        ["provider_unavailable", 86401, 0],
    ],
)
async def test_redis_untrusted_record_fails_closed(row) -> None:
    redis = _RedisFixture()
    redis.row = row
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await RedisProviderCooldown(redis).check()
    assert caught.value.reason == "cooldown_store_unavailable"
