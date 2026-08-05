from __future__ import annotations

import rail_waitlist.provider_registry.application as registry_application
from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.schemas import ProviderCapabilities


class StaticCapabilitiesAdapter:
    def __init__(self, capabilities: ProviderCapabilities) -> None:
        self._capabilities = capabilities

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities


def capabilities(
    provider: Provider,
    *,
    timetable: bool,
    booking: bool,
    waitlist: bool,
    monitoring: bool,
    reservation: bool,
    experimental: bool,
    enabled: bool,
    note: str,
) -> ProviderCapabilities:
    return ProviderCapabilities(
        provider=provider,
        timetable=timetable,
        official_booking_link=booking,
        official_waitlist_link=waitlist,
        seat_monitoring=monitoring,
        reservation_once=reservation,
        experimental=experimental,
        enabled=enabled,
        note=note,
    )


def test_list_capabilities_preserves_order_safe_merge_and_one_settings_instance(
    monkeypatch,
) -> None:
    settings = Settings(_env_file=None)
    settings_calls = 0
    adapter_calls: list[tuple[str, Provider, Settings]] = []

    def get_settings_once() -> Settings:
        nonlocal settings_calls
        settings_calls += 1
        return settings

    timetable_capabilities = {
        provider: capabilities(
            provider,
            timetable=True,
            booking=True,
            waitlist=False,
            monitoring=False,
            reservation=False,
            experimental=False,
            enabled=True,
            note=f"timetable-{provider.value}",
        )
        for provider in (Provider.KORAIL, Provider.SRT)
    }
    mock_capabilities = capabilities(
        Provider.MOCK,
        timetable=True,
        booking=True,
        waitlist=True,
        monitoring=True,
        reservation=True,
        experimental=False,
        enabled=True,
        note="mock",
    )
    execution_capabilities = {
        provider: capabilities(
            provider,
            timetable=False,
            booking=False,
            waitlist=True,
            monitoring=True,
            reservation=True,
            experimental=True,
            enabled=False,
            note=f"execution-{provider.value}",
        )
        for provider in (Provider.KORAIL, Provider.SRT)
    }

    def timetable_provider(provider: Provider, selected: Settings) -> StaticCapabilitiesAdapter:
        adapter_calls.append(("timetable", provider, selected))
        selected_capabilities = (
            mock_capabilities if provider is Provider.MOCK else timetable_capabilities[provider]
        )
        return StaticCapabilitiesAdapter(selected_capabilities)

    def execution_provider(provider: Provider, selected: Settings) -> StaticCapabilitiesAdapter:
        adapter_calls.append(("execution", provider, selected))
        return StaticCapabilitiesAdapter(execution_capabilities[provider])

    class ExperimentalAdapter(StaticCapabilitiesAdapter):
        def __init__(self, provider: Provider, selected: Settings) -> None:
            adapter_calls.append(("experimental", provider, selected))
            super().__init__(
                capabilities(
                    provider,
                    timetable=False,
                    booking=True,
                    waitlist=False,
                    monitoring=False,
                    reservation=False,
                    experimental=True,
                    enabled=selected.experimental_rail_enabled,
                    note=f"experimental-{provider.value}",
                )
            )

    monkeypatch.setattr(registry_application, "get_settings", get_settings_once)
    monkeypatch.setattr(registry_application, "get_timetable_provider", timetable_provider)
    monkeypatch.setattr(registry_application, "get_execution_provider", execution_provider)
    monkeypatch.setattr(registry_application, "ExperimentalRailAdapter", ExperimentalAdapter)

    result = registry_application.list_capabilities()

    assert settings_calls == 1
    assert all(selected is settings for _kind, _provider, selected in adapter_calls)
    assert [(kind, provider) for kind, provider, _settings in adapter_calls] == [
        ("timetable", Provider.KORAIL),
        ("execution", Provider.KORAIL),
        ("timetable", Provider.SRT),
        ("execution", Provider.SRT),
        ("timetable", Provider.MOCK),
        ("experimental", Provider.KORAIL),
        ("experimental", Provider.SRT),
    ]
    assert [(item.provider, item.experimental) for item in result] == [
        (Provider.KORAIL, False),
        (Provider.SRT, False),
        (Provider.MOCK, False),
        (Provider.KORAIL, True),
        (Provider.SRT, True),
    ]
    for item in result[:2]:
        assert item.timetable is True
        assert item.official_booking_link is True
        assert item.official_waitlist_link is False
        assert item.seat_monitoring is True
        assert item.reservation_once is True
        assert item.enabled is True
        assert item.note == f"timetable-{item.provider.value} execution-{item.provider.value}"
    assert result[2] is mock_capabilities
