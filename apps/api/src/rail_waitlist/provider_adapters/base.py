from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..domain import Provider
from ..observations.contracts import SeatObservationRequest, SeatObservationResult
from ..provider_contracts import ProviderUnavailable
from ..provider_registry.contracts import ProviderCapabilities
from ..reservations.contracts import ReservationRequest, ReservationResult
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from ..timetable_management.schemas import StationCatalog, TimetableItem

OFFICIAL_BOOKING_URLS = {
    Provider.KORAIL: "https://www.korail.com/ticket/search/general",
    Provider.SRT: "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000",
    Provider.MOCK: "https://example.invalid/mock-booking",
}


class RailProviderAdapter(ABC):
    provider: Provider

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]: ...

    @abstractmethod
    async def stations(self) -> StationCatalog: ...

    async def observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        if not self.capabilities().seat_monitoring:
            raise ProviderUnavailable(
                f"{self.provider.value} provider does not support seat monitoring"
            )
        if request.provider != self.provider:
            raise ProviderUnavailable("seat observation request provider does not match adapter")
        return await self._observe_seats(request)

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        raise ProviderUnavailable(
            f"{self.provider.value} provider has no seat monitoring implementation"
        )

    async def observation_deferred_until(self) -> datetime | None:
        """Return a provider hold without performing an upstream observation.

        External sources use this preflight to move due work past a shared cooldown. The
        default keeps adapters without a persisted source cooldown unchanged.
        """
        return None

    async def drain_pending_calls(self) -> None:
        """Wait for provider calls that can outlive an observation timeout."""
        return

    async def aclose(self) -> None:
        """Release event-loop-bound resources owned by this adapter."""
        return

    async def reserve_once(self, request: ReservationRequest) -> ReservationResult:
        if not self.capabilities().reservation_once:
            raise ProviderUnavailable(
                f"{self.provider.value} provider does not support one-time reservation"
            )
        if request.provider != self.provider:
            raise ProviderUnavailable("reservation request provider does not match adapter")
        return await self._reserve_once(request)

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        raise ProviderUnavailable(
            f"{self.provider.value} provider has no one-time reservation implementation"
        )

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        if target.provider is not self.provider:
            raise ProviderUnavailable(
                "reservation confirmation target provider does not match adapter"
            )
        return await self._confirm_reservation(target)

    async def _confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return ReservationConfirmationResult(
            provider=self.provider,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            diagnostic_code=ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE,
            source="provider-confirmation-unavailable",
            observed_at=datetime.now(timezone.utc),
        )

    def official_booking_url(self) -> str:
        return OFFICIAL_BOOKING_URLS[self.provider]
