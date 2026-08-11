from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin_auth.models import AdminAccount
from ..domain import WatchStatus
from ..policy import next_interval
from ..watch_management.models import SeatObservation, Watch, WatchCandidate


def _observation_fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def latest_observation_fingerprint(session: AsyncSession, watch: Watch) -> str | None:
    state_vector: list[tuple[str, str | None]] = []
    has_observation = False
    candidates = list(
        (
            await session.scalars(
                select(WatchCandidate)
                .where(WatchCandidate.watch_id == watch.id)
                .order_by(WatchCandidate.priority)
            )
        ).all()
    )
    for candidate in candidates:
        latest = await session.scalar(
            select(SeatObservation)
            .where(SeatObservation.candidate_id == candidate.id)
            .order_by(SeatObservation.observed_at.desc(), SeatObservation.id.desc())
            .limit(1)
        )
        status = latest.status.value if latest is not None else None
        has_observation = has_observation or latest is not None
        state_vector.append((candidate.id, status))
    if not has_observation:
        return None
    return _observation_fingerprint(state_vector)


async def finish_observation_cycle(
    session: AsyncSession,
    watch: Watch,
    previous_fingerprint: str | None,
    now: datetime,
) -> None:
    watch.observation_in_flight_until = None
    current_fingerprint = await latest_observation_fingerprint(session, watch)
    if previous_fingerprint is not None and current_fingerprint == previous_fingerprint:
        watch.unchanged_runs += 1
    else:
        watch.unchanged_runs = 0

    if watch.status not in {
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }:
        watch.next_check_at = None
        return
    candidates = list(
        (
            await session.scalars(
                select(WatchCandidate)
                .where(
                    WatchCandidate.watch_id == watch.id,
                    WatchCandidate.state.in_(["active", "observed", "seat_found"]),
                )
                .order_by(WatchCandidate.priority)
            )
        ).all()
    )
    departure_at = (
        candidates[0].departure_at
        if candidates
        else datetime.combine(
            watch.travel_date, watch.time_from, tzinfo=ZoneInfo("Asia/Seoul")
        ).astimezone(UTC)
    )
    if departure_at.tzinfo is None or departure_at.utcoffset() is None:
        departure_at = departure_at.replace(tzinfo=UTC)
    admin_preferences = await session.scalar(
        select(AdminAccount).where(AdminAccount.singleton_slot == 1)
    )
    observation_interval_seconds = (
        admin_preferences.observation_interval_seconds if admin_preferences is not None else 5
    )
    watch.next_check_at = now + next_interval(
        now,
        departure_at,
        watch.unchanged_runs,
        observation_interval_seconds=observation_interval_seconds,
    )
