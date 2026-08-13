"""Secret-free page models shared by KORAIL Pydoll actors and DOM drivers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from datetime import time as clock_time
from typing import Literal

KORAIL_ROUTE_HEADING = re.compile(
    r"^(.+?)\s*→\s*(.+?)\s*\(\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*\)"
    r"(?:\s*소요시간\s*:\s*.+)?$"
)


@dataclass(frozen=True)
class PydollSeatBox:
    text: str
    classes: frozenset[str]


@dataclass(frozen=True)
class PydollTrainRow:
    kind_text: str
    train_number: str
    route_text: str
    seats: tuple[PydollSeatBox, ...]
    full_text: str = ""


@dataclass(frozen=True)
class PydollPageSnapshot:
    body_text: str
    rows: tuple[PydollTrainRow, ...]
    protection_texts: tuple[str, ...] = ()
    network_responses: tuple[tuple[int, str], ...] = ()
    url: str = ""
    title: str = ""
    reservation_rows: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PydollReservationListSnapshot:
    """Secret-free completeness contract for the authenticated reservation list."""

    url: str
    reservation_rows: tuple[str, ...] = ()
    rendered_card_count: int = 0
    malformed_card_count: int = 0
    page_marker_visible: bool = False
    explicit_empty_visible: bool = False
    loading_visible: bool = False
    stable_observation: bool = False
    protection_detected: bool = False
    network_responses: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or len(self.url) > 2048:
            raise ValueError("reservation-list URL is invalid")
        if self.rendered_card_count < 0 or self.malformed_card_count < 0:
            raise ValueError("reservation-list card counts cannot be negative")
        if self.malformed_card_count > self.rendered_card_count:
            raise ValueError("malformed reservation row count cannot exceed rendered cards")
        if len(self.reservation_rows) + self.malformed_card_count != self.rendered_card_count:
            raise ValueError("reservation-list counts must account for every rendered card")
        if self.explicit_empty_visible and self.rendered_card_count:
            raise ValueError("reservation-list empty state cannot contain rendered cards")
        if any(
            not isinstance(flag, bool)
            for flag in (
                self.page_marker_visible,
                self.explicit_empty_visible,
                self.loading_visible,
                self.stable_observation,
                self.protection_detected,
            )
        ):
            raise ValueError("reservation-list page flags must be boolean")

    @property
    def page_ready(self) -> bool:
        return (
            self.page_marker_visible
            and not self.loading_visible
            and (self.explicit_empty_visible or self.rendered_card_count > 0)
        )

    @property
    def official_read_completed(self) -> bool:
        return self.stable_observation and self.render_complete

    @property
    def render_complete(self) -> bool:
        return self.page_ready and (
            self.explicit_empty_visible
            or (
                self.rendered_card_count > 0
                and self.malformed_card_count == 0
                and len(self.reservation_rows) == self.rendered_card_count
            )
        )

    def with_stable_observation(self) -> PydollReservationListSnapshot:
        return PydollReservationListSnapshot(
            url=self.url,
            reservation_rows=self.reservation_rows,
            rendered_card_count=self.rendered_card_count,
            malformed_card_count=self.malformed_card_count,
            page_marker_visible=self.page_marker_visible,
            explicit_empty_visible=self.explicit_empty_visible,
            loading_visible=self.loading_visible,
            stable_observation=True,
            protection_detected=self.protection_detected,
            network_responses=self.network_responses,
        )


@dataclass(frozen=True, slots=True)
class PydollIssuedTicketSummary:
    """Card-scoped issued-ticket identity with all provider secrets omitted."""

    service_date: date
    train_number: str
    origin: str
    destination: str
    departure_time: clock_time
    arrival_time: clock_time
    seat_class: Literal["standard", "first"]
    passenger_count: int
    car_number: str
    seat_number: str
    returned: bool = False
    operation_stopped: bool = False
    transferred: bool = False

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("train_number", self.train_number, 40),
            ("origin", self.origin, 40),
            ("destination", self.destination, 40),
            ("car_number", self.car_number, 10),
            ("seat_number", self.seat_number, 10),
        ):
            if not isinstance(value, str) or not value or len(value) > maximum:
                raise ValueError(f"{name} is invalid")
        if self.seat_class not in {"standard", "first"}:
            raise ValueError("seat_class is invalid")
        if not 1 <= self.passenger_count <= 9:
            raise ValueError("passenger_count must be between 1 and 9")
        if re.fullmatch(r"[0-9]+", self.car_number) is None:
            raise ValueError("car_number is invalid")
        if re.fullmatch(r"[0-9]+[A-D]", self.seat_number) is None:
            raise ValueError("seat_number is invalid")
        if any(
            not isinstance(value, bool)
            for value in (self.returned, self.operation_stopped, self.transferred)
        ):
            raise ValueError("issued-ticket status flags must be boolean")


@dataclass(frozen=True, slots=True)
class PydollIssuedTicketListSnapshot:
    """Sanitized MyTicket render state; it intentionally has no body/card text."""

    url: str
    tickets: tuple[PydollIssuedTicketSummary, ...] = ()
    rendered_card_count: int = 0
    malformed_card_count: int = 0
    empty_state_visible: bool = False
    protection_detected: bool = False
    network_responses: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or len(self.url) > 2048:
            raise ValueError("issued-ticket URL is invalid")
        if self.rendered_card_count < 0 or self.malformed_card_count < 0:
            raise ValueError("issued-ticket card counts cannot be negative")
        if self.malformed_card_count > self.rendered_card_count:
            raise ValueError("malformed card count cannot exceed rendered cards")
        if len(self.tickets) + self.malformed_card_count != self.rendered_card_count:
            raise ValueError("issued-ticket card counts must account for every rendered card")
        if self.empty_state_visible and self.rendered_card_count:
            raise ValueError("issued-ticket empty state cannot contain rendered cards")

    @property
    def page_ready(self) -> bool:
        return self.empty_state_visible or self.rendered_card_count > 0


def normalize_korail_station(value: str) -> str:
    return " ".join(value.split()).removesuffix("역")


def normalize_korail_train_number(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z-]", "", " ".join(value.split()))
    if not normalized or len(normalized) > 40:
        raise ValueError("KORAIL train number is required")
    digits = "".join(character for character in normalized if character.isdigit())
    return digits.lstrip("0") or "0"
