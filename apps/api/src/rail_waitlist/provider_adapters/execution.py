from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from ..database import SessionFactory
from ..domain import Provider
from ..provider_accounts import ProviderCredentials, get_enabled_provider_credentials
from ..provider_contracts import ProviderUnavailable
from ..schemas import ProviderCapabilities, StationCatalog, TimetableItem
from .base import RailProviderAdapter

ProviderCredentialLoader = Callable[[Provider], Awaitable[ProviderCredentials | None]]


async def default_provider_credential_loader(provider: Provider) -> ProviderCredentials | None:
    async with SessionFactory() as session:
        return await get_enabled_provider_credentials(session, provider)


class FailClosedExecutionAdapter(RailProviderAdapter):
    """Explicit execution boundary for providers without an approved adapter."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=False,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
            note="승인된 background 실행 adapter가 없어 좌석 감시와 예약을 실행하지 않습니다.",
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
        raise ProviderUnavailable(
            f"{self.provider.value} execution provider does not expose timetables"
        )

    async def stations(self) -> StationCatalog:
        raise ProviderUnavailable(
            f"{self.provider.value} execution provider does not expose stations"
        )
