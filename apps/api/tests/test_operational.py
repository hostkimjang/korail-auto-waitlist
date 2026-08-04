from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rail_waitlist.domain import (
    BookingWindowStatus,
    OperationalStatus,
    SeatClass,
    SeatObservationStatus,
)
from rail_waitlist.operational import decide_operational_expiry
from rail_waitlist.schemas import SeatObservationResult
from rail_waitlist.services import apply_operational_projection


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
