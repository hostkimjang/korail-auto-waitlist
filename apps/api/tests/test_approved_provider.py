from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from rail_waitlist.approved_provider import (
    ApprovedCapabilityGrant,
    ApprovedObservationEnvelope,
    ApprovedProviderAdapter,
    ApprovedProviderTransportError,
    ApprovedReservationEnvelope,
    provider_request_fingerprint,
)
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.providers import ProviderUnavailable
from rail_waitlist.schemas import (
    ReservationRequest,
    ReservationResult,
    SeatObservationRequest,
    SeatObservationResult,
)


def observation_request() -> SeatObservationRequest:
    return SeatObservationRequest(
        provider=Provider.KORAIL,
        origin_node_id="NAT010000",
        destination_node_id="NAT014445",
        origin="서울",
        destination="부산",
        train_number="57",
        departure_at=datetime(2026, 8, 1, 9, tzinfo=timezone(timedelta(hours=9))),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
    )


def reservation_request() -> ReservationRequest:
    data = observation_request().model_dump()
    return ReservationRequest(
        **data,
        candidate_id="candidate-1",
        idempotency_key="reserve:candidate-1",
    )


class FakeApprovedTransport:
    provider = Provider.KORAIL
    source = "korail-approved-contract"
    supports_seat_monitoring = True
    supports_reservation_once = True

    def __init__(self) -> None:
        self.observe_calls = 0
        self.reserve_calls = 0
        self.observation_fingerprint: str | None = None
        self.reservation_fingerprint: str | None = None
        self.observation_source = self.source
        self.reservation_source = self.source
        self.failure: Exception | None = None

    async def observe_seats(
        self, request: SeatObservationRequest
    ) -> ApprovedObservationEnvelope:
        self.observe_calls += 1
        if self.failure is not None:
            raise self.failure
        now = datetime.now(UTC)
        return ApprovedObservationEnvelope(
            request_fingerprint=(
                self.observation_fingerprint or provider_request_fingerprint(request)
            ),
            observations=(
                SeatObservationResult(
                    seat_class=request.seat_class,
                    status="available",
                    source=self.observation_source,
                    observed_at=now,
                    fresh_until=now + timedelta(seconds=30),
                ),
            ),
        )

    async def reserve_once(
        self, request: ReservationRequest
    ) -> ApprovedReservationEnvelope:
        self.reserve_calls += 1
        if self.failure is not None:
            raise self.failure
        now = datetime.now(UTC)
        return ApprovedReservationEnvelope(
            request_fingerprint=(
                self.reservation_fingerprint or provider_request_fingerprint(request)
            ),
            result=ReservationResult(
                outcome="payment_required",
                source=self.reservation_source,
                observed_at=now,
                payment_deadline=now + timedelta(minutes=10),
                official_handoff_url="https://www.korail.com/ticket/search",
            ),
        )


def adapter(
    transport: FakeApprovedTransport,
    *,
    seat_monitoring: bool = True,
    reservation_once: bool = True,
) -> ApprovedProviderAdapter:
    return ApprovedProviderAdapter(
        grant=ApprovedCapabilityGrant(
            provider=Provider.KORAIL,
            reference="internal-approval-record",
            seat_monitoring=seat_monitoring,
            reservation_once=reservation_once,
        ),
        transport=transport,
    )


def test_reservation_grant_requires_observation_grant() -> None:
    with pytest.raises(ValueError, match="requires seat monitoring"):
        ApprovedCapabilityGrant(
            provider=Provider.KORAIL,
            reference="approval",
            seat_monitoring=False,
            reservation_once=True,
        )


def test_request_fingerprint_normalizes_same_instant_to_utc() -> None:
    original = observation_request()
    utc_copy = original.model_copy(update={"departure_at": original.departure_at.astimezone(UTC)})

    assert provider_request_fingerprint(original) == provider_request_fingerprint(utc_copy)


def test_request_fingerprint_binds_normalized_station_names() -> None:
    original = observation_request()
    renamed = original.model_copy(update={"origin": "수서"})

    assert provider_request_fingerprint(original) != provider_request_fingerprint(renamed)


async def test_capability_gate_blocks_unapproved_transport_call() -> None:
    transport = FakeApprovedTransport()
    target = adapter(transport, seat_monitoring=False, reservation_once=False)

    assert target.capabilities().seat_monitoring is False
    assert target.capabilities().reservation_once is False
    with pytest.raises(ProviderUnavailable, match="does not support seat monitoring"):
        await target.observe_seats(observation_request())
    assert transport.observe_calls == 0


async def test_observation_accepts_only_exact_identity_and_provenance() -> None:
    transport = FakeApprovedTransport()
    result = await adapter(transport).observe_seats(observation_request())

    assert len(result) == 1
    assert result[0].status.value == "available"
    assert result[0].source == transport.source
    assert transport.observe_calls == 1


async def test_observation_rejects_identity_mismatch() -> None:
    transport = FakeApprovedTransport()
    transport.observation_fingerprint = "0" * 64

    with pytest.raises(ProviderUnavailable, match="identity mismatch"):
        await adapter(transport).observe_seats(observation_request())


async def test_observation_rejects_provenance_mismatch() -> None:
    transport = FakeApprovedTransport()
    transport.observation_source = "unexpected-source"

    with pytest.raises(ProviderUnavailable, match="provenance mismatch"):
        await adapter(transport).observe_seats(observation_request())


async def test_transport_error_does_not_expose_raw_message() -> None:
    transport = FakeApprovedTransport()
    transport.failure = ApprovedProviderTransportError("credential=must-not-leak")

    with pytest.raises(ProviderUnavailable) as raised:
        await adapter(transport).observe_seats(observation_request())
    assert "credential" not in str(raised.value)
    assert "must-not-leak" not in str(raised.value)


async def test_reservation_accepts_exact_idempotent_request_identity() -> None:
    transport = FakeApprovedTransport()
    result = await adapter(transport).reserve_once(reservation_request())

    assert result.outcome.value == "payment_required"
    assert result.source == transport.source
    assert transport.reserve_calls == 1


async def test_reservation_rejects_identity_mismatch() -> None:
    transport = FakeApprovedTransport()
    transport.reservation_fingerprint = "f" * 64

    with pytest.raises(ProviderUnavailable, match="identity mismatch"):
        await adapter(transport).reserve_once(reservation_request())
