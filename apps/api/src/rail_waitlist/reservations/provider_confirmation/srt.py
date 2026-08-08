"""SRT reservation-result and read-only list confirmation normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from ...domain import Provider, SeatClass
from ...provider_adapters.srt_identity import (
    normalize_srt_date,
    normalize_srt_time,
    normalize_srt_train_number,
)
from ...provider_registry.official_url_policy import require_official_handoff_url
from .contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)

KOREA = ZoneInfo("Asia/Seoul")
SRT_RESERVATION_HANDOFF_URL = (
    "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
)
SRT_RESERVATION_LIST_SOURCE = "srtrain-reservation-list"
SRT_RESERVE_RESULT_SOURCE = "srtrain-reserve-result"


@dataclass(frozen=True, slots=True)
class SrtReservationRecord:
    """The minimum redacted fields needed to exact-match an unpaid SRT reservation."""

    train_number: str
    departure_date: str
    departure_time: str
    origin: str
    destination: str
    payment_date: str
    payment_time: str
    paid: bool
    seat_class: SeatClass | None = None
    passenger_count: int | None = None


@dataclass(frozen=True, slots=True)
class SrtReservationListEvidence:
    observed_at: datetime
    credential_version: int | None
    records: tuple[SrtReservationRecord, ...] = ()
    auth_required: bool = False
    provider_blocked: bool = False

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")


class SrtReadOnlyReservationListProbe(Protocol):
    """Fetch reservation records only; it must not reserve, cancel, or pay."""

    async def list_reservations(
        self,
        target: ReservationConfirmationTarget,
    ) -> SrtReservationListEvidence: ...


def _payment_deadline(record: SrtReservationRecord) -> datetime | None:
    raw_date = "".join(character for character in record.payment_date if character.isdigit())
    raw_time = "".join(character for character in record.payment_time if character.isdigit())
    if len(raw_date) != 8 or len(raw_time) < 4:
        return None
    try:
        return datetime.strptime(f"{raw_date}{raw_time[:4]}", "%Y%m%d%H%M").replace(tzinfo=KOREA)
    except ValueError:
        return None


def _matches_trip(record: SrtReservationRecord, target: ReservationConfirmationTarget) -> bool:
    departure = target.departure_at.astimezone(KOREA)
    return (
        normalize_srt_train_number(record.train_number)
        == normalize_srt_train_number(target.train_number)
        and normalize_srt_date(record.departure_date) == departure.strftime("%Y%m%d")
        and normalize_srt_time(record.departure_time) == departure.strftime("%H%M%S")
        and record.origin.strip() == target.origin.strip()
        and record.destination.strip() == target.destination.strip()
    )


def _matches_target(record: SrtReservationRecord, target: ReservationConfirmationTarget) -> bool:
    return (
        _matches_trip(record, target)
        and record.seat_class is target.seat_class
        and record.passenger_count == target.passenger_count
    )


def normalize_srt_reservation_records(
    target: ReservationConfirmationTarget,
    evidence: SrtReservationListEvidence,
    *,
    source: str = SRT_RESERVATION_LIST_SOURCE,
) -> ReservationConfirmationResult:
    """Normalize a reserve result or a read-only reservation list with exact matching."""

    if target.provider is not Provider.SRT:
        raise ValueError("SRT reservation confirmation received a non-SRT target")
    if evidence.provider_blocked:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.PROVIDER_BLOCKED,
            source=source,
            observed_at=evidence.observed_at,
        )
    if evidence.auth_required:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source=source,
            observed_at=evidence.observed_at,
        )
    if evidence.credential_version != target.credential_version:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source=source,
            observed_at=evidence.observed_at,
        )
    trip_matches = tuple(record for record in evidence.records if _matches_trip(record, target))
    if not trip_matches:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source=source,
            observed_at=evidence.observed_at,
        )
    matches = tuple(record for record in trip_matches if _matches_target(record, target))
    if len(matches) != 1 or matches[0].paid:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source=source,
            observed_at=evidence.observed_at,
        )
    return ReservationConfirmationResult(
        provider=target.provider,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source=source,
        observed_at=evidence.observed_at,
        payment_deadline=_payment_deadline(matches[0]),
        official_handoff_url=require_official_handoff_url(
            Provider.SRT,
            SRT_RESERVATION_HANDOFF_URL,
        ),
    )


@dataclass(slots=True)
class SrtReservationListConfirmationAdapter:
    probe: SrtReadOnlyReservationListProbe

    async def confirm(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return normalize_srt_reservation_records(target, await self.probe.list_reservations(target))


def normalize_srt_reserve_result(
    target: ReservationConfirmationTarget,
    record: SrtReservationRecord,
    *,
    observed_at: datetime,
    credential_version: int | None,
) -> ReservationConfirmationResult:
    """Use the already-returned reserve result without issuing a second provider call."""

    return normalize_srt_reservation_records(
        target,
        SrtReservationListEvidence(
            observed_at=observed_at,
            credential_version=credential_version,
            records=(
                SrtReservationRecord(
                    train_number=record.train_number,
                    departure_date=record.departure_date,
                    departure_time=record.departure_time,
                    origin=record.origin,
                    destination=record.destination,
                    payment_date=record.payment_date,
                    payment_time=record.payment_time,
                    paid=record.paid,
                    seat_class=record.seat_class,
                    passenger_count=record.passenger_count,
                ),
            ),
        ),
        source=SRT_RESERVE_RESULT_SOURCE,
    )
