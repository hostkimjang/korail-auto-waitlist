from rail_waitlist.schemas import (
    UiPreferencesRead as CompatibilityUiPreferencesRead,
)
from rail_waitlist.schemas import (
    UiPreferencesUpdate as CompatibilityUiPreferencesUpdate,
)
from rail_waitlist.services import (
    update_admin_ui_preferences as compatibility_update_admin_ui_preferences,
)
from rail_waitlist.ui_preferences.application import (
    update_admin_ui_preferences as owner_update_admin_ui_preferences,
)
from rail_waitlist.ui_preferences.http import router
from rail_waitlist.ui_preferences.schemas import (
    UiPreferencesRead as FeatureUiPreferencesRead,
)
from rail_waitlist.ui_preferences.schemas import (
    UiPreferencesUpdate as FeatureUiPreferencesUpdate,
)


def test_ui_preference_schemas_keep_compatibility_exports() -> None:
    assert CompatibilityUiPreferencesRead is FeatureUiPreferencesRead
    assert CompatibilityUiPreferencesUpdate is FeatureUiPreferencesUpdate


def test_ui_preference_application_keeps_the_services_compatibility_export() -> None:
    assert compatibility_update_admin_ui_preferences is owner_update_admin_ui_preferences


def test_ui_preferences_router_owns_existing_routes() -> None:
    route_contracts = {(route.path, frozenset(route.methods or ())) for route in router.routes}
    assert route_contracts == {
        ("/api/v1/preferences/ui", frozenset({"GET"})),
        ("/api/v1/preferences/ui", frozenset({"PATCH"})),
    }


async def test_ui_preferences_routes_keep_missing_account_404(client) -> None:
    read_response = await client.get("/api/v1/preferences/ui")
    update_response = await client.patch(
        "/api/v1/preferences/ui",
        json={"timetable_refresh_interval_seconds": 15},
    )

    assert read_response.status_code == 404
    assert read_response.json() == {"detail": "administrator account was not found"}
    assert update_response.status_code == 404
    assert update_response.json() == {"detail": "administrator account was not found"}
