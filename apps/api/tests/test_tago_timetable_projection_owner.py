from __future__ import annotations

import ast
import base64
import pickle
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.provider_adapters import tago
from rail_waitlist.provider_adapters.timetable_support import official_unknown_seat_classes
from rail_waitlist.timetable_management import tago_timetable_projection as owner
from rail_waitlist.timetable_management.schemas import (
    SeatAvailability,
    SeatClassAvailability,
    StationItem,
    TimetableItem,
)

API_ROOT = Path(__file__).resolve().parents[1]
KOREA = ZoneInfo("Asia/Seoul")
RETRIEVED_AT = datetime(2026, 8, 8, 3, tzinfo=UTC)
KORAIL_URL = "https://www.korail.com/ticket/main"
SRT_URL = "https://etk.srail.kr/main.do"
LEGACY_TIMETABLE_METHOD_PICKLE = (
    "gASVQQAAAAAAAACMJHJhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWRhcHRlcnMudGFnb5SM"
    "FFRhZ29DbGllbnQudGltZXRhYmxllJOULg=="
)


def _row(
    train_number: str,
    grade: str,
    departure: str,
    arrival: str = "20260801130000",
    *,
    fare: str = "59,800",
) -> dict[str, object]:
    return {
        "trainno": train_number,
        "traingradename": grade,
        "depplandtime": departure,
        "arrplandtime": arrival,
        "depplacename": "서울",
        "arrplacename": "부산",
        "adultcharge": fare,
    }


def _project(
    rows: list[dict[str, object]],
    *,
    provider: Provider = Provider.KORAIL,
    seat_class_projector: owner.UnknownSeatClassProjector = official_unknown_seat_classes,
) -> list[TimetableItem]:
    return owner.project_tago_timetable_rows(
        rows,
        provider=provider,
        origin="서울",
        destination="부산",
        departure_from=datetime(2026, 8, 1, 8, tzinfo=KOREA),
        departure_to=datetime(2026, 8, 1, 12, tzinfo=KOREA),
        retrieved_at=RETRIEVED_AT,
        official_booking_url=KORAIL_URL if provider is Provider.KORAIL else SRT_URL,
        service_timezone=KOREA,
        seat_class_projector=seat_class_projector,
    )


def test_projection_filters_provider_includes_boundaries_and_skips_invalid_times() -> None:
    rows = [
        _row("BEFORE", "KTX", "20260801075959"),
        _row("FROM", "KTX", "20260801080000"),
        _row("BAD-DEPARTURE", "KTX", "invalid"),
        _row("BAD-ARRIVAL", "KTX", "20260801100000", "invalid"),
        _row("TO", "KTX-산천", "20260801120000"),
        _row("AFTER", "KTX", "20260801120001"),
        _row("SRT", "SRT", "20260801100000"),
    ]

    korail = _project(rows)
    srt = _project(rows, provider=Provider.SRT)

    assert [item.train_number for item in korail] == ["FROM", "TO"]
    assert [item.train_number for item in srt] == ["SRT"]


def test_projection_maps_invalid_fare_without_creating_seat_observation_evidence() -> None:
    reasons: list[object] = []

    def keyword_only_seats(
        official_url: str,
        *,
        reason: object,
    ) -> list[SeatClassAvailability]:
        reasons.append(reason)
        return official_unknown_seat_classes(official_url, reason=reason)

    [item] = _project(
        [_row("43", "KTX", "20260801100000", fare="not-a-fare")],
        seat_class_projector=keyword_only_seats,
    )

    assert reasons == ["source_not_configured"]
    assert item.adult_fare is None
    assert item.timetable_source == "TAGO"
    assert item.timetable_retrieved_at == RETRIEVED_AT
    assert item.availability == SeatAvailability(status="unavailable")
    assert [seat.seat_class for seat in item.seat_classes] == [
        SeatClass.STANDARD,
        SeatClass.FIRST,
    ]
    assert {seat.status for seat in item.seat_classes} == {"unknown"}
    assert all(seat.provenance.kind == "not_observed" for seat in item.seat_classes)
    assert all(seat.provenance.reason == "source_not_configured" for seat in item.seat_classes)
    assert all(seat.provenance.source is None for seat in item.seat_classes)
    assert all(seat.provenance.observed_at is None for seat in item.seat_classes)
    assert all(seat.fare is None for seat in item.seat_classes)


async def test_tago_client_keeps_legacy_pickle_wildcard_and_late_projection_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = tago.TagoClient(Settings(_env_file=None))
    client.hydrate_station_catalog(
        [
            StationItem(node_id="N1", name="서울", city_code="11", city_name="서울"),
            StationItem(node_id="N2", name="부산", city_code="26", city_name="부산"),
        ],
        retrieved_at=RETRIEVED_AT,
        refresh_after=RETRIEVED_AT + timedelta(days=1),
    )
    client._remember(
        "timetable:raw:N1:N2:20260801",
        {"rows": [_row("43", "KTX", "20260801100000")], "retrieved_at": RETRIEVED_AT},
    )
    captured: dict[str, object] = {}
    replacement_timezone = timezone(timedelta(hours=9), "replacement-kst")

    def replacement_seats(
        _official_url: str,
        *,
        reason: object,
    ) -> list[SeatClassAvailability]:
        captured["reason"] = reason
        return []

    def replacement_projection(
        rows: object,
        **kwargs: object,
    ) -> list[TimetableItem]:
        captured["rows"] = rows
        captured.update(kwargs)
        return []

    monkeypatch.setattr(owner, "project_tago_timetable_rows", replacement_projection)
    monkeypatch.setattr(tago, "ZoneInfo", lambda _name: replacement_timezone)
    monkeypatch.setattr(tago, "official_unknown_seat_classes", replacement_seats)

    result = await client.timetable(
        Provider.KORAIL,
        "서울",
        "부산",
        datetime(2026, 8, 1, 8, tzinfo=KOREA),
        KORAIL_URL,
        "N1",
        "N2",
        departure_to=datetime(2026, 8, 1, 12, tzinfo=KOREA),
    )

    wildcard: dict[str, object] = {}
    exec("from rail_waitlist.provider_adapters.tago import *", wildcard)
    assert result == []
    assert captured["service_timezone"] is replacement_timezone
    assert captured["seat_class_projector"] is replacement_seats
    assert wildcard["SeatAvailability"] is SeatAvailability
    assert wildcard["TimetableItem"] is TimetableItem
    assert wildcard["official_unknown_seat_classes"] is replacement_seats
    assert (
        pickle.loads(base64.b64decode(LEGACY_TIMETABLE_METHOD_PICKLE)) is tago.TagoClient.timetable
    )


def test_projection_owner_has_no_runtime_reverse_dependency_or_source_reentry() -> None:
    owner_path = (
        API_ROOT / "src" / "rail_waitlist" / "timetable_management" / "tago_timetable_projection.py"
    )
    source_path = API_ROOT / "src" / "rail_waitlist" / "provider_adapters" / "tago.py"
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    owner_imports = {
        (
            node.module,
            node.level,
            tuple((alias.name, alias.asname) for alias in node.names),
        )
        for node in owner_tree.body
        if isinstance(node, ast.ImportFrom)
    }
    owner_plain_imports = {
        (alias.name, alias.asname)
        for node in owner_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    owner_definitions = {
        node.name
        for node in owner_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    source_definitions = {
        node.name
        for node in source_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert owner_imports == {
        ("__future__", 0, (("annotations", None),)),
        ("collections.abc", 0, (("Mapping", None), ("Sequence", None))),
        ("datetime", 0, (("datetime", None), ("tzinfo", None))),
        ("typing", 0, (("Protocol", None),)),
        ("domain", 2, (("Provider", None),)),
        (
            "schemas",
            1,
            (
                ("SeatAvailability", None),
                ("SeatAvailabilityNotObservedReason", None),
                ("SeatClassAvailability", None),
                ("TimetableItem", None),
            ),
        ),
    }
    assert owner_plain_imports == set()
    assert owner_definitions == {
        "UnknownSeatClassProjector",
        "project_tago_timetable_rows",
    }
    assert "project_tago_timetable_rows" not in source_definitions

    code = """
import json
import sys
from rail_waitlist.timetable_management import tago_timetable_projection as owner
print(json.dumps({
    "tago_loaded": "rail_waitlist.provider_adapters.tago" in sys.modules,
    "module": owner.project_tago_timetable_rows.__module__,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == (
        '{"tago_loaded": false, "module": '
        '"rail_waitlist.timetable_management.tago_timetable_projection"}'
    )
