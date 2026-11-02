from __future__ import annotations

from datetime import timedelta
from enum import StrEnum


class ReservationReconciliationResolution(StrEnum):
    """Durable terminal resolution of an ambiguous reservation attempt."""

    CONFIRMED_ABSENT = "confirmed_absent"
    EXHAUSTED_UNRESOLVED = "exhausted_unresolved"


RESERVATION_RECONCILIATION_MAX_ATTEMPTS = 3
RESERVATION_RECONCILIATION_INTERVAL = timedelta(seconds=30)
UNKNOWN_RECONCILIATION_MAX_ATTEMPTS = 6
UNKNOWN_MANUAL_REARM_MIN_RECONCILIATIONS = 3
PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS = 6
# A reconciliation whose official read never happened (OFFICIAL_READ_UNAVAILABLE) keeps the
# evidence budget, so the budget-indexed schedules cannot pace it. Pace it by the attempt's
# age instead: each unreachable read waits about as long as the attempt has already been
# open, which roughly doubles the delay per read until the ceiling.
OFFICIAL_READ_UNAVAILABLE_MIN_RECONCILIATION_INTERVAL = RESERVATION_RECONCILIATION_INTERVAL
OFFICIAL_READ_UNAVAILABLE_MAX_RECONCILIATION_INTERVAL = timedelta(minutes=15)

_UNKNOWN_INCONCLUSIVE_RECONCILIATION_INTERVALS = {
    1: RESERVATION_RECONCILIATION_INTERVAL,
    2: RESERVATION_RECONCILIATION_INTERVAL,
    3: timedelta(minutes=5),
    4: timedelta(minutes=15),
    5: timedelta(minutes=60),
}

_PAYMENT_HOLD_RECONCILIATION_INTERVALS = {
    1: RESERVATION_RECONCILIATION_INTERVAL,
    2: RESERVATION_RECONCILIATION_INTERVAL,
    3: timedelta(minutes=2),
    4: timedelta(minutes=5),
    5: timedelta(minutes=10),
}


def unknown_reconciliation_retry_interval(completed_attempt_count: int) -> timedelta | None:
    """Return the bounded delay after one completed inconclusive UNKNOWN read."""

    return _UNKNOWN_INCONCLUSIVE_RECONCILIATION_INTERVALS.get(completed_attempt_count)


def payment_hold_reconciliation_retry_interval(
    completed_attempt_count: int,
) -> timedelta | None:
    """Return the bounded delay for checking whether an official hold was paid."""

    return _PAYMENT_HOLD_RECONCILIATION_INTERVALS.get(completed_attempt_count)


def official_read_unavailable_backoff(attempt_age: timedelta) -> timedelta:
    """Return the age-based delay after a read that never reached the official page."""

    return min(
        max(attempt_age, OFFICIAL_READ_UNAVAILABLE_MIN_RECONCILIATION_INTERVAL),
        OFFICIAL_READ_UNAVAILABLE_MAX_RECONCILIATION_INTERVAL,
    )


def official_read_unavailable_retry_interval(
    scheduled_interval: timedelta | None,
    *,
    completed_attempt_count: int,
    attempt_age: timedelta,
) -> timedelta | None:
    """Return the delay after an unreachable official read without spending the budget.

    Before any official answer was counted the budget schedule has no entry, so the
    age-based backoff alone schedules the next read; leaving it unscheduled would make the
    attempt look never reconciled and replay the read immediately. Once answers were
    counted, the longer of the budget schedule and the backoff wins, and an exhausted
    budget (no scheduled interval) stays unscheduled.
    """

    backoff = official_read_unavailable_backoff(attempt_age)
    if completed_attempt_count <= 0:
        return backoff if scheduled_interval is None else max(scheduled_interval, backoff)
    if scheduled_interval is None:
        return None
    return max(scheduled_interval, backoff)
