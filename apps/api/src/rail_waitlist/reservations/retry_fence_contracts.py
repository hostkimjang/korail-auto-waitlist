from enum import StrEnum


class AutomaticReservationRetryFenceReason(StrEnum):
    """Closed reasons why another automatic reservation command is fenced."""

    CONFIRMED_ABSENT_RECOVERY_CONSUMED = "confirmed_absent_recovery_consumed"
