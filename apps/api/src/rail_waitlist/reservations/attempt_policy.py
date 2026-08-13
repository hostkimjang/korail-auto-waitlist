from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain import Provider, ReservationOutcome, SeatObservationStatus
from .provider_confirmation.contracts import ReservationConfirmationOutcome

CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX = "confirmed-absent-retry:"
PAYMENT_HOLD_RETRY_EPISODE_PREFIX = "availability-after-hold:"
MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX = "manual-after-hold:"

CONFIRMED_ABSENT_RETRY_OBSERVATIONS = frozenset(
    {
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
    }
)

RESERVATION_RETRY_EDGE_OBSERVATIONS = frozenset(
    {
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }
)


def official_seat_observation_source(provider: Provider) -> str | None:
    """Return the exact execution-adapter provenance allowed to consume a rearm."""
    if provider is Provider.KORAIL:
        return "korail-official-page-browser"
    if provider is Provider.SRT:
        return "srtrain-2.6.7-accountless"
    return None


def payment_hold_retry_episode_key(
    hold_attempt_id: str,
    unavailable_observation_id: str,
) -> str:
    return f"{PAYMENT_HOLD_RETRY_EPISODE_PREFIX}{hold_attempt_id}:{unavailable_observation_id}"


def parse_payment_hold_retry_episode_key(episode_key: str) -> tuple[str, str] | None:
    if not episode_key.startswith(PAYMENT_HOLD_RETRY_EPISODE_PREFIX):
        return None
    encoded_ids = episode_key.removeprefix(PAYMENT_HOLD_RETRY_EPISODE_PREFIX)
    hold_attempt_id, separator, unavailable_observation_id = encoded_ids.partition(":")
    if not separator or not hold_attempt_id or not unavailable_observation_id:
        return None
    return hold_attempt_id, unavailable_observation_id


def manual_payment_hold_rearm_episode_key(
    hold_attempt_id: str,
    candidate_id: str,
    observation_id: str,
) -> str:
    return (
        f"{MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX}"
        f"{hold_attempt_id}:{candidate_id}:{observation_id}"
    )


def parse_manual_payment_hold_rearm_episode_key(
    episode_key: str,
) -> tuple[str, str, str] | None:
    if not episode_key.startswith(MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX):
        return None
    encoded_ids = episode_key.removeprefix(MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX)
    parts = encoded_ids.split(":")
    if len(parts) != 3 or any(not part for part in parts):
        return None
    return parts[0], parts[1], parts[2]


class ConfirmedAbsentRetrySource(Protocol):
    confirmation_outcome: ReservationConfirmationOutcome | None
    confirmation_observed_at: datetime | None
    episode_key: str
    last_reconciled_at: datetime | None
    next_reconcile_at: datetime | None
    outcome: ReservationOutcome
    payment_deadline: datetime | None
    post_deadline_reconciled_at: datetime | None
    reconciliation_attempt_count: int


def is_confirmed_absent_retry_source(attempt: ConfirmedAbsentRetrySource) -> bool:
    """Return whether exact negative evidence can safely re-arm one attempt.

    UNKNOWN may re-arm once only after a bounded reconciliation persisted an exact
    official NOT_FOUND result. The retry episode prefix prevents an ambiguous retry
    from recursively authorizing another provider command.

    Older PAYMENT_REQUIRED rows may predate persisted payment deadlines and the
    post-deadline marker. They would otherwise remain fenced forever even after an
    official reservation-list read proved the hold absent. Keep this compatibility
    path deliberately narrower than the normal expired-hold flow: a missing deadline,
    exact NOT_FOUND confirmation, and a non-retry episode are all required.
    """
    if (
        attempt.confirmation_outcome is not ReservationConfirmationOutcome.NOT_FOUND
        or attempt.confirmation_observed_at is None
        or attempt.episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
    ):
        return False
    if attempt.outcome is ReservationOutcome.UNKNOWN:
        return (
            (attempt.reconciliation_attempt_count or 0) >= 1
            and attempt.last_reconciled_at is not None
            and attempt.next_reconcile_at is None
        )
    return (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.payment_deadline is None
        and attempt.post_deadline_reconciled_at is None
    )
