from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .domain import BookingWindowStatus, OperationalStatus

UNKNOWN_OPERATIONAL_ABSOLUTE_HORIZON = timedelta(minutes=15)
UNKNOWN_OPERATIONAL_RETRY_INTERVAL = timedelta(minutes=15)


class OperationalCandidate(Protocol):
    scheduled_departure_at: datetime
    operational_status: OperationalStatus
    booking_window_status: BookingWindowStatus
    operational_source: str | None
    operational_observed_at: datetime | None
    operational_fresh_until: datetime | None


@dataclass(frozen=True, slots=True)
class OperationalExpiryDecision:
    expire: bool
    retry_at: datetime | None = None


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def has_fresh_operational_provenance(candidate: OperationalCandidate, now: datetime) -> bool:
    source = candidate.operational_source
    observed_at = candidate.operational_observed_at
    fresh_until = candidate.operational_fresh_until
    if not source or observed_at is None or fresh_until is None:
        return False
    return as_utc(observed_at) <= as_utc(fresh_until) and as_utc(fresh_until) >= as_utc(now)


def decide_operational_expiry(
    candidate: OperationalCandidate,
    now: datetime,
) -> OperationalExpiryDecision:
    """Fail closed only on fresh terminal evidence or a bounded unknown-state horizon."""

    normalized_now = as_utc(now)
    if has_fresh_operational_provenance(candidate, normalized_now):
        if (
            candidate.operational_status
            in {
                OperationalStatus.DEPARTED_ORIGIN,
                OperationalStatus.CANCELLED,
            }
            or candidate.booking_window_status is BookingWindowStatus.CLOSED
        ):
            return OperationalExpiryDecision(expire=True)
        if candidate.operational_status in {
            OperationalStatus.DELAYED,
            OperationalStatus.BOARDING,
        } or candidate.booking_window_status in {
            BookingWindowStatus.OPEN,
            BookingWindowStatus.WAITLIST,
        }:
            return OperationalExpiryDecision(expire=False)

    scheduled_departure = as_utc(candidate.scheduled_departure_at)
    if normalized_now < scheduled_departure:
        return OperationalExpiryDecision(expire=False)
    horizon = scheduled_departure + UNKNOWN_OPERATIONAL_ABSOLUTE_HORIZON
    if normalized_now >= horizon:
        return OperationalExpiryDecision(expire=True)
    return OperationalExpiryDecision(
        expire=False,
        retry_at=min(normalized_now + UNKNOWN_OPERATIONAL_RETRY_INTERVAL, horizon),
    )
