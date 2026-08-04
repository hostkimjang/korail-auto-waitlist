from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .domain import Provider
from .metrics import PROVIDER_OPERATION_DURATION, PROVIDER_OPERATIONS
from .providers import OfficialTimetableAdapter, ProviderUnavailable, RailProviderAdapter
from .schemas import (
    ProviderCapabilities,
    ReservationRequest,
    ReservationResult,
    SeatObservationRequest,
    SeatObservationResult,
    StationCatalog,
    TimetableItem,
)

_SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


def provider_request_fingerprint(
    request: SeatObservationRequest | ReservationRequest,
) -> str:
    """Return a stable, secret-free identity for one normalized provider request."""

    payload = request.model_dump(mode="json")
    payload["departure_at"] = (
        request.departure_at.astimezone(UTC).replace(microsecond=0).isoformat()
    )
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovedCapabilityGrant:
    """Locally recorded approval evidence; it never contains credentials or endpoint details."""

    provider: Provider
    reference: str
    seat_monitoring: bool = False
    reservation_once: bool = False

    def __post_init__(self) -> None:
        if self.provider not in {Provider.KORAIL, Provider.SRT}:
            raise ValueError("approved capability grants only apply to KORAIL or SRT")
        normalized_reference = self.reference.strip()
        if not normalized_reference or len(normalized_reference) > 160:
            raise ValueError("approval reference must contain 1 to 160 characters")
        if self.reservation_once and not self.seat_monitoring:
            raise ValueError("reservation approval requires seat monitoring approval")
        object.__setattr__(self, "reference", normalized_reference)


@dataclass(frozen=True)
class ApprovedObservationEnvelope:
    request_fingerprint: str
    observations: tuple[SeatObservationResult, ...]


@dataclass(frozen=True)
class ApprovedReservationEnvelope:
    request_fingerprint: str
    result: ReservationResult


class ApprovedProviderTransport(Protocol):
    """Spec-specific transport implemented only after an approved contract is available.

    The transport owns authentication and upstream DTO mapping.  This boundary accepts only
    normalized domain results, so raw responses and credentials cannot cross into workers.
    """

    provider: Provider
    source: str
    supports_seat_monitoring: bool
    supports_reservation_once: bool

    async def observe_seats(
        self, request: SeatObservationRequest
    ) -> ApprovedObservationEnvelope: ...

    async def reserve_once(
        self, request: ReservationRequest
    ) -> ApprovedReservationEnvelope: ...


class ApprovedProviderTransportError(RuntimeError):
    """A sanitized transport failure that is safe to map to a provider error."""


class ApprovedProviderAdapter(RailProviderAdapter):
    """Fail-closed adapter seam for a future, explicitly approved provider transport."""

    def __init__(
        self,
        *,
        grant: ApprovedCapabilityGrant,
        transport: ApprovedProviderTransport,
        timetable_adapter: RailProviderAdapter | None = None,
    ) -> None:
        self.provider = grant.provider
        self._grant = grant
        self._transport = transport
        self._timetable_adapter = timetable_adapter or OfficialTimetableAdapter(self.provider)
        if transport.provider != self.provider:
            raise ValueError("approved transport provider does not match its grant")
        if self._timetable_adapter.provider != self.provider:
            raise ValueError("timetable adapter provider does not match approved provider")
        if not _SOURCE_PATTERN.fullmatch(transport.source):
            raise ValueError("approved transport source must be a public, normalized identifier")

    def capabilities(self) -> ProviderCapabilities:
        seat_monitoring = (
            self._grant.seat_monitoring and self._transport.supports_seat_monitoring
        )
        reservation_once = (
            seat_monitoring
            and self._grant.reservation_once
            and self._transport.supports_reservation_once
        )
        return ProviderCapabilities(
            provider=self.provider,
            timetable=True,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=seat_monitoring,
            reservation_once=reservation_once,
            enabled=True,
            note=(
                "정식 명세 transport와 별도 승인 근거가 모두 확인된 capability만 활성화합니다."
            ),
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        return await self._timetable_adapter.timetable(
            origin,
            destination,
            departure_from,
            origin_node_id,
            destination_node_id,
            departure_to,
        )

    async def stations(self) -> StationCatalog:
        return await self._timetable_adapter.stations()

    async def _observe_seats(
        self, request: SeatObservationRequest
    ) -> list[SeatObservationResult]:
        started = time.perf_counter()
        try:
            envelope = await self._transport.observe_seats(request)
        except (ApprovedProviderTransportError, ProviderUnavailable):
            self._record_operation("observe", "failed", started)
            raise ProviderUnavailable("approved provider seat observation failed") from None

        try:
            expected_fingerprint = provider_request_fingerprint(request)
            if envelope.request_fingerprint != expected_fingerprint:
                raise ProviderUnavailable("approved provider observation identity mismatch")
            if len(envelope.observations) != 1:
                raise ProviderUnavailable(
                    "approved provider must return exactly one requested seat class"
                )
            result = envelope.observations[0]
            if result.seat_class != request.seat_class:
                raise ProviderUnavailable("approved provider returned a different seat class")
            if result.source != self._transport.source:
                raise ProviderUnavailable("approved provider observation provenance mismatch")
        except (ProviderUnavailable, ValueError):
            self._record_operation("observe", "rejected", started)
            raise

        self._record_operation("observe", "succeeded", started)
        return [result]

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        started = time.perf_counter()
        try:
            envelope = await self._transport.reserve_once(request)
        except (ApprovedProviderTransportError, ProviderUnavailable):
            self._record_operation("reserve_once", "failed", started)
            raise ProviderUnavailable("approved provider reservation failed") from None

        try:
            expected_fingerprint = provider_request_fingerprint(request)
            if envelope.request_fingerprint != expected_fingerprint:
                raise ProviderUnavailable("approved provider reservation identity mismatch")
            if envelope.result.source != self._transport.source:
                raise ProviderUnavailable("approved provider reservation provenance mismatch")
        except (ProviderUnavailable, ValueError):
            self._record_operation("reserve_once", "rejected", started)
            raise

        self._record_operation("reserve_once", "succeeded", started)
        return envelope.result

    def _record_operation(self, operation: str, result: str, started: float) -> None:
        labels = (self.provider.value, operation)
        PROVIDER_OPERATIONS.labels(*labels, result).inc()
        PROVIDER_OPERATION_DURATION.labels(*labels).observe(time.perf_counter() - started)
