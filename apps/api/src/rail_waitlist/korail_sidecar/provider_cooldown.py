"""One credential-free KORAIL admission hold shared by search and authentication."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

type ProviderCooldownReason = Literal[
    "provider_unavailable", "provider_access_restricted", "cooldown_store_unavailable"
]

REASONS = frozenset(
    {"provider_unavailable", "provider_access_restricted", "cooldown_store_unavailable"}
)
KEY = "rail-waitlist:korail:provider-admission:v1"
# The last official credential submission lives under its own fixed key so the admission
# hash keeps its closed three-field schema. Redis is the restart-surviving owner.
LOGIN_SUBMISSION_KEY = "rail-waitlist:korail:login-submission:v1"
# Protection holds follow SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS. The floor matches the
# setting's own minimum so a caller can shorten the hold but never turn it into a
# tight retry loop against the provider's protection response.
DEFAULT_PROTECTION_SECONDS = 60
MINIMUM_PROTECTION_SECONDS = 60
# KORAIL scores login submissions by their spacing, and an adapter restart forgets the
# in-memory session. Only the adapter submits credentials, so it alone configures a floor
# (KORAIL_LOGIN_SUBMISSION_MIN_INTERVAL_SECONDS); 0 disables pacing for other owners.
MAXIMUM_LOGIN_SUBMISSION_INTERVAL_SECONDS = 7200


class ProviderCooldownDeferred(Exception):
    """The next provider action was deferred without a credential or submission verdict."""

    reason: ProviderCooldownReason
    retry_after_seconds: int

    def __init__(self, reason: ProviderCooldownReason, retry_after_seconds: int) -> None:
        if type(reason) is not str or reason not in REASONS:
            raise ValueError("invalid provider cooldown reason")
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 86400:
            raise ValueError("invalid provider cooldown interval")
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


class ProviderCooldown(Protocol):
    async def check(self) -> None: ...

    async def query_failed(self, duration_seconds: int = 300) -> None: ...

    async def login_failed(self, status: int | None, failure: str | None) -> None: ...

    async def protection_detected(self, duration_seconds: int | None = None) -> None: ...

    async def authentication_succeeded(self) -> None: ...

    async def admit_login_submission(self) -> None:
        """Defer a non-essential credential submission inside the configured interval."""
        ...

    async def record_login_submission(self) -> None:
        """Remember one official credential submission, whatever its verdict."""
        ...

    async def close(self) -> None: ...


class _RedisCommands(Protocol):
    """Typed boundary for redis-py's otherwise unannotated async command dispatcher."""

    def execute_command(self, *arguments: str | int) -> Awaitable[object]: ...

    async def aclose(self) -> None: ...


def _duration(seconds: int, minimum: int) -> int:
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise ValueError("invalid provider cooldown duration")
    return max(minimum, seconds)


def _protection_setting(seconds: int) -> int:
    if type(seconds) is not int or not MINIMUM_PROTECTION_SECONDS <= seconds <= 86400:
        raise ValueError("invalid provider protection cooldown setting")
    return seconds


def _login_interval_setting(seconds: int) -> int:
    if type(seconds) is not int or not 0 <= seconds <= MAXIMUM_LOGIN_SUBMISSION_INTERVAL_SECONDS:
        raise ValueError("invalid login submission interval setting")
    return seconds


class MemoryProviderCooldown:
    """Explicit test adapter; production runtime must supply the persistent Redis owner."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        protection_seconds: int = DEFAULT_PROTECTION_SECONDS,
        login_submission_min_interval_seconds: int = 0,
    ) -> None:
        self._clock = clock
        self._protection_seconds = _protection_setting(protection_seconds)
        self._login_interval_seconds = _login_interval_setting(
            login_submission_min_interval_seconds
        )
        self._deadline = 0.0
        self._reason: ProviderCooldownReason = "provider_unavailable"
        self._login_failures = 0
        self._last_login_submission: float | None = None
        self._lock = asyncio.Lock()

    def remaining(self) -> int:
        return min(86400, max(0, math.ceil(self._deadline - self._clock())))

    async def check(self) -> None:
        async with self._lock:
            remaining = self.remaining()
            if remaining:
                raise ProviderCooldownDeferred(self._reason, remaining)

    async def _open(self, reason: ProviderCooldownReason, seconds: int) -> None:
        deadline = self._clock() + seconds
        if deadline > self._deadline or (
            deadline == self._deadline and reason == "provider_access_restricted"
        ):
            self._deadline, self._reason = deadline, reason

    async def query_failed(self, duration_seconds: int = 300) -> None:
        async with self._lock:
            await self._open("provider_unavailable", _duration(duration_seconds, 300))

    async def login_failed(self, status: int | None, failure: str | None) -> None:
        if type(status) is not int or not 500 <= status <= 599 or failure != "http_error":
            return
        async with self._lock:
            self._login_failures = min(3, self._login_failures + 1)
            await self._open("provider_unavailable", (300, 600, 900)[self._login_failures - 1])

    def protection_duration(self, duration_seconds: int | None = None) -> int:
        """Resolve one protection hold from the configured default and the shared floor."""

        if duration_seconds is None:
            return self._protection_seconds
        return _duration(duration_seconds, MINIMUM_PROTECTION_SECONDS)

    async def protection_detected(self, duration_seconds: int | None = None) -> None:
        seconds = self.protection_duration(duration_seconds)
        async with self._lock:
            await self._open("provider_access_restricted", seconds)

    async def authentication_succeeded(self) -> None:
        # Only fresh official authentication/probe evidence resets the login sequence.
        # The current outage/protection hold survives authentication success.
        async with self._lock:
            self._login_failures = 0

    @property
    def login_submission_interval_seconds(self) -> int:
        return self._login_interval_seconds

    def login_submission_remaining(self) -> int:
        """Seconds left in the interval since this process's last submission."""

        last = self._last_login_submission
        if last is None or self._login_interval_seconds == 0:
            return 0
        remaining = math.ceil(last + self._login_interval_seconds - self._clock())
        return min(self._login_interval_seconds, max(0, remaining))

    def note_login_submission(self) -> None:
        self._last_login_submission = self._clock()

    async def admit_login_submission(self) -> None:
        remaining = self.login_submission_remaining()
        if remaining:
            # Pacing exists only to stay clear of KORAIL's protection scoring, so the
            # deferral reuses that closed reason instead of widening the API contract.
            raise ProviderCooldownDeferred("provider_access_restricted", remaining)

    async def record_login_submission(self) -> None:
        self.note_login_submission()

    async def close(self) -> None:
        return None


# Redis TIME provides a shared UTC deadline across processes and restarts. The hash
# keeps the bounded login sequence for a day; expiry of the hold does not reset it.
# All writers update the same fixed key and preserve a longer existing deadline.
_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local fields = redis.call('HGETALL', KEYS[1])
local reason = 'provider_unavailable'
local deadline = 0
local count = 0
if #fields > 0 then
  if #fields ~= 6 then return redis.error_reply('invalid admission state') end
  local r = redis.call('HGET', KEYS[1], 'reason')
  local d = tonumber(redis.call('HGET', KEYS[1], 'expires_at_ms'))
  local c = tonumber(redis.call('HGET', KEYS[1], 'login_failures'))
  if (r ~= 'provider_unavailable' and r ~= 'provider_access_restricted') or
     not d or d < 0 or d % 1 ~= 0 or not c or c < 0 or c > 3 or c % 1 ~= 0 or
     d > now + 86400000 then return redis.error_reply('invalid admission state') end
  reason, deadline, count = r, d, c
end
local op = ARGV[1]
local duration = tonumber(ARGV[2])
if op == 'login' then
  count = math.min(3, math.max(count + 1, tonumber(ARGV[3])))
  duration = ({300, 600, 900})[count]
elseif op == 'authenticated' then
  count = 0
elseif op == 'merge' then
  count = math.max(count, tonumber(ARGV[3]))
elseif op ~= 'read' and op ~= 'query' and op ~= 'protection' then
  return redis.error_reply('invalid admission operation')
end
if op == 'query' or op == 'login' or op == 'protection' or op == 'merge' then
  local candidate = now + duration * 1000
  local next_reason = op == 'protection' and 'provider_access_restricted' or 'provider_unavailable'
  if op == 'merge' then
    if ARGV[4] ~= 'provider_access_restricted' and ARGV[4] ~= 'provider_unavailable' then
      return redis.error_reply('invalid admission reason')
    end
    next_reason = ARGV[4]
  end
  if candidate > deadline or
     (candidate == deadline and next_reason == 'provider_access_restricted') then
    reason, deadline = next_reason, candidate
  end
end
if op ~= 'read' then
  redis.call('HSET', KEYS[1], 'reason', reason, 'expires_at_ms', deadline, 'login_failures', count)
  redis.call('PEXPIRE', KEYS[1], 86400000)
end
return {reason, math.max(0, math.ceil((deadline - now) / 1000)), count}
"""

# Returns elapsed milliseconds since the recorded submission, or -1 when none is known.
# A future timestamp (clock step) reads as zero elapsed, which keeps the floor closed.
_LOGIN_SUBMISSION_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
if ARGV[1] == 'record' then
  redis.call('HSET', KEYS[1], 'submitted_at_ms', now)
  redis.call('PEXPIRE', KEYS[1], 86400000)
  return 0
elseif ARGV[1] ~= 'read' then
  return redis.error_reply('invalid login submission operation')
end
local raw = redis.call('HGET', KEYS[1], 'submitted_at_ms')
if not raw then return -1 end
local last = tonumber(raw)
if not last or last < 0 or last % 1 ~= 0 then
  return redis.error_reply('invalid login submission state')
end
return math.max(0, now - last)
"""


class RedisProviderCooldown:
    """Persistent provider hold with fail-closed admission on any storage/schema error."""

    def __init__(
        self,
        redis: _RedisCommands,
        *,
        owns_client: bool = False,
        protection_seconds: int = DEFAULT_PROTECTION_SECONDS,
        login_submission_min_interval_seconds: int = 0,
    ) -> None:
        self._redis = redis
        self._owns_client = owns_client
        self._fallback = MemoryProviderCooldown(
            protection_seconds=protection_seconds,
            login_submission_min_interval_seconds=login_submission_min_interval_seconds,
        )
        self._store_failed = False
        self._lock = asyncio.Lock()

    async def _command(
        self, operation: str, seconds: int = 0
    ) -> tuple[ProviderCooldownReason, int, int]:
        row: object = await asyncio.wait_for(
            self._redis.execute_command(
                "EVAL",
                _SCRIPT,
                1,
                KEY,
                operation,
                str(seconds),
                str(self._fallback._login_failures),
                self._fallback._reason,
            ),
            timeout=3,
        )
        if type(row) is not list or len(row) != 3:
            raise ValueError("invalid provider cooldown record")
        reason = row[0].decode("ascii") if type(row[0]) is bytes else row[0]
        if (
            reason not in {"provider_unavailable", "provider_access_restricted"}
            or type(row[1]) is not int
            or not 0 <= row[1] <= 86400
            or type(row[2]) is not int
            or not 0 <= row[2] <= 3
        ):
            raise ValueError("invalid provider cooldown record")
        closed_reason: ProviderCooldownReason = (
            "provider_access_restricted"
            if reason == "provider_access_restricted"
            else "provider_unavailable"
        )
        return closed_reason, row[1], row[2]

    async def check(self) -> None:
        async with self._lock:
            try:
                if self._store_failed:
                    # Merge a known minimum; never repeat the ambiguous login
                    # increment. Admission waits until this write is confirmed.
                    await self._command("merge", self._fallback.remaining())
                reason, remaining, count = await self._command("read")
            except Exception:  # noqa: BLE001 -- storage and schema errors deny admission, redacted.
                raise ProviderCooldownDeferred(
                    "cooldown_store_unavailable", max(60, self._fallback.remaining())
                ) from None
            self._store_failed = False
            self._fallback._login_failures = count
            if remaining:
                raise ProviderCooldownDeferred(reason, remaining)

    async def _observe(self, operation: str, seconds: int = 0) -> None:
        try:
            _, _, count = await self._command(operation, seconds)
            self._fallback._login_failures = count
        except Exception:  # noqa: BLE001 -- retain the provider verdict; block the next admission.
            # Preserve the real provider failure at the caller. Storage uncertainty
            # prevents the *next* admission; it is not a fabricated skipped request.
            self._store_failed = True

    async def query_failed(self, duration_seconds: int = 300) -> None:
        seconds = _duration(duration_seconds, 300)
        async with self._lock:
            await self._fallback.query_failed(seconds)
            await self._observe("query", seconds)

    async def login_failed(self, status: int | None, failure: str | None) -> None:
        if type(status) is not int or not 500 <= status <= 599 or failure != "http_error":
            return
        async with self._lock:
            await self._fallback.login_failed(status, failure)
            await self._observe("login")

    async def protection_detected(self, duration_seconds: int | None = None) -> None:
        seconds = self._fallback.protection_duration(duration_seconds)
        async with self._lock:
            await self._fallback.protection_detected(seconds)
            await self._observe("protection", seconds)

    async def authentication_succeeded(self) -> None:
        async with self._lock:
            await self._fallback.authentication_succeeded()
            await self._observe("authenticated")

    async def _login_submission_command(self, operation: str) -> int:
        row: object = await asyncio.wait_for(
            self._redis.execute_command(
                "EVAL", _LOGIN_SUBMISSION_SCRIPT, 1, LOGIN_SUBMISSION_KEY, operation
            ),
            timeout=3,
        )
        if type(row) is not int or row < -1:
            raise ValueError("invalid login submission record")
        return row

    async def admit_login_submission(self) -> None:
        interval = self._fallback.login_submission_interval_seconds
        if interval == 0:
            return
        local_remaining = self._fallback.login_submission_remaining()
        try:
            elapsed_ms = await self._login_submission_command("read")
        except Exception:  # noqa: BLE001 -- an unknown last submission cannot admit a login.
            raise ProviderCooldownDeferred(
                "cooldown_store_unavailable", max(60, local_remaining)
            ) from None
        shared_remaining = (
            0 if elapsed_ms < 0 else max(0, math.ceil((interval * 1000 - elapsed_ms) / 1000))
        )
        remaining = min(interval, max(local_remaining, shared_remaining))
        if remaining:
            raise ProviderCooldownDeferred("provider_access_restricted", remaining)

    async def record_login_submission(self) -> None:
        # The submission already happened; storage failure must not hide it from the
        # caller. This process still paces itself from the local fallback timestamp.
        self._fallback.note_login_submission()
        try:
            await self._login_submission_command("record")
        except Exception:  # noqa: BLE001 -- best-effort ledger write, details redacted.
            return

    async def close(self) -> None:
        if self._owns_client:
            await self._redis.aclose()
