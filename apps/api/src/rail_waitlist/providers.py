from __future__ import annotations

from datetime import datetime

from .config import Settings, get_settings
from .domain import Provider
from .provider_adapters.base import OFFICIAL_BOOKING_URLS as OFFICIAL_BOOKING_URLS
from .provider_adapters.base import RailProviderAdapter
from .provider_adapters.execution import FailClosedExecutionAdapter
from .provider_adapters.execution import (
    ProviderCredentialLoader as ProviderCredentialLoader,
)
from .provider_adapters.execution import (
    default_provider_credential_loader as default_provider_credential_loader,
)
from .provider_adapters.korail_execution import KorailBrowserExecutionAdapter
from .provider_adapters.mock import MockProviderAdapter
from .provider_adapters.mock import mock_seat_classes as mock_seat_classes
from .provider_adapters.srt_execution import SrtLiveExecutionAdapter
from .provider_adapters.tago import (
    TagoClient as TagoClient,
)
from .provider_adapters.tago import (
    TagoPage as TagoPage,
)
from .provider_adapters.tago import (
    default_tago_client as default_tago_client,
)
from .provider_adapters.tago import (
    response_page as response_page,
)
from .provider_adapters.timetable import OfficialTimetableAdapter
from .provider_adapters.timetable_support import (
    normalize_departure_window as normalize_departure_window,
)
from .provider_adapters.timetable_support import (
    normalize_station_name as normalize_station_name,
)
from .provider_adapters.timetable_support import (
    official_unknown_seat_classes as official_unknown_seat_classes,
)
from .provider_contracts import ExecutionProvider, TimetableProvider
from .provider_contracts import ProviderUnavailable as ProviderUnavailable
from .provider_contracts import RouteValidationError as RouteValidationError
from .schemas import (
    ProviderCapabilities,
    StationCatalog,
    TimetableItem,
)


class ExperimentalRailAdapter(RailProviderAdapter):
    def __init__(self, provider: Provider, settings: Settings | None = None) -> None:
        self.provider = provider
        self.settings = settings or get_settings()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
            experimental=True,
            enabled=self.settings.experimental_rail_enabled,
            note="실험 어댑터는 미구현이며 비공식 endpoint를 호출하지 않습니다.",
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        raise NotImplementedError("experimental provider has no external implementation")

    async def stations(self) -> StationCatalog:
        raise NotImplementedError("experimental provider has no external implementation")


def get_timetable_provider(
    provider: Provider, settings: Settings | None = None
) -> TimetableProvider:
    """Resolve adapters used only by request-time timetable and station flows."""

    settings = settings or get_settings()
    if provider == Provider.MOCK:
        return MockProviderAdapter()
    return OfficialTimetableAdapter(provider, settings)


def get_execution_provider(
    provider: Provider, settings: Settings | None = None
) -> ExecutionProvider:
    """Resolve adapters allowed to execute background observation or reservation work."""

    settings = settings or get_settings()
    if provider == Provider.MOCK:
        return MockProviderAdapter()
    if provider == Provider.KORAIL:
        return KorailBrowserExecutionAdapter(settings)
    if provider == Provider.SRT:
        return SrtLiveExecutionAdapter(settings)
    return FailClosedExecutionAdapter(provider)


def get_provider(provider: Provider, settings: Settings | None = None) -> TimetableProvider:
    """Compatibility alias for the historical request-time provider registry."""

    return get_timetable_provider(provider, settings)


def list_capabilities(settings: Settings | None = None) -> list[ProviderCapabilities]:
    settings = settings or get_settings()
    official_capabilities: list[ProviderCapabilities] = []
    for provider in (Provider.KORAIL, Provider.SRT):
        timetable = get_timetable_provider(provider, settings).capabilities()
        execution = get_execution_provider(provider, settings).capabilities()
        official_capabilities.append(
            timetable.model_copy(
                update={
                    "seat_monitoring": execution.seat_monitoring,
                    "reservation_once": execution.reservation_once,
                    "note": f"{timetable.note or ''} {execution.note or ''}".strip(),
                }
            )
        )
    return [
        *official_capabilities,
        get_timetable_provider(Provider.MOCK, settings).capabilities(),
        ExperimentalRailAdapter(Provider.KORAIL, settings).capabilities(),
        ExperimentalRailAdapter(Provider.SRT, settings).capabilities(),
    ]
