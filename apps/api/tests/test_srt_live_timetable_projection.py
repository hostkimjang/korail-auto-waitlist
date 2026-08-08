from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rail_waitlist.domain import Provider
from rail_waitlist.provider_adapters.base import OFFICIAL_BOOKING_URLS
from rail_waitlist.srt_live_timetable import _seat_class as legacy_seat_class
from rail_waitlist.srt_live_timetable import map_srt_live_timetable as legacy_map
from rail_waitlist.srt_sidecar.contracts import (
    SrtOfficialSeatStatus,
    SrtTimetableTrain,
)
from rail_waitlist.timetable_management.srt_live_timetable import (
    _seat_class,
    map_srt_live_timetable,
)

API_ROOT = Path(__file__).resolve().parents[1]
KOREA = ZoneInfo("Asia/Seoul")
OBSERVED_AT = datetime(2030, 8, 1, 7, 59, tzinfo=KOREA)


def train(
    train_number: str,
    *,
    standard_status: SrtOfficialSeatStatus,
    first_status: SrtOfficialSeatStatus,
) -> SrtTimetableTrain:
    return SrtTimetableTrain(
        train_number=train_number,
        train_type="SRT",
        origin="수서",
        destination="부산",
        departure_at=datetime(2030, 8, 1, 8, 30, tzinfo=KOREA),
        arrival_at=datetime(2030, 8, 1, 11, 10, tzinfo=KOREA),
        standard_status=standard_status,
        first_status=first_status,
        observed_at=OBSERVED_AT,
        delay_minutes=7,
        adult_fare=52_600,
    )


def action_kinds(seat) -> list[str]:
    return [action.kind for action in seat.actions]


def test_srt_live_timetable_legacy_symbols_are_exact_canonical_objects() -> None:
    assert legacy_map is map_srt_live_timetable
    assert legacy_seat_class is _seat_class
    assert map_srt_live_timetable.__module__ == (
        "rail_waitlist.timetable_management.srt_live_timetable"
    )


def test_srt_live_timetable_preserves_train_projection_and_order() -> None:
    items = map_srt_live_timetable(
        [
            train("321", standard_status="available", first_status="sold_out"),
            train("323", standard_status="waitlist_available", first_status="not_offered"),
        ]
    )

    assert [item.train_number for item in items] == ["321", "323"]
    first = items[0]
    assert first.provider is Provider.SRT
    assert first.train_type == "SRT"
    assert first.origin == "수서"
    assert first.destination == "부산"
    assert first.departure_at == datetime(2030, 8, 1, 8, 30, tzinfo=KOREA)
    assert first.arrival_at == datetime(2030, 8, 1, 11, 10, tzinfo=KOREA)
    assert first.adult_fare == 52_600
    assert first.timetable_source == "official_provider"
    assert first.timetable_retrieved_at == OBSERVED_AT
    assert str(first.official_booking_url) == OFFICIAL_BOOKING_URLS[Provider.SRT]
    assert first.availability is not None
    assert first.availability.status == "available"
    assert first.availability.source == "srtrain-2.6.7-accountless"
    assert first.availability.observed_at == OBSERVED_AT

    standard, special = first.seat_classes
    assert standard.seat_class == "standard"
    assert standard.fare == 52_600
    assert special.seat_class == "first"
    assert special.fare is None
    assert action_kinds(standard) == ["official_check", "add_to_watch"]
    assert action_kinds(special) == ["add_to_watch"]
    assert standard.provenance.kind == "official_provider"
    assert standard.provenance.source == "srtrain-2.6.7-accountless"
    assert standard.provenance.observed_at == OBSERVED_AT.astimezone(UTC)


@pytest.mark.parametrize(
    ("status", "expected_actions"),
    [
        ("available", ["official_check", "add_to_watch"]),
        ("waitlist_available", ["official_waitlist", "add_to_watch"]),
        ("sold_out", ["add_to_watch"]),
        ("unknown", []),
        ("not_offered", []),
    ],
)
def test_srt_live_timetable_preserves_fail_closed_actions_by_status(
    status: SrtOfficialSeatStatus,
    expected_actions: list[str],
) -> None:
    item = map_srt_live_timetable([train("325", standard_status=status, first_status=status)])[0]

    assert [seat.status for seat in item.seat_classes] == [status, status]
    assert [action_kinds(seat) for seat in item.seat_classes] == [
        expected_actions,
        expected_actions,
    ]
    assert item.seat_classes[0].fare == 52_600
    assert item.seat_classes[1].fare is None


def test_srt_live_timetable_empty_input_stays_empty() -> None:
    assert map_srt_live_timetable([]) == []


def test_srt_live_timetable_import_orders_preserve_exact_identity() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management.srt_live_timetable import (
        _seat_class as CanonicalSeat,
        map_srt_live_timetable as CanonicalMap,
    )
    from rail_waitlist.srt_live_timetable import (
        _seat_class as LegacySeat,
        map_srt_live_timetable as LegacyMap,
    )
else:
    from rail_waitlist.srt_live_timetable import (
        _seat_class as LegacySeat,
        map_srt_live_timetable as LegacyMap,
    )
    from rail_waitlist.timetable_management.srt_live_timetable import (
        _seat_class as CanonicalSeat,
        map_srt_live_timetable as CanonicalMap,
    )

print(json.dumps({
    "map_identity": LegacyMap is CanonicalMap,
    "seat_identity": LegacySeat is CanonicalSeat,
    "map_module": CanonicalMap.__module__,
    "seat_module": CanonicalSeat.__module__,
}, sort_keys=True))
"""

    for import_order in ("canonical-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "map_identity": True,
            "map_module": "rail_waitlist.timetable_management.srt_live_timetable",
            "seat_identity": True,
            "seat_module": "rail_waitlist.timetable_management.srt_live_timetable",
        }
