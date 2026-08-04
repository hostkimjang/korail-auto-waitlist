from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from rail_waitlist.domain import Provider
from rail_waitlist.timetable_management import http as timetable_http

KOREA = ZoneInfo("Asia/Seoul")


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
