"""Secret-free contracts for read-only confirmation of a reservation hold.

Confirmation is intentionally separate from a reservation attempt.  In particular,
``not_found`` and ``inconclusive`` never prove that another reserve request is safe.
Provider implementations receive only an exact, redacted identity and credential
generation; passwords, cookies, reservation numbers, and raw provider payloads are
outside this contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from .domain import Provider, SeatClass
from .schemas import OFFICIAL_HOST_ROOTS

_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ReservationConfirmationOutcome(StrEnum):
    CONFIRMED_PAYMENT_REQUIRED = "confirmed_payment_required"
    NOT_FOUND = "not_found"
    AUTH_REQUIRED = "auth_required"
    PROVIDER_BLOCKED = "provider_blocked"
    INCONCLUSIVE = "inconclusive"


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


def require_official_handoff_url(provider: Provider, value: str) -> str:
    """Apply the existing HTTPS provider-host allowlist without retaining a URL payload."""

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
    ):
        raise ValueError("official handoff URL must be a credential-free HTTPS URL")
    host = parsed.hostname.lower().rstrip(".")
    if not any(host == root or host.endswith(f".{root}") for root in OFFICIAL_HOST_ROOTS[provider]):
        raise ValueError("official handoff URL must use the provider allowlist")
    return value


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
