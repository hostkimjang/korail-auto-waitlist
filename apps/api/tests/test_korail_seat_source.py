from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from korail2.korail2 import KorailError, NoResultsError
from requests.adapters import HTTPAdapter

from rail_waitlist.korail_seat_source import (
    KorailLiveSeatSource,
    _DefaultTimeoutAdapter,
    map_korail_seat_state,
)
from rail_waitlist.schemas import (
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)
from rail_waitlist.seat_status_cooldown import MemoryCooldownStore

KOREA = ZoneInfo("Asia/Seoul")


@dataclass
class FakeTrain:
    train_no: str = "00026"
    dep_date: str = "20260730"
    dep_time: str = "120000"
    general_seat: str = "11"
    special_seat: str = "13"
    wait_reserve_flag: int | None = None
    reserve_possible_name: str | None = "예약 가능"


class FakeClient:
    def __init__(self, trains: list[FakeTrain], *, error: Exception | None = None) -> None:
        self.trains = trains
        self.error = error
        self.calls = 0

    def search_train(self, *args, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.trains


def timetable_item(train_number: str = "26") -> TimetableItem:
    departure = datetime(2026, 7, 30, 12, tzinfo=KOREA)
    unknown = SeatAvailabilityProvenance(kind="not_observed", reason="source_not_configured")
    return TimetableItem(
        provider="korail",
        train_number=train_number,
        train_type="KTX",
        origin="대전",
        destination="서울",
        departure_at=departure,
        arrival_at=datetime(2026, 7, 30, 13, 4, tzinfo=KOREA),
        timetable_source="TAGO",
        timetable_retrieved_at=departure,
        seat_classes=[
            SeatClassAvailability(seat_class="standard", status="unknown", provenance=unknown),
            SeatClassAvailability(seat_class="first", status="unknown", provenance=unknown),
        ],
        official_booking_url="https://www.korail.com/ticket/search",
    )


def source(
    client: FakeClient,
    *,
    monotonic=lambda: 100.0,
    cooldown_store=None,
) -> KorailLiveSeatSource:
    return KorailLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=30,
        timeout_seconds=3,
        rate_limit_cooldown_seconds=1800,
        protection_cooldown_seconds=300,
        client_factory=lambda: client,
        passenger_factory=lambda count: ("adult", count),
        monotonic=monotonic,
        cooldown_store=cooldown_store,
    )


def overlay_arguments() -> dict[str, object]:
    return {
        "origin": "대전",
        "destination": "서울",
        "departure_from": datetime(2026, 7, 30, 12, tzinfo=KOREA),
        "departure_to": datetime(2026, 7, 30, 18, tzinfo=KOREA),
        "passenger_count": 1,
    }


def test_korail_seat_code_mapping_is_fail_closed() -> None:
    assert map_korail_seat_state("11") == "available"
    assert map_korail_seat_state("13") == "sold_out"
    assert map_korail_seat_state("00") == "not_offered"
    assert map_korail_seat_state("13", "매진임박") == "limited"
    assert map_korail_seat_state("13", "입석+예매") == "standing_plus_seat"
    assert map_korail_seat_state("unexpected") == "unknown"


def test_default_transport_replaces_requests_none_timeout() -> None:
    adapter = _DefaultTimeoutAdapter(7)
    with patch.object(HTTPAdapter, "send", return_value=object()) as send:
        adapter.send(object(), timeout=None)
    assert send.call_args.kwargs["timeout"] == 7


async def test_exact_match_overlays_status_and_status_driven_actions() -> None:
    client = FakeClient([FakeTrain()])
    result = await source(client).overlay([timetable_item()], **overlay_arguments())

    standard, first = result[0].seat_classes
    assert standard.status == "available"
    assert [action.kind for action in standard.actions] == ["official_check", "add_to_watch"]
    assert first.status == "sold_out"
    assert [action.kind for action in first.actions] == ["add_to_watch"]
    assert standard.provenance.kind == "official_provider"
    assert standard.provenance.source == "korail2-0.4.0-accountless"


async def test_waitlist_is_standard_class_and_keeps_both_allowed_actions() -> None:
    client = FakeClient([FakeTrain(wait_reserve_flag=9)])
    result = await source(client).overlay([timetable_item()], **overlay_arguments())
    standard = result[0].seat_classes[0]
    assert standard.status == "waitlist_available"
    assert [action.kind for action in standard.actions] == [
        "official_waitlist",
        "add_to_watch",
    ]


async def test_train_level_standing_label_does_not_override_special_class_code() -> None:
    client = FakeClient(
        [
            FakeTrain(
                general_seat="13",
                special_seat="13",
                reserve_possible_name="입석+예매",
            )
        ]
    )

    result = await source(client).overlay([timetable_item()], **overlay_arguments())

    standard, first = result[0].seat_classes
    assert standard.status == "standing_plus_seat"
    assert first.status == "sold_out"
    assert [action.kind for action in standard.actions] == ["official_check", "add_to_watch"]
    assert [action.kind for action in first.actions] == ["add_to_watch"]


async def test_identical_queries_share_one_inflight_request_and_ttl_cache() -> None:
    gate = asyncio.Event()

    class BlockingClient(FakeClient):
        def search_train(self, *args, **kwargs):
            self.calls += 1
            gate.set()
            return self.trains

    client = BlockingClient([FakeTrain()])
    seat_source = source(client)
    first, second = await asyncio.gather(
        seat_source.overlay([timetable_item()], **overlay_arguments()),
        seat_source.overlay([timetable_item()], **overlay_arguments()),
    )
    await seat_source.overlay([timetable_item()], **overlay_arguments())
    assert gate.is_set()
    assert client.calls == 1
    assert first == second


async def test_valid_empty_result_is_cached_without_repeating_provider_call() -> None:
    client = FakeClient([], error=NoResultsError())
    seat_source = source(client)

    first = await seat_source.overlay([timetable_item()], **overlay_arguments())
    second = await seat_source.overlay([timetable_item()], **overlay_arguments())

    assert client.calls == 1
    for result in (first, second):
        assert {seat.provenance.reason for seat in result[0].seat_classes} == {"no_exact_match"}
        assert all(
            seat.status == "unknown" and seat.actions == [] for seat in result[0].seat_classes
        )


async def test_protection_error_opens_provider_cooldown_without_second_call() -> None:
    client = FakeClient([], error=KorailError("미허가 도구", "-8003"))
    seat_source = source(client)

    first = await seat_source.overlay([timetable_item()], **overlay_arguments())
    second = await seat_source.overlay([timetable_item()], **overlay_arguments())

    assert client.calls == 1
    for result in (first, second):
        assert {seat.provenance.reason for seat in result[0].seat_classes} == {
            "provider_access_restricted"
        }
        assert all(
            seat.status == "unknown" and seat.actions == [] for seat in result[0].seat_classes
        )


async def test_non_protection_korail_error_uses_short_source_unavailable_hold() -> None:
    def clock() -> float:
        return 100.0

    cooldown_store = MemoryCooldownStore(clock)
    client = FakeClient([], error=KorailError("일시적인 제공자 오류", "-9999"))
    seat_source = source(client, monotonic=clock, cooldown_store=cooldown_store)

    first = await seat_source.overlay([timetable_item()], **overlay_arguments())
    second = await seat_source.overlay([timetable_item()], **overlay_arguments())
    cooldown = await cooldown_store.get("korail")

    assert client.calls == 1
    assert cooldown is not None
    assert cooldown.reason == "source_unavailable"
    assert cooldown.retry_after_seconds == 30
    for result in (first, second):
        assert {seat.provenance.reason for seat in result[0].seat_classes} == {
            "source_unavailable"
        }


async def test_shared_cooldown_store_blocks_a_second_api_instance() -> None:
    def clock() -> float:
        return 100.0

    cooldown_store = MemoryCooldownStore(clock)
    restricted = FakeClient([], error=KorailError("미허가 도구", "-8003"))
    untouched = FakeClient([FakeTrain()])

    await source(restricted, monotonic=clock, cooldown_store=cooldown_store).overlay(
        [timetable_item()], **overlay_arguments()
    )
    result = await source(untouched, monotonic=clock, cooldown_store=cooldown_store).overlay(
        [timetable_item()], **overlay_arguments()
    )

    assert restricted.calls == 1
    assert untouched.calls == 0
    assert {seat.provenance.reason for seat in result[0].seat_classes} == {
        "provider_access_restricted"
    }


async def test_disabled_source_never_constructs_or_calls_client() -> None:
    client = FakeClient([FakeTrain()])
    seat_source = KorailLiveSeatSource(
        enabled=False,
        cache_ttl_seconds=30,
        timeout_seconds=3,
        rate_limit_cooldown_seconds=1800,
        protection_cooldown_seconds=300,
        client_factory=lambda: client,
    )
    result = await seat_source.overlay([timetable_item()], **overlay_arguments())
    assert client.calls == 0
    assert {seat.provenance.reason for seat in result[0].seat_classes} == {"source_not_configured"}
