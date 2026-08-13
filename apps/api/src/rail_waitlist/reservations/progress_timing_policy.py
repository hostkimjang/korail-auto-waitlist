from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_reservation_terminal_time(
    terminal_at: datetime,
    progress_times: Iterable[datetime],
) -> datetime:
    """Keep a terminal timestamp at or after every accepted progress timestamp."""

    normalized = _aware_utc(terminal_at)
    for progress_at in progress_times:
        normalized = max(normalized, _aware_utc(progress_at))
    return normalized


def persisted_reservation_progress_times(value: object) -> tuple[datetime, ...]:
    """Read only valid timestamps from the JSON progress evidence boundary."""

    if not isinstance(value, (list, tuple)):
        return ()
    parsed: list[datetime] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        raw = item.get("occurred_at")
        if isinstance(raw, datetime):
            parsed.append(_aware_utc(raw))
            continue
        if not isinstance(raw, str):
            continue
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        parsed.append(_aware_utc(timestamp))
    return tuple(parsed)
