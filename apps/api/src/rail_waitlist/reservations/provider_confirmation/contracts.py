"""Secret-free provider-neutral reservation confirmation contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ...domain import Provider, SeatClass
from ...provider_registry.official_url_policy import require_official_handoff_url

_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SEAT_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class ReservationConfirmationOutcome(StrEnum):
    CONFIRMED_PAYMENT_REQUIRED = "confirmed_payment_required"
    CONFIRMED_PAID = "confirmed_paid"
    NOT_FOUND = "not_found"
    AUTH_REQUIRED = "auth_required"
    PROVIDER_BLOCKED = "provider_blocked"
    INCONCLUSIVE = "inconclusive"


class ReservationConfirmationPurpose(StrEnum):
    INITIAL = "initial"
    PAYMENT_FOLLOW_UP = "payment_follow_up"


@dataclass(frozen=True, slots=True)
class ReservationConfirmationSeat:
    """Sanitized seat identity already persisted for the claimed attempt."""

    car_number: str
    seat_number: str

    def __post_init__(self) -> None:
        for name, value in (
            ("car_number", self.car_number),
            ("seat_number", self.seat_number),
        ):
            normalized = value.strip().upper()
            if not normalized or len(normalized) > 10:
                raise ValueError(f"{name} must be between 1 and 10 characters")
            if _SEAT_IDENTIFIER_PATTERN.fullmatch(normalized) is None:
                raise ValueError(f"{name} contains unsupported characters")
            object.__setattr__(self, name, normalized)


@dataclass(frozen=True, slots=True)
class ReservationConfirmationTarget:
    """Exact provider-side identity of one previously claimed reservation attempt."""

    attempt_id: str
    candidate_id: str
    provider: Provider
    train_number: str
    origin: str
    destination: str
    departure_at: datetime
    seat_class: SeatClass
    passenger_count: int
    credential_version: int
    arrival_at: datetime | None = None
    purpose: ReservationConfirmationPurpose = ReservationConfirmationPurpose.INITIAL
    reserved_seats: tuple[ReservationConfirmationSeat, ...] = ()

    def __setstate__(self, state: object) -> None:
        """Restore pre-purpose slot pickles with fail-closed defaults."""

        if not isinstance(state, list) or len(state) not in {11, 13}:
            raise ValueError("invalid reservation confirmation target state")
        values = (
            [
                *state,
                ReservationConfirmationPurpose.INITIAL,
                (),
            ]
            if len(state) == 11
            else state
        )
        for name, value in zip(self.__slots__, values, strict=True):
            object.__setattr__(self, name, value)
        self.__post_init__()

    def __post_init__(self) -> None:
        for name, value in (
            ("attempt_id", self.attempt_id),
            ("candidate_id", self.candidate_id),
            ("train_number", self.train_number),
            ("origin", self.origin),
            ("destination", self.destination),
        ):
            if not value.strip():
                raise ValueError(f"{name} cannot be blank")
        if self.provider not in {Provider.KORAIL, Provider.SRT}:
            raise ValueError("reservation confirmation supports only KORAIL or SRT")
        if self.origin.strip() == self.destination.strip():
            raise ValueError("reservation confirmation route must have distinct stations")
        if self.departure_at.tzinfo is None or self.departure_at.utcoffset() is None:
            raise ValueError("departure_at must include a timezone")
        if self.arrival_at is not None:
            if self.arrival_at.tzinfo is None or self.arrival_at.utcoffset() is None:
                raise ValueError("arrival_at must include a timezone")
            if self.arrival_at <= self.departure_at:
                raise ValueError("arrival_at must be later than departure_at")
        if self.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}:
            raise ValueError("reservation confirmation requires a concrete supported seat class")
        if not 1 <= self.passenger_count <= 9:
            raise ValueError("passenger_count must be between 1 and 9")
        if self.credential_version < 1:
            raise ValueError("credential_version must be positive")
        if not isinstance(self.purpose, ReservationConfirmationPurpose):
            raise ValueError("reservation confirmation purpose is invalid")
        if not isinstance(self.reserved_seats, tuple) or any(
            not isinstance(seat, ReservationConfirmationSeat) for seat in self.reserved_seats
        ):
            raise ValueError("reserved_seats must contain sanitized confirmation seats")
        seat_keys = tuple((seat.car_number, seat.seat_number) for seat in self.reserved_seats)
        if len(seat_keys) != len(set(seat_keys)):
            raise ValueError("reserved_seats must contain unique car and seat pairs")
        if len(self.reserved_seats) > self.passenger_count:
            raise ValueError("reserved_seats cannot exceed passenger_count")
        if self.purpose is ReservationConfirmationPurpose.INITIAL and self.reserved_seats:
            raise ValueError("only payment follow-up may carry reserved seats")


@dataclass(frozen=True, slots=True)
class ReservationConfirmationResult:
    """Normalized evidence only; it contains no provider transport material."""

    provider: Provider
    outcome: ReservationConfirmationOutcome
    source: str
    observed_at: datetime
    payment_deadline: datetime | None = None
    official_handoff_url: str | None = None

    def __post_init__(self) -> None:
        if _SOURCE_PATTERN.fullmatch(self.source) is None:
            raise ValueError("confirmation source must be a stable sanitized identifier")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        if self.payment_deadline is not None and (
            self.payment_deadline.tzinfo is None or self.payment_deadline.utcoffset() is None
        ):
            raise ValueError("payment_deadline must include a timezone")
        if self.provider not in {Provider.KORAIL, Provider.SRT}:
            raise ValueError("reservation confirmation supports only KORAIL or SRT")
        confirmed = self.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        if confirmed != (self.official_handoff_url is not None):
            raise ValueError("only a confirmed payment hold may contain an official handoff URL")
        if not confirmed and self.payment_deadline is not None:
            raise ValueError("only a confirmed payment hold may contain a payment deadline")
        if self.official_handoff_url is not None:
            require_official_handoff_url(self.provider, self.official_handoff_url)

    @property
    def permits_automatic_reservation_retry(self) -> bool:
        """A confirmation result is never an automatic-retry authorization."""

        return False


class ReservationConfirmationAdapter(Protocol):
    """Read-only provider seam; implementations must never reserve, cancel, or pay."""

    async def confirm(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult: ...
