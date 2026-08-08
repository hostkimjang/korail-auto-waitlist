from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..observations.status_policy import ACTIONABLE_SEAT_STATUSES
from ..outbox import add_outbox_event
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate
from ..watch_management.transition_runtime import apply_watch_transition
from .attempt_claim_application import ReservationAttemptClaimDependencies
from .attempt_claim_application import (
    begin_reservation_attempt as begin_reservation_attempt_application,
)
from .attempt_policy import is_confirmed_absent_retry_source
from .attempt_result_application import (
    ReservationAttemptResultDependencies,
    record_reservation_confirmation,
)
from .attempt_result_application import (
    complete_reservation_attempt as complete_reservation_attempt_application,
)
from .contracts import ReservationResult
from .domain import reservation_attempt_result_policy
from .payment_hold_application import is_payment_hold_ended
from .provider_confirmation.contracts import ReservationConfirmationResult


def reservation_attempt_claim_dependencies() -> ReservationAttemptClaimDependencies:
    """Compose claim policy without ending the caller-owned transaction."""

    return ReservationAttemptClaimDependencies(
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        is_payment_hold_ended=is_payment_hold_ended,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        actionable_seat_statuses=ACTIONABLE_SEAT_STATUSES,
    )


async def begin_reservation_attempt(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    idempotency_key: str,
    *,
    episode_key: str | None = None,
    retry_authorized: bool = False,
    credential_version: int | None = None,
) -> tuple[ReservationAttempt, bool]:
    return await begin_reservation_attempt_application(
        session,
        watch,
        candidate,
        idempotency_key,
        episode_key=episode_key,
        retry_authorized=retry_authorized,
        credential_version=credential_version,
        dependencies=reservation_attempt_claim_dependencies(),
    )


def reservation_attempt_result_dependencies() -> ReservationAttemptResultDependencies:
    """Compose result policy without translating domain errors or committing."""

    return ReservationAttemptResultDependencies(
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        now=lambda: datetime.now(UTC),
        result_policy=reservation_attempt_result_policy,
        record_reservation_confirmation=record_reservation_confirmation,
    )


async def complete_reservation_attempt(
    session: AsyncSession,
    watch: Watch,
    candidate: WatchCandidate,
    attempt: ReservationAttempt,
    result: ReservationResult,
    confirmation: ReservationConfirmationResult | None = None,
) -> None:
    await complete_reservation_attempt_application(
        session,
        watch,
        candidate,
        attempt,
        result,
        confirmation,
        dependencies=reservation_attempt_result_dependencies(),
    )
