from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from rail_waitlist.domain import ReservationOutcome, SeatObservationStatus
from rail_waitlist.models import ReservationAttempt
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.attempt_policy import (
    CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX,
    CONFIRMED_ABSENT_RETRY_OBSERVATIONS,
    MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX,
    MANUAL_UNKNOWN_REARM_EPISODE_PREFIX,
    PAYMENT_HOLD_RETRY_EPISODE_PREFIX,
    RESERVATION_RETRY_EDGE_OBSERVATIONS,
    is_confirmed_absent_retry_source,
    is_unresolved_unknown_manual_rearm_source,
    manual_unknown_rearm_episode_key,
    parse_manual_unknown_rearm_episode_key,
)
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
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
    last_reconciled_at: datetime | None = None,
    next_reconcile_at: datetime | None = None,
    payment_deadline: datetime | None = None,
    post_deadline_reconciled_at: datetime | None = None,
    reconciliation_attempt_count: int = 0,
    reconciliation_resolution: ReservationReconciliationResolution | None = None,
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
        last_reconciled_at=last_reconciled_at,
        next_reconcile_at=next_reconcile_at,
        payment_deadline=payment_deadline,
        post_deadline_reconciled_at=post_deadline_reconciled_at,
        reconciliation_attempt_count=reconciliation_attempt_count,
        reconciliation_resolution=reconciliation_resolution,
    )


def test_services_reexports_the_canonical_confirmed_absent_policy() -> None:
    assert LEGACY_CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX == CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX
    assert legacy_is_confirmed_absent_retry_source is is_confirmed_absent_retry_source


def test_exact_confirmed_absence_rearms_only_legacy_payment_hold_without_deadline() -> None:
    assert is_confirmed_absent_retry_source(make_attempt(ReservationOutcome.PAYMENT_REQUIRED))


def test_reconciled_unknown_exact_absence_rearms_only_a_non_retry_episode() -> None:
    reconciled_at = datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC)
    assert is_confirmed_absent_retry_source(
        make_attempt(
            ReservationOutcome.UNKNOWN,
            last_reconciled_at=reconciled_at,
            reconciliation_attempt_count=1,
            reconciliation_resolution=(ReservationReconciliationResolution.CONFIRMED_ABSENT),
        )
    )
    assert not is_confirmed_absent_retry_source(
        make_attempt(
            ReservationOutcome.UNKNOWN,
            episode_key=f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}attempt-0",
            last_reconciled_at=reconciled_at,
            reconciliation_attempt_count=1,
            reconciliation_resolution=(ReservationReconciliationResolution.CONFIRMED_ABSENT),
        )
    )


@pytest.mark.parametrize(
    "attempt",
    [
        make_attempt(ReservationOutcome.NOT_AVAILABLE),
        make_attempt(ReservationOutcome.UNKNOWN),
        make_attempt(
            ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            last_reconciled_at=datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC),
            reconciliation_attempt_count=1,
        ),
        make_attempt(
            ReservationOutcome.UNKNOWN,
            confirmation_observed_at=None,
            last_reconciled_at=datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC),
            reconciliation_attempt_count=1,
        ),
        make_attempt(
            ReservationOutcome.UNKNOWN,
            last_reconciled_at=datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC),
        ),
        make_attempt(ReservationOutcome.UNKNOWN, reconciliation_attempt_count=1),
        make_attempt(
            ReservationOutcome.UNKNOWN,
            last_reconciled_at=datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC),
            next_reconcile_at=datetime(2026, 8, 5, 0, 0, 31, tzinfo=UTC),
            reconciliation_attempt_count=1,
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
        SeatObservationStatus.STANDING_ONLY,
        SeatObservationStatus.SOLD_OUT,
        SeatObservationStatus.NOT_OFFERED,
        SeatObservationStatus.DEPARTED,
        SeatObservationStatus.OUT_OF_SERVICE,
    }


def test_confirmed_absent_unknown_requires_fresh_bookable_seat_inventory() -> None:
    assert CONFIRMED_ABSENT_RETRY_OBSERVATIONS == {
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
    }


def test_unresolved_unknown_manual_rearm_opens_at_three_inconclusive_reads() -> None:
    reconciled_at = datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC)

    assert is_unresolved_unknown_manual_rearm_source(
        make_attempt(
            ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            last_reconciled_at=reconciled_at,
            next_reconcile_at=datetime(2026, 8, 5, 0, 5, 1, tzinfo=UTC),
            reconciliation_attempt_count=3,
        )
    )
    assert not is_unresolved_unknown_manual_rearm_source(
        make_attempt(
            ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            last_reconciled_at=reconciled_at,
            reconciliation_attempt_count=2,
        )
    )


def test_exhausted_final_not_found_is_manual_only_and_confirmed_absence_is_not() -> None:
    reconciled_at = datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC)
    exhausted = make_attempt(
        ReservationOutcome.UNKNOWN,
        last_reconciled_at=reconciled_at,
        reconciliation_attempt_count=6,
        reconciliation_resolution=ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED,
    )
    confirmed_absent = make_attempt(
        ReservationOutcome.UNKNOWN,
        last_reconciled_at=reconciled_at,
        reconciliation_attempt_count=2,
        reconciliation_resolution=ReservationReconciliationResolution.CONFIRMED_ABSENT,
    )

    assert is_unresolved_unknown_manual_rearm_source(exhausted)
    assert not is_unresolved_unknown_manual_rearm_source(confirmed_absent)
    assert is_confirmed_absent_retry_source(confirmed_absent)


def test_manual_unknown_episode_key_is_exact_and_nonrecursive() -> None:
    key = manual_unknown_rearm_episode_key("attempt-1", "candidate-1", "observation-1")

    assert key == "manual-unknown:attempt-1:candidate-1:observation-1"
    assert parse_manual_unknown_rearm_episode_key(key) == (
        "attempt-1",
        "candidate-1",
        "observation-1",
    )
    assert parse_manual_unknown_rearm_episode_key("manual-unknown:missing") is None
    assert not is_unresolved_unknown_manual_rearm_source(
        make_attempt(
            ReservationOutcome.UNKNOWN,
            episode_key=key,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            last_reconciled_at=datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC),
            reconciliation_attempt_count=3,
        )
    )


@pytest.mark.parametrize(
    ("episode_key", "expected"),
    [
        ("availability:first", True),
        ("availability-after:unavailable-observation", True),
        ("auth:1:authenticated-generation", True),
        (f"{PAYMENT_HOLD_RETRY_EPISODE_PREFIX}hold-attempt:unavailable-observation", True),
        (f"{CONFIRMED_ABSENT_RETRY_EPISODE_PREFIX}source-attempt", False),
        (
            f"{MANUAL_UNKNOWN_REARM_EPISODE_PREFIX}source-attempt:candidate:observation",
            False,
        ),
        (
            f"{MANUAL_PAYMENT_HOLD_REARM_EPISODE_PREFIX}source-attempt:candidate:observation",
            False,
        ),
    ],
)
@pytest.mark.parametrize(
    "policy",
    [is_confirmed_absent_retry_source, is_unresolved_unknown_manual_rearm_source],
)
def test_unknown_recovery_episode_prefix_matrix_is_nonrecursive(
    episode_key: str,
    expected: bool,
    policy: Callable[[ReservationAttempt], bool],
) -> None:
    reconciled_at = datetime(2026, 8, 5, 0, 0, 1, tzinfo=UTC)
    if policy is is_confirmed_absent_retry_source:
        attempt = make_attempt(
            ReservationOutcome.UNKNOWN,
            episode_key=episode_key,
            last_reconciled_at=reconciled_at,
            reconciliation_attempt_count=2,
            reconciliation_resolution=ReservationReconciliationResolution.CONFIRMED_ABSENT,
        )
    else:
        attempt = make_attempt(
            ReservationOutcome.UNKNOWN,
            episode_key=episode_key,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            last_reconciled_at=reconciled_at,
            reconciliation_attempt_count=3,
        )

    assert policy(attempt) is expected
