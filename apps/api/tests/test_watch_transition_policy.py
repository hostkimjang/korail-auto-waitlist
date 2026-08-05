from __future__ import annotations

from datetime import UTC, datetime
from itertools import product

import pytest

from rail_waitlist.domain import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    WatchStatus,
)
from rail_waitlist.watch_management.transition_policy import (
    AllowedWatchTransition,
    NextCheckPolicy,
    NoOpWatchTransition,
    RejectedWatchTransition,
    build_watch_transition_identity,
    decide_watch_transition,
)


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    product(WatchStatus, repeat=2),
)
def test_transition_decision_covers_the_complete_status_matrix(
    current_status: WatchStatus,
    target_status: WatchStatus,
) -> None:
    decision = decide_watch_transition(current_status, target_status)

    if current_status is target_status:
        assert decision == NoOpWatchTransition(status=current_status)
    elif target_status in ALLOWED_TRANSITIONS[current_status]:
        assert isinstance(decision, AllowedWatchTransition)
        assert decision.previous_status is current_status
        assert decision.target_status is target_status
    else:
        assert decision == RejectedWatchTransition(
            previous_status=current_status,
            target_status=target_status,
            detail=f"cannot transition {current_status.value} to {target_status.value}",
        )


def test_transition_matrix_is_complete_for_every_watch_status() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(WatchStatus)
    assert all(
        target in WatchStatus for targets in ALLOWED_TRANSITIONS.values() for target in targets
    )


@pytest.mark.parametrize("current_status", list(WatchStatus))
@pytest.mark.parametrize("target_status", list(WatchStatus))
def test_allowed_transition_field_mutation_policy(
    current_status: WatchStatus,
    target_status: WatchStatus,
) -> None:
    decision = decide_watch_transition(current_status, target_status)
    if not isinstance(decision, AllowedWatchTransition):
        return

    if target_status is WatchStatus.SCHEDULED:
        assert decision.next_check_policy is NextCheckPolicy.TRANSITION_AT_IF_SEAT_MONITORING
        assert decision.clear_cooldown is True
    elif target_status is WatchStatus.PAUSED or target_status in TERMINAL_STATUSES:
        assert decision.next_check_policy is NextCheckPolicy.CLEAR
        assert decision.clear_cooldown is False
    else:
        assert decision.next_check_policy is NextCheckPolicy.PRESERVE
        assert decision.clear_cooldown is False


@pytest.mark.parametrize(
    ("reason", "expected_reason"),
    [
        (None, "transition_to_seat_found"),
        ("", "transition_to_seat_found"),
        ("   ", "   "),
        ("x" * 161, "x" * 160),
    ],
)
def test_transition_identity_preserves_reason_and_event_key_contract(
    reason: str | None,
    expected_reason: str,
) -> None:
    transition = decide_watch_transition(WatchStatus.WATCHING, WatchStatus.SEAT_FOUND)
    assert isinstance(transition, AllowedWatchTransition)
    transition_at = datetime(2030, 8, 1, 3, 4, 5, 6789, tzinfo=UTC)

    identity = build_watch_transition_identity(
        transition,
        watch_id="watch-1",
        transition_at=transition_at,
        reason=reason,
    )

    token = "watching:seat_found:2030-08-01T03:04:05.006789+00:00"
    assert identity.transition_at is transition_at
    assert identity.reason == expected_reason
    assert identity.transition_token == token
    assert identity.status_event_dedupe_key == f"watch:watch-1:transition:{token}"
