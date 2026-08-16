from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rail_waitlist.domain import Provider, ReservationPolicy, SeatClass, WatchStatus
from rail_waitlist.models import ReservationAttempt, Watch, WatchCandidate
from rail_waitlist.reservations.execution_application import (
    ReservationExecutionDependencies,
    ReservationExecutionTarget,
    execute_reservation,
)
from rail_waitlist.services import (
    add_outbox_event,
    apply_watch_transition,
    complete_reservation_attempt,
    get_or_create_provider_circuit,
    record_reservation_confirmation,
)
from rail_waitlist.srt_reservation import SRT_RESERVATION_SOURCE


class FailIfReservationRuns:
    def __init__(self) -> None:
        self.calls = 0

    async def reserve_once(self, _request) -> None:
        self.calls += 1
        raise AssertionError("elapsed departure must be fenced before provider I/O")

    async def confirm_reservation(self, _target) -> None:
        raise AssertionError("elapsed departure must not require confirmation")


@pytest.mark.parametrize(
    ("actual_departed", "has_remaining_candidate", "expected_watch_status"),
    [
        (True, False, WatchStatus.EXPIRED),
        (False, True, WatchStatus.WATCHING),
    ],
)
async def test_elapsed_actual_or_estimated_departure_blocks_attempt_and_provider(
    app,
    actual_departed: bool,
    has_remaining_candidate: bool,
    expected_watch_status: WatchStatus,
) -> None:
    now = datetime(2026, 8, 16, 1, tzinfo=UTC)
    scheduled_departure = now + timedelta(hours=2)
    async with app.state.test_session_factory() as session:
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="대전",
            destination_node_id="MOCK-DAEJEON",
            travel_date=scheduled_departure.date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=(f"departure-fence-{actual_departed}-{has_remaining_candidate}"),
        )
        candidate = WatchCandidate(
            train_number="MOCK-001",
            departure_at=scheduled_departure,
            scheduled_departure_at=scheduled_departure,
            estimated_departure_at=(
                now + timedelta(hours=1) if actual_departed else now - timedelta(seconds=1)
            ),
            actual_departure_at=(now - timedelta(seconds=1) if actual_departed else None),
            arrival_at=scheduled_departure + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
            manual_rearm_source_attempt_id="source-attempt",
            manual_rearm_authorized_at=now - timedelta(minutes=1),
        )
        watch.candidates.append(candidate)
        if has_remaining_candidate:
            watch.candidates.append(
                WatchCandidate(
                    train_number="MOCK-002",
                    departure_at=scheduled_departure + timedelta(minutes=30),
                    scheduled_departure_at=scheduled_departure + timedelta(minutes=30),
                    arrival_at=scheduled_departure + timedelta(hours=2),
                    seat_class=SeatClass.STANDARD,
                    priority=2,
                    state="observed",
                )
            )
        session.add(watch)
        await session.commit()
        target = ReservationExecutionTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=watch.provider,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=candidate.departure_at,
            arrival_at=candidate.arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            reservation_episode_key="availability:departure-fence",
        )

    begin_calls = 0

    async def fail_if_attempt_begins(
        _session: AsyncSession,
        _watch: Watch,
        _candidate: WatchCandidate,
        _idempotency_key: str,
        **_kwargs: object,
    ) -> tuple[ReservationAttempt, bool]:
        nonlocal begin_calls
        begin_calls += 1
        raise AssertionError("elapsed departure must be fenced before attempt claim")

    async def unused_auth_update(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mock departure fence must not update provider authentication")

    adapter = FailIfReservationRuns()
    await execute_reservation(
        adapter,
        target,
        dependencies=ReservationExecutionDependencies(
            session_factory=app.state.test_session_factory,
            get_or_create_provider_circuit=get_or_create_provider_circuit,
            apply_watch_transition=apply_watch_transition,
            begin_reservation_attempt=fail_if_attempt_begins,
            add_outbox_event=add_outbox_event,
            complete_reservation_attempt=complete_reservation_attempt,
            record_reservation_confirmation=record_reservation_confirmation,
            update_provider_auth_status=unused_auth_update,
            provider_call_errors=(RuntimeError, ValueError),
            srt_exact_reservation_source=SRT_RESERVATION_SOURCE,
            now=lambda: now,
        ),
    )

    assert begin_calls == 0
    assert adapter.calls == 0
    async with app.state.test_session_factory() as session:
        persisted_watch = await session.get(Watch, target.watch_id)
        persisted_candidate = await session.get(WatchCandidate, target.candidate_id)
        attempt_count = await session.scalar(select(func.count()).select_from(ReservationAttempt))

        assert persisted_watch is not None
        assert persisted_candidate is not None
        assert persisted_watch.status is expected_watch_status
        assert persisted_candidate.state == "expired"
        assert persisted_candidate.manual_rearm_source_attempt_id is None
        assert persisted_candidate.manual_rearm_authorized_at is None
        assert attempt_count == 0
