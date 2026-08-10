from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..watch_management.models import SeatObservation


async def latest_candidate_seat_detected_at(
    session: AsyncSession,
    candidate_id: str,
    *,
    attempt_started_at: datetime,
) -> datetime | None:
    """Return the latest persisted candidate observation that cannot postdate the attempt."""

    observed_at = await session.scalar(
        select(SeatObservation.observed_at)
        .where(
            SeatObservation.candidate_id == candidate_id,
            SeatObservation.observed_at <= attempt_started_at,
        )
        .order_by(SeatObservation.observed_at.desc(), SeatObservation.id.desc())
        .limit(1)
    )
    if observed_at is None:
        return None
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return observed_at.replace(tzinfo=UTC)
    return observed_at.astimezone(UTC)
