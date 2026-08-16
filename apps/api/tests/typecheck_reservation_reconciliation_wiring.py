"""Strict mypy witness for the services reconciliation compatibility wiring."""

from sqlalchemy.ext.asyncio import AsyncSession

from rail_waitlist import services
from rail_waitlist.domain import Provider
from rail_waitlist.provider_account_management.schemas import RailProviderAuthStatus
from rail_waitlist.reservations.reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)


async def update_provider_auth_status(
    session: AsyncSession,
    provider: Provider,
    status: RailProviderAuthStatus,
    *,
    expected_credential_version: int,
) -> bool:
    del session, provider, status, expected_credential_version
    return True


def assemble_services_dependencies() -> ReservationReconciliationStateDependencies:
    return ReservationReconciliationStateDependencies(
        apply_watch_transition=services.apply_watch_transition,
        add_outbox_event=services.add_outbox_event,
        record_reservation_confirmation=services.record_reservation_confirmation,
        update_provider_auth_status=update_provider_auth_status,
        utc_instant=services._utc_instant,
    )
