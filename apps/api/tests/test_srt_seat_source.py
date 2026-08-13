from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.provider_adapters.srt_seat_source as srt_seat_source_module
from rail_waitlist.provider_adapters.srt_seat_source import (
    SrtLiveSeatSource,
    SrtLiveTimetableUnavailable,
    _AccountlessSrtClient,
    _default_client_factory,
    map_srt_seat_state,
    normalize_srt_time,
    normalize_srt_train_number,
)
from rail_waitlist.provider_adapters.srt_station_roster import (
    SrtStationRosterUnavailable,
)
from rail_waitlist.schemas import (
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    TimetableItem,
)
from rail_waitlist.seat_status_cooldown import MemoryCooldownStore

KOREA = ZoneInfo("Asia/Seoul")


@dataclass
class FakeTrain:
    train_name: str = "SRT"
    train_number: str = "00028"
    dep_date: str = "20260801"
    dep_time: str = "123700"
    dep_station_name: str = "수서"
    dep_station_code: str = "0551"
    arr_date: str = "20260801"
    arr_time: str = "153000"
    arr_station_name: str = "부산"
    arr_station_code: str = "0020"
    general_seat_state: str = "매진"
    special_seat_state: str = "예약가능"
    reserve_wait_possible_code: str = "9"
    delay_minutes: int | None = None
    adult_fare: int | None = None


def timetable_item(
    *, train_number: str = "28", departure_at: str = "2026-08-01T12:37:00+09:00"
) -> TimetableItem:
    official_url = "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000"
    unknown_seats = [
        SeatClassAvailability(
            seat_class=seat_class,
            status="unknown",
            provenance=SeatAvailabilityProvenance(
                kind="not_observed", reason="public_api_not_available"
            ),
            actions=[
                SeatAvailabilityAction(kind="official_check", url=official_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ],
        )
        for seat_class in ("standard", "first")
    ]
    departure = datetime.fromisoformat(departure_at)
    return TimetableItem(
        provider="srt",
        train_number=train_number,
        train_type="SRT",
        origin="수서",
        destination="부산",
        departure_at=departure,
        arrival_at=departure.replace(hour=15),
        timetable_source="TAGO",
        timetable_retrieved_at=departure,
        availability=SeatAvailability(status="unavailable"),
        seat_classes=unknown_seats,
        official_booking_url=official_url,
    )


class FakeClient:
    def __init__(self, trains, calls, *, pause: float = 0) -> None:
        self.trains = trains
        self.calls = calls
        self.pause = pause

    def search_train(self, dep, arr, date, time, time_limit=None, available_only=True):
        self.calls.append((dep, arr, date, time, time_limit, available_only))
        if self.pause:
            time_module = __import__("time")
            time_module.sleep(self.pause)
        return self.trains


class FakeCodeAwareClient:
    def __init__(self, trains) -> None:
        self.trains = trains
        self.calls = []

    def _search_train(self, **kwargs):
        self.calls.append(kwargs)
        return self.trains


def observation_request(**overrides) -> SeatObservationRequest:
    payload = {
        "provider": "srt",
        "origin_node_id": "0017",
        "destination_node_id": "0020",
        "origin": "수서",
        "destination": "부산",
        "train_number": "28",
        "departure_at": datetime(2026, 8, 1, 12, 37, tzinfo=KOREA),
        "seat_class": "standard",
        "passenger_count": 1,
    }
    payload.update(overrides)
    return SeatObservationRequest(**payload)


async def test_observe_returns_one_exact_official_seat_class_observation():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
    )

    result = await source.observe(
        observation_request(
            departure_at=datetime(2026, 8, 1, 3, 37, tzinfo=UTC),
            seat_class="first",
        ),
        origin="수서",
        destination="부산",
    )

    assert calls == [("수서", "부산", "20260801", "000000", "235959", False)]
    assert len(result) == 1
    assert result[0].seat_class.value == "first"
    assert result[0].status.value == "available"
    assert result[0].source == "srtrain-2.6.7-accountless"
    assert result[0].fresh_until - result[0].observed_at == timedelta(seconds=30)
    assert result[0].error_category is None


async def test_observe_shares_one_batch_query_between_two_seat_classes():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls, pause=0.05),
    )

    standard, first = await asyncio.gather(
        source.observe(observation_request(), origin="수서", destination="부산"),
        source.observe(observation_request(seat_class="first"), origin="수서", destination="부산"),
    )

    assert len(calls) == 1
    assert standard[0].seat_class.value == "standard"
    assert standard[0].status.value == "waitlist_available"
    assert first[0].seat_class.value == "first"
    assert first[0].status.value == "available"


async def test_observe_shares_one_service_day_query_between_different_trains():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient(
            [
                FakeTrain(),
                FakeTrain(train_number="00030", dep_time="133700"),
            ],
            calls,
            pause=0.05,
        ),
    )

    first, second = await asyncio.gather(
        source.observe(observation_request(), origin="수서", destination="부산"),
        source.observe(
            observation_request(
                train_number="30",
                departure_at=datetime(2026, 8, 1, 13, 37, tzinfo=KOREA),
            ),
            origin="수서",
            destination="부산",
        ),
    )

    assert calls == [("수서", "부산", "20260801", "000000", "235959", False)]
    assert first[0].status.value == "waitlist_available"
    assert second[0].status.value == "waitlist_available"


async def test_observe_rejects_unsupported_requests_without_upstream_calls():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
    )

    results = [
        await source.observe(
            observation_request(provider="korail"), origin="수서", destination="부산"
        ),
        await source.observe(
            observation_request(passenger_count=2), origin="수서", destination="부산"
        ),
        await source.observe(
            observation_request(seat_class="infant"), origin="수서", destination="부산"
        ),
        await source.observe(observation_request(), origin="광운대", destination="부산"),
    ]
    disabled = SrtLiveSeatSource(
        enabled=False,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
    )
    results.append(await disabled.observe(observation_request(), origin="수서", destination="부산"))

    assert calls == []
    assert all(result[0].status.value == "error" for result in results)
    assert all(result[0].error_category == "provider_unavailable" for result in results)


async def test_observe_requires_exact_train_date_and_departure_time_match():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient(
            [
                FakeTrain(train_number="30"),
                FakeTrain(dep_date="20260802"),
                FakeTrain(dep_time="123800"),
            ],
            calls,
        ),
    )

    result = await source.observe(observation_request(), origin="수서", destination="부산")

    assert len(calls) == 1
    assert result[0].status.value == "error"
    assert result[0].error_category == "provider_unavailable"


async def test_observe_unknown_official_seat_text_is_a_schema_error():
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient(
            [
                FakeTrain(
                    general_seat_state="unexpected provider text", reserve_wait_possible_code=""
                )
            ],
            [],
        ),
    )

    result = await source.observe(observation_request(), origin="수서", destination="부산")

    assert result[0].status.value == "error"
    assert result[0].error_category == "schema_mismatch"


async def test_observe_timeout_and_protection_fail_closed_and_open_cooldown():
    from SRT.errors import SRTNetFunnelError

    calls = []

    class ProtectedClient:
        def search_train(self, *args, **kwargs):
            calls.append("protected")
            raise SRTNetFunnelError("raw response must not escape")

    protected = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=ProtectedClient,
    )
    first = await protected.observe(observation_request(), origin="수서", destination="부산")
    second = await protected.observe(observation_request(), origin="수서", destination="부산")

    assert calls == ["protected"]
    assert first[0].status.value == second[0].status.value == "error"
    assert first[0].error_category == second[0].error_category == "provider_unavailable"
    assert "raw response" not in first[0].model_dump_json()

    timeout = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        timeout_seconds=0.001,
        client_factory=lambda: FakeClient([FakeTrain()], [], pause=0.05),
    )
    timed_out = await timeout.observe(observation_request(), origin="수서", destination="부산")

    assert timed_out[0].status.value == "error"
    assert timed_out[0].error_category == "timeout"
    await timeout.drain_pending_calls()


async def test_timeout_keeps_upstream_owned_until_drain_finishes(
    caplog: pytest.LogCaptureFixture,
):
    started = threading.Event()
    release = threading.Event()

    class BlockingClient:
        def search_train(self, *args, **kwargs):
            started.set()
            assert release.wait(timeout=2)
            return [FakeTrain()]

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        timeout_seconds=0.01,
        client_factory=BlockingClient,
    )

    caplog.set_level(logging.INFO, logger="rail_waitlist.srt_provider_adapter")
    timed_out = await source.observe(
        observation_request(),
        origin="수서",
        destination="부산",
    )
    assert started.is_set()
    assert timed_out[0].error_category == "timeout"

    drain_task = asyncio.create_task(source.drain_pending_calls())
    await asyncio.sleep(0.01)
    assert not drain_task.done()

    release.set()
    await asyncio.wait_for(drain_task, timeout=1)
    await asyncio.sleep(0)

    assert "SRT 운영사 조회를 시작합니다" in caplog.text
    assert "event=provider_call_timed_out" in caplog.text
    assert "upstream_still_running=true" in caplog.text
    assert "event=provider_call_finished_after_timeout outcome=success" in caplog.text


async def test_timeout_logs_late_failure_without_exposing_provider_exception(
    caplog: pytest.LogCaptureFixture,
):
    started = threading.Event()
    release = threading.Event()

    class FailingClient:
        def search_train(self, *args, **kwargs):
            started.set()
            assert release.wait(timeout=2)
            raise RuntimeError("raw-provider-secret")

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        timeout_seconds=0.01,
        client_factory=FailingClient,
    )
    caplog.set_level(logging.INFO, logger="rail_waitlist.srt_provider_adapter")

    timed_out = await source.observe(
        observation_request(),
        origin="수서",
        destination="부산",
    )
    assert started.is_set()
    assert timed_out[0].error_category == "timeout"

    release.set()
    await source.drain_pending_calls()
    await asyncio.sleep(0)

    assert "event=provider_call_finished_after_timeout outcome=failed" in caplog.text
    assert "raw-provider-secret" not in caplog.text


async def test_observe_rate_limit_opens_cooldown_without_exposing_raw_error():
    calls = []

    class RateLimitedClient:
        def search_train(self, *args, **kwargs):
            calls.append("rate-limited")
            raise RuntimeError("429 raw response must not escape")

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=RateLimitedClient,
    )

    first = await source.observe(observation_request(), origin="수서", destination="부산")
    second = await source.observe(observation_request(), origin="수서", destination="부산")

    assert calls == ["rate-limited"]
    assert first[0].error_category == second[0].error_category == "provider_unavailable"
    assert "raw response" not in first[0].model_dump_json()


async def test_non_string_vendor_error_still_opens_fail_closed_cooldown():
    class NonStringMessageError(RuntimeError):
        def __str__(self):
            return object()

    cooldown = MemoryCooldownStore()
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        cooldown_store=cooldown,
    )

    await source._open_cooldown("source_unavailable", NonStringMessageError())

    active = await cooldown.get("srt")
    assert active is not None
    assert active.reason == "source_unavailable"


async def test_source_cooldown_preflight_defers_without_an_upstream_call():
    calls = []
    clock = [10.0]
    cooldown_store = MemoryCooldownStore(lambda: clock[0])
    await cooldown_store.set("srt", "source_unavailable", 120)
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
        monotonic=lambda: clock[0],
        cooldown_store=cooldown_store,
    )

    before = datetime.now(UTC)
    deferred_until = await source.observation_deferred_until()
    after = datetime.now(UTC)

    assert deferred_until is not None
    assert before + timedelta(seconds=119) <= deferred_until <= after + timedelta(seconds=120)
    assert calls == []


async def test_overlays_only_an_exact_train_date_and_departure_match():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
    )
    exact = timetable_item()
    different_departure = timetable_item(
        train_number="30", departure_at="2026-08-01T13:37:00+09:00"
    )

    result = await source.overlay(
        [exact, different_departure],
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert calls == [("수서", "부산", "20260801", "120000", "180000", False)]
    assert [seat.status for seat in result[0].seat_classes] == [
        "waitlist_available",
        "available",
    ]
    assert all(
        seat.provenance.kind == "official_provider"
        and seat.provenance.source == "srtrain-2.6.7-accountless"
        and seat.provenance.observed_at is not None
        and seat.provenance.observed_at.tzinfo is not None
        for seat in result[0].seat_classes
    )
    assert [seat.status for seat in result[1].seat_classes] == ["unknown", "unknown"]


async def test_search_timetable_returns_complete_official_rows_without_guessing_fare():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient(
            [
                FakeTrain(
                    train_number="00329",
                    dep_date="20260801",
                    dep_time="235500",
                    arr_date="20260802",
                    arr_time="023000",
                    general_seat_state="예약가능",
                    special_seat_state="매진",
                    reserve_wait_possible_code="",
                    delay_minutes=11,
                )
            ],
            calls,
        ),
    )

    result = await source.search_timetable(
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 23, 30, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 23, 59, tzinfo=KOREA),
        passenger_count=1,
    )

    assert calls == [("수서", "부산", "20260801", "233000", "235900", False)]
    assert len(result) == 1
    train = result[0]
    assert train.train_number == "00329"
    assert train.train_type == "SRT"
    assert train.origin == "수서"
    assert train.destination == "부산"
    assert train.departure_at == datetime(2026, 8, 1, 23, 55, tzinfo=KOREA)
    assert train.arrival_at == datetime(2026, 8, 2, 2, 30, tzinfo=KOREA)
    assert train.standard_status == "available"
    assert train.first_status == "sold_out"
    assert train.observed_at.tzinfo is not None
    assert train.delay_minutes == 11
    assert train.adult_fare is None
    assert train.source == "srtrain-2.6.7-accountless"


async def test_search_timetable_supports_official_seoul_cross_operation_route():
    calls = []
    train = FakeTrain(
        train_number="00162",
        dep_date="20260805",
        dep_time="123700",
        dep_station_name="대전",
        dep_station_code="0010",
        arr_date="20260805",
        arr_time="134700",
        arr_station_name="알 수 없는 역 코드 (업데이트 필요)",
        arr_station_code="0001",
    )
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([train], calls),
    )

    result = await source.search_timetable(
        origin="대전",
        destination="서울",
        departure_from=datetime(2026, 8, 5, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 5, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert calls == [("대전", "서울", "20260805", "120000", "180000", False)]
    assert [(item.train_number, item.origin, item.destination) for item in result] == [
        ("00162", "대전", "서울")
    ]


async def test_search_timetable_normalizes_roster_failure_without_upstream_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def unavailable_roster() -> object:
        raise SrtStationRosterUnavailable("dependency detail must not escape")

    monkeypatch.setattr(srt_seat_source_module, "load_srt_station_roster", unavailable_roster)
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([], calls),
    )

    with pytest.raises(
        SrtLiveTimetableUnavailable,
        match="^SRT station roster is unavailable$",
    ) as captured:
        await source.search_timetable(
            origin="수서",
            destination="부산",
            departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
            departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
            passenger_count=1,
        )

    assert isinstance(captured.value.__cause__, SrtStationRosterUnavailable)
    assert calls == []


def test_default_accountless_client_passes_cross_operation_codes_to_srtrain():
    upstream = FakeCodeAwareClient([])
    client = _AccountlessSrtClient(upstream)

    client.search_train(
        "대전",
        "서울",
        "20260805",
        "120000",
        time_limit="180000",
        available_only=False,
    )

    assert upstream.calls == [
        {
            "dep": "대전",
            "arr": "서울",
            "date": "20260805",
            "time": "120000",
            "time_limit": "180000",
            "arr_code": "0001",
            "dep_code": "0010",
            "available_only": False,
            "use_netfunnel_cache": True,
        }
    ]


def test_default_accountless_client_injects_observable_netfunnel_helper():
    client = _default_client_factory()

    assert client._client.netfunnel_helper.__class__.__name__ == "LoggingNetFunnelHelper"
    assert client._client.netfunnel_helper._flow == "accountless"


async def test_timetable_search_reuses_singleflight_and_cache_for_exact_window():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls, pause=0.05),
    )
    arguments = {
        "origin": "수서",
        "destination": "부산",
        "departure_from": datetime(2026, 8, 1, 12, tzinfo=KOREA),
        "departure_to": datetime(2026, 8, 1, 18, tzinfo=KOREA),
        "passenger_count": 1,
    }

    first, second = await asyncio.gather(
        source.search_timetable(**arguments),
        source.search_timetable(**arguments),
    )
    cached = await source.search_timetable(**arguments)

    assert len(calls) == 1
    assert first == second == cached


async def test_timetable_search_skips_incomplete_rows_but_preserves_valid_rows():
    calls = []
    incomplete = FakeTrain()
    incomplete.arr_station_name = ""
    incomplete.arr_station_code = ""
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([incomplete, FakeTrain()], calls),
    )

    result = await source.search_timetable(
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert len(result) == 1
    assert result[0].train_number == "00028"


async def test_timetable_search_netfunnel_failure_opens_shared_cooldown():
    from SRT.errors import SRTNetFunnelError

    calls = []

    class ProtectedClient:
        def search_train(self, *args, **kwargs):
            calls.append("protected")
            raise SRTNetFunnelError("raw response must not escape")

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=ProtectedClient,
    )
    arguments = {
        "origin": "수서",
        "destination": "부산",
        "departure_from": datetime(2026, 8, 1, 12, tzinfo=KOREA),
        "departure_to": datetime(2026, 8, 1, 18, tzinfo=KOREA),
        "passenger_count": 1,
    }

    with pytest.raises(RuntimeError, match="access is restricted"):
        await source.search_timetable(**arguments)
    with pytest.raises(RuntimeError, match="cooling down"):
        await source.search_timetable(**arguments)

    assert calls == ["protected"]


async def test_source_failure_preserves_tago_unknown_statuses():
    class FailingClient:
        def search_train(self, *args, **kwargs):
            raise RuntimeError("upstream body must not escape")

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=FailingClient,
    )
    original = timetable_item()

    result = await source.overlay(
        [original],
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert [seat.status for seat in result[0].seat_classes] == ["unknown", "unknown"]
    assert {seat.provenance.reason for seat in result[0].seat_classes} == {"source_unavailable"}


async def test_one_malformed_source_train_does_not_discard_an_exact_valid_match():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([object(), FakeTrain()], calls),
    )

    result = await source.overlay(
        [timetable_item()],
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert [seat.status for seat in result[0].seat_classes] == [
        "waitlist_available",
        "available",
    ]


async def test_disabled_and_multi_passenger_requests_never_call_one_person_source():
    calls = []
    disabled = SrtLiveSeatSource(
        enabled=False,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
    )
    enabled = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls),
    )
    arguments = {
        "origin": "수서",
        "destination": "부산",
        "departure_from": datetime(2026, 8, 1, 12, tzinfo=KOREA),
        "departure_to": datetime(2026, 8, 1, 18, tzinfo=KOREA),
    }

    original = timetable_item()
    disabled_result = await disabled.overlay([original], passenger_count=1, **arguments)
    passenger_result = await enabled.overlay([original], passenger_count=2, **arguments)
    assert {seat.provenance.reason for seat in disabled_result[0].seat_classes} == {
        "source_not_configured"
    }
    assert {seat.provenance.reason for seat in passenger_result[0].seat_classes} == {
        "passenger_count_not_supported"
    }
    assert calls == []


async def test_unsupported_station_marks_route_without_exposing_provider_error():
    class UnsupportedStationClient:
        def search_train(self, *args, **kwargs):
            raise ValueError("raw station details must not escape")

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=UnsupportedStationClient,
    )

    result = await source.overlay(
        [timetable_item()],
        origin="대전",
        destination="서울",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert {seat.provenance.reason for seat in result[0].seat_classes} == {"unsupported_route"}


async def test_provider_access_restriction_is_distinct_from_general_failure():
    from SRT.errors import SRTNetFunnelError

    class RestrictedClient:
        def search_train(self, *args, **kwargs):
            raise SRTNetFunnelError("raw provider response must not escape")

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=RestrictedClient,
    )

    result = await source.overlay(
        [timetable_item()],
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert {seat.provenance.reason for seat in result[0].seat_classes} == {
        "provider_access_restricted"
    }


async def test_successful_batch_without_exact_identity_marks_no_exact_match():
    calls = []
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain(train_number="99")], calls),
    )

    result = await source.overlay(
        [timetable_item()],
        origin="수서",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
        passenger_count=1,
    )

    assert {seat.provenance.reason for seat in result[0].seat_classes} == {"no_exact_match"}


async def test_singleflight_and_ttl_cache_issue_one_batch_query_per_window():
    calls = []
    clock = [10.0]
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=lambda: FakeClient([FakeTrain()], calls, pause=0.05),
        monotonic=lambda: clock[0],
    )
    arguments = {
        "origin": "수서",
        "destination": "부산",
        "departure_from": datetime(2026, 8, 1, 12, tzinfo=KOREA),
        "departure_to": datetime(2026, 8, 1, 18, tzinfo=KOREA),
        "passenger_count": 1,
    }

    await asyncio.gather(
        source.overlay([timetable_item()], **arguments),
        source.overlay([timetable_item()], **arguments),
    )
    await source.overlay([timetable_item()], **arguments)
    assert len(calls) == 1

    clock[0] = 41.0
    await source.overlay([timetable_item()], **arguments)
    assert len(calls) == 2


async def test_provider_gate_limits_different_windows_to_one_concurrent_query():
    active = 0
    maximum = 0
    lock = threading.Lock()

    class CountingClient:
        def search_train(self, *args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return []

    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        client_factory=CountingClient,
    )
    await asyncio.gather(
        source.overlay(
            [timetable_item()],
            origin="수서",
            destination="부산",
            departure_from=datetime(2026, 8, 1, 12, tzinfo=KOREA),
            departure_to=datetime(2026, 8, 1, 18, tzinfo=KOREA),
            passenger_count=1,
        ),
        source.overlay(
            [timetable_item(departure_at="2026-08-02T12:37:00+09:00")],
            origin="수서",
            destination="부산",
            departure_from=datetime(2026, 8, 2, 12, tzinfo=KOREA),
            departure_to=datetime(2026, 8, 2, 18, tzinfo=KOREA),
            passenger_count=1,
        ),
    )

    assert maximum == 1


def test_normalizes_train_and_time_and_maps_only_known_seat_states():
    assert normalize_srt_train_number("SRT 00028") == "28"
    assert normalize_srt_time("1237") == "123700"
    assert map_srt_seat_state("예약가능") == "available"
    assert map_srt_seat_state("매진") == "sold_out"
    assert map_srt_seat_state("해당없음") == "not_offered"
    assert map_srt_seat_state("unexpected provider text") == "unknown"
