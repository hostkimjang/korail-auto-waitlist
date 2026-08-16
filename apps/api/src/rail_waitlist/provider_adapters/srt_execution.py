from __future__ import annotations

from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..domain import Provider, ReservationOutcome
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
from ..srt_sidecar.client import SrtProviderAdapterClient
from ..srt_sidecar.reservation import SrtReservationExecutor, default_srt_reservation_executor
from ..timetable_management.schemas import StationCatalog, TimetableItem
from .base import RailProviderAdapter
from .execution import ProviderCredentialLoader, default_provider_credential_loader
from .srt_source_runtime import (
    SrtSeatObserver,
    default_srt_execution_source,
    srt_background_monitoring_enabled,
)


class SrtLiveExecutionAdapter(RailProviderAdapter):
    """Background-only SRT observation adapter with an explicit three-part opt-in."""

    provider = Provider.SRT

    def __init__(
        self,
        settings: Settings | None = None,
        source: SrtSeatObserver | None = None,
        credential_loader: ProviderCredentialLoader = default_provider_credential_loader,
        reservation_executor: SrtReservationExecutor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._source = source
        self._owns_source = source is None
        self._credential_loader = credential_loader
        self._reservation_executor = reservation_executor
        if self._reservation_executor is None and not self.settings.srt_provider_adapter_enabled:
            self._reservation_executor = default_srt_reservation_executor()

    def _source_instance(self) -> SrtSeatObserver:
        if self._source is None:
            self._source = default_srt_execution_source(self.settings)
        return self._source

    def capabilities(self) -> ProviderCapabilities:
        enabled = srt_background_monitoring_enabled(self.settings)
        reservation_enabled = enabled and self.settings.srt_reservation_once_enabled
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
                "SRT 계정 없는 좌석 관측을 background 감시에 사용합니다. "
                "명시적으로 활성화한 대기는 저장된 계정으로 결제 직전 임시 예약을 "
                "한 번만 시도하며 결제는 실행하지 않습니다."
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
        raise ProviderUnavailable("SRT execution provider does not expose timetables")

    async def stations(self) -> StationCatalog:
        raise ProviderUnavailable("SRT execution provider does not expose stations")

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        source = self._source_instance()
        return await source.observe(
            request,
            origin=request.origin,
            destination=request.destination,
        )

    async def observation_deferred_until(self) -> datetime | None:
        if not self.capabilities().seat_monitoring:
            return None
        source = self._source_instance()
        return await source.observation_deferred_until()

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        try:
            credentials = await self._credential_loader(self.provider)
        except RuntimeError:
            credentials = None
        if credentials is None:
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="srtrain-2.6.7-reservation",
                observed_at=datetime.now(timezone.utc),
            )
        if (
            request.expected_credential_version is not None
            and credentials.credential_version != request.expected_credential_version
        ):
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="srtrain-2.6.7-reservation",
                observed_at=datetime.now(timezone.utc),
                credential_version=request.expected_credential_version,
            )
        reservation_executor = self._reservation_executor
        if reservation_executor is None:
            source = self._source_instance()
            if not isinstance(source, SrtProviderAdapterClient):
                raise ProviderUnavailable("SRT provider adapter is unavailable")
            reservation_executor = source
        result = await reservation_executor.reserve_once(request, credentials)
        return result.model_copy(update={"credential_version": credentials.credential_version})

    async def _confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        try:
            credentials = await self._credential_loader(self.provider)
        except RuntimeError:
            credentials = None
        if credentials is None:
            return ReservationConfirmationResult(
                provider=self.provider,
                outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
                source="srtrain-reservation-list",
                observed_at=datetime.now(timezone.utc),
            )
        if credentials.credential_version != target.credential_version:
            return ReservationConfirmationResult(
                provider=self.provider,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                diagnostic_code=(ReservationConfirmationDiagnosticCode.CREDENTIAL_CONTEXT_MISMATCH),
                source="srtrain-reservation-list",
                observed_at=datetime.now(timezone.utc),
            )
        confirmer = self._reservation_executor
        if confirmer is None:
            source = self._source_instance()
            if not isinstance(source, SrtProviderAdapterClient):
                raise ProviderUnavailable("SRT provider adapter is unavailable")
            confirmer = source
        return await confirmer.confirm_reservation(target, credentials)

    async def drain_pending_calls(self) -> None:
        if self._source is not None:
            await self._source.drain_pending_calls()

    async def aclose(self) -> None:
        if self._owns_source and self._source is not None:
            close_source = getattr(self._source, "aclose", None)
            if close_source is not None:
                await close_source()
            self._source = None
