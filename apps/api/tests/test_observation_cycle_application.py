from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import rail_waitlist.observations.cycle_application as cycle_module
from rail_waitlist.domain import Provider, SeatObservationStatus, WatchStatus
from rail_waitlist.models import (
    AdminAccount,
    SeatObservation,
    Watch,
    WatchCandidate,
)
from rail_waitlist.observations.cycle_application import (
    _observation_fingerprint,
    finish_observation_cycle,
    latest_observation_fingerprint,
)
from rail_waitlist.services import (
    finish_observation_cycle as compatibility_finish_observation_cycle,
)
from rail_waitlist.services import (
    latest_observation_fingerprint as compatibility_latest_observation_fingerprint,
)
from rail_waitlist.services import request_hash


def make_watch(*, status: WatchStatus = WatchStatus.WATCHING) -> Watch:
    return Watch(
        provider=Provider.MOCK,
        origin="서울",
        origin_node_id="N-SEOUL",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2026, 8, 5),
        time_from=time(12),
        time_to=time(18),
        train_numbers=["KTX-001"],
        notification_channel_ids=[],
        mode="official",
        status=status,
        dedupe_key=f"observation-cycle-{status.value}",
    )


def make_candidate(priority: int, *, state: str = "active") -> WatchCandidate:
    return WatchCandidate(
        train_number=f"KTX-{priority:03}",
        departure_at=datetime(2026, 8, 5, 3 + priority, tzinfo=UTC),
        seat_class="standard",
        priority=priority,
        state=state,
    )


def test_services_keeps_observation_cycle_compatibility_identities() -> None:
    assert compatibility_latest_observation_fingerprint is latest_observation_fingerprint
    assert compatibility_finish_observation_cycle is finish_observation_cycle


@pytest.mark.parametrize(
    "state_vector",
    [
        [("candidate-1", "SOLD_OUT")],
        [("candidate-2", None), ("candidate-1", "AVAILABLE")],
        [("한글-id", "WAITLIST_AVAILABLE")],
    ],
)
def test_observation_fingerprint_is_byte_compatible_with_legacy_request_hash(
    state_vector: list[tuple[str, str | None]],
) -> None:
    assert _observation_fingerprint(state_vector) == request_hash(state_vector)


async def test_latest_fingerprint_uses_priority_and_latest_status_not_timestamp(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    first_at = datetime(2026, 8, 5, 1, tzinfo=UTC)

    async with factory() as session:
        watch = make_watch()
        later_priority = make_candidate(2)
        earlier_priority = make_candidate(1)
        watch.candidates.extend([later_priority, earlier_priority])
        session.add(watch)
        await session.flush()

        assert await latest_observation_fingerprint(session, watch) is None

        session.add_all(
            [
                SeatObservation(
                    id="observation-priority-2",
                    candidate=later_priority,
                    status=SeatObservationStatus.AVAILABLE,
                    source="test",
                    observed_at=first_at,
                    fresh_until=first_at + timedelta(minutes=1),
                ),
                SeatObservation(
                    id="observation-priority-1",
                    candidate=earlier_priority,
                    status=SeatObservationStatus.SOLD_OUT,
                    source="test",
                    observed_at=first_at,
                    fresh_until=first_at + timedelta(minutes=1),
                ),
            ]
        )
        await session.flush()
        expected = _observation_fingerprint(
            [
                (earlier_priority.id, SeatObservationStatus.SOLD_OUT.value),
                (later_priority.id, SeatObservationStatus.AVAILABLE.value),
            ]
        )
        initial = await latest_observation_fingerprint(session, watch)

        session.add(
            SeatObservation(
                id="observation-same-status-later",
                candidate=earlier_priority,
                status=SeatObservationStatus.SOLD_OUT,
                source="test",
                observed_at=first_at + timedelta(seconds=1),
                fresh_until=first_at + timedelta(minutes=1),
            )
        )
        await session.flush()
        same_status = await latest_observation_fingerprint(session, watch)

        session.add(
            SeatObservation(
                id="observation-changed-status-latest",
                candidate=earlier_priority,
                status=SeatObservationStatus.LIMITED,
                source="test",
                observed_at=first_at + timedelta(seconds=2),
                fresh_until=first_at + timedelta(minutes=1),
            )
        )
        await session.flush()
        changed_status = await latest_observation_fingerprint(session, watch)

        assert initial == expected
        assert same_status == initial
        assert changed_status != initial


@pytest.mark.parametrize("interval_seconds", [None, 17])
async def test_finish_cycle_uses_default_or_admin_observation_interval(
    db_engine,
    interval_seconds: int | None,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 1, tzinfo=UTC)

    async with factory() as session:
        watch = make_watch()
        watch.candidates.append(make_candidate(1))
        session.add(watch)
        if interval_seconds is not None:
            session.add(
                AdminAccount(
                    username="cycle-admin",
                    password_hash="not-a-real-password-hash",
                    observation_interval_seconds=interval_seconds,
                )
            )
        await session.flush()

        await finish_observation_cycle(session, watch, None, now)

        assert watch.unchanged_runs == 0
        assert watch.next_check_at == now + timedelta(seconds=interval_seconds or 5)


async def test_candidate_less_cycle_uses_kst_service_departure_fallback(
    db_engine,
    monkeypatch,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 1, tzinfo=UTC)
    captured_departures: list[datetime] = []

    def capture_interval(
        _now: datetime,
        departure_at: datetime,
        _unchanged_runs: int,
        *,
        observation_interval_seconds: int,
    ) -> timedelta:
        captured_departures.append(departure_at)
        return timedelta(seconds=observation_interval_seconds)

    monkeypatch.setattr(cycle_module, "next_interval", capture_interval)

    async with factory() as session:
        watch = make_watch()
        watch.time_from = time(12, 30)
        session.add(watch)
        await session.flush()

        await finish_observation_cycle(session, watch, None, now)

        assert captured_departures == [datetime(2026, 8, 5, 3, 30, tzinfo=UTC)]


async def test_terminal_cycle_clears_next_check_without_owning_commit(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 1, tzinfo=UTC)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.EXPIRED)
        watch.next_check_at = now + timedelta(minutes=1)
        session.add(watch)
        await session.flush()

        await finish_observation_cycle(session, watch, None, now)

        assert watch.next_check_at is None


@pytest.mark.parametrize("commit_cycle", [True, False])
async def test_cycle_summary_shares_caller_commit_or_rollback(
    db_engine,
    commit_cycle: bool,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime(2026, 8, 5, 1, tzinfo=UTC)

    async with factory() as session:
        watch = make_watch()
        candidate = make_candidate(1)
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        session.add(
            SeatObservation(
                candidate=candidate,
                status=SeatObservationStatus.SOLD_OUT,
                source="test",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=1),
            )
        )
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        previous = await latest_observation_fingerprint(session, watch)
        await finish_observation_cycle(session, watch, previous, observed_at)
        if commit_cycle:
            await session.commit()
        else:
            await session.rollback()

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        if commit_cycle:
            assert watch.unchanged_runs == 1
            persisted_next_check = watch.next_check_at
            assert persisted_next_check is not None
            if persisted_next_check.tzinfo is None or persisted_next_check.utcoffset() is None:
                persisted_next_check = persisted_next_check.replace(tzinfo=UTC)
            assert persisted_next_check == observed_at + timedelta(seconds=5)
        else:
            assert watch.unchanged_runs == 0
            assert watch.next_check_at is None
