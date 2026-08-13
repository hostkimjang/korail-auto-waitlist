from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from requests import RequestException
from SRT import (  # type: ignore[import-untyped]
    SRTError,
    SRTLoginError,
    SRTNotLoggedInError,
    SRTResponseError,
)
from SRT.errors import SRTNetFunnelError  # type: ignore[import-untyped]

from ..observations.contracts import SeatObservationRequest, SeatObservationResult
from ..provider_account_management.contracts import ProviderCredentials
from ..reservations.contracts import ReservationRequest, ReservationResult
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from ..timetable_management.schemas import SeatAvailabilityStatus, TimetableItem
from .contracts import SrtLoginRequest, SrtLoginResult, SrtSessionStatus
from .session_contract import SrtSessionActorSnapshot


class SrtTimetableTrainProjection(Protocol):
    @property
    def train_number(self) -> str: ...

    @property
    def train_type(self) -> str: ...

    @property
    def origin(self) -> str: ...

    @property
    def destination(self) -> str: ...

    @property
    def departure_at(self) -> datetime: ...

    @property
    def arrival_at(self) -> datetime: ...

    @property
    def standard_status(self) -> SeatAvailabilityStatus: ...

    @property
    def first_status(self) -> SeatAvailabilityStatus: ...

    @property
    def observed_at(self) -> datetime: ...

    @property
    def delay_minutes(self) -> int | None: ...

    @property
    def adult_fare(self) -> int | None: ...

    @property
    def source(self) -> str: ...


class SrtProviderSource(Protocol):
    async def observation_deferred_until(self) -> datetime | None: ...

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]: ...

    async def overlay(
        self,
        items: list[TimetableItem],
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[TimetableItem]: ...

    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> Sequence[SrtTimetableTrainProjection]: ...

    async def drain_pending_calls(self) -> None: ...

    async def read_only_call_pending(self, request_id: str) -> bool: ...


class SrtProviderExecutor(Protocol):
    async def verify_credentials(self, credentials: ProviderCredentials) -> bool: ...

    async def prewarm_credentials(self, credentials: ProviderCredentials) -> bool: ...

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult: ...

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
        credentials: ProviderCredentials,
    ) -> ReservationConfirmationResult: ...

    def session_snapshot(self) -> SrtSessionActorSnapshot: ...


@dataclass(frozen=True)
class SrtLoginExceptionTypes:
    auth_required: tuple[type[Exception], ...]
    provider_blocked: tuple[type[Exception], ...]
    failed: tuple[type[Exception], ...]


def default_srt_login_exception_types(
    *,
    auth_required: tuple[type[Exception], ...] | None = None,
    provider_blocked: tuple[type[Exception], ...] | None = None,
    failed: tuple[type[Exception], ...] | None = None,
) -> SrtLoginExceptionTypes:
    return SrtLoginExceptionTypes(
        auth_required=(SRTLoginError, SRTNotLoggedInError)
        if auth_required is None
        else auth_required,
        provider_blocked=(SRTNetFunnelError,) if provider_blocked is None else provider_blocked,
        failed=(RequestException, SRTResponseError, SRTError) if failed is None else failed,
    )


async def build_session_status(
    source: SrtProviderSource,
    executor: SrtProviderExecutor,
    *,
    monotonic: Callable[[], float],
) -> SrtSessionStatus:
    snapshot = executor.session_snapshot()
    deferred_until = await source.observation_deferred_until()
    now = monotonic()

    def age(value: float | None) -> float | None:
        return None if value is None else max(0.0, now - value)

    return SrtSessionStatus(
        state=snapshot.state,
        credential_generation=snapshot.credential_generation,
        locally_reusable=snapshot.locally_reusable,
        created_age_seconds=age(snapshot.created_at_monotonic),
        last_verified_age_seconds=age(snapshot.last_verified_at_monotonic),
        last_used_age_seconds=age(snapshot.last_used_at_monotonic),
        local_reuse_remaining_seconds=(
            None
            if snapshot.local_reuse_until_monotonic is None
            else max(0.0, snapshot.local_reuse_until_monotonic - now)
        ),
        observation_deferred_until=deferred_until,
    )


async def prewarm_or_verify_login(
    data: SrtLoginRequest,
    executor: SrtProviderExecutor,
    *,
    exception_types: SrtLoginExceptionTypes,
) -> SrtLoginResult:
    credentials = data.credential.to_credentials()
    method = (
        executor.prewarm_credentials if data.operation == "prewarm" else executor.verify_credentials
    )
    try:
        authenticated = await method(credentials)
    except ValueError:
        return SrtLoginResult(outcome="invalid_identifier")
    except Exception as error:  # noqa: BLE001 - response-shape failures stay sanitized.
        if isinstance(error, exception_types.auth_required):
            return SrtLoginResult(outcome="auth_required")
        if isinstance(error, exception_types.provider_blocked):
            return SrtLoginResult(outcome="provider_blocked")
        if isinstance(error, exception_types.failed):
            return SrtLoginResult(outcome="failed")
        return SrtLoginResult(outcome="failed")
    return SrtLoginResult(outcome="authenticated" if authenticated else "auth_required")
