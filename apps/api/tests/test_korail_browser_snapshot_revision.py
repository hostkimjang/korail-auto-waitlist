from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from rail_waitlist.models import (
    KorailBrowserSeatSnapshot,
    KorailBrowserSnapshotBatch,
    OutboxEvent,
    ReservationAttempt,
    SeatObservation,
    WatchTransitionHistory,
)
from rail_waitlist.schemas import KORAIL_BROWSER_COMPANION_SOURCE


ENDPOINT = "/api/v1/korail-browser-snapshot-revision"


async def test_revision_requires_admin_session(public_client):
    response = await public_client.get(ENDPOINT)

    assert response.status_code == 401


async def test_revision_returns_only_latest_fresh_snapshot_without_side_effects(app, client):
    now = datetime.now(UTC).replace(microsecond=0)
    stale = now - timedelta(minutes=3)
    latest_fresh = now - timedelta(seconds=1)
    async with app.state.test_session_factory() as session:
        session.add_all(
            [
                KorailBrowserSnapshotBatch(
                    origin="대전",
                    destination="서울",
                    travel_date=now.date(),
                    passenger_count=1,
                    source=KORAIL_BROWSER_COMPANION_SOURCE,
                    observed_at=stale,
                    fresh_until=now - timedelta(seconds=1),
                    created_at=stale,
                ),
                KorailBrowserSnapshotBatch(
                    origin="대전",
                    destination="서울",
                    travel_date=now.date(),
                    passenger_count=1,
                    source=KORAIL_BROWSER_COMPANION_SOURCE,
                    observed_at=now - timedelta(seconds=2),
                    fresh_until=now + timedelta(minutes=1),
                    created_at=now - timedelta(seconds=2),
                ),
                KorailBrowserSnapshotBatch(
                    origin="대전",
                    destination="서울",
                    travel_date=now.date(),
                    passenger_count=1,
                    source=KORAIL_BROWSER_COMPANION_SOURCE,
                    observed_at=latest_fresh,
                    fresh_until=now + timedelta(minutes=1),
                    created_at=latest_fresh,
                ),
            ]
        )
        await session.commit()
        models = (
            KorailBrowserSnapshotBatch,
            KorailBrowserSeatSnapshot,
            SeatObservation,
            ReservationAttempt,
            WatchTransitionHistory,
            OutboxEvent,
        )
        counts_before = [
            await session.scalar(select(func.count()).select_from(model)) for model in models
        ]

    response = await client.get(ENDPOINT)

    async with app.state.test_session_factory() as session:
        counts_after = [
            await session.scalar(select(func.count()).select_from(model)) for model in models
        ]

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"revision": latest_fresh.isoformat().replace("+00:00", "Z")}
    assert counts_after == counts_before


async def test_revision_is_null_when_no_fresh_snapshot(client):
    response = await client.get(ENDPOINT)

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"revision": None}
