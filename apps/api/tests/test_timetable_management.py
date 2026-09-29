from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from rail_waitlist.domain import Provider
from rail_waitlist.timetable_management import http as timetable_http

KOREA = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        ("source_unavailable", "source_unavailable"),
        ("provider_access_restricted", "provider_access_restricted"),
        ("source_not_configured", "source_not_configured"),
        ("passenger_count_not_supported", "passenger_count_not_supported"),
        ("unrecognized failure", "source_unavailable"),
    ],
)
async def test_tago_fallback_preserves_live_failure_without_seat_actions(
    app, client, monkeypatch, failure, expected_reason
):
    from rail_waitlist.korail_browser_seat_source import KorailBrowserTimetableUnavailable
    from rail_waitlist.provider_adapters.timetable_support import official_unknown_seat_classes
    from rail_waitlist.timetable_management import application
    from rail_waitlist.timetable_management.schemas import TimetableItem

    class FailedLiveSource:
        async def search_timetable(self, **kwargs):
            raise KorailBrowserTimetableUnavailable(failure)

    class FallbackAdapter:
        async def timetable(self, **kwargs):
            return [
                TimetableItem(
                    provider=Provider.KORAIL,
                    train_number="30",
                    train_type="KTX",
                    origin="대전",
                    destination="서울",
                    departure_at=datetime(2026, 10, 1, 12, tzinfo=KOREA),
                    arrival_at=datetime(2026, 10, 1, 13, 4, tzinfo=KOREA),
                    timetable_source="TAGO",
                    timetable_retrieved_at=datetime(2026, 9, 30, 4, tzinfo=KOREA),
                    seat_classes=official_unknown_seat_classes(
                        "https://www.korail.com/ticket/main", reason="source_not_configured"
                    ),
                    official_booking_url="https://www.korail.com/ticket/main",
                )
            ]

    monkeypatch.setattr(app.state, "korail_browser_seat_source", FailedLiveSource())
    monkeypatch.setattr(application, "get_timetable_provider", lambda provider: FallbackAdapter())
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": "korail",
            "origin": "대전",
            "destination": "서울",
            "departure_from": "2026-10-01T12:00:00+09:00",
            "departure_to": "2026-10-01T18:00:00+09:00",
        },
    )

    assert response.status_code == 200
    row = response.json()[0]
    assert row["train_number"] == "30"
    assert row["timetable_source"] == "TAGO"
    for seat in row["seat_classes"]:
        assert seat["status"] == "unknown"
        assert seat["provenance"] == {
            "kind": "not_observed",
            "source": None,
            "observed_at": None,
            "fresh_until": None,
            "reason": expected_reason,
        }
        assert seat["actions"] == []
        assert seat["registration_evidence_id"] is None


def test_timetable_routes_are_owned_only_by_feature_router(app) -> None:
    expected = {
        ("/api/v1/timetables", "GET"),
        ("/api/v1/timetable-snapshots", "GET"),
        ("/api/v1/seat-status/refresh", "POST"),
    }
    owned: dict[tuple[str, str], list[str]] = {key: [] for key in expected}

    routes = []
    for included in app.routes:
        original_router = getattr(included, "original_router", None)
        routes.extend(original_router.routes if original_router is not None else [included])

    for route in routes:
        for method in getattr(route, "methods", set()):
            key = (route.path, method)
            if key in owned:
                owned[key].append(route.endpoint.__module__)

    assert owned == {key: ["rail_waitlist.timetable_management.http"] for key in expected}


async def test_background_snapshot_refresh_uses_fresh_session(monkeypatch) -> None:
    session = object()
    session_opened = 0
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def session_factory():
        nonlocal session_opened
        session_opened += 1
        yield session

    async def capture_load(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(timetable_http, "_load_items_for_http", capture_load)
    app = SimpleNamespace(state=SimpleNamespace(timetable_snapshot_session_factory=session_factory))
    request = SimpleNamespace(app=app)
    departure_from = datetime(2026, 8, 1, 8, tzinfo=KOREA)
    departure_to = datetime(2026, 8, 1, 12, tzinfo=KOREA)

    result = await timetable_http._load_timetable_snapshot_in_background(
        request=request,
        provider=Provider.KORAIL,
        origin="서울",
        destination="부산",
        departure_from=departure_from,
        departure_to=departure_to,
        passenger_count=2,
        origin_node_id="N1",
        destination_node_id="N3",
    )

    assert result == []
    assert session_opened == 1
    assert captured == {
        "app": app,
        "session": session,
        "provider": Provider.KORAIL,
        "origin": "서울",
        "destination": "부산",
        "departure_from": departure_from,
        "departure_to": departure_to,
        "passenger_count": 2,
        "origin_node_id": "N1",
        "destination_node_id": "N3",
    }
