from __future__ import annotations

from datetime import timedelta

RESERVATION_RECONCILIATION_MAX_ATTEMPTS = 3
RESERVATION_RECONCILIATION_INTERVAL = timedelta(seconds=30)
UNKNOWN_RECONCILIATION_MAX_ATTEMPTS = 6

_UNKNOWN_INCONCLUSIVE_RECONCILIATION_INTERVALS = {
    1: RESERVATION_RECONCILIATION_INTERVAL,
    2: RESERVATION_RECONCILIATION_INTERVAL,
    3: timedelta(minutes=5),
    4: timedelta(minutes=15),
    5: timedelta(minutes=60),
}


def unknown_reconciliation_retry_interval(completed_attempt_count: int) -> timedelta | None:
    """Return the bounded delay after one completed inconclusive UNKNOWN read."""

    return _UNKNOWN_INCONCLUSIVE_RECONCILIATION_INTERVALS.get(completed_attempt_count)
