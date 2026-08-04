from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from rail_waitlist import srt_provider_adapter_service as adapter_service
from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.provider_accounts import ProviderCredentials
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from rail_waitlist.schemas import (
    ReservationRequest,
    ReservationResult,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    SeatObservationResult,
    TimetableItem,
)
from rail_waitlist.srt_provider_adapter import (
    SRT_PROVIDER_ADAPTER_ORIGIN,
    SrtProviderAdapterClient,
    SrtProviderAdapterUnavailable,
)
from rail_waitlist.srt_provider_adapter_contract import (
    SrtCredentialRequest,
    SrtTimetableSearchRequest,
    SrtTimetableTrain,
)
from rail_waitlist.srt_provider_adapter_service import create_srt_provider_adapter_app
from rail_waitlist.srt_reservation import SrtSessionActorSnapshot, SrtSessionActorState

TOKEN = "srt-sidecar-contract-token-value-32-bytes"


async def test_srt_sidecar_cache_defaults_to_one_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SRT_SEAT_STATUS_CACHE_TTL_SECONDS", raising=False)

    source, redis = adapter_service._build_default_source()
    try:
        assert source.cache_ttl_seconds == 1
    finally:
        await redis.aclose()


class FakeSource:
    def __init__(self) -> None:
        self.observe_calls = 0
        self.overlay_calls = 0
        self.timetable_calls = 0
        self.drain_calls = 0
        self.deferred_until = datetime.now(UTC) + timedelta(minutes=2)

    async def observation_deferred_until(self):
        return self.deferred_until

    async def observe(self, request, *, origin, destination):
        self.observe_calls += 1
        assert origin == request.origin
        assert destination == request.destination
        observed_at = datetime.now(UTC)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status="sold_out",
                source="test-srt-sidecar",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(seconds=30),
            )
        ]

    async def overlay(self, items, **_kwargs):
        self.overlay_calls += 1
        return items

    async def search_timetable(self, **kwargs):
        self.timetable_calls += 1
        assert kwargs == {
            "origin": "수서",
            "destination": "부산",
            "departure_from": datetime(2026, 8, 3, 12, 30, tzinfo=UTC),
            "departure_to": datetime(2026, 8, 3, 13, 30, tzinfo=UTC),
            "passenger_count": 1,
        }
        return [
            SrtTimetableTrain(
                train_number="00329",
                train_type="SRT",
                origin="수서",
                destination="부산",
                departure_at=kwargs["departure_from"],
                arrival_at=kwargs["departure_to"] + timedelta(hours=1),
                standard_status="sold_out",
                first_status="available",
                observed_at=datetime(2026, 8, 3, 3, 29, tzinfo=UTC),
                delay_minutes=7,
                adult_fare=None,
            )
        ]

    async def drain_pending_calls(self):
        self.drain_calls += 1


class FakeExecutor:
    def __init__(self) -> None:
        self.login_versions: list[int] = []
        self.reserve_versions: list[int] = []
        self.confirm_versions: list[int] = []
        self.confirm_arrivals: list[datetime | None] = []

    def session_snapshot(self):
        return SrtSessionActorSnapshot(
            state=SrtSessionActorState.READY,
            credential_generation=7,
            created_at_monotonic=10.0,
            last_verified_at_monotonic=11.0,
            last_used_at_monotonic=12.0,
            local_reuse_until_monotonic=312.0,
            locally_reusable=True,
        )

    async def verify_credentials(self, credentials):
        self.login_versions.append(credentials.credential_version)
        return True

    async def prewarm_credentials(self, credentials):
        self.login_versions.append(credentials.credential_version)
        return True

    async def reserve_once(self, request, credentials):
        self.reserve_versions.append(credentials.credential_version)
        return ReservationResult(
            outcome=ReservationOutcome.NOT_AVAILABLE,
            source="test-srt-sidecar",
            observed_at=datetime.now(UTC),
        )

    async def confirm_reservation(self, target, credentials):
        self.confirm_versions.append(credentials.credential_version)
        self.confirm_arrivals.append(target.arrival_at)
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="test-srt-confirmation",
            observed_at=datetime.now(UTC),
        )


def observation_request() -> SeatObservationRequest:
    return SeatObservationRequest(
        provider=Provider.SRT,
        origin_node_id="0017",
        destination_node_id="0020",
        origin="수서",
        destination="부산",
        train_number="329",
        departure_at=datetime(2026, 8, 3, 12, 30, tzinfo=UTC),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
    )


def reservation_request() -> ReservationRequest:
    return ReservationRequest(
        **observation_request().model_dump(),
        candidate_id="candidate-srt-sidecar",
        idempotency_key="reserve-srt-sidecar-once",
    )


def confirmation_target() -> ReservationConfirmationTarget:
    request = reservation_request()
    return ReservationConfirmationTarget(
        attempt_id="attempt-srt-sidecar",
        candidate_id=request.candidate_id,
        provider=request.provider,
        train_number=request.train_number,
        origin=request.origin,
        destination=request.destination,
        departure_at=request.departure_at,
        arrival_at=request.departure_at + timedelta(hours=2),
        seat_class=request.seat_class,
        passenger_count=request.passenger_count,
        credential_version=7,
    )


def timetable_item() -> TimetableItem:
    departure_at = datetime(2026, 8, 3, 12, 30, tzinfo=UTC)
    return TimetableItem(
        provider=Provider.SRT,
        train_number="329",
        train_type="SRT",
        origin="수서",
        destination="부산",
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=2),
        timetable_source="TAGO",
        timetable_retrieved_at=datetime.now(UTC),
        seat_classes=[
            SeatClassAvailability(
                seat_class=SeatClass.STANDARD,
                status="unknown",
                provenance=SeatAvailabilityProvenance(
                    kind="not_observed",
                    reason="source_not_configured",
                ),
            )
        ],
        official_booking_url=(
            "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000"
        ),
    )


@pytest.mark.asyncio
async def test_sidecar_client_contract_reuses_process_owned_source_and_session() -> None:
    source = FakeSource()
    executor = FakeExecutor()
    app = create_srt_provider_adapter_app(
        source=source,
        executor=executor,
        token=TOKEN,
        monotonic=lambda: 20.0,
    )
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        client = SrtProviderAdapterClient(
            SRT_PROVIDER_ADAPTER_ORIGIN,
            10,
            TOKEN,
            transport=transport,
        )
        status = await client.session_status()
        credentials = ProviderCredentials("1234567890", "private-password", 7)
        assert await client.verify_credentials(credentials) is True
        assert await client.prewarm_credentials(credentials) is True
        observations = await client.observe(
            observation_request(),
            origin="수서",
            destination="부산",
        )
        item = timetable_item()
        overlaid = await client.overlay(
            [item],
            origin="수서",
            destination="부산",
            departure_from=item.departure_at,
            departure_to=item.departure_at + timedelta(hours=1),
            passenger_count=1,
        )
        timetable = await client.search_timetable(
            origin="수서",
            destination="부산",
            departure_from=item.departure_at,
            departure_to=item.departure_at + timedelta(hours=1),
            passenger_count=1,
        )
        reservation = await client.reserve_once(reservation_request(), credentials)
        confirmation = await client.confirm_reservation(confirmation_target(), credentials)
        await client.aclose()

    assert status.state is SrtSessionActorState.READY
    assert status.credential_generation == 7
    assert status.locally_reusable is True
    assert status.created_age_seconds == 10.0
    assert status.last_verified_age_seconds == 9.0
    assert status.last_used_age_seconds == 8.0
    assert status.local_reuse_remaining_seconds == 292.0
    assert status.observation_deferred_until == source.deferred_until
    assert observations[0].status == "sold_out"
    assert overlaid == [item]
    assert timetable == [
        SrtTimetableTrain(
            train_number="00329",
            train_type="SRT",
            origin="수서",
            destination="부산",
            departure_at=datetime(2026, 8, 3, 12, 30, tzinfo=UTC),
            arrival_at=datetime(2026, 8, 3, 14, 30, tzinfo=UTC),
            standard_status="sold_out",
            first_status="available",
            observed_at=datetime(2026, 8, 3, 3, 29, tzinfo=UTC),
            delay_minutes=7,
            adult_fare=None,
        )
    ]
    assert reservation.outcome is ReservationOutcome.NOT_AVAILABLE
    assert confirmation.outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert source.observe_calls == 1
    assert source.overlay_calls == 1
    assert source.timetable_calls == 1
    assert source.drain_calls == 1
    assert executor.login_versions == [7, 7]
    assert executor.reserve_versions == [7]
    assert executor.confirm_arrivals == [datetime(2026, 8, 3, 14, 30, tzinfo=UTC)]
    assert executor.confirm_versions == [7]


@pytest.mark.asyncio
async def test_sidecar_token_auth_and_validation_errors_never_echo_credentials() -> None:
    app = create_srt_provider_adapter_app(
        source=FakeSource(),
        executor=FakeExecutor(),
        token=TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    raw_secret = "credential-must-never-be-returned"

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            base_url=SRT_PROVIDER_ADAPTER_ORIGIN,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client,
    ):
        ready = await client.get("/readyz")
        unauthorized = await client.get("/v1/session-status")
        authorized = await client.get(
            "/v1/session-status",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        invalid = await client.post(
            "/v1/prewarm-or-verify-login",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "operation": "verify",
                "credential": {
                    "login_method": "membership_number",
                    "login_id": raw_secret,
                    "password": raw_secret,
                    "credential_version": 7,
                    "unexpected": raw_secret,
                },
            },
        )

    assert ready.status_code == 200
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.headers["cache-control"] == "no-store"
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "request_validation_failed"}
    assert raw_secret not in invalid.text
    assert invalid.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "url",
    [
        "https://srt-provider-adapter:8002",
        "http://srt-provider-adapter:8003",
        "http://localhost:8002",
        "http://srt-provider-adapter:8002/path",
        "http://user@srt-provider-adapter:8002",
        "http://srt-provider-adapter:8002?token=bad",
    ],
)
def test_client_and_settings_reject_non_internal_adapter_urls(url: str) -> None:
    with pytest.raises(ValueError, match="exact internal sidecar origin"):
        SrtProviderAdapterClient(url, 10, TOKEN)
    with pytest.raises(ValidationError, match="SRT_PROVIDER_ADAPTER_URL"):
        Settings(_env_file=None, srt_provider_adapter_url=url)


def test_adapter_token_is_strong_and_secret_repr_is_redacted() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        SrtProviderAdapterClient(SRT_PROVIDER_ADAPTER_ORIGIN, 10, "short")
    with pytest.raises(ValidationError, match="SRT_PROVIDER_ADAPTER_TOKEN"):
        Settings(
            _env_file=None,
            srt_provider_adapter_enabled=True,
            srt_provider_adapter_token="short",
        )

    credential = SrtCredentialRequest(
        login_method="membership_number",
        login_id="1234567890",
        password="not-visible-in-repr",
        credential_version=1,
    )
    assert "1234567890" not in repr(credential)
    assert "not-visible-in-repr" not in repr(credential)


def test_timetable_search_contract_is_strict_and_timezone_aware() -> None:
    valid = {
        "origin": "수서",
        "destination": "부산",
        "departure_from": "2026-08-03T12:00:00+09:00",
        "departure_to": "2026-08-03T18:00:00+09:00",
        "passenger_count": 1,
    }
    assert SrtTimetableSearchRequest.model_validate(valid).passenger_count == 1

    for invalid in (
        {**valid, "departure_from": "2026-08-03T12:00:00"},
        {**valid, "passenger_count": 2},
        {**valid, "unexpected": "not-allowed"},
        {**valid, "departure_to": "2026-08-04T01:00:00+09:00"},
    ):
        with pytest.raises(ValidationError):
            SrtTimetableSearchRequest.model_validate(invalid)

    with pytest.raises(ValidationError):
        SrtTimetableTrain.model_validate(
            {
                "train_number": "329",
                "train_type": "SRT",
                "origin": "수서",
                "destination": "부산",
                "departure_at": "2026-08-03T12:30:00",
                "arrival_at": "2026-08-03T15:30:00+09:00",
                "standard_status": "sold_out",
                "first_status": "available",
                "observed_at": datetime.now(UTC),
            }
        )


@pytest.mark.asyncio
async def test_client_does_not_follow_redirects_or_accept_unvalidated_responses() -> None:
    async def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "http://example.invalid/secret"})

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(redirect),
    )
    with pytest.raises(SrtProviderAdapterUnavailable, match="HTTP 307"):
        await client.session_status()
    await client.aclose()
