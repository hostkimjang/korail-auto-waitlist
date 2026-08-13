"""Normalize reservation terminal time after a wall-clock rollback."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0034_progress_terminal_time"
down_revision = "0033_observation_in_flight"
branch_labels = None
depends_on = None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if not isinstance(value, str):
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _progress_times(value: object) -> tuple[datetime, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, list):
        return ()
    parsed: list[datetime] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        timestamp = _parse_timestamp(item.get("occurred_at"))
        if timestamp is not None:
            parsed.append(timestamp)
    return tuple(parsed)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, started_at, finished_at, progress_stages FROM reservation_attempts "
            "WHERE finished_at IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        finished_at = _parse_timestamp(row["finished_at"])
        progress_times = _progress_times(row["progress_stages"])
        if finished_at is None or not progress_times:
            continue
        normalized = max(finished_at, *progress_times)
        if normalized <= finished_at:
            continue
        persisted_value: datetime | str = normalized
        if connection.dialect.name == "sqlite":
            # SQLite evaluates the timestamp-order CHECK lexically. Preserve the
            # delimiter convention used by the existing row so a valid later
            # instant cannot compare before started_at merely because " " < "T".
            started_at_raw = row["started_at"]
            separator = "T" if isinstance(started_at_raw, str) and "T" in started_at_raw else " "
            persisted_value = normalized.isoformat(sep=separator)
        connection.execute(
            sa.text("UPDATE reservation_attempts SET finished_at = :finished_at WHERE id = :id"),
            {"finished_at": persisted_value, "id": row["id"]},
        )


def downgrade() -> None:
    # Restoring an impossible terminal-before-progress timestamp would reintroduce
    # the API failure, so this data-only normalization is intentionally irreversible.
    pass
