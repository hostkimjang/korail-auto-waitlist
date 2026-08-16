from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from ..domain import Provider, ReservationOutcome, SeatObservationStatus
from .provider_confirmation.contracts import ReservationConfirmationOutcome
from .reconciliation_policy import (
    UNKNOWN_MANUAL_REARM_MIN_RECONCILIATIONS,
    ReservationReconciliationResolution,
)
from .retry_fence_contracts import (
    AutomaticReservationRetryFenceReason as _AutomaticReservationRetryFenceReason,
)

CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX = "confirmed-absent-retry:"
PAYMENT_HOLD_RETRY_EPISODE_PREFIX = "availability-after-hold:"
MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX = "manual-after-hold:"
MANUAL_UNKNOWN_REARM_EPISODE_PREFIX = "manual-unknown:"

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
        SeatObservationStatus.STANDING_ONLY,
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


def manual_unknown_rearm_episode_key(
    source_attempt_id: str,
    candidate_id: str,
    observation_id: str,
) -> str:
    return (
        f"{MANUAL_UNKNOWN_REARM_EPISODE_PREFIX}{source_attempt_id}:{candidate_id}:{observation_id}"
    )


def parse_manual_unknown_rearm_episode_key(
    episode_key: str,
) -> tuple[str, str, str] | None:
    if not episode_key.startswith(MANUAL_UNKNOWN_REARM_EPISODE_PREFIX):
        return None
    encoded_ids = episode_key.removeprefix(MANUAL_UNKNOWN_REARM_EPISODE_PREFIX)
    parts = encoded_ids.split(":")
    if len(parts) != 3 or any(not part for part in parts):
        return None
    return parts[0], parts[1], parts[2]


class ConfirmedAbsentRetrySource(Protocol):
    id: str
    confirmation_outcome: ReservationConfirmationOutcome | None
    confirmation_observed_at: datetime | None
    episode_key: str
    last_reconciled_at: datetime | None
    next_reconcile_at: datetime | None
    outcome: ReservationOutcome
    payment_deadline: datetime | None
    post_deadline_reconciled_at: datetime | None
    reconciliation_attempt_count: int
    reconciliation_resolution: ReservationReconciliationResolution | None


def exact_paid_reservation_attempt_id(
    attempts: Iterable[ConfirmedAbsentRetrySource],
) -> str | None:
    """Return a watch-wide absolute fence for exact official paid evidence."""

    return next(
        (
            attempt.id
            for attempt in attempts
            if attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
        ),
        None,
    )


def active_unresolved_unknown_attempt_ids(
    attempts: Iterable[ConfirmedAbsentRetrySource],
) -> frozenset[str]:
    """Return every unconsumed UNKNOWN command that can still own a reservation."""

    materialized = tuple(attempts)
    unconsumed_ids = unconsumed_unknown_attempt_ids(materialized)
    return frozenset(
        attempt.id
        for attempt in materialized
        if attempt.outcome is ReservationOutcome.UNKNOWN
        and attempt.confirmation_outcome is not ReservationConfirmationOutcome.CONFIRMED_PAID
        and attempt.reconciliation_resolution
        is not ReservationReconciliationResolution.CONFIRMED_ABSENT
        and attempt.id in unconsumed_ids
    )


def unconsumed_unknown_attempt_ids(
    attempts: Iterable[ConfirmedAbsentRetrySource],
) -> frozenset[str]:
    """Return UNKNOWN sources that have not produced their single recovery child."""

    materialized = tuple(attempts)
    consumed_source_ids: set[str] = set()
    for attempt in materialized:
        parsed_manual = parse_manual_unknown_rearm_episode_key(attempt.episode_key)
        if parsed_manual is not None:
            consumed_source_ids.add(parsed_manual[0])
        if attempt.episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX):
            source_id = attempt.episode_key.removeprefix(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
            if source_id:
                consumed_source_ids.add(source_id)
    return frozenset(
        attempt.id
        for attempt in materialized
        if attempt.outcome is ReservationOutcome.UNKNOWN
        and attempt.confirmation_outcome is not ReservationConfirmationOutcome.CONFIRMED_PAID
        and attempt.id not in consumed_source_ids
    )


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
        or attempt.episode_key.startswith(MANUAL_UNKNOWN_REARM_EPISODE_PREFIX)
        or attempt.episode_key.startswith(MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX)
    ):
        return False
    if attempt.outcome is ReservationOutcome.UNKNOWN:
        return (
            attempt.reconciliation_resolution
            is ReservationReconciliationResolution.CONFIRMED_ABSENT
            and (attempt.reconciliation_attempt_count or 0) >= 1
            and attempt.last_reconciled_at is not None
            and attempt.next_reconcile_at is None
        )
    return (
        attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        and attempt.payment_deadline is None
        and attempt.post_deadline_reconciled_at is None
    )


def automatic_reservation_retry_fence_reason(
    attempt: ConfirmedAbsentRetrySource,
) -> _AutomaticReservationRetryFenceReason | None:
    """Project a closed reason only when the one-shot recovery itself was exhausted."""

    episode_key = attempt.episode_key or ""
    source_attempt_id = (
        episode_key.removeprefix(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
        if episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
        else None
    )
    if (
        bool(source_attempt_id)
        and attempt.outcome is ReservationOutcome.UNKNOWN
        and attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
        and attempt.reconciliation_resolution
        is ReservationReconciliationResolution.CONFIRMED_ABSENT
    ):
        return _AutomaticReservationRetryFenceReason.CONFIRMED_ABSENT_RECOVERY_CONSUMED
    return None


def is_unresolved_unknown_manual_rearm_source(
    attempt: ConfirmedAbsentRetrySource,
) -> bool:
    """Return whether a user may authorize one bounded UNKNOWN retry."""

    if (
        attempt.outcome is not ReservationOutcome.UNKNOWN
        or attempt.confirmation_observed_at is None
        or attempt.last_reconciled_at is None
        or (attempt.reconciliation_attempt_count or 0) < UNKNOWN_MANUAL_REARM_MIN_RECONCILIATIONS
        or attempt.episode_key.startswith(CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX)
        or attempt.episode_key.startswith(MANUAL_UNKNOWN_REARM_EPISODE_PREFIX)
        or attempt.episode_key.startswith(MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX)
        or attempt.reconciliation_resolution is ReservationReconciliationResolution.CONFIRMED_ABSENT
    ):
        return False
    if attempt.confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE:
        return True
    return (
        attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
        and attempt.reconciliation_resolution
        is ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED
        and attempt.next_reconcile_at is None
    )
