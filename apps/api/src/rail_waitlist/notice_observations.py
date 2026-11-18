"""Bounded diagnostics for KORAIL dialogs the notice closer could not dismiss.

When strict and fallback notice handling both fail, the adapter stores one
structural summary (control and input categories, public-notice evidence, panel size)
plus closed rejection reasons in Redis so the settings screen can show what changed on
the official page. The summary never contains announcement body text, hrefs, cookies,
or credentials. It expires on its own, and the next completed official search clears it
so a resolved popup is not shown as a current failure.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

logger = logging.getLogger("rail_waitlist.notice_observations")

NOTICE_OBSERVATION_KEY = "rail-waitlist:notice-observation:latest"
NOTICE_OBSERVATION_TTL_SECONDS = 48 * 3600
NOTICE_RESOLVE_RETRY_SECONDS = 60
NOTICE_CLOSE_METHODS = ("strict_close_button", "fallback_close_button", "fallback_escape", "none")
NOTICE_OBSERVATION_STAGES = (
    "search_notice_unrecognized",
    "search_notice_persisted",
    "search_notice_action_limit",
    "search_notice_changed",
    "search_notice_close_unknown",
)
# Closed vocabulary produced by the browser-side judgement. Page text never becomes a reason.
NOTICE_REJECTION_REASONS = (
    "no_public_evidence",
    "no_close_control",
    "ambiguous_close_label",
    "close_unavailable",
    "reminder_label_conflict",
    "input_field",
    "form_submission",
    "embedded_frame",
    "nested_dialog",
)


def known_rejection_reasons(value: object) -> tuple[str, ...]:
    """Keep the closed reason vocabulary only, in first-seen order and without repeats."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(
        dict.fromkeys(
            item
            for item in value[:20]
            if isinstance(item, str) and item in NOTICE_REJECTION_REASONS
        )
    )


@dataclass(frozen=True, slots=True)
class UnrecognizedNoticeReport:
    stage: str
    close_method: str
    attempts: int
    shape_summary: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NoticeObservationRecord:
    observed_at: datetime
    stage: str
    close_method: str
    attempts: int
    shape_summary: str
    reasons: tuple[str, ...] = ()


class RedisNoticeObservationWriter:
    """Stores the latest unresolved dialog shape; a Redis outage never stops searches.

    The resolve method runs after a completed official search. Searches are serialized by
    the browser gate, so a success after a recorded failure means that popup no longer
    blocks the search page. One process deletes the key at most once per recorded episode.
    """

    def __init__(self, redis: Redis, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._redis = redis
        self._monotonic = monotonic
        # A restarted adapter cannot know whether an earlier process left a record.
        self._may_hold_record = True
        self._resolve_retry_at = 0.0

    def record(self, report: UnrecognizedNoticeReport) -> None:
        payload = json.dumps(
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "stage": report.stage,
                "close_method": report.close_method,
                "attempts": report.attempts,
                "shape_summary": report.shape_summary[:400],
                "reasons": list(known_rejection_reasons(report.reasons)),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._may_hold_record = True
        try:
            self._redis.set(NOTICE_OBSERVATION_KEY, payload, ex=NOTICE_OBSERVATION_TTL_SECONDS)
        except (RedisError, OSError):
            logger.warning("공지 관측 스냅샷 기록 실패 stage=%s", report.stage)

    def resolve(self) -> None:
        if not self._may_hold_record or self._monotonic() < self._resolve_retry_at:
            return
        try:
            self._redis.delete(NOTICE_OBSERVATION_KEY)
        except (RedisError, OSError):
            self._resolve_retry_at = self._monotonic() + NOTICE_RESOLVE_RETRY_SECONDS
            logger.warning("공지 관측 스냅샷 정리 실패")
            return
        self._may_hold_record = False


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
    reasons = parsed.get("reasons", [])
    if not isinstance(reasons, list):
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
        reasons=known_rejection_reasons(reasons),
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
    "NOTICE_REJECTION_REASONS",
    "NOTICE_RESOLVE_RETRY_SECONDS",
    "NoticeObservationRecord",
    "RedisNoticeObservationReader",
    "RedisNoticeObservationWriter",
    "UnrecognizedNoticeReport",
    "known_rejection_reasons",
]
