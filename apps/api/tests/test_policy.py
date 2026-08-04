from datetime import date, datetime, time, timedelta, timezone

import pytest

from rail_waitlist.domain import Provider, SeatObservationMode, WatchStatus
from rail_waitlist.policy import build_watch_dedupe_key, classify_provider_failure, next_interval


@pytest.mark.parametrize("target", [1, 5, 600])
def test_next_interval_uses_one_global_exact_cadence(target: int) -> None:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    actual = next_interval(
        now,
        now + timedelta(days=30),
        unchanged_runs=99,
        observation_mode=SeatObservationMode.FOCUSED,
        focused_interval_seconds=20,
        balanced_interval_seconds=120,
        observation_interval_seconds=target,
    )

    assert actual == timedelta(seconds=target)


@pytest.mark.parametrize(("target", "expected"), [(0, 1), (601, 600)])
def test_next_interval_defensively_clamps_global_cadence(target: int, expected: int) -> None:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert next_interval(
        now, now + timedelta(hours=1), observation_interval_seconds=target
    ) == timedelta(seconds=expected)


def test_dedupe_key_is_order_independent_for_train_numbers():
    args = (Provider.KORAIL, "서울", "부산", date(2026, 8, 1), time(8), time(12), "standard", 1)
    assert build_watch_dedupe_key(*args, ["002", "001"]) == build_watch_dedupe_key(
        *args, ["001", "002"]
    )


def test_dedupe_key_preserves_station_node_identity():
    args = (
        Provider.KORAIL,
        "서울",
        "부산",
        date(2026, 8, 1),
        time(8),
        time(12),
        "standard",
        1,
        ["001"],
    )
    assert build_watch_dedupe_key(*args, "N-SEOUL", "N-BUSAN") != build_watch_dedupe_key(
        *args, "N-SEOUL-OTHER", "N-BUSAN"
    )
    assert build_watch_dedupe_key(*args, " N-SEOUL ", "N-BUSAN") == build_watch_dedupe_key(
        *args, "N-SEOUL", "N-BUSAN"
    )


def test_429_and_challenge_policy():
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    limited = classify_provider_failure(429, now)
    assert limited.status == WatchStatus.COOLDOWN
    assert limited.cooldown_seconds == 1800
    assert limited.requires_manual_resume is False
    assert limited.official_handoff_required is False

    for signal in (
        -8002,
        "CODE -8002",
        -8003,
        "CODE -8003",
        "macro_err1",
        403,
        "captcha",
        "NetFunnel",
        "abnormal access",
        "automation_detected",
    ):
        challenge = classify_provider_failure(signal, now)
        assert challenge.status == WatchStatus.AUTH_REQUIRED
        assert challenge.cooldown_seconds == 300
        assert challenge.requires_manual_resume is True
        assert challenge.official_handoff_required is True
        assert challenge.reason == "provider_block_or_challenge"


def test_authentication_and_unknown_failure_do_not_claim_official_handoff():
    auth = classify_provider_failure("login_failed")
    assert auth.status == WatchStatus.AUTH_REQUIRED
    assert auth.requires_manual_resume is True
    assert auth.official_handoff_required is False

    unknown = classify_provider_failure("unexpected_response")
    assert unknown.status == WatchStatus.FAILED
    assert unknown.requires_manual_resume is True
    assert unknown.official_handoff_required is False
