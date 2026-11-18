"""Redis snapshot contract for unresolved official-dialog shapes."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import get_args

import pytest

from rail_waitlist import notice_observations
from rail_waitlist.korail_sidecar.pydoll.search_notice import (
    FALLBACK_OBSERVE_SCRIPT,
    OBSERVE_NOTICE_SCRIPT,
)
from rail_waitlist.notice_observations import (
    NOTICE_OBSERVATION_KEY,
    NOTICE_OBSERVATION_TTL_SECONDS,
    NOTICE_REJECTION_REASONS,
    NOTICE_RESOLVE_RETRY_SECONDS,
    NoticeObservationRecord,
    RedisNoticeObservationReader,
    RedisNoticeObservationWriter,
    UnrecognizedNoticeReport,
    _parse_record,
)
from rail_waitlist.operation_summary.schemas import OperationUnresolvedNotice
from rail_waitlist.operations import _unresolved_notice


class FakeSyncRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail
        self.deletes = 0

    def set(self, key: str, value: str, *, ex: int) -> None:
        if self.fail:
            raise notice_observations.RedisError("down")
        self.values[key] = value
        self.ttls[key] = ex

    def delete(self, key: str) -> int:
        if self.fail:
            raise notice_observations.RedisError("down")
        self.deletes += 1
        return int(self.values.pop(key, None) is not None)


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


def test_writer_keeps_only_the_closed_rejection_reasons():
    redis = FakeSyncRedis()
    RedisNoticeObservationWriter(redis).record(
        _report(reasons=("input_field", "홍길동 회원번호", "input_field", "no_close_control"))
    )
    stored = json.loads(redis.values[NOTICE_OBSERVATION_KEY])
    assert stored["reasons"] == ["input_field", "no_close_control"]


def test_completed_search_clears_the_record_once_per_failure_episode():
    redis = FakeSyncRedis()
    writer = RedisNoticeObservationWriter(redis)
    # A record may be left by an earlier adapter process, so the first success clears it.
    redis.values[NOTICE_OBSERVATION_KEY] = "{}"
    writer.resolve()
    writer.resolve()
    assert NOTICE_OBSERVATION_KEY not in redis.values
    assert redis.deletes == 1

    writer.record(_report())
    writer.resolve()
    writer.resolve()
    assert NOTICE_OBSERVATION_KEY not in redis.values
    assert redis.deletes == 2


def test_resolve_outage_never_raises_and_retries_after_the_backoff():
    now = [0.0]
    redis = FakeSyncRedis(fail=True)
    writer = RedisNoticeObservationWriter(redis, monotonic=lambda: now[0])
    writer.resolve()

    redis.fail = False
    redis.values[NOTICE_OBSERVATION_KEY] = "{}"
    writer.resolve()
    assert NOTICE_OBSERVATION_KEY in redis.values

    now[0] = NOTICE_RESOLVE_RETRY_SECONDS + 1
    writer.resolve()
    assert NOTICE_OBSERVATION_KEY not in redis.values


def test_reader_keeps_known_reasons_and_accepts_records_without_them():
    payload = {
        "observed_at": "2026-11-17T07:14:18+00:00",
        "stage": "search_notice_unrecognized",
        "close_method": "fallback_close_button",
        "attempts": 0,
        "shape_summary": "div aria-modal=true dialog evidence=layer+reminder",
    }
    legacy = _parse_record(json.dumps(payload))
    assert legacy is not None
    assert legacy.reasons == ()
    current = _parse_record(
        json.dumps({**payload, "reasons": ["input_field", "unknown_reason", "input_field"]})
    )
    assert current is not None
    assert current.reasons == ("input_field",)
    assert _parse_record(json.dumps({**payload, "reasons": "input_field"})) is None


def test_reason_vocabulary_is_shared_by_browser_script_writer_and_api():
    emitted = set(
        re.findall(
            r"reasons\.push\('([a-z_]+)'\)|reasons: \['([a-z_]+)'\]",
            OBSERVE_NOTICE_SCRIPT + FALLBACK_OBSERVE_SCRIPT,
        )
    )
    assert {name for pair in emitted for name in pair if name} == set(NOTICE_REJECTION_REASONS)
    (literal,) = get_args(OperationUnresolvedNotice.model_fields["reasons"].annotation)
    assert get_args(literal) == NOTICE_REJECTION_REASONS


def test_operations_summary_projects_reasons_from_the_record():
    record = NoticeObservationRecord(
        observed_at=datetime(2026, 11, 17, 7, 14, tzinfo=UTC),
        stage="search_notice_unrecognized",
        close_method="fallback_close_button",
        attempts=0,
        shape_summary="div aria-modal=true dialog evidence=none",
        reasons=("no_public_evidence", "no_close_control"),
    )
    notice = _unresolved_notice(record)
    assert notice is not None
    assert notice.reasons == ["no_public_evidence", "no_close_control"]
