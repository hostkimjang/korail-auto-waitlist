from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .domain import Provider
from .reservation_confirmation import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .schemas import (
    ProviderCapabilities,
    ReservationRequest,
    ReservationResult,
    SeatObservationRequest,
    SeatObservationResult,
    StationCatalog,
    TimetableItem,
)


class ProviderUnavailable(RuntimeError):
    """Sanitized failure at a provider integration boundary."""


class RouteValidationError(ValueError):
    """Invalid route identity or departure window supplied to a timetable provider."""


class ProviderCapabilitySource(Protocol):
    provider: Provider

    def capabilities(self) -> ProviderCapabilities: ...


class TimetableProvider(ProviderCapabilitySource, Protocol):
    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]: ...

    async def stations(self) -> StationCatalog: ...

    def official_booking_url(self) -> str: ...


class ObservationProvider(ProviderCapabilitySource, Protocol):
    async def observation_deferred_until(self) -> datetime | None: ...

    async def observe_seats(
        self, request: SeatObservationRequest
    ) -> list[SeatObservationResult]: ...


class ReservationProvider(Protocol):
    async def reserve_once(self, request: ReservationRequest) -> ReservationResult: ...


class ConfirmationProvider(Protocol):
    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult: ...


class ProviderLifecycle(Protocol):
    async def drain_pending_calls(self) -> None: ...

    async def aclose(self) -> None: ...


class ReservationExecutionProvider(ReservationProvider, ConfirmationProvider, Protocol):
    """Roles required while executing one already-authorized reservation attempt."""


class ReconciliationProvider(ProviderCapabilitySource, ConfirmationProvider, Protocol):
    """Provider operations required by read-only reservation reconciliation."""


class ReconciliationExecutionProvider(
    ReconciliationProvider,
    ProviderLifecycle,
    Protocol,
):
    """Task-scoped reconciliation provider plus its separately owned lifecycle."""


class ExecutionProvider(
    ObservationProvider,
    ReservationProvider,
    ConfirmationProvider,
    ProviderLifecycle,
    Protocol,
):
    """One task-scoped provider instance shared across worker execution roles."""
