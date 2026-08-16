from __future__ import annotations

import pytest

from rail_waitlist.domain import ReservationOutcome
from rail_waitlist.reservations.domain import (
    ReservationAttemptResultPolicy,
    reservation_attempt_manual_check_required,
    reservation_attempt_result_policy,
)
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
)
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
)
from rail_waitlist.services import (
    ReservationAttemptResultPolicy as LegacyReservationAttemptResultPolicy,
)
from rail_waitlist.services import (
    reservation_attempt_result_policy as legacy_reservation_attempt_result_policy,
)


@pytest.mark.parametrize(
    (
        "outcome",
        "expected_retryable",
        "expected_manual_check_required",
        "expected_retry_condition",
    ),
    [
        (ReservationOutcome.PENDING, False, False, None),
        (ReservationOutcome.PAYMENT_REQUIRED, False, False, None),
        (ReservationOutcome.RESERVED, False, False, None),
        (
            ReservationOutcome.NOT_AVAILABLE,
            True,
            False,
            "new_availability_episode",
        ),
        (
            ReservationOutcome.AUTH_REQUIRED,
            False,
            False,
            "provider_account_reverified",
        ),
        (
            ReservationOutcome.PROVIDER_BLOCKED,
            False,
            True,
            "provider_account_reverified",
        ),
        (ReservationOutcome.FAILED, False, False, None),
        (ReservationOutcome.UNKNOWN, False, True, None),
    ],
)
def test_reservation_attempt_result_policy_projects_each_outcome(
    outcome: ReservationOutcome,
    expected_retryable: bool,
    expected_manual_check_required: bool,
    expected_retry_condition: str | None,
) -> None:
    policy = reservation_attempt_result_policy(outcome)

    assert policy == ReservationAttemptResultPolicy(
        retryable=expected_retryable,
        manual_check_required=expected_manual_check_required,
        retry_condition=expected_retry_condition,
    )


@pytest.mark.parametrize(
    (
        "outcome",
        "confirmation_outcome",
        "reconciliation_resolution",
        "expected",
    ),
    [
        (ReservationOutcome.UNKNOWN, None, None, True),
        (ReservationOutcome.PROVIDER_BLOCKED, None, None, True),
        (ReservationOutcome.FAILED, None, None, False),
        (ReservationOutcome.NOT_AVAILABLE, None, None, False),
        (
            ReservationOutcome.UNKNOWN,
            ReservationConfirmationOutcome.CONFIRMED_PAID,
            None,
            False,
        ),
        (
            ReservationOutcome.UNKNOWN,
            ReservationConfirmationOutcome.NOT_FOUND,
            ReservationReconciliationResolution.CONFIRMED_ABSENT,
            False,
        ),
        (
            ReservationOutcome.UNKNOWN,
            ReservationConfirmationOutcome.INCONCLUSIVE,
            ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED,
            True,
        ),
    ],
    ids=(
        "unknown-unresolved",
        "provider-blocked",
        "predispatch-failed",
        "not-available",
        "confirmed-paid",
        "confirmed-absent",
        "exhausted-unresolved",
    ),
)
def test_manual_check_projection_closes_only_exact_resolutions(
    outcome: ReservationOutcome,
    confirmation_outcome: ReservationConfirmationOutcome | None,
    reconciliation_resolution: ReservationReconciliationResolution | None,
    expected: bool,
) -> None:
    assert (
        reservation_attempt_manual_check_required(
            outcome,
            confirmation_outcome=confirmation_outcome,
            reconciliation_resolution=reconciliation_resolution,
        )
        is expected
    )


def test_services_keeps_the_reservation_policy_compatibility_exports() -> None:
    assert LegacyReservationAttemptResultPolicy is ReservationAttemptResultPolicy
    assert legacy_reservation_attempt_result_policy is reservation_attempt_result_policy
