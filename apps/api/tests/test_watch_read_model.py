from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import Provider, WatchStatus
from rail_waitlist.models import Watch
from rail_waitlist.watch_management.read_model import watch_read

NOW = datetime(2026, 8, 11, 5, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "in_flight_until", "expected"),
    [
        (WatchStatus.WATCHING, NOW + timedelta(seconds=30), "in_progress"),
        (WatchStatus.WATCHING, NOW, "idle"),
        (WatchStatus.WATCHING, NOW - timedelta(seconds=1), "idle"),
        (WatchStatus.PAUSED, NOW + timedelta(seconds=30), "idle"),
    ],
)
async def test_watch_read_projects_only_an_active_unexpired_observation_claim_as_in_progress(
    db_engine,
    status: WatchStatus,
    in_flight_until: datetime,
    expected: str,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            destination="부산",
            travel_date=date(2026, 8, 12),
            time_from=time(8),
            time_to=time(12),
            status=status,
            mode="official",
            dedupe_key=f"read-observation-{status.value}-{expected}",
            next_check_at=NOW - timedelta(seconds=1),
            observation_in_flight_until=in_flight_until,
            candidates=[],
        )
        session.add(watch)
        await session.flush()

        projected = await watch_read(
            session,
            watch,
            latest_observations={},
            latest_reservation_attempts={},
            manual_rearm_ready_providers=frozenset(),
            read_at=NOW,
        )

    assert projected.observation_execution_state == expected
    assert projected.next_check_at == NOW - timedelta(seconds=1)
