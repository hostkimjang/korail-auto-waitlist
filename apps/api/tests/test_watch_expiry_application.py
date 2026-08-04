from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import (
    BookingWindowStatus,
    OperationalStatus,
    Provider,
    ReservationOutcome,
    SeatClass,
    WatchStatus,
)
from rail_waitlist.models import (
    OutboxEvent,
    ReservationAttempt,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.services import apply_watch_transition
from rail_waitlist.watch_management.expiry_application import (
    WatchExpiryDependencies,
    _locked_expirable_watch_query,
    expire_elapsed_watches,
)

KST = timezone(timedelta(hours=9))


def _watch(
    *,
    identifier: str | None = None,
    dedupe_key: str,
    travel_date: date,
    time_from: time = time(8),
    time_to: time = time(12),
    status: WatchStatus = WatchStatus.WATCHING,
) -> Watch:
    return Watch(
        id=identifier,
        provider=Provider.MOCK,
        origin="서울",
        destination="부산",
        travel_date=travel_date,
        time_from=time_from,
        time_to=time_to,
        status=status,
        mode="mock",
        dedupe_key=dedupe_key,
    )


def _candidate(
    *,
    train_number: str,
    departure_at: datetime,
    priority: int,
    state: str = "active",
    **operational_fields,
) -> WatchCandidate:
    return WatchCandidate(
        train_number=train_number,
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=2),
        seat_class=SeatClass.STANDARD,
        priority=priority,
        state=state,
        **operational_fields,
    )


def _dependencies(apply_transition=apply_watch_transition) -> WatchExpiryDependencies:
    return WatchExpiryDependencies(apply_watch_transition=apply_transition)


async def test_expiry_processes_and_locks_watches_in_id_order_with_atomic_events(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
    first_id = "00000000-0000-0000-0000-000000000001"
    second_id = "00000000-0000-0000-0000-000000000002"
    processed_ids: list[str] = []

    async def recording_transition(session, watch, target, *, reason=None):
        processed_ids.append(watch.id)
        return await apply_watch_transition(session, watch, target, reason=reason)

    async with factory() as session:
        session.add_all(
            [
                _watch(
                    identifier=second_id,
                    dedupe_key="expiry-order-second",
                    travel_date=date(2000, 1, 1),
                ),
                _watch(
                    identifier=first_id,
                    dedupe_key="expiry-order-first",
                    travel_date=date(2000, 1, 1),
                ),
            ]
        )
        await session.commit()

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(recording_transition),
            )
            == 2
        )

    assert processed_ids == [first_id, second_id]
    async with factory() as session:
        histories = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory).order_by(WatchTransitionHistory.watch_id)
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.event_type == "watch.status_changed")
                    .order_by(OutboxEvent.aggregate_id)
                )
            ).all()
        )

    assert [history.watch_id for history in histories] == [first_id, second_id]
    assert [history.to_status for history in histories] == [
        WatchStatus.EXPIRED,
        WatchStatus.EXPIRED,
    ]
    assert [event.aggregate_id for event in events] == [first_id, second_id]


def test_expiry_lock_query_uses_for_update() -> None:
    compiled = str(_locked_expirable_watch_query("watch-id").compile(dialect=postgresql.dialect()))

    assert compiled.rstrip().endswith("FOR UPDATE")


@pytest.mark.parametrize("with_stale_attempt", [False, True])
async def test_partial_candidate_expiry_commits_independently_from_stale_recovery(
    db_engine,
    with_stale_attempt: bool,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
    watch = _watch(
        dedupe_key=f"partial-expiry-{with_stale_attempt}",
        travel_date=now.astimezone(KST).date(),
    )
    terminal_candidate = _candidate(
        train_number="TERMINAL",
        departure_at=now - timedelta(minutes=16),
        priority=1,
    )
    active_candidate = _candidate(
        train_number="ACTIVE",
        departure_at=now + timedelta(hours=2),
        priority=2,
    )
    watch.candidates.extend([terminal_candidate, active_candidate])

    async with factory() as session:
        session.add(watch)
        if with_stale_attempt:
            session.add(
                ReservationAttempt(
                    candidate=terminal_candidate,
                    attempt_sequence=1,
                    episode_key="stale-partial-expiry",
                    idempotency_key="stale-partial-expiry",
                    started_at=now - timedelta(minutes=6),
                    outcome=ReservationOutcome.PENDING,
                )
            )
        await session.commit()
        watch_id = watch.id
        terminal_id = terminal_candidate.id
        active_id = active_candidate.id

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(),
            )
            == 0
        )

    async with factory() as session:
        persisted_watch = await session.get(Watch, watch_id)
        persisted_terminal = await session.get(WatchCandidate, terminal_id)
        persisted_active = await session.get(WatchCandidate, active_id)
        outbox_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == watch_id)
        )
        attempts = await session.scalar(select(func.count()).select_from(ReservationAttempt))

    assert persisted_watch.status is WatchStatus.WATCHING
    assert persisted_terminal.state == "expired"
    assert persisted_active.state == "active"
    assert attempts == int(with_stale_attempt)
    assert outbox_events == 0


async def test_expiry_rolls_back_candidates_transition_history_and_outbox_on_failure(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
    watch = _watch(
        dedupe_key="expiry-rollback",
        travel_date=now.astimezone(KST).date(),
    )
    candidate = _candidate(
        train_number="ROLLBACK",
        departure_at=now - timedelta(minutes=16),
        priority=1,
    )
    watch.candidates.append(candidate)

    async with factory() as session:
        session.add(watch)
        await session.commit()
        watch_id = watch.id
        candidate_id = candidate.id

    async def failing_transition(session, locked_watch, target, *, reason=None):
        await apply_watch_transition(session, locked_watch, target, reason=reason)
        raise RuntimeError("forced transition failure")

    async with factory() as session:
        with pytest.raises(RuntimeError, match="forced transition failure"):
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(failing_transition),
            )

    async with factory() as session:
        persisted_watch = await session.get(Watch, watch_id)
        persisted_candidate = await session.get(WatchCandidate, candidate_id)
        history_count = await session.scalar(
            select(func.count()).select_from(WatchTransitionHistory)
        )
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxEvent))

    assert persisted_watch.status is WatchStatus.WATCHING
    assert persisted_candidate.state == "active"
    assert history_count == 0
    assert outbox_count == 0


async def test_candidate_less_expiry_uses_kst_cross_midnight_deadline(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 2, 15, tzinfo=timezone.utc)
    service_date = date(2026, 8, 2)
    elapsed = _watch(
        dedupe_key="elapsed-kst-window",
        travel_date=service_date,
        time_from=time(22),
        time_to=time(23, 59),
    )
    crossing = _watch(
        dedupe_key="active-cross-midnight-window",
        travel_date=service_date,
        time_from=time(23, 45),
        time_to=time(0, 30),
    )

    async with factory() as session:
        session.add_all([elapsed, crossing])
        await session.commit()
        elapsed_id = elapsed.id
        crossing_id = crossing.id

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(),
            )
            == 1
        )

    async with factory() as session:
        persisted_elapsed = await session.get(Watch, elapsed_id)
        persisted_crossing = await session.get(Watch, crossing_id)

    assert persisted_elapsed.status is WatchStatus.EXPIRED
    assert persisted_crossing.status is WatchStatus.WATCHING


async def test_unknown_operational_horizon_expires_watch_with_reason(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
    watch = _watch(
        dedupe_key="unknown-horizon",
        travel_date=now.astimezone(KST).date(),
        status=WatchStatus.SEAT_FOUND,
    )
    watch.candidates.append(
        _candidate(
            train_number="UNKNOWN-HORIZON",
            departure_at=now - timedelta(minutes=15, seconds=1),
            priority=1,
            state="seat_found",
        )
    )

    async with factory() as session:
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(),
            )
            == 1
        )

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)
        history = await session.scalar(
            select(WatchTransitionHistory).where(WatchTransitionHistory.watch_id == watch_id)
        )

    assert persisted.status is WatchStatus.EXPIRED
    assert history.reason == "all_candidates_operationally_terminal_or_horizon_elapsed"


async def test_fresh_delay_and_open_window_preserve_elapsed_candidate(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 5, 3, tzinfo=timezone.utc)
    scheduled_departure = now - timedelta(hours=7)
    watch = _watch(
        dedupe_key="fresh-delay-preserves-watch",
        travel_date=scheduled_departure.astimezone(KST).date(),
        status=WatchStatus.SEAT_FOUND,
    )
    candidate = _candidate(
        train_number="DELAYED",
        departure_at=scheduled_departure,
        priority=1,
        state="seat_found",
        scheduled_departure_at=scheduled_departure,
        operational_status=OperationalStatus.DELAYED,
        booking_window_status=BookingWindowStatus.OPEN,
        delay_minutes=90,
        operational_source="operational-test",
        operational_observed_at=now - timedelta(minutes=1),
        operational_fresh_until=now + timedelta(minutes=5),
    )
    watch.candidates.append(candidate)

    async with factory() as session:
        session.add(watch)
        await session.commit()
        watch_id = watch.id
        candidate_id = candidate.id

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(),
            )
            == 0
        )

    async with factory() as session:
        persisted_watch = await session.get(Watch, watch_id)
        persisted_candidate = await session.get(WatchCandidate, candidate_id)

    assert persisted_watch.status is WatchStatus.SEAT_FOUND
    assert persisted_candidate.state == "seat_found"


async def test_fresh_closed_window_expires_future_service_date_without_date_gate(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 8, 2, 13, 30, tzinfo=timezone.utc)
    scheduled_departure = now + timedelta(hours=2)
    watch = _watch(
        dedupe_key="future-service-fresh-closed",
        travel_date=scheduled_departure.astimezone(KST).date(),
    )
    watch.candidates.append(
        _candidate(
            train_number="FRESH-CLOSED",
            departure_at=scheduled_departure,
            priority=1,
            scheduled_departure_at=scheduled_departure,
            operational_status=OperationalStatus.SCHEDULED,
            booking_window_status=BookingWindowStatus.CLOSED,
            operational_source="operational-test",
            operational_observed_at=now - timedelta(minutes=1),
            operational_fresh_until=now + timedelta(minutes=5),
        )
    )

    async with factory() as session:
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(),
            )
            == 1
        )

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)

    assert persisted.status is WatchStatus.EXPIRED


async def test_future_departure_is_not_expired_by_next_day_arrival(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service_date = date(2026, 8, 5)
    local_now = datetime.combine(service_date, time(23, 50), tzinfo=KST)
    now = local_now.astimezone(timezone.utc)
    departure_at = (local_now + timedelta(minutes=5)).astimezone(timezone.utc)
    watch = _watch(
        dedupe_key="late-night-selected-departure",
        travel_date=service_date,
        time_from=time(23, 45),
        time_to=time(0, 55),
        status=WatchStatus.SEAT_FOUND,
    )
    candidate = _candidate(
        train_number="LATE",
        departure_at=departure_at,
        priority=1,
        state="seat_found",
    )
    candidate.arrival_at = (local_now + timedelta(hours=3, minutes=5)).astimezone(timezone.utc)
    watch.candidates.append(candidate)

    async with factory() as session:
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        assert (
            await expire_elapsed_watches(
                session,
                now,
                dependencies=_dependencies(),
            )
            == 0
        )

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)

    assert persisted.status is WatchStatus.SEAT_FOUND
    assert persisted.candidates[0].state == "seat_found"
