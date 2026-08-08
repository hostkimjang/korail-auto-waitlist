from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..outbox import add_outbox_event
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from ..watch_management.transition_runtime import apply_watch_transition
from .attempt_result_application import record_reservation_confirmation
from .payment_hold_application import _utc_instant
from .provider_confirmation.contracts import ReservationConfirmationResult
from .reconciliation_state_application import (
    AddOutboxEvent,
    ApplyWatchTransition,
    RecordReservationConfirmation,
    ReservationReconciliationStateDependencies,
    UtcInstant,
)
from .reconciliation_state_application import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_application,
)


def reservation_reconciliation_state_dependencies(
    *,
    apply_watch_transition_override: ApplyWatchTransition | None = None,
    add_outbox_event_override: AddOutboxEvent | None = None,
    record_reservation_confirmation_override: RecordReservationConfirmation | None = None,
    utc_instant_override: UtcInstant | None = None,
) -> ReservationReconciliationStateDependencies:
    """Compose state side effects without owning or ending the caller's transaction."""

    return ReservationReconciliationStateDependencies(
        apply_watch_transition=(
            apply_watch_transition
            if apply_watch_transition_override is None
            else apply_watch_transition_override
        ),
        add_outbox_event=(
            add_outbox_event if add_outbox_event_override is None else add_outbox_event_override
        ),
        record_reservation_confirmation=(
            record_reservation_confirmation
            if record_reservation_confirmation_override is None
            else record_reservation_confirmation_override
        ),
        utc_instant=_utc_instant if utc_instant_override is None else utc_instant_override,
    )


async def apply_reservation_reconciliation(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    confirmation: ReservationConfirmationResult,
    *,
    reconciled_at: datetime,
    dependencies: ReservationReconciliationStateDependencies | None = None,
) -> None:
    """Apply reconciliation state with feature-owned production dependencies."""

    if dependencies is None:
        dependencies = reservation_reconciliation_state_dependencies()
    await apply_reservation_reconciliation_application(
        session,
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=reconciled_at,
        dependencies=dependencies,
    )
