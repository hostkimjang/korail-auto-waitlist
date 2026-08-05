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
from rail_waitlist.providers import (
    OFFICIAL_BOOKING_URLS,
    FailClosedExecutionAdapter,
    OfficialTimetableAdapter,
    RailProviderAdapter,
    get_execution_provider,
    get_timetable_provider,
)


def test_provider_facade_reexports_base_and_fail_closed_objects_by_identity() -> None:
    assert RailProviderAdapter is OwnerRailProviderAdapter
    assert FailClosedExecutionAdapter is OwnerFailClosedExecutionAdapter
    assert OFFICIAL_BOOKING_URLS is owner_booking_urls


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


def test_official_timetable_registry_keeps_the_shared_tago_singleton() -> None:
    settings = Settings(_env_file=None)

    korail = get_timetable_provider(Provider.KORAIL, settings)
    srt = get_timetable_provider(Provider.SRT, settings)

    assert isinstance(korail, OfficialTimetableAdapter)
    assert isinstance(srt, OfficialTimetableAdapter)
    assert korail.tago_client is srt.tago_client
