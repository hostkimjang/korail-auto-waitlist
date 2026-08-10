from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from rail_waitlist.domain import ReservationOutcome
from rail_waitlist.korail_browser_automation import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
)
from rail_waitlist.korail_browser_seat_source import (
    KorailBrowserSeatSource,
    _AdapterFailure,
)
from rail_waitlist.korail_reservation_contract import (
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
    KorailReservationOutcomeValue,
    KorailReserveOnceRequest,
    KorailReserveOnceResult,
)
from rail_waitlist.korail_search_bootstrap import (
    KorailStationIdentity,
    build_korail_general_search_url,
)
from rail_waitlist.provider_accounts import ProviderCredentials
from rail_waitlist.provider_login_verification import ProviderLoginVerificationOutcome
from rail_waitlist.schemas import (
    ReservationRequest,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    TimetableItem,
)
from rail_waitlist.seat_status_cooldown import MemoryCooldownStore

KOREA = ZoneInfo("Asia/Seoul")


class FakeTransport:
    def __init__(
        self,
        result: BrowserSeatSearchResult | None = None,
        *,
        error: Exception | None = None,
        reservation_result: KorailReserveOnceResult | None = None,
    ) -> None:
        self.result = result or browser_result()
        self.error = error
        self.reservation_result = reservation_result or KorailReserveOnceResult(
            outcome="payment_required",
            reason="payment_required",
            seat_clicked=True,
            reservation_clicked=True,
        )
        self.calls = 0
        self.requests: list[BrowserSeatSearchRequest] = []
        self.login_requests: list[KorailLoginVerifyRequest] = []
        self.reservation_requests: list[KorailReserveOnceRequest] = []
        self.closed = False
        self.gate = asyncio.Event()
        self.gate.set()

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.calls += 1
        self.requests.append(request)
        await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result

    async def verify_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        self.login_requests.append(request)
        if self.error is not None:
            raise self.error
        return KorailLoginVerifyResult(outcome="authenticated")

    async def reserve(self, request: KorailReserveOnceRequest) -> KorailReserveOnceResult:
        self.reservation_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reservation_result

    async def reserve_with_progress(self, request, on_progress):
        self.reservation_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.reservation_result

    async def close(self) -> None:
        self.closed = True


def timetable_item(train_number: str = "43") -> TimetableItem:
    departure = datetime(2026, 8, 3, 15, 45, tzinfo=KOREA)
    unknown = SeatAvailabilityProvenance(kind="not_observed", reason="source_not_configured")
    return TimetableItem(
        provider="korail",
        train_number=train_number,
        train_type="KTX",
        origin="서울",
        destination="부산",
        departure_at=departure,
        arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
        timetable_source="TAGO",
        timetable_retrieved_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
        seat_classes=[
            SeatClassAvailability(seat_class="standard", status="unknown", provenance=unknown),
            SeatClassAvailability(seat_class="first", status="unknown", provenance=unknown),
        ],
        official_booking_url="https://www.korail.com/ticket/search",
    )


def browser_result(train_number: str = "43") -> BrowserSeatSearchResult:
    return BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        passenger_count=1,
        observed_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
        trains=[
            BrowserTrainSnapshot(
                train_number=train_number,
                train_type="KTX",
                departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
                arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
                standard="standing_plus_seat",
                first="sold_out",
                adult_fare=59_800,
            )
        ],
    )


def observation_request(
    *,
    train_number: str = "43",
    departure_at: datetime | None = None,
    seat_class: str = "standard",
) -> SeatObservationRequest:
    return SeatObservationRequest(
        provider="korail",
        origin_node_id="NAT010000",
        destination_node_id="NAT014445",
        origin="서울",
        destination="부산",
        train_number=train_number,
        departure_at=departure_at or datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
        seat_class=seat_class,
        passenger_count=1,
    )


def source(
    transport: FakeTransport,
    *,
    cooldown_store: MemoryCooldownStore | None = None,
    now: datetime | None = None,
) -> KorailBrowserSeatSource:
    return KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://adapter.invalid",
        cache_ttl_seconds=30,
        timeout_seconds=35,
        rate_limit_cooldown_seconds=1800,
        protection_cooldown_seconds=300,
        transport=transport,
        monotonic=lambda: 100.0,
        now=lambda: now or datetime(2026, 8, 2, 2, 47, tzinfo=KOREA),
        cooldown_store=cooldown_store,
    )


def overlay_arguments(passenger_count: int = 1) -> dict[str, object]:
    return {
        "origin": "서울",
        "destination": "부산",
        "departure_from": datetime(2026, 8, 3, 14, tzinfo=KOREA),
        "departure_to": datetime(2026, 8, 3, 18, tzinfo=KOREA),
        "passenger_count": passenger_count,
    }


async def test_primary_timetable_preserves_separate_official_search_url() -> None:
    search_url = build_korail_general_search_url(
        origin=KorailStationIdentity("0001", "서울"),
        destination=KorailStationIdentity("0020", "부산"),
        travel_date=date(2026, 8, 3),
        departure_time=time(0),
    )
    transport = FakeTransport(
        browser_result().model_copy(update={"official_search_url": search_url})
    )

    result = await source(transport).search_timetable(**overlay_arguments())

    assert str(result[0].official_booking_url) == "https://www.korail.com/ticket/search/general"
    assert str(result[0].official_search_url) == search_url


async def test_exact_browser_snapshot_overlays_status_and_actions() -> None:
    transport = FakeTransport()
    seat_source = source(transport)

    result = await seat_source.overlay([timetable_item()], **overlay_arguments())
    await seat_source.close()

    standard, first = result[0].seat_classes
    assert standard.status == "standing_plus_seat"
    assert [action.kind for action in standard.actions] == ["official_check", "add_to_watch"]
    assert first.status == "sold_out"
    assert [action.kind for action in first.actions] == ["add_to_watch"]
    assert standard.provenance.kind == "official_provider"
    assert standard.provenance.source == "korail-official-page-browser"
    assert transport.requests[0].departure_from == time(0, 0)
    assert transport.requests[0].departure_to == time(18, 0)
    assert transport.closed is True


async def test_same_day_morning_query_starts_at_requested_future_hour_in_kst() -> None:
    service_date = date(2026, 8, 2)
    departure = datetime(2026, 8, 2, 5, 30, tzinfo=KOREA)
    item = timetable_item().model_copy(
        update={
            "departure_at": departure,
            "arrival_at": departure + timedelta(hours=2, minutes=45),
        }
    )
    result = browser_result().model_copy(
        update={
            "travel_date": service_date,
            "trains": [
                browser_result().trains[0].model_copy(update={"departure_at": departure})
            ],
        }
    )
    transport = FakeTransport(result)
    seat_source = source(
        transport,
        now=datetime(2026, 8, 2, 2, 47, tzinfo=KOREA),
    )

    overlaid = await seat_source.overlay(
        [item],
        origin="서울",
        destination="부산",
        departure_from=datetime(2026, 8, 2, 5, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 2, 9, tzinfo=KOREA),
        passenger_count=1,
    )

    assert transport.calls == 1
    assert transport.requests[0].departure_from == time(5)
    assert transport.requests[0].departure_to == time(9)
    assert {seat.status for seat in overlaid[0].seat_classes} == {
        "standing_plus_seat",
        "sold_out",
    }


async def test_same_day_past_window_skips_browser_in_kst() -> None:
    departure = datetime(2026, 8, 2, 1, 30, tzinfo=KOREA)
    item = timetable_item().model_copy(
        update={
            "departure_at": departure,
            "arrival_at": departure + timedelta(hours=2, minutes=45),
        }
    )
    transport = FakeTransport()
    seat_source = source(
        transport,
        now=datetime(2026, 8, 2, 2, 47, tzinfo=KOREA),
    )

    marked = await seat_source.overlay(
        [item],
        origin="서울",
        destination="부산",
        departure_from=datetime(2026, 8, 2, 0, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 2, 2, tzinfo=KOREA),
        passenger_count=1,
    )

    assert transport.calls == 0
    assert {seat.provenance.reason for seat in marked[0].seat_classes} == {
        "departure_window_elapsed"
    }


async def test_login_verification_delegates_once_without_timetable_search() -> None:
    transport = FakeTransport()
    seat_source = source(transport)

    result = await seat_source.verify_login(
        ProviderCredentials(
            login_method="email",
            login_id="member@example.com",
            password="not-a-real-password",
            credential_version=4,
        )
    )

    assert result.outcome is ProviderLoginVerificationOutcome.AUTHENTICATED
    assert len(transport.login_requests) == 1
    assert transport.login_requests[0].credential.login_method == "email"
    assert transport.login_requests[0].credential.version == "4"
    assert transport.calls == 0


async def test_reservation_preserves_verified_phone_login_method() -> None:
    transport = FakeTransport()
    seat_source = source(transport)
    departure_at = datetime(2026, 8, 3, 15, 45, tzinfo=KOREA)

    result = await seat_source.reserve_once(
        ReservationRequest(
            provider="korail",
            origin_node_id="NAT010000",
            destination_node_id="NAT014445",
            origin="서울",
            destination="부산",
            train_number="43",
            departure_at=departure_at,
            arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
            seat_class="standard",
            passenger_count=1,
            candidate_id="candidate-phone-login",
            idempotency_key="reserve:candidate-phone-login",
        ),
        ProviderCredentials(
            login_method="phone",
            login_id="01000000000",
            password="not-a-real-password",
            credential_version=7,
        ),
    )

    assert result.outcome.value == "payment_required"
    assert len(transport.reservation_requests) == 1
    assert transport.reservation_requests[0].credential.login_method == "phone"
    assert transport.calls == 0


async def test_uncertain_progress_stream_failure_is_a_no_replay_unknown_fence() -> None:
    transport = FakeTransport(
        error=_AdapterFailure(
            "source_unavailable",
            reservation_command_uncertain=True,
        )
    )
    departure_at = datetime(2026, 8, 3, 15, 45, tzinfo=KOREA)

    async def on_progress(stage):
        return None

    result = await source(transport).reserve_once_with_progress(
        ReservationRequest(
            provider="korail",
            origin_node_id="NAT010000",
            destination_node_id="NAT014445",
            origin="서울",
            destination="부산",
            train_number="43",
            departure_at=departure_at,
            arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
            seat_class="standard",
            passenger_count=1,
            candidate_id="candidate-stream-uncertain",
            idempotency_key="reserve:candidate-stream-uncertain",
        ),
        ProviderCredentials(
            login_method="phone",
            login_id="01000000000",
            password="not-a-real-password",
            credential_version=7,
        ),
        on_progress,
    )

    assert result.outcome is ReservationOutcome.UNKNOWN
    assert len(transport.reservation_requests) == 1


@pytest.mark.parametrize("sidecar_outcome", ["consent_required", "action_required"])
async def test_manual_action_reservation_outcomes_remain_unknown(
    sidecar_outcome: KorailReservationOutcomeValue,
) -> None:
    transport = FakeTransport(
        reservation_result=KorailReserveOnceResult(
            outcome=sidecar_outcome,
            reason="official_manual_action_required",
            seat_clicked=True,
            reservation_clicked=False,
        )
    )

    result = await source(transport).reserve_once(
        ReservationRequest(
            provider="korail",
            origin_node_id="NAT010000",
            destination_node_id="NAT014445",
            origin="서울",
            destination="부산",
            train_number="43",
            departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
            arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
            seat_class="standard",
            passenger_count=1,
            candidate_id=f"candidate-{sidecar_outcome}",
            idempotency_key=f"reserve:candidate-{sidecar_outcome}",
        ),
        ProviderCredentials(
            login_method="membership_number",
            login_id="fixture-member",
            password="not-a-real-password",
            credential_version=3,
        ),
    )

    assert result.outcome is ReservationOutcome.UNKNOWN


async def test_true_auth_required_reservation_outcome_remains_auth_required() -> None:
    transport = FakeTransport(
        reservation_result=KorailReserveOnceResult(
            outcome="auth_required",
            reason="official_auth_required",
            seat_clicked=True,
            reservation_clicked=False,
        )
    )

    result = await source(transport).reserve_once(
        ReservationRequest(
            provider="korail",
            origin_node_id="NAT010000",
            destination_node_id="NAT014445",
            origin="서울",
            destination="부산",
            train_number="43",
            departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
            arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
            seat_class="standard",
            passenger_count=1,
            candidate_id="candidate-auth-required",
            idempotency_key="reserve:candidate-auth-required",
        ),
        ProviderCredentials(
            login_method="membership_number",
            login_id="fixture-member",
            password="not-a-real-password",
            credential_version=3,
        ),
    )

    assert result.outcome is ReservationOutcome.AUTH_REQUIRED


async def test_reservation_maps_only_sidecar_progress_with_actual_timestamps() -> None:
    progress_times = [
        datetime(2026, 8, 3, 6, 45, 1, tzinfo=UTC),
        datetime(2026, 8, 3, 6, 45, 2, tzinfo=UTC),
        datetime(2026, 8, 3, 6, 45, 3, tzinfo=UTC),
        datetime(2026, 8, 3, 6, 45, 4, tzinfo=UTC),
    ]
    transport = FakeTransport(
        reservation_result=KorailReserveOnceResult(
            outcome="payment_required",
            reason="reservation_pending_payment",
            seat_clicked=True,
            reservation_clicked=True,
            session_ready_at=progress_times[0],
            target_rechecked_at=progress_times[1],
            seat_selected_at=progress_times[2],
            reservation_requested_at=progress_times[3],
        )
    )

    result = await source(transport).reserve_once(
        ReservationRequest(
            provider="korail",
            origin_node_id="NAT010000",
            destination_node_id="NAT014445",
            origin="서울",
            destination="부산",
            train_number="43",
            departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
            arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
            seat_class="standard",
            passenger_count=1,
            candidate_id="candidate-progress",
            idempotency_key="reserve:candidate-progress",
        ),
        ProviderCredentials(
            login_method="membership_number",
            login_id="fixture-member",
            password="not-a-real-password",
            credential_version=3,
        ),
    )

    assert [stage.stage for stage in result.progress_stages] == [
        "authenticated_session_ready",
        "target_rechecked",
        "seat_selected",
        "reservation_requested",
    ]
    assert [stage.occurred_at for stage in result.progress_stages] == progress_times
    assert result.observed_at >= progress_times[-1]


async def test_each_login_verification_reuses_the_verified_credential_generation() -> None:
    transport = FakeTransport()
    seat_source = source(transport)
    credentials = ProviderCredentials(
        login_method="membership_number",
        login_id="fixture-member",
        password="not-a-real-password",
        credential_version=7,
    )

    await seat_source.verify_login(credentials)
    await seat_source.verify_login(credentials)

    versions = [request.credential.version for request in transport.login_requests]
    assert versions == ["7", "7"]


async def test_coupled_cheongryong_auxiliary_number_exact_matches_midnight_query() -> None:
    transport = FakeTransport(browser_result("9032"))

    result = await source(transport).overlay(
        [timetable_item("09032")],
        **overlay_arguments(),
    )

    assert {seat.status for seat in result[0].seat_classes} == {
        "standing_plus_seat",
        "sold_out",
    }
    assert {seat.provenance.kind for seat in result[0].seat_classes} == {
        "official_provider"
    }
    assert transport.requests[0].departure_from == time(0, 0)


async def test_identity_mismatch_remains_unknown() -> None:
    transport = FakeTransport(browser_result("47"))

    result = await source(transport).overlay([timetable_item()], **overlay_arguments())

    assert {seat.status for seat in result[0].seat_classes} == {"unknown"}
    assert {seat.provenance.reason for seat in result[0].seat_classes} == {
        "no_exact_match"
    }


async def test_response_route_identity_mismatch_is_rejected_before_overlay() -> None:
    payload = browser_result()
    transport = FakeTransport(payload.model_copy(update={"origin": "대전"}))

    result = await source(transport).overlay([timetable_item()], **overlay_arguments())

    assert {seat.status for seat in result[0].seat_classes} == {"unknown"}
    assert {seat.provenance.reason for seat in result[0].seat_classes} == {
        "source_unavailable"
    }


async def test_multi_passenger_request_fails_closed_without_browser_call() -> None:
    transport = FakeTransport()

    result = await source(transport).overlay(
        [timetable_item()], **overlay_arguments(passenger_count=2)
    )

    assert transport.calls == 0
    assert {seat.provenance.reason for seat in result[0].seat_classes} == {
        "passenger_count_not_supported"
    }


async def test_singleflight_and_ttl_cache_issue_one_sidecar_request() -> None:
    transport = FakeTransport()
    transport.gate.clear()
    seat_source = source(transport)
    first = asyncio.create_task(
        seat_source.overlay([timetable_item()], **overlay_arguments())
    )
    second = asyncio.create_task(
        seat_source.overlay([timetable_item()], **overlay_arguments())
    )
    await asyncio.sleep(0)
    transport.gate.set()

    await asyncio.gather(first, second)
    await seat_source.overlay([timetable_item()], **overlay_arguments())

    assert transport.calls == 1


async def test_protection_cooldown_blocks_second_sidecar_request() -> None:
    transport = FakeTransport(
        error=_AdapterFailure("provider_access_restricted", protection=True)
    )
    cooldown = MemoryCooldownStore(lambda: 100.0)
    seat_source = source(transport, cooldown_store=cooldown)

    first = await seat_source.overlay([timetable_item()], **overlay_arguments())
    second = await seat_source.overlay([timetable_item()], **overlay_arguments())

    assert transport.calls == 1
    assert {seat.provenance.reason for seat in first[0].seat_classes} == {
        "provider_access_restricted"
    }
    assert {seat.provenance.reason for seat in second[0].seat_classes} == {
        "provider_access_restricted"
    }


async def test_ordinary_failures_never_open_more_than_five_minute_cooldown() -> None:
    now = 100.0
    cooldown = MemoryCooldownStore(lambda: now)
    seat_source = source(FakeTransport(), cooldown_store=cooldown)
    key = BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        departure_from=time(0, 0),
        departure_to=time(18, 0),
        passenger_count=1,
    ).cache_key()

    for _ in range(8):
        await seat_source._open_cooldown(_AdapterFailure("source_unavailable"), key)

    provider_wide = await cooldown.get("korail-browser")
    query_cooldown = seat_source._query_cooldowns[key]

    assert provider_wide is None
    assert query_cooldown.expires_at - now == 300


async def test_empty_today_failure_does_not_block_a_different_service_date() -> None:
    transport = FakeTransport(error=_AdapterFailure("source_unavailable"))
    cooldown = MemoryCooldownStore(lambda: 100.0)
    seat_source = source(transport, cooldown_store=cooldown)

    today_result = await seat_source.overlay([timetable_item()], **overlay_arguments())

    tomorrow = date(2026, 8, 4)
    tomorrow_departure = datetime.combine(tomorrow, time(15, 45), tzinfo=KOREA)
    tomorrow_item = timetable_item().model_copy(
        update={
            "departure_at": tomorrow_departure,
            "arrival_at": tomorrow_departure + timedelta(hours=2, minutes=45),
        }
    )
    transport.error = None
    transport.result = browser_result().model_copy(
        update={
            "travel_date": tomorrow,
            "trains": [
                browser_result().trains[0].model_copy(
                    update={"departure_at": tomorrow_departure}
                )
            ],
        }
    )
    tomorrow_result = await seat_source.overlay(
        [tomorrow_item],
        **{
            **overlay_arguments(),
            "departure_from": datetime(2026, 8, 4, 14, tzinfo=KOREA),
            "departure_to": datetime(2026, 8, 4, 18, tzinfo=KOREA),
        },
    )

    assert transport.calls == 2
    assert {seat.provenance.reason for seat in today_result[0].seat_classes} == {
        "source_unavailable"
    }
    assert {seat.status for seat in tomorrow_result[0].seat_classes} == {
        "standing_plus_seat",
        "sold_out",
    }
    assert await cooldown.get("korail-browser") is None


async def test_legacy_shared_source_failure_does_not_block_a_new_query() -> None:
    transport = FakeTransport()
    cooldown = MemoryCooldownStore(lambda: 100.0)
    await cooldown.set("korail-browser", "source_unavailable", 300)
    seat_source = source(transport, cooldown_store=cooldown)

    deferred_until = await seat_source.observation_deferred_until()
    result = await seat_source.overlay([timetable_item()], **overlay_arguments())

    assert deferred_until is None
    assert transport.calls == 1
    assert {seat.status for seat in result[0].seat_classes} == {
        "standing_plus_seat",
        "sold_out",
    }


async def test_same_hour_observations_share_one_search_and_exact_match_train_classes() -> None:
    result = browser_result().model_copy(
        update={
            "trains": [
                BrowserTrainSnapshot(
                    train_number="00043",
                    train_type="KTX",
                    departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
                    arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
                    standard="available",
                    first="sold_out",
                ),
                BrowserTrainSnapshot(
                    train_number="47",
                    train_type="KTX",
                    departure_at=datetime(2026, 8, 3, 15, 55, tzinfo=KOREA),
                    arrival_at=datetime(2026, 8, 3, 18, 40, tzinfo=KOREA),
                    standard="sold_out",
                    first="limited",
                ),
            ]
        }
    )
    transport = FakeTransport(result)
    transport.gate.clear()
    seat_source = source(transport)
    standard_task = asyncio.create_task(
        seat_source.observe(
            observation_request(train_number="43", seat_class="standard"),
            origin="서울",
            destination="부산",
        )
    )
    first_task = asyncio.create_task(
        seat_source.observe(
            observation_request(
                train_number="00047",
                departure_at=datetime(2026, 8, 3, 15, 55, tzinfo=KOREA),
                seat_class="first",
            ),
            origin="서울",
            destination="부산",
        )
    )
    await asyncio.sleep(0)
    transport.gate.set()

    standard, first = await asyncio.gather(standard_task, first_task)
    await seat_source.close()

    assert transport.calls == 1
    assert standard[0].seat_class == "standard"
    assert standard[0].status == "available"
    assert first[0].seat_class == "first"
    assert first[0].status == "limited"
    assert {standard[0].source, first[0].source} == {
        "korail-official-page-browser"
    }


async def test_observation_identity_mismatch_and_transport_failure_fail_closed() -> None:
    mismatch_transport = FakeTransport(browser_result("47"))
    mismatch = await source(mismatch_transport).observe(
        observation_request(),
        origin="서울",
        destination="부산",
    )
    failure_transport = FakeTransport(error=RuntimeError("synthetic sidecar failure"))
    failure = await source(failure_transport).observe(
        observation_request(),
        origin="서울",
        destination="부산",
    )

    assert mismatch_transport.calls == 1
    assert mismatch[0].status == "error"
    assert mismatch[0].error_category == "provider_unavailable"
    assert failure_transport.calls == 1
    assert failure[0].status == "error"
    assert failure[0].error_category == "provider_unavailable"


async def test_shared_cooldown_preflight_makes_no_sidecar_request() -> None:
    transport = FakeTransport()
    cooldown = MemoryCooldownStore(lambda: 100.0)
    await cooldown.set("korail-browser", "provider_access_restricted", 900)
    seat_source = source(transport, cooldown_store=cooldown)

    deferred_until = await seat_source.observation_deferred_until()
    result = await seat_source.observe(
        observation_request(),
        origin="서울",
        destination="부산",
    )

    assert deferred_until is not None
    assert deferred_until > datetime.now(UTC)
    assert transport.calls == 0
    assert result[0].status == "error"
    assert result[0].error_category == "provider_unavailable"
