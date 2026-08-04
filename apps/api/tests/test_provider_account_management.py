from rail_waitlist.provider_account_management.http import router
from rail_waitlist.provider_account_management.schemas import (
    RailProviderAccountRead as FeatureRailProviderAccountRead,
)
from rail_waitlist.provider_account_management.schemas import (
    RailProviderAccountUpsert as FeatureRailProviderAccountUpsert,
)
from rail_waitlist.provider_account_management.schemas import (
    RailProviderRuntimeStatusRead as FeatureRailProviderRuntimeStatusRead,
)
from rail_waitlist.schemas import RailProviderAccountRead as CompatibilityRailProviderAccountRead
from rail_waitlist.schemas import (
    RailProviderAccountUpsert as CompatibilityRailProviderAccountUpsert,
)
from rail_waitlist.schemas import (
    RailProviderRuntimeStatusRead as CompatibilityRailProviderRuntimeStatusRead,
)


def test_provider_account_schemas_keep_compatibility_exports() -> None:
    assert CompatibilityRailProviderAccountRead is FeatureRailProviderAccountRead
    assert CompatibilityRailProviderAccountUpsert is FeatureRailProviderAccountUpsert
    assert CompatibilityRailProviderRuntimeStatusRead is FeatureRailProviderRuntimeStatusRead


def test_provider_account_management_router_owns_existing_routes() -> None:
    route_contracts = {(route.path, frozenset(route.methods or ())) for route in router.routes}
    assert route_contracts == {
        ("/api/v1/provider-accounts", frozenset({"GET"})),
        ("/api/v1/provider-accounts/{provider}", frozenset({"PUT"})),
        ("/api/v1/provider-accounts/{provider}", frozenset({"DELETE"})),
        ("/api/v1/provider-runtime-status", frozenset({"GET"})),
    }
