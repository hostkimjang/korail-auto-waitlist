import pytest

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.provider_contracts import ProviderUnavailable as ContractProviderUnavailable
from rail_waitlist.provider_contracts import RouteValidationError as ContractRouteValidationError
from rail_waitlist.providers import ProviderUnavailable, RouteValidationError, list_capabilities


def test_legacy_provider_errors_reexport_the_canonical_contract_objects() -> None:
    assert ProviderUnavailable is ContractProviderUnavailable
    assert RouteValidationError is ContractRouteValidationError


@pytest.mark.parametrize(
    ("provider", "settings_overrides", "seat_monitoring", "reservation_once"),
    [
        (Provider.KORAIL, {}, False, False),
        (
            Provider.KORAIL,
            {
                "EXPERIMENTAL_RAIL_ENABLED": True,
                "korail_browser_adapter_enabled": True,
                "korail_browser_adapter_token": "b" * 32,
                "korail_seat_monitoring_enabled": True,
            },
            True,
            False,
        ),
        (
            Provider.KORAIL,
            {
                "EXPERIMENTAL_RAIL_ENABLED": True,
                "korail_browser_adapter_enabled": True,
                "korail_browser_adapter_token": "b" * 32,
                "korail_seat_monitoring_enabled": True,
                "korail_reservation_once_enabled": True,
            },
            True,
            True,
        ),
        (Provider.SRT, {}, False, False),
        (
            Provider.SRT,
            {
                "EXPERIMENTAL_RAIL_ENABLED": True,
                "srt_seat_status_enabled": True,
                "srt_seat_monitoring_enabled": True,
            },
            True,
            False,
        ),
        (
            Provider.SRT,
            {
                "EXPERIMENTAL_RAIL_ENABLED": True,
                "srt_seat_status_enabled": True,
                "srt_seat_monitoring_enabled": True,
                "srt_reservation_once_enabled": True,
            },
            True,
            True,
        ),
    ],
)
def test_public_capability_merge_preserves_timetable_and_execution_gate_intersection(
    provider: Provider,
    settings_overrides: dict[str, object],
    seat_monitoring: bool,
    reservation_once: bool,
) -> None:
    settings = Settings(_env_file=None, **settings_overrides)

    public_capability = next(
        capability
        for capability in list_capabilities(settings)
        if capability.provider is provider and capability.experimental is False
    )

    assert public_capability.timetable is True
    assert public_capability.official_booking_link is True
    assert public_capability.seat_monitoring is seat_monitoring
    assert public_capability.reservation_once is reservation_once
