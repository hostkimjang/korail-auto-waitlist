from inspect import signature

import rail_waitlist.provider_adapters.korail_execution as korail_execution_module
import rail_waitlist.provider_adapters.srt_execution as srt_execution_module
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
from rail_waitlist.provider_adapters.execution import (
    ProviderCredentialLoader as OwnerProviderCredentialLoader,
)
from rail_waitlist.provider_adapters.execution import (
    default_provider_credential_loader as owner_default_provider_credential_loader,
)
from rail_waitlist.provider_adapters.experimental import (
    ExperimentalRailAdapter as OwnerExperimentalRailAdapter,
)
from rail_waitlist.provider_adapters.korail_execution import (
    KorailBrowserExecutionAdapter as OwnerKorailBrowserExecutionAdapter,
)
from rail_waitlist.provider_adapters.mock import MockProviderAdapter as OwnerMockProviderAdapter
from rail_waitlist.provider_adapters.mock import mock_seat_classes as owner_mock_seat_classes
from rail_waitlist.provider_adapters.srt_execution import (
    SrtLiveExecutionAdapter as OwnerSrtLiveExecutionAdapter,
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
from rail_waitlist.provider_registry.application import (
    get_execution_provider as owner_get_execution_provider,
)
from rail_waitlist.provider_registry.application import get_provider as owner_get_provider
from rail_waitlist.provider_registry.application import (
    get_timetable_provider as owner_get_timetable_provider,
)
from rail_waitlist.provider_registry.application import (
    list_capabilities as owner_list_capabilities,
)
from rail_waitlist.providers import (
    OFFICIAL_BOOKING_URLS,
    ExperimentalRailAdapter,
    FailClosedExecutionAdapter,
    KorailBrowserExecutionAdapter,
    MockProviderAdapter,
    OfficialTimetableAdapter,
    ProviderCredentialLoader,
    RailProviderAdapter,
    SrtLiveExecutionAdapter,
    TagoClient,
    TagoPage,
    default_provider_credential_loader,
    default_tago_client,
    get_execution_provider,
    get_provider,
    get_timetable_provider,
    list_capabilities,
    mock_seat_classes,
    normalize_departure_window,
    normalize_station_name,
    official_unknown_seat_classes,
    response_page,
)
from rail_waitlist.srt_reservation import default_srt_reservation_executor


class LifecycleSource:
    def __init__(self) -> None:
        self.drain_calls = 0
        self.close_calls = 0

    async def drain_pending_calls(self) -> None:
        self.drain_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1


def test_provider_facade_reexports_base_and_fail_closed_objects_by_identity() -> None:
    assert RailProviderAdapter is OwnerRailProviderAdapter
    assert FailClosedExecutionAdapter is OwnerFailClosedExecutionAdapter
    assert OFFICIAL_BOOKING_URLS is owner_booking_urls


def test_provider_facade_reexports_execution_objects_by_identity() -> None:
    assert ProviderCredentialLoader is OwnerProviderCredentialLoader
    assert default_provider_credential_loader is owner_default_provider_credential_loader
    assert KorailBrowserExecutionAdapter is OwnerKorailBrowserExecutionAdapter
    assert SrtLiveExecutionAdapter is OwnerSrtLiveExecutionAdapter


def test_provider_facade_reexports_registry_objects_by_identity() -> None:
    assert ExperimentalRailAdapter is OwnerExperimentalRailAdapter
    assert get_timetable_provider is owner_get_timetable_provider
    assert get_execution_provider is owner_get_execution_provider
    assert get_provider is owner_get_provider
    assert list_capabilities is owner_list_capabilities


def test_execution_adapters_bind_the_canonical_default_credential_loader() -> None:
    for adapter_type in (OwnerKorailBrowserExecutionAdapter, OwnerSrtLiveExecutionAdapter):
        credential_loader = signature(adapter_type.__init__).parameters["credential_loader"]

        assert credential_loader.default is owner_default_provider_credential_loader


def test_provider_facade_reexports_timetable_objects_by_identity() -> None:
    assert TagoClient is OwnerTagoClient
    assert default_tago_client is owner_default_tago_client
    assert OfficialTimetableAdapter is OwnerOfficialTimetableAdapter
    assert TagoPage is OwnerTagoPage
    assert response_page is owner_response_page
    assert normalize_station_name is owner_normalize_station_name
    assert normalize_departure_window is owner_normalize_departure_window
    assert official_unknown_seat_classes is owner_official_unknown_seat_classes


def test_provider_facade_reexports_mock_objects_by_identity() -> None:
    assert MockProviderAdapter is OwnerMockProviderAdapter
    assert mock_seat_classes is owner_mock_seat_classes


def test_canonical_mock_adapter_keeps_the_complete_executable_capability_contract() -> None:
    capabilities = OwnerMockProviderAdapter().capabilities()

    assert capabilities.provider is Provider.MOCK
    assert capabilities.timetable is True
    assert capabilities.official_booking_link is True
    assert capabilities.official_waitlist_link is True
    assert capabilities.seat_monitoring is True
    assert capabilities.reservation_once is True
    assert capabilities.experimental is False
    assert capabilities.enabled is True


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

    first_korail = get_execution_provider(Provider.KORAIL, settings)
    second_korail = get_execution_provider(Provider.KORAIL, settings)
    first_srt = get_execution_provider(Provider.SRT, settings)
    second_srt = get_execution_provider(Provider.SRT, settings)

    assert type(first_korail) is OwnerKorailBrowserExecutionAdapter
    assert type(second_korail) is OwnerKorailBrowserExecutionAdapter
    assert first_korail is not second_korail
    assert first_korail._source is None
    assert second_korail._source is None
    assert type(first_srt) is OwnerSrtLiveExecutionAdapter
    assert type(second_srt) is OwnerSrtLiveExecutionAdapter
    assert first_srt is not second_srt
    assert first_srt._source is None
    assert second_srt._source is None
    assert first_srt._reservation_executor is default_srt_reservation_executor()
    assert second_srt._reservation_executor is first_srt._reservation_executor


async def test_srt_execution_adapter_closes_only_its_owned_source(monkeypatch) -> None:
    settings = Settings(_env_file=None)
    borrowed_source = LifecycleSource()
    explicit_executor = object()

    def fail_if_default_executor_is_requested():
        raise AssertionError("explicit SRT executor must bypass the default factory")

    monkeypatch.setattr(
        srt_execution_module,
        "default_srt_reservation_executor",
        fail_if_default_executor_is_requested,
    )
    borrowed_adapter = OwnerSrtLiveExecutionAdapter(
        settings,
        borrowed_source,
        reservation_executor=explicit_executor,
    )

    await borrowed_adapter.drain_pending_calls()
    await borrowed_adapter.aclose()

    assert borrowed_source.drain_calls == 1
    assert borrowed_source.close_calls == 0
    assert borrowed_adapter._source is borrowed_source

    owned_source = LifecycleSource()
    monkeypatch.setattr(
        srt_execution_module,
        "default_srt_execution_source",
        lambda _settings: owned_source,
    )
    owned_adapter = OwnerSrtLiveExecutionAdapter(
        settings,
        reservation_executor=explicit_executor,
    )
    assert owned_adapter._source_instance() is owned_source

    await owned_adapter.drain_pending_calls()
    await owned_adapter.aclose()

    assert owned_source.drain_calls == 1
    assert owned_source.close_calls == 1
    assert owned_adapter._source is None


async def test_korail_execution_adapter_closes_only_its_owned_managed_source(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None)
    borrowed_source = LifecycleSource()
    borrowed_adapter = OwnerKorailBrowserExecutionAdapter(settings, borrowed_source)

    await borrowed_adapter.drain_pending_calls()
    await borrowed_adapter.aclose()

    assert borrowed_source.drain_calls == 1
    assert borrowed_source.close_calls == 0
    assert borrowed_adapter._source is borrowed_source

    owned_source = LifecycleSource()
    monkeypatch.setattr(
        korail_execution_module,
        "ManagedKorailSeatObserver",
        LifecycleSource,
    )
    monkeypatch.setattr(
        korail_execution_module,
        "default_korail_execution_source",
        lambda _settings: owned_source,
    )
    owned_adapter = OwnerKorailBrowserExecutionAdapter(settings)
    assert owned_adapter._source_instance() is owned_source

    await owned_adapter.drain_pending_calls()
    await owned_adapter.aclose()

    assert owned_source.drain_calls == 1
    assert owned_source.close_calls == 1
    assert owned_adapter._source is None


def test_mock_registries_keep_canonical_adapters_fresh() -> None:
    settings = Settings(_env_file=None)

    adapters = [
        get_timetable_provider(Provider.MOCK, settings),
        get_execution_provider(Provider.MOCK, settings),
        get_provider(Provider.MOCK, settings),
        get_timetable_provider(Provider.MOCK, settings),
    ]

    assert all(type(adapter) is OwnerMockProviderAdapter for adapter in adapters)
    assert len({id(adapter) for adapter in adapters}) == len(adapters)


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
