import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from rail_waitlist.domain import Provider
from rail_waitlist.providers import MockProviderAdapter
from rail_waitlist.timetable_snapshot_cache import (
    TimetableSnapshotCache,
    TimetableSnapshotKey,
)


def snapshot_key(train_offset: int = 0) -> TimetableSnapshotKey:
    departure = datetime(2026, 8, 1, 8 + train_offset, tzinfo=UTC)
    return TimetableSnapshotKey.from_request(
        provider=Provider.MOCK,
        origin="서울",
        destination="부산",
        departure_from=departure,
        departure_to=departure + timedelta(hours=4),
        passenger_count=1,
        origin_node_id="N-SEOUL",
        destination_node_id="N-BUSAN",
    )


@pytest.mark.asyncio
async def test_snapshot_cache_returns_pydantic_deep_copies():
    cache = TimetableSnapshotCache()
    items = await MockProviderAdapter().timetable(
        "서울",
        "부산",
        datetime(2026, 8, 1, 8, tzinfo=UTC),
        departure_to=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    key = snapshot_key()
    await cache.store(key, items)

    first = await cache.get(key)
    assert first is not None
    first[0].train_number = "MUTATED"

    second = await cache.get(key)
    assert second is not None
    assert second[0].train_number != "MUTATED"


@pytest.mark.asyncio
async def test_snapshot_cache_evicts_old_entries_and_expires_them():
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = TimetableSnapshotCache(
        max_entries=2,
        ttl=timedelta(hours=24),
        clock=lambda: now,
    )
    items = await MockProviderAdapter().timetable(
        "서울",
        "부산",
        datetime(2026, 8, 1, 8, tzinfo=UTC),
        departure_to=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    first, second, third = snapshot_key(0), snapshot_key(1), snapshot_key(2)
    await cache.store(first, items)
    await cache.store(second, items)
    await cache.store(third, items)

    assert await cache.get(first) is None
    assert await cache.get(second) is not None

    now += timedelta(hours=24)
    assert await cache.get(second) is None
    assert await cache.get(third) is None


@pytest.mark.asyncio
async def test_snapshot_refresh_singleflights_and_keeps_serving_last_successful_items():
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = TimetableSnapshotCache(
        refresh_interval=timedelta(seconds=5),
        clock=lambda: now,
    )
    original = await MockProviderAdapter().timetable(
        "서울",
        "부산",
        datetime(2026, 8, 1, 8, tzinfo=UTC),
        departure_to=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    refreshed = [item.model_copy(update={"train_number": "REFRESHED"}) for item in original]
    key = snapshot_key()
    await cache.store(key, original)
    now += timedelta(seconds=5)
    gate = asyncio.Event()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await gate.wait()
        return refreshed

    assert await cache.refresh_if_due(key, loader) is True
    assert await cache.refresh_if_due(key, loader) is False
    cached_during_refresh = await cache.get(key)
    assert cached_during_refresh is not None
    assert cached_during_refresh[0].train_number != "REFRESHED"

    gate.set()
    await cache.drain_pending_refreshes()
    cached_after_refresh = await cache.get(key)
    assert cached_after_refresh is not None
    assert calls == 1
    assert cached_after_refresh[0].train_number == "REFRESHED"


@pytest.mark.asyncio
async def test_snapshot_refresh_failures_back_off_without_discarding_last_success():
    now = datetime(2026, 8, 1, tzinfo=UTC)
    cache = TimetableSnapshotCache(
        refresh_interval=timedelta(seconds=5),
        refresh_failure_backoff=timedelta(seconds=10),
        refresh_max_backoff=timedelta(seconds=30),
        clock=lambda: now,
    )
    items = await MockProviderAdapter().timetable(
        "서울",
        "부산",
        datetime(2026, 8, 1, 8, tzinfo=UTC),
        departure_to=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    key = snapshot_key()
    await cache.store(key, items)
    now += timedelta(seconds=5)

    async def failing_loader():
        raise RuntimeError("upstream unavailable")

    assert await cache.refresh_if_due(key, failing_loader) is True
    await cache.drain_pending_refreshes()
    assert await cache.refresh_if_due(key, failing_loader) is False
    cached = await cache.get(key)
    assert cached is not None
    assert cached[0].train_number == items[0].train_number

    now += timedelta(seconds=10)
    assert await cache.refresh_if_due(key, failing_loader) is True
    await cache.drain_pending_refreshes()
