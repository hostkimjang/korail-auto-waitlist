from datetime import UTC, datetime

import pytest

from rail_waitlist.domain import ReservationOutcome, SeatObservationStatus
from rail_waitlist.models import ReservationAttempt
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.attempt_policy import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
    RESERVATION_RETRY_EDGE_OBSERVATIONS,
    is_confirmed_absent_retry_source,
)
from rail_waitlist.services import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX as LEGACY_CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
)
from rail_waitlist.services import (
    is_confirmed_absent_retry_source as legacy_is_confirmed_absent_retry_source,
)


def make_attempt(
    outcome: ReservationOutcome,
    *,
    episode_key: str = "availability:first",
    confirmation_outcome: ReservationConfirmationOutcome | None = (
        ReservationConfirmationOutcome.NOT_FOUND
    ),
    confirmation_observed_at: datetime | None = datetime(2026, 8, 5, tzinfo=UTC),
    payment_deadline: datetime | None = None,
    post_deadline_reconciled_at: datetime | None = None,
) -> ReservationAttempt:
    return ReservationAttempt(
        candidate_id="candidate-1",
        attempt_sequence=1,
        episode_key=episode_key,
        idempotency_key="attempt-1",
        outcome=outcome,
        confirmation_outcome=confirmation_outcome,
        confirmation_source=("official-list" if confirmation_outcome is not None else None),
        confirmation_observed_at=confirmation_observed_at,
        payment_deadline=payment_deadline,
        post_deadline_reconciled_at=post_deadline_reconciled_at,
    )


def test_services_reexports_the_canonical_confirmed_absent_policy() -> None:
    assert LEGACY_CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX == CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX
    assert legacy_is_confirmed_absent_retry_source is is_confirmed_absent_retry_source


@pytest.mark.parametrize(
    "outcome",
    [ReservationOutcome.UNKNOWN, ReservationOutcome.PAYMENT_REQUIRED],
)
def test_exact_confirmed_absence_rearms_supported_legacy_outcomes(
    outcome: ReservationOutcome,
) -> None:
    assert is_confirmed_absent_retry_source(make_attempt(outcome)) is True


@pytest.mark.parametrize(
    "attempt",
    [
        make_attempt(ReservationOutcome.NOT_AVAILABLE),
        make_attempt(
            ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        ),
        make_attempt(ReservationOutcome.UNKNOWN, confirmation_observed_at=None),
        make_attempt(
            ReservationOutcome.UNKNOWN,
            episode_key=f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}attempt-0",
        ),
        make_attempt(
            ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=datetime(2026, 8, 5, 1, tzinfo=UTC),
        ),
        make_attempt(
            ReservationOutcome.PAYMENT_REQUIRED,
            post_deadline_reconciled_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
        ),
    ],
)
def test_confirmed_absent_policy_fails_closed_outside_the_compatibility_path(
    attempt: ReservationAttempt,
) -> None:
    assert is_confirmed_absent_retry_source(attempt) is False


def test_retry_edge_statuses_are_only_conclusive_non_actionable_observations() -> None:
    assert RESERVATION_RETRY_EDGE_OBSERVATIONS == {
        SeatObservationStatus.UNAVAILABLE,
        SeatObservationStatus.NOT_ENOUGH_SEATS,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }
