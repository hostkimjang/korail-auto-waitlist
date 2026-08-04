from __future__ import annotations

from dataclasses import dataclass

from ..domain import ReservationOutcome


@dataclass(frozen=True, slots=True)
class ReservationAttemptResultPolicy:
    retryable: bool
    manual_check_required: bool
    retry_condition: str | None


def reservation_attempt_result_policy(
    outcome: ReservationOutcome,
) -> ReservationAttemptResultPolicy:
    """Project the retry/manual-check contract shared by events and API reads."""
    return ReservationAttemptResultPolicy(
        retryable=outcome is ReservationOutcome.NOT_AVAILABLE,
        manual_check_required=outcome
        in {
            ReservationOutcome.UNKNOWN,
            ReservationOutcome.PROVIDER_BLOCKED,
            ReservationOutcome.FAILED,
        },
        retry_condition=(
            "new_availability_episode"
            if outcome is ReservationOutcome.NOT_AVAILABLE
            else (
                "provider_account_reverified"
                if outcome
                in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }
                else None
            )
        ),
    )
