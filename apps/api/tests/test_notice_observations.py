"""Redis snapshot contract for unresolved official-dialog shapes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from rail_waitlist import notice_observations
from rail_waitlist.notice_observations import (
    NOTICE_OBSERVATION_KEY,
    NOTICE_OBSERVATION_TTL_SECONDS,
    NoticeObservationRecord,
    RedisNoticeObservationReader,
    RedisNoticeObservationWriter,
    UnrecognizedNoticeReport,
    _parse_record,
)


class FakeSyncRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail

    def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise notice_observations.RedisError("down")
        self.values[key] = value
        self.ttls[key] = ex


class FakeAsyncRedis:
    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.values = values or {}
        self.fail = fail

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise notice_observations.RedisError("down")
        return self.values.get(key)


def _report(**changes: object) -> UnrecognizedNoticeReport:
    fields: dict[str, object] = {
        "stage": "search_notice_unrecognized",
        "close_method": "fallback_close_button",
        "attempts": 1,
        "shape_summary": "div.korailPopup aria-modal=true 1 controls [창닫기] no risky tags",
    }
    fields.update(changes)
    return UnrecognizedNoticeReport(**fields)  # type: ignore[arg-type]


def test_writer_stores_bounded_structural_summary_with_ttl():
    redis = FakeSyncRedis()
    writer = RedisNoticeObservationWriter(redis)

    writer.record(_report(shape_summary="x" * 600))

    assert redis.ttls[NOTICE_OBSERVATION_KEY] == NOTICE_OBSERVATION_TTL_SECONDS
    stored = json.loads(redis.values[NOTICE_OBSERVATION_KEY])
    assert stored["stage"] == "search_notice_unrecognized"
    assert stored["close_method"] == "fallback_close_button"
    assert stored["attempts"] == 1
    assert stored["shape_summary"] == "x" * 400
    assert "observed_at" in stored


def test_writer_outage_never_raises_into_the_search_flow():
    writer = RedisNoticeObservationWriter(FakeSyncRedis(fail=True))
    writer.record(_report())


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": "brand_new_stage"},
        {"close_method": "magic_click"},
        {"attempts": 99},
        {"shape_summary": ""},
        {"observed_at": "not-a-date"},
        {"observed_at": "2026-11-11T00:00:00"},
    ],
)
def test_reader_fails_closed_for_invalid_snapshots(overrides):
    payload = {
        "observed_at": "2026-11-11T02:50:00+00:00",
        "stage": "search_notice_persisted",
        "close_method": "fallback_escape",
        "attempts": 2,
        "shape_summary": "dialog aria-modal=none 0 controls [] no risky tags",
    }
    payload.update(overrides)
    assert _parse_record(json.dumps(payload)) is None


async def test_reader_returns_the_stored_record_or_none():
    stored = json.dumps(
        {
            "observed_at": "2026-11-11T02:50:00+00:00",
            "stage": "search_notice_action_limit",
            "close_method": "strict_close_button",
            "attempts": 5,
            "shape_summary": "div.ReactModal__Content aria-modal=true 2 controls [a | b]"
            " risky:form",
        }
    )
    reader = RedisNoticeObservationReader(FakeAsyncRedis({NOTICE_OBSERVATION_KEY: stored}))

    record = await reader.read()

    assert record == NoticeObservationRecord(
        observed_at=datetime(2026, 11, 11, 2, 50, tzinfo=UTC),
        stage="search_notice_action_limit",
        close_method="strict_close_button",
        attempts=5,
        shape_summary="div.ReactModal__Content aria-modal=true 2 controls [a | b] risky:form",
    )
    missing = await RedisNoticeObservationReader(FakeAsyncRedis()).read()
    assert missing is None
    broken = await RedisNoticeObservationReader(FakeAsyncRedis(fail=True)).read()
    assert broken is None
