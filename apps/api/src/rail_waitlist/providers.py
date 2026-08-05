from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from .config import Settings, get_settings
from .database import SessionFactory
from .domain import Provider, ReservationOutcome, SeatClass
from .korail_execution import (
    KorailSeatObserver,
    ManagedKorailSeatObserver,
    default_korail_execution_source,
    korail_background_monitoring_enabled,
)
from .provider_accounts import ProviderCredentials, get_enabled_provider_credentials
from .provider_adapters.base import OFFICIAL_BOOKING_URLS, RailProviderAdapter
from .provider_adapters.execution import FailClosedExecutionAdapter
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
    normalize_departure_window,
    normalize_station_name,
)
from .provider_adapters.timetable_support import (
    official_unknown_seat_classes as official_unknown_seat_classes,
)
from .provider_contracts import (
    ExecutionProvider,
    ProviderUnavailable,
    RouteValidationError,
    TimetableProvider,
)
from .reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .schemas import (
    ProviderCapabilities,
    ReservationRequest,
    ReservationResult,
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    SeatObservationResult,
    StationCatalog,
    StationItem,
    TimetableItem,
)
from .srt_execution import (
    SrtSeatObserver,
    default_srt_execution_source,
    srt_background_monitoring_enabled,
)
from .srt_provider_adapter import SrtProviderAdapterClient
from .srt_reservation import SrtReservationExecutor, default_srt_reservation_executor

ProviderCredentialLoader = Callable[[Provider], Awaitable[ProviderCredentials | None]]


async def default_provider_credential_loader(provider: Provider) -> ProviderCredentials | None:
    async with SessionFactory() as session:
        return await get_enabled_provider_credentials(session, provider)


def mock_seat_classes(index: int, observed_at: datetime) -> list[SeatClassAvailability]:
    status_pairs = [
        ("available", "sold_out"),
        ("sold_out", "waitlist_available"),
        ("stale", "error"),
    ]
    booking_url = OFFICIAL_BOOKING_URLS[Provider.MOCK]

    def actions(status: str) -> list[SeatAvailabilityAction]:
        if status == "available":
            return [
                SeatAvailabilityAction(kind="official_check", url=booking_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        if status == "sold_out":
            return [SeatAvailabilityAction(kind="add_to_watch")]
        if status == "waitlist_available":
            return [
                SeatAvailabilityAction(kind="official_waitlist", url=booking_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        if status in {"stale", "error"}:
            return [SeatAvailabilityAction(kind="retry_provider")]
        return [SeatAvailabilityAction(kind="official_check", url=booking_url)]

    fares = (59_800, 83_700)
    return [
        SeatClassAvailability(
            seat_class=seat_class,
            status=status,
            provenance=SeatAvailabilityProvenance(
                kind="mock",
                source="mock",
                observed_at=observed_at,
            ),
            fare=fare,
            actions=actions(status),
        )
        for seat_class, status, fare in zip(
            (SeatClass.STANDARD, SeatClass.FIRST),
            status_pairs[index % len(status_pairs)],
            fares,
            strict=True,
        )
    ]


class MockProviderAdapter(RailProviderAdapter):
    provider = Provider.MOCK

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=True,
            official_booking_link=True,
            official_waitlist_link=True,
            seat_monitoring=True,
            reservation_once=True,
            note="상태 전이 검증용 mock provider이며 외부 요청을 보내지 않습니다.",
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
        if (origin_node_id is None) != (destination_node_id is None):
            raise RouteValidationError("origin and destination node ids must be provided together")
        if origin_node_id is not None and destination_node_id is not None:
            stations = {station.node_id: station for station in (await self.stations()).stations}
            if (
                origin_node_id == destination_node_id
                or origin_node_id not in stations
                or destination_node_id not in stations
                or normalize_station_name(stations[origin_node_id].name)
                != normalize_station_name(origin)
                or normalize_station_name(stations[destination_node_id].name)
                != normalize_station_name(destination)
            ):
                raise RouteValidationError("mock station node id and name pair is invalid")
        local_from, local_to = normalize_departure_window(departure_from, departure_to)
        window_to = local_to or local_from + timedelta(minutes=80)
        fixture_count = ((window_to - local_from) // timedelta(minutes=40)) + 1
        observed_at = datetime.now(timezone.utc)
        return [
            TimetableItem(
                provider=self.provider,
                train_number=f"MOCK-{index + 1:03d}",
                train_type="MOCK",
                origin=origin,
                destination=destination,
                departure_at=local_from + timedelta(minutes=index * 40),
                arrival_at=local_from + timedelta(minutes=120 + index * 40),
                adult_fare=10000 + index * 1000,
                timetable_source="mock",
                timetable_retrieved_at=observed_at,
                availability=SeatAvailability(
                    status="available",
                    source="mock",
                    observed_at=observed_at,
                ),
                seat_classes=mock_seat_classes(index, observed_at),
                official_booking_url=self.official_booking_url(),
            )
            for index in range(fixture_count)
        ]

    async def stations(self) -> StationCatalog:
        return StationCatalog(
            provider=self.provider,
            source="mock",
            retrieved_at=datetime.now(timezone.utc),
            catalog_scope="mock",
            provider_membership="mock",
            note="외부 요청 없이 상태 전이 검증에만 사용하는 mock 역 카탈로그입니다.",
            stations=[
                StationItem(node_id="MOCK-SEOUL", name="서울", city_code="11", city_name="서울"),
                StationItem(node_id="MOCK-SUSEO", name="수서", city_code="11", city_name="서울"),
                StationItem(node_id="MOCK-DAEJEON", name="대전", city_code="30", city_name="대전"),
                StationItem(node_id="MOCK-BUSAN", name="부산", city_code="26", city_name="부산"),
            ],
        )

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        observed_at = datetime.now(timezone.utc)
        statuses = {
            SeatClass.STANDARD: "available",
            SeatClass.FIRST: "sold_out",
            SeatClass.INFANT: "not_offered",
            SeatClass.FREE: "not_offered",
            SeatClass.WAITLIST: "waitlist_available",
        }
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=statuses[request.seat_class],
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
            )
        ]

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        observed_at = datetime.now(timezone.utc)
        return ReservationResult(
            outcome="payment_required",
            source="mock",
            observed_at=observed_at,
            payment_deadline=observed_at + timedelta(minutes=20),
            official_handoff_url=self.official_booking_url(),
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
        if credentials is None or credentials.credential_version != target.credential_version:
            return ReservationConfirmationResult(
                provider=self.provider,
                outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
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
