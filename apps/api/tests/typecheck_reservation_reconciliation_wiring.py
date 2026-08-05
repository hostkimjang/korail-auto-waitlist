"""Strict mypy witness for the services reconciliation compatibility wiring."""

from rail_waitlist import services
from rail_waitlist.reservations.reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)


def assemble_services_dependencies() -> ReservationReconciliationStateDependencies:
    return ReservationReconciliationStateDependencies(
        apply_watch_transition=services.apply_watch_transition,
        add_outbox_event=services.add_outbox_event,
        record_reservation_confirmation=services.record_reservation_confirmation,
        utc_instant=services._utc_instant,
    )
