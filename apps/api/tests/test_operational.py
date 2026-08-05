from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import (
    BookingWindowStatus,
    OperationalStatus,
    Provider,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import OutboxEvent, SeatObservation, Watch, WatchCandidate
from rail_waitlist.observations.operational_projection_application import (
    OperationalProjectionCandidate,
    apply_operational_projection,
)
from rail_waitlist.operational import decide_operational_expiry
from rail_waitlist.schemas import SeatObservationResult
from rail_waitlist.services import (
    OperationalProjectionCandidate as CompatibilityOperationalProjectionCandidate,
)
from rail_waitlist.services import (
    apply_operational_projection as compatibility_apply_operational_projection,
)
from rail_waitlist.services import record_seat_observation


@dataclass
class Candidate:
    scheduled_departure_at: datetime
    operational_status: OperationalStatus = OperationalStatus.UNKNOWN
    booking_window_status: BookingWindowStatus = BookingWindowStatus.UNKNOWN
    operational_source: str | None = None
    operational_observed_at: datetime | None = None
    operational_fresh_until: datetime | None = None
    actual_departure_at: datetime | None = None
    estimated_departure_at: datetime | None = None
    delay_minutes: int | None = None


def test_legacy_services_projection_exports_preserve_canonical_identity() -> None:
    assert compatibility_apply_operational_projection is apply_operational_projection
    assert CompatibilityOperationalProjectionCandidate is OperationalProjectionCandidate


def test_unknown_operational_state_is_retained_before_fifteen_minute_horizon() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    scheduled_departure = now - timedelta(minutes=14, seconds=59)
    candidate = Candidate(scheduled_departure_at=scheduled_departure)

    decision = decide_operational_expiry(candidate, now)

    assert not decision.expire
    assert decision.retry_at == scheduled_departure + timedelta(minutes=15)


def test_unknown_operational_state_expires_after_fifteen_minute_horizon() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(scheduled_departure_at=now - timedelta(minutes=15, seconds=1))

    assert decide_operational_expiry(candidate, now).expire


def test_fresh_delay_and_open_window_override_unknown_state_horizon() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(
        scheduled_departure_at=now - timedelta(hours=1),
        operational_status=OperationalStatus.DELAYED,
        booking_window_status=BookingWindowStatus.OPEN,
        operational_source="test",
        operational_observed_at=now - timedelta(minutes=1),
        operational_fresh_until=now + timedelta(minutes=5),
    )

    decision = decide_operational_expiry(candidate, now)

    assert not decision.expire
    assert decision.retry_at is None


def test_stale_terminal_operational_state_does_not_expire_candidate() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(
        scheduled_departure_at=now + timedelta(hours=2),
        operational_status=OperationalStatus.CANCELLED,
        operational_source="test",
        operational_observed_at=now - timedelta(hours=2),
        operational_fresh_until=now - timedelta(minutes=1),
    )

    assert not decide_operational_expiry(candidate, now).expire


def test_departed_seat_response_projects_fresh_terminal_operation() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(scheduled_departure_at=now - timedelta(minutes=5))
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.DEPARTED,
        source="korail.official",
        observed_at=now,
        fresh_until=now + timedelta(minutes=1),
    )

    apply_operational_projection(candidate, result)

    assert candidate.operational_status is OperationalStatus.DEPARTED_ORIGIN
    assert candidate.booking_window_status is BookingWindowStatus.CLOSED
    assert candidate.operational_source == "korail.official"
    assert candidate.actual_departure_at == now


@pytest.mark.parametrize(
    "status",
    [
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
        SeatObservationStatus.STANDING_PLUS_SEAT,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.RESERVATION_COMPLETED,
    ],
)
def test_booking_open_observations_project_fresh_open_window(
    status: SeatObservationStatus,
) -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(scheduled_departure_at=now + timedelta(hours=1))
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=status,
        source="authorized-test-source",
        observed_at=now,
        fresh_until=now + timedelta(minutes=1),
    )

    apply_operational_projection(candidate, result)

    assert candidate.booking_window_status is BookingWindowStatus.OPEN
    assert candidate.operational_source == "authorized-test-source"
    assert candidate.operational_observed_at == now
    assert candidate.operational_fresh_until == now + timedelta(minutes=1)


def test_waitlist_observation_projects_fresh_waitlist_window() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(scheduled_departure_at=now + timedelta(hours=1))
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.WAITLIST_AVAILABLE,
        source="authorized-test-source",
        observed_at=now,
        fresh_until=now + timedelta(minutes=1),
    )

    apply_operational_projection(candidate, result)

    assert candidate.booking_window_status is BookingWindowStatus.WAITLIST
    assert candidate.operational_source == "authorized-test-source"


def test_out_of_service_observation_projects_cancelled_closed_state() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(scheduled_departure_at=now + timedelta(hours=1))
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.OUT_OF_SERVICE,
        source="authorized-test-source",
        observed_at=now,
        fresh_until=now + timedelta(minutes=1),
    )

    apply_operational_projection(candidate, result)

    assert candidate.operational_status is OperationalStatus.CANCELLED
    assert candidate.booking_window_status is BookingWindowStatus.CLOSED
    assert candidate.operational_source == "authorized-test-source"


def test_sold_out_does_not_claim_that_booking_window_is_closed() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    candidate = Candidate(scheduled_departure_at=now + timedelta(hours=1))
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.SOLD_OUT,
        source="srt.official",
        observed_at=now,
        fresh_until=now + timedelta(minutes=1),
    )

    apply_operational_projection(candidate, result)

    assert candidate.operational_status is OperationalStatus.UNKNOWN
    assert candidate.booking_window_status is BookingWindowStatus.UNKNOWN
    assert candidate.operational_source is None


def test_delay_estimate_updates_live_departure_without_changing_identity() -> None:
    now = datetime(2026, 8, 2, 1, tzinfo=UTC)
    scheduled = now + timedelta(hours=1)
    candidate = Candidate(scheduled_departure_at=scheduled)
    result = SeatObservationResult(
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.SOLD_OUT,
        source="korail.official",
        observed_at=now,
        fresh_until=now + timedelta(minutes=1),
        delay_minutes=13,
    )

    apply_operational_projection(candidate, result)

    assert candidate.scheduled_departure_at == scheduled
    assert candidate.estimated_departure_at == scheduled + timedelta(minutes=13)
    assert candidate.delay_minutes == 13
    assert candidate.operational_status is OperationalStatus.DELAYED


@pytest.mark.parametrize("commit_projection", [True, False])
async def test_projection_observation_and_outbox_share_commit_or_rollback(
    db_engine,
    commit_projection: bool,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime(2026, 8, 2, 1, tzinfo=UTC)

    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="N-SUSEO",
            destination="부산",
            destination_node_id="N-BUSAN",
            travel_date=date(2026, 8, 2),
            time_from=time(12),
            time_to=time(18),
            train_numbers=["SRT-301"],
            notification_channel_ids=[],
            mode="official",
            status=WatchStatus.WATCHING,
            dedupe_key="operational-projection-atomicity",
        )
        candidate = WatchCandidate(
            train_number="SRT-301",
            departure_at=observed_at + timedelta(hours=1),
            seat_class="standard",
            priority=1,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        watch_id = watch.id
        candidate_id = candidate.id

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        assert watch is not None
        assert candidate is not None
        await record_seat_observation(
            session,
            watch,
            candidate,
            SeatObservationResult(
                seat_class=SeatClass.STANDARD,
                status=SeatObservationStatus.OUT_OF_SERVICE,
                source="authorized-test-source",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=1),
            ),
            apply_status_transition=False,
        )
        if commit_projection:
            await session.commit()
        else:
            await session.rollback()

    async with factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        observations = list(
            (
                await session.scalars(
                    select(SeatObservation).where(SeatObservation.candidate_id == candidate_id)
                )
            ).all()
        )
        outbox_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == watch_id,
                        OutboxEvent.event_type == "watch.seat_observed",
                    )
                )
            ).all()
        )

        assert candidate is not None
        if commit_projection:
            assert candidate.operational_status is OperationalStatus.CANCELLED
            assert candidate.booking_window_status is BookingWindowStatus.CLOSED
            assert len(observations) == 1
            assert len(outbox_events) == 1
        else:
            assert candidate.operational_status is OperationalStatus.UNKNOWN
            assert candidate.booking_window_status is BookingWindowStatus.UNKNOWN
            assert observations == []
            assert outbox_events == []
