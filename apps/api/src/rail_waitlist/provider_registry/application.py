from __future__ import annotations

from ..config import Settings, get_settings
from ..domain import Provider
from ..provider_adapters.execution import FailClosedExecutionAdapter
from ..provider_adapters.experimental import ExperimentalRailAdapter
from ..provider_adapters.korail_execution import KorailBrowserExecutionAdapter
from ..provider_adapters.mock import MockProviderAdapter
from ..provider_adapters.srt_execution import SrtLiveExecutionAdapter
from ..provider_adapters.timetable import OfficialTimetableAdapter
from ..provider_contracts import ExecutionProvider, TimetableProvider
from ..schemas import ProviderCapabilities


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
