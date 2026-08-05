from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rail_waitlist.domain import ReservationOutcome
from rail_waitlist.models import ReservationAttempt
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.payment_hold_application import (
    _utc_instant,
    is_payment_hold_ended,
    payment_hold_end_reason,
)
from rail_waitlist.services import _utc_instant as compatibility_utc_instant
from rail_waitlist.services import (
    is_payment_hold_ended as compatibility_is_payment_hold_ended,
)
from rail_waitlist.services import (
    payment_hold_end_reason as compatibility_payment_hold_end_reason,
)


def make_attempt(
    *,
    outcome: ReservationOutcome = ReservationOutcome.PAYMENT_REQUIRED,
    confirmation_outcome: ReservationConfirmationOutcome | None = None,
    payment_deadline: datetime | None = None,
    post_deadline_reconciled_at: datetime | None = None,
) -> ReservationAttempt:
    return ReservationAttempt(
        candidate_id="candidate-1",
        idempotency_key="payment-hold-policy-1",
        outcome=outcome,
        confirmation_outcome=confirmation_outcome,
        payment_deadline=payment_deadline,
        post_deadline_reconciled_at=post_deadline_reconciled_at,
    )


@pytest.mark.parametrize(
    (
        "attempt",
        "expected_reason",
    ),
    [
        (
            make_attempt(
                outcome=ReservationOutcome.RESERVED,
                confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
                post_deadline_reconciled_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
            ),
            None,
        ),
        (
            make_attempt(
                confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            ),
            None,
        ),
        (
            make_attempt(
                confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
                post_deadline_reconciled_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
            ),
            "confirmed_payment_hold_no_longer_present",
        ),
        (
            make_attempt(
                confirmation_outcome=(ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
                payment_deadline=datetime(2026, 8, 5, 1, tzinfo=UTC),
                post_deadline_reconciled_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
            ),
            "confirmed_payment_deadline_elapsed",
        ),
        (
            make_attempt(
                confirmation_outcome=(ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
                payment_deadline=datetime(2026, 8, 5, 1, tzinfo=UTC) + timedelta(seconds=1),
                post_deadline_reconciled_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
            ),
            None,
        ),
        (
            make_attempt(
                confirmation_outcome=None,
                payment_deadline=datetime(2026, 8, 5, 1, tzinfo=UTC),
                post_deadline_reconciled_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
            ),
            None,
        ),
    ],
)
def test_payment_hold_end_reason_matrix(
    attempt: ReservationAttempt,
    expected_reason: str | None,
) -> None:
    assert payment_hold_end_reason(attempt) == expected_reason
    assert is_payment_hold_ended(attempt) is (expected_reason is not None)


def test_naive_and_aware_deadlines_are_compared_as_the_same_utc_instant() -> None:
    naive = datetime(2026, 8, 5, 1)
    aware = datetime(2026, 8, 5, 1, tzinfo=UTC)
    naive_attempt = make_attempt(
        confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        payment_deadline=naive,
        post_deadline_reconciled_at=aware,
    )
    aware_attempt = make_attempt(
        confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        payment_deadline=aware,
        post_deadline_reconciled_at=aware,
    )

    assert payment_hold_end_reason(naive_attempt) == "confirmed_payment_deadline_elapsed"
    assert payment_hold_end_reason(aware_attempt) == "confirmed_payment_deadline_elapsed"
    assert _utc_instant(naive) == _utc_instant(aware)


def test_services_keeps_payment_hold_compatibility_identities() -> None:
    assert compatibility_utc_instant is _utc_instant
    assert compatibility_payment_hold_end_reason is payment_hold_end_reason
    assert compatibility_is_payment_hold_ended is is_payment_hold_ended
