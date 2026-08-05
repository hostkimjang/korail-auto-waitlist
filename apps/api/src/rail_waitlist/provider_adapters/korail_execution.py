from __future__ import annotations

from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..domain import Provider, ReservationOutcome
from ..korail_execution import (
    KorailSeatObserver,
    ManagedKorailSeatObserver,
    default_korail_execution_source,
    korail_background_monitoring_enabled,
)
from ..provider_contracts import ProviderUnavailable
from ..reservation_confirmation import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from ..schemas import (
    ProviderCapabilities,
    ReservationRequest,
    ReservationResult,
    SeatObservationRequest,
    SeatObservationResult,
    StationCatalog,
    TimetableItem,
)
from .base import RailProviderAdapter
from .execution import ProviderCredentialLoader, default_provider_credential_loader


class KorailBrowserExecutionAdapter(RailProviderAdapter):
    """Background-only official-page observation with an explicit three-part opt-in."""

    provider = Provider.KORAIL

    def __init__(
        self,
        settings: Settings | None = None,
        source: KorailSeatObserver | None = None,
        credential_loader: ProviderCredentialLoader = default_provider_credential_loader,
    ) -> None:
        self.settings = settings or get_settings()
        self._source = source
        self._owns_source = source is None
        self._credential_loader = credential_loader

    def _source_instance(self) -> KorailSeatObserver:
        if self._source is None:
            self._source = default_korail_execution_source(self.settings)
        return self._source

    def capabilities(self) -> ProviderCapabilities:
        enabled = korail_background_monitoring_enabled(self.settings)
        reservation_enabled = enabled and self.settings.korail_reservation_once_enabled
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=False,
            official_waitlist_link=False,
            seat_monitoring=enabled,
            reservation_once=reservation_enabled,
            experimental=True,
            enabled=enabled,
            note=(
                "서버 관리 표준 Chromium의 공식 결과 DOM을 background 감시에만 사용합니다. "
                "보호 응답에서는 shared cooldown으로 중단합니다. 명시적으로 활성화한 "
                "대기는 결제 직전 임시 예약을 한 번만 시도하며 결제는 실행하지 않습니다."
            ),
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
        raise ProviderUnavailable("KORAIL execution provider does not expose timetables")

    async def stations(self) -> StationCatalog:
        raise ProviderUnavailable("KORAIL execution provider does not expose stations")

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        return await self._source_instance().observe(
            request,
            origin=request.origin,
            destination=request.destination,
        )

    async def observation_deferred_until(self) -> datetime | None:
        if not self.capabilities().seat_monitoring:
            return None
        return await self._source_instance().observation_deferred_until()

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        try:
            credentials = await self._credential_loader(self.provider)
        except RuntimeError:
            credentials = None
        if credentials is None:
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="korail-pydoll-reservation",
                observed_at=datetime.now(timezone.utc),
            )
        if (
            request.expected_credential_version is not None
            and credentials.credential_version != request.expected_credential_version
        ):
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="korail-pydoll-reservation",
                observed_at=datetime.now(timezone.utc),
                credential_version=request.expected_credential_version,
            )
        result = await self._source_instance().reserve_once(request, credentials)
        return result.model_copy(update={"credential_version": credentials.credential_version})

    async def _confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return await self._source_instance().confirm_reservation(target)

    async def drain_pending_calls(self) -> None:
        if self._source is not None:
            await self._source.drain_pending_calls()

    async def aclose(self) -> None:
        if self._owns_source and isinstance(self._source, ManagedKorailSeatObserver):
            await self._source.aclose()
            self._source = None
