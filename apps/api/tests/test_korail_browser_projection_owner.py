from __future__ import annotations

import base64
import pickle
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rail_waitlist.domain import SeatClass
from rail_waitlist.korail_browser_automation import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
)
from rail_waitlist.korail_search_bootstrap import (
    KorailStationIdentity,
    build_korail_general_search_url,
)
from rail_waitlist.timetable_management import korail_browser_projection as owner
from rail_waitlist.timetable_management.schemas import (
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)

KOREA = ZoneInfo("Asia/Seoul")
SOURCE_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "rail_waitlist" / "korail_browser_seat_source.py"
)
LEGACY_PICKLES = {
    "normalize": (
        "gASVSAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jBdfbm9ybWFsaXplX3RyYWluX251bWJlcpSTlC4="
    ),
    "seat": (
        "gASVPAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jAtfc2VhdF9jbGFzc5STlC4="
    ),
    "overlay": (
        "gASVVgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jCVLb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5fb3ZlcmxheV9pdGVtlJOULg=="
    ),
    "not_observed": (
        "gASVWwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jCpLb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5fbWFya19ub3Rfb2JzZXJ2ZWSUk5Qu"
    ),
}
LEGACY_PUBLIC_PROJECTION_SYMBOLS = {
    "OFFICIAL_KORAIL_SEARCH_URL",
    "BrowserTrainSnapshot",
    "SeatAvailability",
    "SeatAvailabilityAction",
    "SeatAvailabilityProvenance",
    "SeatClassAvailability",
}


def _result() -> BrowserSeatSearchResult:
    return BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        passenger_count=1,
        observed_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
        official_search_url=build_korail_general_search_url(
            origin=KorailStationIdentity("0001", "서울"),
            destination=KorailStationIdentity("0020", "부산"),
            travel_date=date(2026, 8, 3),
            departure_time=datetime.min.time(),
        ),
        trains=[
            BrowserTrainSnapshot(
                train_number="09032",
                train_type="KTX",
                departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
                arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
                adult_fare=59_800,
                standard="waitlist_available",
                first="sold_out",
            ),
            BrowserTrainSnapshot(
                train_number="47",
                train_type="KTX",
                departure_at=datetime(2026, 8, 3, 19, 0, tzinfo=KOREA),
                arrival_at=datetime(2026, 8, 3, 21, 40, tzinfo=KOREA),
                standard="available",
                first="available",
            ),
        ],
    )


def _timetable_item() -> TimetableItem:
    not_observed = SeatAvailabilityProvenance(
        kind="not_observed",
        reason="source_not_configured",
    )
    return TimetableItem(
        provider="korail",
        train_number="9032",
        train_type="KTX",
        origin="서울",
        destination="부산",
        departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
        arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
        timetable_source="TAGO",
        timetable_retrieved_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
        seat_classes=[
            SeatClassAvailability(
                seat_class="standard",
                status="unknown",
                provenance=not_observed,
            ),
            SeatClassAvailability(
                seat_class="first",
                status="unknown",
                provenance=not_observed,
            ),
        ],
        official_booking_url="https://www.korail.com/ticket/search/general",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("09032", "9032"), ("KTX 000", "0"), ("열차 없음", "0"), ("１２３", "１２３")],
)
def test_train_number_normalizer_preserves_the_legacy_permissive_contract(
    value: object,
    expected: str,
) -> None:
    assert owner.normalize_train_number(value) == expected


def test_primary_projection_keeps_window_url_fare_actions_and_provenance() -> None:
    result = owner.project_primary_timetable(
        _result(),
        departure_from=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 3, 18, 0, tzinfo=KOREA),
    )

    assert len(result) == 1
    item = result[0]
    assert item.train_number == "9032"
    assert item.adult_fare == 59_800
    assert str(item.official_booking_url) == "https://www.korail.com/ticket/search/general"
    assert str(item.official_search_url).startswith("https://www.korail.com/ticket/search/list?")
    standard, first = item.seat_classes
    assert standard.seat_class is SeatClass.STANDARD
    assert standard.fare == 59_800
    assert [action.kind for action in standard.actions] == [
        "official_waitlist",
        "add_to_watch",
    ]
    assert standard.provenance.observed_at == datetime(2026, 8, 1, 4, tzinfo=UTC)
    assert first.seat_class is SeatClass.FIRST
    assert first.fare is None
    assert [action.kind for action in first.actions] == ["add_to_watch"]
    invalid_url_sold_out = owner._seat_class(
        SeatClass.FIRST,
        "sold_out",
        datetime(2026, 8, 1, 4, tzinfo=UTC),
        "not a URL",
    )
    assert [action.kind for action in invalid_url_sold_out.actions] == ["add_to_watch"]
    standing_only = owner._seat_class(
        SeatClass.STANDARD,
        "standing_only",
        datetime(2026, 8, 1, 4, tzinfo=UTC),
        "https://www.korail.com/ticket/search/general",
    )
    assert [action.kind for action in standing_only.actions] == [
        "official_check",
        "add_to_watch",
    ]


def test_overlay_projection_uses_last_exact_snapshot_and_preserves_item_order() -> None:
    exact_item = _timetable_item()
    observed_standard = owner._seat_class(
        SeatClass.STANDARD,
        "available",
        datetime(2026, 8, 1, 3, tzinfo=UTC),
        exact_item.official_booking_url,
    )
    unmatched_item = _timetable_item().model_copy(
        update={
            "train_number": "999",
            "seat_classes": [observed_standard, _timetable_item().seat_classes[1]],
        }
    )
    first_exact = (
        _result().trains[0].model_copy(update={"standard": "sold_out", "first": "sold_out"})
    )
    last_exact = (
        _result().trains[0].model_copy(update={"standard": "available", "first": "limited"})
    )
    result = _result().model_copy(update={"trains": [first_exact, last_exact]})
    normalized_inputs: list[object] = []

    def normalize_train_number(value: object) -> str:
        normalized_inputs.append(value)
        return owner.normalize_train_number(value)

    projected = owner.project_overlay_items(
        [exact_item, unmatched_item],
        result,
        train_number_normalizer=normalize_train_number,
    )

    assert normalized_inputs == ["09032", "09032", "9032", "999"]
    assert [item.train_number for item in projected] == ["9032", "999"]
    assert [seat.status for seat in projected[0].seat_classes] == ["available", "limited"]
    assert str(projected[0].official_search_url).startswith(
        "https://www.korail.com/ticket/search/list?"
    )
    assert projected[1].seat_classes[0].status == "available"
    assert projected[1].seat_classes[0].provenance.kind == "official_provider"
    assert projected[1].seat_classes[1].status == "unknown"
    assert projected[1].seat_classes[1].provenance.reason == "no_exact_match"


def test_legacy_module_and_class_helpers_are_exact_owner_aliases_and_unpickle() -> None:
    import rail_waitlist.korail_browser_seat_source as legacy

    assert legacy._normalize_train_number is owner.normalize_train_number
    assert legacy._seat_class is owner._seat_class
    assert legacy.KorailBrowserSeatSource._overlay_item is owner.overlay_item
    assert legacy.KorailBrowserSeatSource._mark_not_observed is owner.mark_not_observed
    assert legacy.KorailBrowserSeatSource._project_overlay_items is owner.project_overlay_items
    for symbol in LEGACY_PUBLIC_PROJECTION_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(owner, symbol)
    wildcard: dict[str, object] = {}
    exec("from rail_waitlist.korail_browser_seat_source import *", wildcard)
    assert LEGACY_PUBLIC_PROJECTION_SYMBOLS <= wildcard.keys()
    assert (
        pickle.loads(base64.b64decode(LEGACY_PICKLES["normalize"])) is owner.normalize_train_number
    )
    assert pickle.loads(base64.b64decode(LEGACY_PICKLES["seat"])) is owner._seat_class
    assert pickle.loads(base64.b64decode(LEGACY_PICKLES["overlay"])) is owner.overlay_item
    assert pickle.loads(base64.b64decode(LEGACY_PICKLES["not_observed"])) is owner.mark_not_observed
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "def _normalize_train_number" not in source
    assert "def _seat_class" not in source
    assert "def _overlay_item" not in source
    assert "def _mark_not_observed" not in source
    assert "def _project_overlay_items" not in source


async def test_source_resolves_projection_seams_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rail_waitlist.korail_browser_seat_source as legacy

    class SearchTransport:
        async def search(self, _request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
            return _result()

    projected_statuses: list[str] = []
    projected_items: list[str] = []
    not_observed_reasons: list[str] = []

    def project_seat(*args, **kwargs):
        projected_statuses.append(args[1])
        return owner._seat_class(*args, **kwargs)

    def project_item(*args, **kwargs):
        projected_items.append(args[0].train_number)
        return owner.overlay_item(*args, **kwargs)

    def mark_not_observed(items, reason):
        not_observed_reasons.append(reason)
        return owner.mark_not_observed(items, reason)

    source = legacy.KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://korail-browser:8091",
        cache_ttl_seconds=1,
        timeout_seconds=1,
        rate_limit_cooldown_seconds=10,
        protection_cooldown_seconds=10,
        transport=SearchTransport(),
        now=lambda: datetime(2026, 8, 1, 12, tzinfo=KOREA),
    )
    monkeypatch.setattr(legacy, "_normalize_train_number", lambda _value: "patched")
    monkeypatch.setattr(legacy, "_seat_class", project_seat)
    monkeypatch.setattr(source, "_overlay_item", project_item)
    monkeypatch.setattr(source, "_mark_not_observed", mark_not_observed)

    result = await source.search_timetable(
        origin="서울",
        destination="부산",
        departure_from=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 3, 18, 0, tzinfo=KOREA),
        passenger_count=1,
    )
    mismatch = _timetable_item().model_copy(
        update={
            "departure_at": datetime(2026, 8, 3, 15, 46, tzinfo=KOREA),
            "arrival_at": datetime(2026, 8, 3, 18, 31, tzinfo=KOREA),
        }
    )
    overlaid = await source.overlay(
        [_timetable_item(), mismatch],
        origin="서울",
        destination="부산",
        departure_from=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 3, 18, 0, tzinfo=KOREA),
        passenger_count=1,
    )

    assert result[0].train_number == "patched"
    assert [seat.status for seat in overlaid[0].seat_classes] == [
        "waitlist_available",
        "sold_out",
    ]
    assert projected_statuses == [
        "waitlist_available",
        "sold_out",
        "waitlist_available",
        "sold_out",
    ]
    assert projected_items == ["9032"]
    assert not_observed_reasons == ["no_exact_match"]
