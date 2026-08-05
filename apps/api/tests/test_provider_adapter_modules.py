import rail_waitlist.provider_adapters.tago as tago_module
from rail_waitlist.approved_provider import ApprovedProviderAdapter
from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.provider_adapters.base import (
    OFFICIAL_BOOKING_URLS as owner_booking_urls,
)
from rail_waitlist.provider_adapters.base import RailProviderAdapter as OwnerRailProviderAdapter
from rail_waitlist.provider_adapters.execution import (
    FailClosedExecutionAdapter as OwnerFailClosedExecutionAdapter,
)
from rail_waitlist.provider_adapters.tago import TagoClient as OwnerTagoClient
from rail_waitlist.provider_adapters.tago import TagoPage as OwnerTagoPage
from rail_waitlist.provider_adapters.tago import (
    default_tago_client as owner_default_tago_client,
)
from rail_waitlist.provider_adapters.tago import response_page as owner_response_page
from rail_waitlist.provider_adapters.timetable import (
    OfficialTimetableAdapter as OwnerOfficialTimetableAdapter,
)
from rail_waitlist.provider_adapters.timetable_support import (
    normalize_departure_window as owner_normalize_departure_window,
)
from rail_waitlist.provider_adapters.timetable_support import (
    normalize_station_name as owner_normalize_station_name,
)
from rail_waitlist.provider_adapters.timetable_support import (
    official_unknown_seat_classes as owner_official_unknown_seat_classes,
)
from rail_waitlist.providers import (
    OFFICIAL_BOOKING_URLS,
    FailClosedExecutionAdapter,
    OfficialTimetableAdapter,
    RailProviderAdapter,
    TagoClient,
    TagoPage,
    default_tago_client,
    get_execution_provider,
    get_timetable_provider,
    normalize_departure_window,
    normalize_station_name,
    official_unknown_seat_classes,
    response_page,
)


def test_provider_facade_reexports_base_and_fail_closed_objects_by_identity() -> None:
    assert RailProviderAdapter is OwnerRailProviderAdapter
    assert FailClosedExecutionAdapter is OwnerFailClosedExecutionAdapter
    assert OFFICIAL_BOOKING_URLS is owner_booking_urls


def test_provider_facade_reexports_timetable_objects_by_identity() -> None:
    assert TagoClient is OwnerTagoClient
    assert default_tago_client is owner_default_tago_client
    assert OfficialTimetableAdapter is OwnerOfficialTimetableAdapter
    assert TagoPage is OwnerTagoPage
    assert response_page is owner_response_page
    assert normalize_station_name is owner_normalize_station_name
    assert normalize_departure_window is owner_normalize_departure_window
    assert official_unknown_seat_classes is owner_official_unknown_seat_classes


def test_fail_closed_adapter_keeps_all_execution_capabilities_disabled() -> None:
    adapter = FailClosedExecutionAdapter(Provider.SRT)

    capabilities = adapter.capabilities()

    assert capabilities.provider is Provider.SRT
    assert capabilities.timetable is False
    assert capabilities.official_booking_link is False
    assert capabilities.official_waitlist_link is False
    assert capabilities.seat_monitoring is False
    assert capabilities.reservation_once is False


def test_existing_approved_adapter_inherits_the_canonical_base_object() -> None:
    assert issubclass(ApprovedProviderAdapter, OwnerRailProviderAdapter)


def test_execution_registry_keeps_adapters_fresh_and_sources_lazy() -> None:
    settings = Settings(_env_file=None)

    first = get_execution_provider(Provider.KORAIL, settings)
    second = get_execution_provider(Provider.KORAIL, settings)

    assert first is not second
    assert first._source is None
    assert second._source is None


def test_official_timetable_registry_keeps_the_canonical_shared_tago_singleton(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(tago_module, "_default_tago_client", None)

    owner_client = owner_default_tago_client()

    korail = get_timetable_provider(Provider.KORAIL, settings)
    srt = get_timetable_provider(Provider.SRT, settings)

    assert default_tago_client() is owner_client
    assert isinstance(korail, OfficialTimetableAdapter)
    assert isinstance(srt, OfficialTimetableAdapter)
    assert korail.tago_client is owner_client
    assert srt.tago_client is owner_client


def test_official_timetable_adapter_explicit_client_bypasses_default_factory(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None)
    provided_client = OwnerTagoClient(settings)

    def fail_if_called() -> OwnerTagoClient:
        raise AssertionError("default TAGO client must not be requested")

    monkeypatch.setattr(tago_module, "default_tago_client", fail_if_called)

    adapter = OwnerOfficialTimetableAdapter(
        Provider.KORAIL,
        settings,
        tago_client=provided_client,
    )

    assert adapter.tago_client is provided_client
