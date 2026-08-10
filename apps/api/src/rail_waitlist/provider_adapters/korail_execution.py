from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from redis.asyncio import Redis

from ..config import Settings, get_settings
from ..domain import Provider, ReservationOutcome
from ..korail_browser_seat_source import KorailBrowserSeatSource
from ..observations.contracts import SeatObservationRequest, SeatObservationResult
from ..provider_account_management.contracts import ProviderCredentials
from ..provider_contracts import ProviderUnavailable
from ..provider_registry.contracts import ProviderCapabilities
from ..reservations.contracts import ReservationProgressStage, ReservationRequest, ReservationResult
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from ..seat_status_cooldown import RedisCooldownStore
from ..timetable_management.schemas import StationCatalog, TimetableItem
from .base import RailProviderAdapter
from .execution import ProviderCredentialLoader, default_provider_credential_loader


class KorailSeatObserver(Protocol):
    async def observation_deferred_until(self) -> datetime | None: ...

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]: ...

    async def drain_pending_calls(self) -> None: ...

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult: ...

    async def reserve_once_with_progress(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
        on_progress: Callable[[ReservationProgressStage], Awaitable[None]],
    ) -> ReservationResult: ...

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult: ...


@dataclass
class ManagedKorailSeatObserver:
    """Own one Chromium source and Redis client for a single Celery task loop."""

    source: KorailBrowserSeatSource
    redis: Redis

    async def observation_deferred_until(self) -> datetime | None:
        return await self.source.observation_deferred_until()

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        return await self.source.observe(
            request,
            origin=origin,
            destination=destination,
        )

    async def drain_pending_calls(self) -> None:
        await self.source.drain_pending_calls()

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult:
        return await self.source.reserve_once(request, credentials)

    async def reserve_once_with_progress(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
        on_progress: Callable[[ReservationProgressStage], Awaitable[None]],
    ) -> ReservationResult:
        return await self.source.reserve_once_with_progress(request, credentials, on_progress)

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return await self.source.confirm_reservation(target)

    async def aclose(self) -> None:
        try:
            await self.source.close()
        finally:
            await self.redis.aclose()


@dataclass(frozen=True)
class KorailExecutionSourceConfig:
    redis_url: str
    adapter_url: str
    adapter_token: str | None
    cache_ttl_seconds: int
    timeout_seconds: float
    rate_limit_cooldown_seconds: int
    protection_cooldown_seconds: int
    allow_fullstack_test_url: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> KorailExecutionSourceConfig:
        return cls(
            redis_url=settings.redis_url,
            adapter_url=settings.korail_browser_adapter_url,
            adapter_token=settings.korail_browser_adapter_token,
            cache_ttl_seconds=settings.korail_browser_adapter_cache_ttl_seconds,
            timeout_seconds=settings.korail_browser_adapter_timeout_seconds,
            rate_limit_cooldown_seconds=settings.seat_status_rate_limit_cooldown_seconds,
            protection_cooldown_seconds=settings.seat_status_protection_cooldown_seconds,
            allow_fullstack_test_url=settings.environment == "test",
        )


def korail_background_monitoring_enabled(settings: Settings) -> bool:
    """Keep recurring Chromium traffic behind its own explicit opt-in."""

    return (
        settings.experimental_rail_enabled
        and settings.korail_browser_adapter_enabled
        and settings.korail_seat_monitoring_enabled
    )


def _source_for_config(config: KorailExecutionSourceConfig) -> ManagedKorailSeatObserver:
    redis = Redis.from_url(config.redis_url, decode_responses=True)
    source = KorailBrowserSeatSource(
        enabled=True,
        adapter_url=config.adapter_url,
        token=config.adapter_token,
        cache_ttl_seconds=config.cache_ttl_seconds,
        timeout_seconds=config.timeout_seconds,
        rate_limit_cooldown_seconds=config.rate_limit_cooldown_seconds,
        protection_cooldown_seconds=config.protection_cooldown_seconds,
        cooldown_store=RedisCooldownStore(redis),
        allow_fullstack_test_url=config.allow_fullstack_test_url,
    )
    return ManagedKorailSeatObserver(source=source, redis=redis)


def default_korail_execution_source(settings: Settings) -> ManagedKorailSeatObserver:
    """Create one source owned by the current Celery task event loop."""

    if not korail_background_monitoring_enabled(settings):
        raise RuntimeError("KORAIL background monitoring is not explicitly enabled")
    return _source_for_config(KorailExecutionSourceConfig.from_settings(settings))


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
        credentials = await self._reservation_credentials(request)
        if isinstance(credentials, ReservationResult):
            return credentials
        result = await self._source_instance().reserve_once(request, credentials)
        return result.model_copy(update={"credential_version": credentials.credential_version})

    async def reserve_once_with_progress(
        self,
        request: ReservationRequest,
        on_progress: Callable[[ReservationProgressStage], Awaitable[None]],
    ) -> ReservationResult:
        if not self.capabilities().reservation_once:
            raise ProviderUnavailable("korail provider does not support one-time reservation")
        if request.provider != self.provider:
            raise ProviderUnavailable("reservation request provider does not match adapter")
        credentials = await self._reservation_credentials(request)
        if isinstance(credentials, ReservationResult):
            return credentials
        result = await self._source_instance().reserve_once_with_progress(
            request,
            credentials,
            on_progress,
        )
        return result.model_copy(update={"credential_version": credentials.credential_version})

    async def _reservation_credentials(
        self,
        request: ReservationRequest,
    ) -> ProviderCredentials | ReservationResult:
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
        return credentials

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
