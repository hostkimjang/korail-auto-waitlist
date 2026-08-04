from pathlib import Path

import pytest

from rail_waitlist.event_stream.http import router as event_stream_router
from rail_waitlist.provider_registry.http import router as provider_registry_router
from rail_waitlist.seat_status_operations.http import router as seat_status_operations_router
from rail_waitlist.timetable_management.catalog_http import router as station_catalog_router
from rail_waitlist.timetable_management.official_evidence_http import (
    confirmation_router as official_confirmation_router,
)
from rail_waitlist.timetable_management.official_evidence_http import (
    snapshot_router as official_snapshot_router,
)

SOURCE_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "rail_waitlist"
ROUTE_CONTRACTS = (
    (official_snapshot_router, {("GET", "/api/v1/korail-browser-snapshot-revision")}),
    (provider_registry_router, {("GET", "/api/v1/providers")}),
    (station_catalog_router, {("GET", "/api/v1/stations")}),
    (seat_status_operations_router, {("GET", "/api/v1/seat-status/status")}),
    (
        official_confirmation_router,
        {("POST", "/api/v1/seat-observations/official-page-confirmations")},
    ),
    (event_stream_router, {("GET", "/api/v1/events")}),
)


def _route_contract(router) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set())
        if method in {"GET", "POST"}
    }


def test_remaining_routes_have_coherent_owners_and_no_legacy_central_module() -> None:
    for router, expected in ROUTE_CONTRACTS:
        assert _route_contract(router) == expected
    assert not (SOURCE_PACKAGE / "api.py").exists()


def test_remaining_feature_routers_keep_the_original_registration_order(app) -> None:
    included_routers = [
        route.original_router
        for route in app.routes
        if getattr(route, "original_router", None) is not None
    ]
    positions = [included_routers.index(router) for router, _expected in ROUTE_CONTRACTS]

    assert positions == sorted(positions)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/korail-browser-snapshot-revision"),
        ("GET", "/api/v1/providers"),
        ("GET", "/api/v1/stations?provider=mock"),
        ("GET", "/api/v1/seat-status/status"),
        ("POST", "/api/v1/seat-observations/official-page-confirmations"),
        ("GET", "/api/v1/events"),
    ],
)
async def test_remaining_feature_routes_require_admin_session(
    public_client,
    method: str,
    path: str,
) -> None:
    response = await public_client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == 401
