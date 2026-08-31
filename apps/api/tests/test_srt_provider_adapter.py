from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from rail_waitlist import srt_provider_adapter_service as adapter_service
from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.provider_account_management.contracts import ProviderCredentials
from rail_waitlist.provider_call_context import (
    REQUEST_ID_HEADER,
    bind_request_id,
    current_request_id,
    validated_log_id,
)
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationDiagnosticCode,
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
from rail_waitlist.srt_provider_adapter_service import create_srt_provider_adapter_app
from rail_waitlist.srt_sidecar.client import (
    SRT_PROVIDER_ADAPTER_ORIGIN,
    SrtProviderAdapterClient,
    SrtProviderAdapterUnavailable,
)
from rail_waitlist.srt_sidecar.contracts import (
    SrtCredentialRequest,
    SrtReservationConfirmationResult,
    SrtTimetableSearchRequest,
    SrtTimetableTrain,
)
from rail_waitlist.srt_sidecar.read_only_lifecycle import (
    READ_ONLY_CALL_ID_HEADER,
    SrtReadOnlyCallRegistry,
)
from rail_waitlist.srt_sidecar.session_contract import (
    SrtSessionActorSnapshot,
    SrtSessionActorState,
)

TOKEN = "srt-sidecar-contract-token-value-32-bytes"


async def test_srt_sidecar_uses_operational_cache_and_cooldown_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SRT_SEAT_STATUS_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SRT_SEAT_STATUS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS", raising=False)

    source, redis = adapter_service._build_default_source()
    try:
        assert source.cache_ttl_seconds == 1
        assert source.timeout_seconds == 60
        assert source.rate_limit_cooldown_seconds == 300
        assert source.protection_cooldown_seconds == 60
    finally:
        await redis.aclose()


class FakeSource:
    def __init__(self) -> None:
        self.observe_calls = 0
        self.overlay_calls = 0
        self.timetable_calls = 0
        self.drain_calls = 0
        self.request_ids: list[str | None] = []
        self.pending_request_ids: set[str] = set()
        self.deferred_until = datetime.now(UTC) + timedelta(minutes=2)

    async def observation_deferred_until(self):
        return self.deferred_until

    async def observe(self, request, *, origin, destination):
        self.observe_calls += 1
        self.request_ids.append(current_request_id())
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

    async def read_only_call_pending(self, request_id: str) -> bool:
        self.request_ids.append(request_id)
        return request_id in self.pending_request_ids


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
async def test_sidecar_client_contract_reuses_process_owned_source_and_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
        confirmation_request_id = "42b41ae2322242b18e98ec989d09a994"
        with caplog.at_level(logging.INFO), bind_request_id(confirmation_request_id):
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
    confirmation_logs = [
        record.getMessage()
        for record in caplog.records
        if "operation=confirm_reservation" in record.getMessage()
        and f"request_id={confirmation_request_id}" in record.getMessage()
    ]
    assert any(
        "event=provider_confirmation_completed" in message
        and "outcome=not_found" in message
        and "diagnostic_code=none" in message
        and "source=test-srt-confirmation" in message
        and "phase=completed" in message
        for message in confirmation_logs
    )
    assert any(
        "event=provider_sidecar_request_completed" in message
        and "terminal_outcome=not_found" in message
        and "diagnostic_code=none" in message
        and "source=test-srt-confirmation" in message
        and "phase=completed" in message
        for message in confirmation_logs
    )
    assert source.observe_calls == 1
    assert source.overlay_calls == 1
    assert source.timetable_calls == 1
    assert source.drain_calls == 1
    assert executor.login_versions == [7, 7]
    assert executor.reserve_versions == [7]
    assert executor.confirm_arrivals == [datetime(2026, 8, 3, 14, 30, tzinfo=UTC)]
    assert executor.confirm_versions == [7]


def test_srt_confirmation_wire_requires_and_round_trips_closed_diagnostic() -> None:
    now = datetime(2026, 8, 3, 3, 29, tzinfo=UTC)
    valid = SrtReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        diagnostic_code=(ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS),
        source="test-srt-confirmation",
        observed_at=now,
    )

    assert valid.model_dump(mode="json")["diagnostic_code"] == "official_record_ambiguous"
    assert (
        valid.to_domain().diagnostic_code
        is ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS
    )
    with pytest.raises(ValidationError, match="requires a diagnostic code"):
        SrtReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="test-srt-confirmation",
            observed_at=now,
        )
    with pytest.raises(ValidationError, match="requires a diagnostic code"):
        SrtReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            diagnostic_code=(ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT),
            source="test-srt-confirmation",
            observed_at=now,
        )


@pytest.mark.asyncio
async def test_srt_inconclusive_wire_and_logs_preserve_closed_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class InconclusiveExecutor(FakeExecutor):
        async def confirm_reservation(self, target, _credentials):
            return ReservationConfirmationResult(
                provider=target.provider,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                diagnostic_code=(ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS),
                source="test-srt-confirmation",
                observed_at=datetime.now(UTC),
            )

    app = create_srt_provider_adapter_app(
        source=FakeSource(),
        executor=InconclusiveExecutor(),
        token=TOKEN,
        monotonic=lambda: 20.0,
    )
    request_id = "52b41ae2322242b18e98ec989d09a994"
    async with app.router.lifespan_context(app):
        client = SrtProviderAdapterClient(
            SRT_PROVIDER_ADAPTER_ORIGIN,
            10,
            TOKEN,
            transport=httpx.ASGITransport(app=app),
        )
        with caplog.at_level(logging.INFO), bind_request_id(request_id):
            result = await client.confirm_reservation(
                confirmation_target(),
                ProviderCredentials("1234567890", "private-password", 7),
            )
        await client.aclose()

    assert result.diagnostic_code is ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS
    correlated = [
        record.getMessage()
        for record in caplog.records
        if f"request_id={request_id}" in record.getMessage()
        and "operation=confirm_reservation" in record.getMessage()
    ]
    assert sum("diagnostic_code=official_record_ambiguous" in item for item in correlated) == 2
    assert all("private-password" not in item for item in correlated)


@pytest.mark.asyncio
async def test_srt_confirmation_exception_log_is_correlated_and_secret_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "srt-confirmation-secret-must-not-appear"

    class FailingExecutor(FakeExecutor):
        async def confirm_reservation(self, _target, _credentials):
            raise RuntimeError(secret)

    app = create_srt_provider_adapter_app(
        source=FakeSource(),
        executor=FailingExecutor(),
        token=TOKEN,
        monotonic=lambda: 20.0,
    )
    request_id = "62b41ae2322242b18e98ec989d09a994"
    async with app.router.lifespan_context(app):
        client = SrtProviderAdapterClient(
            SRT_PROVIDER_ADAPTER_ORIGIN,
            10,
            TOKEN,
            transport=httpx.ASGITransport(app=app),
        )
        with caplog.at_level(logging.INFO), bind_request_id(request_id):
            with pytest.raises(SrtProviderAdapterUnavailable):
                await client.confirm_reservation(
                    confirmation_target(),
                    ProviderCredentials("1234567890", "private-password", 7),
                )
        await client.aclose()

    assert (
        "event=provider_confirmation_failed provider=SRT operation=confirm_reservation "
        f"request_id={request_id} outcome=inconclusive "
        "diagnostic_code=official_read_unavailable "
        "source=srtrain-reservation-list phase=official_read"
    ) in caplog.text
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_client_drain_waits_for_only_its_pending_read_only_request() -> None:
    request_id = "5790e635307c4549a7728d01455bf92c"
    instance_id = "768e0ce66bce4cc2af9ef152ea25d831"
    registered_call_id: str | None = None
    status_calls = 0
    terminal = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal registered_call_id, status_calls
        if request.url.path == "/v1/read-only-call-register":
            registration = json.loads(request.content)
            assert registration["request_id"] == request_id
            registered_call_id = registration["call_id"]
            assert request.headers[REQUEST_ID_HEADER] != request_id
            return httpx.Response(
                200,
                json={"accepted": True, "instance_id": instance_id},
            )
        if request.url.path == "/v1/observe":
            assert request.headers[READ_ONLY_CALL_ID_HEADER] == registered_call_id
            return httpx.Response(
                200,
                json={"observations": []},
                headers={REQUEST_ID_HEADER: request_id},
            )
        if request.url.path == "/v1/read-only-call-status":
            status_calls += 1
            assert request.headers[REQUEST_ID_HEADER] != request_id
            assert request.url.params["call_id"] == registered_call_id
            return httpx.Response(
                200,
                json={
                    "state": "terminal" if terminal.is_set() else "pending",
                    "instance_id": instance_id,
                },
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(respond),
    )
    with bind_request_id(request_id):
        await client.observe(observation_request(), origin="수서", destination="부산")

    drain = asyncio.create_task(client.drain_pending_calls())
    await asyncio.sleep(0.03)
    assert not drain.done()
    assert status_calls >= 1

    terminal.set()
    await asyncio.wait_for(drain, timeout=1)
    await client.aclose()

    assert status_calls >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "http", "malformed", "unknown"])
async def test_client_drain_retries_unknown_status_failures(failure: str) -> None:
    request_id = "5790e635307c4549a7728d01455bf92c"
    instance_id = "768e0ce66bce4cc2af9ef152ea25d831"
    observed = asyncio.Event()
    failure_sent = False
    status_calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal failure_sent, status_calls
        if request.url.path == "/v1/read-only-call-register":
            return httpx.Response(
                200,
                json={"accepted": True, "instance_id": instance_id},
            )
        if request.url.path == "/v1/observe":
            observed.set()
            return httpx.Response(200, json={"observations": []})
        if request.url.path == "/v1/read-only-call-status":
            status_calls += 1
            if not observed.is_set():
                return httpx.Response(
                    200,
                    json={"state": "pending", "instance_id": instance_id},
                )
            if not failure_sent:
                failure_sent = True
                if failure == "transport":
                    raise httpx.ConnectError("temporary status failure", request=request)
                if failure == "http":
                    return httpx.Response(503, json={})
                if failure == "unknown":
                    return httpx.Response(
                        200,
                        json={"state": "unknown", "instance_id": instance_id},
                    )
                return httpx.Response(
                    200,
                    json={"state": "invalid", "instance_id": instance_id},
                )
            return httpx.Response(
                200,
                json={"state": "terminal", "instance_id": instance_id},
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(respond),
    )
    with bind_request_id(request_id):
        await client.observe(observation_request(), origin="수서", destination="부산")

    await asyncio.wait_for(client.drain_pending_calls(), timeout=1)
    await client.aclose()

    assert failure_sent is True
    assert status_calls >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status_mode", ["pending", "unknown", "transport_error"])
async def test_client_read_only_drain_deadline_fences_all_later_requests(
    status_mode: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "5790e635307c4549a7728d01455bf92c"
    instance_id = "768e0ce66bce4cc2af9ef152ea25d831"
    request_paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        if request.url.path == "/v1/read-only-call-register":
            return httpx.Response(200, json={"accepted": True, "instance_id": instance_id})
        if request.url.path == "/v1/observe":
            return httpx.Response(200, json={"observations": []})
        if request.url.path == "/v1/read-only-call-status":
            if status_mode == "transport_error":
                raise httpx.ConnectError("persistent status failure", request=request)
            return httpx.Response(
                200,
                json={"state": status_mode, "instance_id": instance_id},
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(respond),
        read_only_drain_timeout_seconds=0.03,
    )
    with bind_request_id(request_id):
        await client.observe(observation_request(), origin="수서", destination="부산")

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(SrtProviderAdapterUnavailable, match="fenced after read-only drain"),
    ):
        await asyncio.wait_for(client.drain_pending_calls(), timeout=0.5)

    credentials = ProviderCredentials("1234567890", "private-password", 7)
    with pytest.raises(SrtProviderAdapterUnavailable, match="fenced after read-only drain"):
        await client.reserve_once(reservation_request(), credentials)
    with pytest.raises(SrtProviderAdapterUnavailable, match="fenced after read-only drain"):
        await client.aclose()

    assert "/v1/reserve-once" not in request_paths
    assert "event=provider_sidecar_drain_deadline_exceeded" in caplog.text


@pytest.mark.asyncio
async def test_outer_read_timeout_still_drains_the_pre_registered_call() -> None:
    request_id = "5790e635307c4549a7728d01455bf92c"
    instance_id = "768e0ce66bce4cc2af9ef152ea25d831"
    registered = asyncio.Event()
    terminal = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/read-only-call-register":
            registered.set()
            return httpx.Response(
                200,
                json={"accepted": True, "instance_id": instance_id},
            )
        if request.url.path == "/v1/observe":
            raise httpx.ReadTimeout("outer timeout", request=request)
        if request.url.path == "/v1/read-only-call-status":
            return httpx.Response(
                200,
                json={
                    "state": "terminal" if terminal.is_set() else "pending",
                    "instance_id": instance_id,
                },
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(respond),
    )
    with bind_request_id(request_id), pytest.raises(SrtProviderAdapterUnavailable):
        await client.observe(observation_request(), origin="수서", destination="부산")

    assert registered.is_set()
    drain = asyncio.create_task(client.drain_pending_calls())
    await asyncio.sleep(0.06)
    assert not drain.done()

    terminal.set()
    await asyncio.wait_for(drain, timeout=1)
    await client.aclose()


@pytest.mark.asyncio
async def test_status_rpc_is_authenticated_and_does_not_track_itself() -> None:
    source = FakeSource()
    tracked_request_id = "768e0ce66bce4cc2af9ef152ea25d831"
    tracked_call_id = "5790e635307c4549a7728d01455bf92c"
    source.pending_request_ids.add(tracked_request_id)
    app = create_srt_provider_adapter_app(source=source, executor=FakeExecutor(), token=TOKEN)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            base_url=SRT_PROVIDER_ADAPTER_ORIGIN,
            transport=httpx.ASGITransport(app=app),
            trust_env=False,
        ) as client,
    ):
        unauthorized = await client.get(
            "/v1/read-only-call-status",
            params={"call_id": tracked_call_id},
        )
        register_request_id = "c53c460ef7f54aa6b83f870e9b110a22"
        registered = await client.post(
            "/v1/read-only-call-register",
            json={"call_id": tracked_call_id, "request_id": tracked_request_id},
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: register_request_id,
            },
        )
        observed = await client.post(
            "/v1/observe",
            json={
                "request": observation_request().model_dump(mode="json"),
                "origin": "수서",
                "destination": "부산",
            },
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: tracked_request_id,
                READ_ONLY_CALL_ID_HEADER: tracked_call_id,
            },
        )
        status_request_id = "a32cb303bc2d4c18823d510fb86f12dc"
        pending = await client.get(
            "/v1/read-only-call-status",
            params={"call_id": tracked_call_id},
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: status_request_id,
            },
        )

    assert unauthorized.status_code == 401
    assert registered.status_code == 200
    assert observed.status_code == 200
    assert pending.status_code == 200
    assert pending.json()["state"] == "pending"
    assert validated_log_id(pending.json()["instance_id"]) is not None
    assert source.request_ids == [tracked_request_id, tracked_request_id]
    assert pending.headers[REQUEST_ID_HEADER] == status_request_id


@pytest.mark.asyncio
async def test_read_only_registry_distinguishes_unknown_pending_and_terminal_tombstone() -> None:
    source = FakeSource()
    now = [10.0]
    call_id = "5790e635307c4549a7728d01455bf92c"
    request_id = "768e0ce66bce4cc2af9ef152ea25d831"
    registry = SrtReadOnlyCallRegistry(
        source,
        registration_grace_seconds=2,
        terminal_tombstone_seconds=3,
        monotonic=lambda: now[0],
    )

    assert await registry.status(call_id) == "unknown"
    assert await registry.register(call_id, request_id) is True
    assert await registry.status(call_id) == "pending"

    now[0] = 12.0
    assert await registry.status(call_id) == "terminal"
    assert await registry.begin(call_id, request_id) is False

    now[0] = 15.0
    assert await registry.status(call_id) == "unknown"


@pytest.mark.asyncio
async def test_registry_lazily_expires_orphans_and_bounds_terminal_tombstones() -> None:
    source = FakeSource()
    now = [10.0]
    first_call_id = "5790e635307c4549a7728d01455bf92c"
    second_call_id = "a32cb303bc2d4c18823d510fb86f12dc"
    third_call_id = "c53c460ef7f54aa6b83f870e9b110a22"
    request_id = "768e0ce66bce4cc2af9ef152ea25d831"
    registry = SrtReadOnlyCallRegistry(
        source,
        registration_grace_seconds=1,
        terminal_tombstone_seconds=10,
        max_terminal_tombstones=1,
        monotonic=lambda: now[0],
    )

    assert await registry.register(first_call_id, request_id) is True
    now[0] = 11.0
    assert await registry.register(second_call_id, request_id) is True
    assert await registry.status(first_call_id) == "terminal"

    now[0] = 12.0
    assert await registry.register(third_call_id, request_id) is True
    assert await registry.status(first_call_id) == "unknown"
    assert await registry.status(second_call_id) == "terminal"


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


@pytest.mark.asyncio
async def test_srt_sidecar_validates_echoes_and_resets_internal_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = FakeSource()
    app = create_srt_provider_adapter_app(source=source, executor=FakeExecutor(), token=TOKEN)
    valid_request_id = "768e0ce66bce4cc2af9ef152ea25d831"
    malicious = "invalid-id request_id=ffffffffffffffffffffffffffffffff"

    with caplog.at_level(logging.INFO):
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                base_url=SRT_PROVIDER_ADAPTER_ORIGIN,
                transport=httpx.ASGITransport(app=app),
                trust_env=False,
                follow_redirects=False,
            ) as client,
        ):
            accepted = await client.post(
                "/v1/observe",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    REQUEST_ID_HEADER: valid_request_id,
                },
                json={
                    "request": observation_request().model_dump(mode="json"),
                    "origin": "수서",
                    "destination": "부산",
                },
            )
            replaced = await client.post(
                "/v1/observe",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    REQUEST_ID_HEADER: malicious,
                },
                json={
                    "request": observation_request().model_dump(mode="json"),
                    "origin": "수서",
                    "destination": "부산",
                },
            )
            unauthorized = await client.get(
                "/v1/session-status",
                headers={REQUEST_ID_HEADER: valid_request_id},
            )

    replaced_request_id = replaced.headers[REQUEST_ID_HEADER]
    # Direct authenticated compatibility callers may omit lifecycle registration;
    # the canonical worker client always supplies it and is the only drain-safe path.
    assert accepted.status_code == 200
    assert replaced.status_code == 200
    assert accepted.headers[REQUEST_ID_HEADER] == valid_request_id
    assert source.request_ids[0] == valid_request_id
    assert validated_log_id(replaced_request_id) == replaced_request_id
    assert replaced_request_id != malicious
    assert source.request_ids[1] == replaced_request_id
    assert REQUEST_ID_HEADER not in unauthorized.headers
    assert malicious not in caplog.text
    assert current_request_id() is None


@pytest.mark.asyncio
async def test_legacy_login_exception_reassignment_keeps_outcome_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LegacyLoginError(Exception):
        pass

    class FailingExecutor(FakeExecutor):
        async def verify_credentials(self, credentials):
            raise LegacyLoginError

    monkeypatch.setattr(adapter_service, "SRTLoginError", LegacyLoginError)
    app = create_srt_provider_adapter_app(
        source=FakeSource(),
        executor=FailingExecutor(),
        token=TOKEN,
    )

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            base_url=SRT_PROVIDER_ADAPTER_ORIGIN,
            transport=httpx.ASGITransport(app=app),
            trust_env=False,
            follow_redirects=False,
        ) as client,
    ):
        response = await client.post(
            "/v1/prewarm-or-verify-login",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "operation": "verify",
                "credential": {
                    "login_method": "membership_number",
                    "login_id": "1234567890",
                    "password": "private-password",
                    "credential_version": 7,
                },
            },
        )

    assert response.status_code == 200
    assert response.json() == {"outcome": "auth_required"}


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


@pytest.mark.parametrize("outer_timeout", [59, 60])
def test_settings_require_outer_srt_sidecar_timeout_to_exceed_operation_budget(
    outer_timeout: float,
) -> None:
    with pytest.raises(ValidationError, match="must be greater"):
        Settings(
            _env_file=None,
            srt_provider_adapter_enabled=True,
            srt_provider_adapter_token=TOKEN,
            srt_seat_status_timeout_seconds=60,
            srt_provider_adapter_timeout_seconds=outer_timeout,
        )


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


@pytest.mark.asyncio
async def test_srt_client_propagates_request_id_and_logs_closed_lifecycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "5790e635307c4549a7728d01455bf92c"
    captured_headers: list[httpx.Headers] = []

    async def success(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers)
        return httpx.Response(
            200,
            json={"state": "cold", "locally_reusable": False},
        )

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(success),
    )
    with caplog.at_level(logging.INFO), bind_request_id(request_id):
        result = await client.session_status()
    await client.aclose()

    assert result.state is SrtSessionActorState.COLD
    assert captured_headers[0][REQUEST_ID_HEADER] == request_id
    lifecycle = [
        record.getMessage()
        for record in caplog.records
        if "provider_sidecar_request_" in record.getMessage()
    ]
    assert len(lifecycle) == 2
    assert all(f"request_id={request_id}" in message for message in lifecycle)
    assert "event=provider_sidecar_request_started" in lifecycle[0]
    assert "event=provider_sidecar_request_completed" in lifecycle[1]
    assert current_request_id() is None


@pytest.mark.asyncio
async def test_srt_client_generates_a_new_request_id_for_each_unbound_call() -> None:
    captured_headers: list[httpx.Headers] = []

    async def success(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers)
        return httpx.Response(
            200,
            json={"state": "cold", "locally_reusable": False},
        )

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(success),
    )
    await client.session_status()
    await client.session_status()
    await client.aclose()

    request_ids = [headers[REQUEST_ID_HEADER] for headers in captured_headers]
    assert len(set(request_ids)) == 2
    assert all(validated_log_id(request_id) == request_id for request_id in request_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (httpx.ReadTimeout("timeout"), "timeout"),
        (httpx.ConnectError("offline"), "transport_error"),
    ],
)
async def test_srt_client_logs_closed_transport_failure(
    error: httpx.HTTPError,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        error.request = request
        raise error

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(fail),
    )
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(SrtProviderAdapterUnavailable),
    ):
        await client.session_status()
    await client.aclose()

    assert "event=provider_sidecar_request_failed" in caplog.text
    assert f"outcome={outcome}" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "outcome"),
    [
        (503, {}, "http_status"),
        (200, {}, "validation_error"),
    ],
)
async def test_srt_client_logs_closed_response_failures(
    status_code: int,
    payload: object,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    client = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(respond),
    )
    with caplog.at_level(logging.WARNING), pytest.raises(SrtProviderAdapterUnavailable):
        await client.session_status()
    await client.aclose()

    assert "event=provider_sidecar_request_failed" in caplog.text
    assert f"outcome={outcome}" in caplog.text
