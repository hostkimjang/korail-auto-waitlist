from enum import StrEnum


class ManualReservationRearmReason(StrEnum):
    PAYMENT_HOLD_ENDED = "payment_hold_ended"
    UNKNOWN_RESULT_UNRESOLVED = "unknown_result_unresolved"
