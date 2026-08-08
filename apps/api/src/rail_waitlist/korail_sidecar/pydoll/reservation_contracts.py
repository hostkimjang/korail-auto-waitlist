"""Value contracts shared by KORAIL Pydoll reservation layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as clock_time
from enum import StrEnum

from .auth_contracts import KorailCredentialInput


class KorailReservationSeatClass(StrEnum):
    GENERAL = "general"
    SPECIAL = "special"

    @property
    def label(self) -> str:
        return "일반실" if self is self.GENERAL else "특실"


class KorailReservationOutcome(StrEnum):
    PAYMENT_REQUIRED = "payment_required"
    AUTH_REQUIRED = "auth_required"
    CONSENT_REQUIRED = "consent_required"
    ACTION_REQUIRED = "action_required"
    PROVIDER_BLOCKED = "provider_blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class KorailReservationRequest:
    origin: str
    destination: str
    travel_date: date
    train_number: str
    train_type: str | None
    departure_time: clock_time
    arrival_time: clock_time
    seat_class: KorailReservationSeatClass
    credential: KorailCredentialInput = field(repr=False)


@dataclass(frozen=True)
class KorailReservationResult:
    outcome: KorailReservationOutcome
    reason: str
    seat_clicked: bool = False
    reservation_clicked: bool = False
    session_ready_at: datetime | None = None
    target_rechecked_at: datetime | None = None
    seat_selected_at: datetime | None = None
    reservation_requested_at: datetime | None = None
