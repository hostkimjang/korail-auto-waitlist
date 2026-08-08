from __future__ import annotations

from datetime import datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

__all__ = ("select_browser_departure_from",)

KOREA = ZoneInfo("Asia/Seoul")


def select_browser_departure_from(
    local_from: datetime,
    local_to: datetime,
    *,
    now: datetime,
    timezone: ZoneInfo = KOREA,
) -> clock_time | None:
    """Choose the earliest hour KORAIL's current KST picker can select."""
    local_now = now.astimezone(timezone)
    if local_from.date() > local_now.date():
        return clock_time(0, 0)
    if local_from.date() < local_now.date() or local_to < local_now:
        return None
    current_hour = local_now.replace(minute=0, second=0, microsecond=0)
    effective_from = max(local_from, current_hour)
    if effective_from > local_to:
        return None
    return effective_from.time().replace(tzinfo=None)
