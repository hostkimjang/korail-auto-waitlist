"""Bounded diagnostics for KORAIL dialogs the notice closer could not dismiss.

When strict and fallback notice handling both fail, the adapter stores one
structural summary (dialog classes, control labels, risky elements) in Redis so
the settings screen can show what changed on the official page. The summary never
contains announcement body text, hrefs, cookies, or credentials, and it expires
on its own so a stale shape cannot be mistaken for the current page.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

logger = logging.getLogger("rail_waitlist.notice_observations")

NOTICE_OBSERVATION_KEY = "rail-waitlist:notice-observation:latest"
NOTICE_OBSERVATION_TTL_SECONDS = 48 * 3600
NOTICE_CLOSE_METHODS = ("strict_close_button", "fallback_close_button", "fallback_escape", "none")
NOTICE_OBSERVATION_STAGES = (
    "search_notice_unrecognized",
    "search_notice_persisted",
    "search_notice_action_limit",
    "search_notice_changed",
    "search_notice_close_unknown",
)


@dataclass(frozen=True, slots=True)
class UnrecognizedNoticeReport:
    stage: str
    close_method: str
    attempts: int
    shape_summary: str


@dataclass(frozen=True, slots=True)
class NoticeObservationRecord:
    observed_at: datetime
    stage: str
    close_method: str
    attempts: int
    shape_summary: str


class RedisNoticeObservationWriter:
    """Stores the latest unresolved dialog shape; a Redis outage never stops searches."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def record(self, report: UnrecognizedNoticeReport) -> None:
        payload = json.dumps(
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "stage": report.stage,
                "close_method": report.close_method,
                "attempts": report.attempts,
                "shape_summary": report.shape_summary[:400],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self._redis.set(NOTICE_OBSERVATION_KEY, payload, ex=NOTICE_OBSERVATION_TTL_SECONDS)
        except (RedisError, OSError):
            logger.warning("공지 관측 스냅샷 기록 실패 stage=%s", report.stage)


def _parse_record(value: object) -> NoticeObservationRecord | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    observed_at = parsed.get("observed_at")
    stage = parsed.get("stage")
    close_method = parsed.get("close_method")
    attempts = parsed.get("attempts")
    shape_summary = parsed.get("shape_summary")
    if (
        not isinstance(observed_at, str)
        or not isinstance(stage, str)
        or not isinstance(close_method, str)
    ):
        return None
    if not isinstance(shape_summary, str) or not 0 < len(shape_summary) <= 400:
        return None
    if stage not in NOTICE_OBSERVATION_STAGES or close_method not in NOTICE_CLOSE_METHODS:
        return None
    if type(attempts) is not int or not 0 <= attempts <= 10:
        return None
    try:
        moment = datetime.fromisoformat(observed_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return NoticeObservationRecord(
        observed_at=moment.astimezone(UTC),
        stage=stage,
        close_method=close_method,
        attempts=attempts,
        shape_summary=shape_summary,
    )


class RedisNoticeObservationReader:
    """Reads the unresolved-dialog snapshot for the summary route; absent means none."""

    def __init__(self, redis: AsyncRedis) -> None:
        self._redis = redis

    async def read(self) -> NoticeObservationRecord | None:
        try:
            value = await self._redis.get(NOTICE_OBSERVATION_KEY)
        except (RedisError, OSError, TimeoutError):
            return None
        return _parse_record(value)


__all__ = [
    "NOTICE_CLOSE_METHODS",
    "NOTICE_OBSERVATION_KEY",
    "NOTICE_OBSERVATION_STAGES",
    "NOTICE_OBSERVATION_TTL_SECONDS",
    "NoticeObservationRecord",
    "RedisNoticeObservationReader",
    "RedisNoticeObservationWriter",
    "UnrecognizedNoticeReport",
]
