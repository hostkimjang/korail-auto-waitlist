"""Strict mypy witness for the services-to-result-application compatibility wiring."""

from datetime import UTC, datetime

from rail_waitlist import services
from rail_waitlist.reservations.attempt_result_application import (
    ReservationAttemptResultDependencies,
)


def assemble_services_dependencies() -> ReservationAttemptResultDependencies:
    return ReservationAttemptResultDependencies(
        apply_watch_transition=services.apply_watch_transition,
        add_outbox_event=services.add_outbox_event,
        now=lambda: datetime.now(UTC),
        result_policy=services.reservation_attempt_result_policy,
        record_reservation_confirmation=services.record_reservation_confirmation,
    )
