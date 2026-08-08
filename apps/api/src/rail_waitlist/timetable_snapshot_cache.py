from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .domain import Provider
from .timetable_management.schemas import TimetableItem

TIMETABLE_SNAPSHOT_TTL = timedelta(hours=24)
TIMETABLE_SNAPSHOT_MAX_ENTRIES = 128
TIMETABLE_SNAPSHOT_REFRESH_INTERVAL = timedelta(seconds=60)
TIMETABLE_SNAPSHOT_REFRESH_FAILURE_BACKOFF = timedelta(seconds=30)
TIMETABLE_SNAPSHOT_REFRESH_MAX_BACKOFF = timedelta(minutes=5)


@dataclass(frozen=True)
class TimetableSnapshotKey:
    provider: Provider
    origin: str
    destination: str
    departure_from: datetime
    departure_to: datetime
    passenger_count: int
    origin_node_id: str | None
    destination_node_id: str | None

    @classmethod
    def from_request(
        cls,
        *,
        provider: Provider,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
        origin_node_id: str | None,
        destination_node_id: str | None,
    ) -> TimetableSnapshotKey:
        return cls(
            provider=provider,
            origin=origin.strip(),
            destination=destination.strip(),
            departure_from=_canonical_datetime(departure_from),
            departure_to=_canonical_datetime(departure_to),
            passenger_count=passenger_count,
            origin_node_id=origin_node_id.strip() if origin_node_id is not None else None,
            destination_node_id=(
                destination_node_id.strip() if destination_node_id is not None else None
            ),
        )


@dataclass(frozen=True)
class _TimetableSnapshot:
    expires_at: datetime
    refresh_after: datetime
    refresh_failures: int
    items: list[TimetableItem]


class TimetableSnapshotCache:
    """Per-process snapshot cache; deployment requires exactly one API replica."""

    def __init__(
        self,
        *,
        max_entries: int = TIMETABLE_SNAPSHOT_MAX_ENTRIES,
        ttl: timedelta = TIMETABLE_SNAPSHOT_TTL,
        refresh_interval: timedelta = TIMETABLE_SNAPSHOT_REFRESH_INTERVAL,
        refresh_failure_backoff: timedelta = TIMETABLE_SNAPSHOT_REFRESH_FAILURE_BACKOFF,
        refresh_max_backoff: timedelta = TIMETABLE_SNAPSHOT_REFRESH_MAX_BACKOFF,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._ttl = ttl
        self._refresh_interval = refresh_interval
        self._refresh_failure_backoff = refresh_failure_backoff
        self._refresh_max_backoff = refresh_max_backoff
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: OrderedDict[TimetableSnapshotKey, _TimetableSnapshot] = OrderedDict()
        self._lock = asyncio.Lock()
        self._refresh_tasks: dict[TimetableSnapshotKey, asyncio.Task[None]] = {}

    async def store(self, key: TimetableSnapshotKey, items: list[TimetableItem]) -> None:
        now = _canonical_datetime(self._clock())
        async with self._lock:
            self._purge_expired(now)
            self._entries[key] = _TimetableSnapshot(
                expires_at=now + self._ttl,
                refresh_after=now + self._refresh_interval,
                refresh_failures=0,
                items=[item.model_copy(deep=True) for item in items],
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    async def get(self, key: TimetableSnapshotKey) -> list[TimetableItem] | None:
        now = _canonical_datetime(self._clock())
        async with self._lock:
            self._purge_expired(now)
            snapshot = self._entries.get(key)
            if snapshot is None:
                return None
            self._entries.move_to_end(key)
            return [item.model_copy(deep=True) for item in snapshot.items]

    async def refresh_if_due(
        self,
        key: TimetableSnapshotKey,
        loader: Callable[[], Awaitable[list[TimetableItem]]],
    ) -> bool:
        """Start at most one bounded background reload for an already-cached journey.

        Snapshot readers always receive the last successful result immediately.  A reload
        only becomes eligible after the local interval, and failed reloads exponentially
        defer the next attempt.  The loader remains responsible for provider-specific
        cooldown and singleflight policies.
        """
        now = _canonical_datetime(self._clock())
        async with self._lock:
            self._purge_expired(now)
            snapshot = self._entries.get(key)
            if snapshot is None or snapshot.refresh_after > now or key in self._refresh_tasks:
                return False
            self._refresh_tasks[key] = asyncio.create_task(self._refresh(key, loader))
            return True

    async def drain_pending_refreshes(self) -> None:
        """Wait for scheduled reloads before shutdown without exposing provider details."""
        while True:
            async with self._lock:
                tasks = tuple(self._refresh_tasks.values())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        async with self._lock:
            tasks = tuple(self._refresh_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh(
        self,
        key: TimetableSnapshotKey,
        loader: Callable[[], Awaitable[list[TimetableItem]]],
    ) -> None:
        try:
            items = await loader()
        except Exception:  # noqa: BLE001 - an isolated background loader must retain the last safe snapshot.
            await self._record_refresh_failure(key)
        else:
            await self.store(key, items)
        finally:
            async with self._lock:
                self._refresh_tasks.pop(key, None)

    async def _record_refresh_failure(self, key: TimetableSnapshotKey) -> None:
        now = _canonical_datetime(self._clock())
        async with self._lock:
            self._purge_expired(now)
            snapshot = self._entries.get(key)
            if snapshot is None:
                return
            failures = snapshot.refresh_failures + 1
            multiplier = 2 ** min(failures - 1, 16)
            delay = min(
                self._refresh_failure_backoff * multiplier,
                self._refresh_max_backoff,
            )
            self._entries[key] = _TimetableSnapshot(
                expires_at=snapshot.expires_at,
                refresh_after=now + delay,
                refresh_failures=failures,
                items=snapshot.items,
            )
            self._entries.move_to_end(key)

    def _purge_expired(self, now: datetime) -> None:
        expired_keys = [
            key for key, snapshot in self._entries.items() if snapshot.expires_at <= now
        ]
        for key in expired_keys:
            del self._entries[key]


def _canonical_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
