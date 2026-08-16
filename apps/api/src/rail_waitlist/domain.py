from __future__ import annotations

from enum import StrEnum


class Provider(StrEnum):
    KORAIL = "korail"
    SRT = "srt"
    MOCK = "mock"


class SeatClass(StrEnum):
    STANDARD = "standard"
    FIRST = "first"
    INFANT = "infant"
    FREE = "free"
    WAITLIST = "waitlist"
    ANY = "any"


class SeatObservationStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    LIMITED = "limited"
    STANDING_PLUS_SEAT = "standing_plus_seat"
    STANDING_ONLY = "standing_only"
    NOT_ENOUGH_SEATS = "not_enough_seats"
    SOLD_OUT = "sold_out"
    WAITLIST_AVAILABLE = "waitlist_available"
    RESERVATION_COMPLETED = "reservation_completed"
    NOT_OFFERED = "not_offered"
    DEPARTED = "departed"
    OUT_OF_SERVICE = "out_of_service"
    STALE = "stale"
    ERROR = "error"


class OperationalStatus(StrEnum):
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    BOARDING = "boarding"
    DEPARTED_ORIGIN = "departed_origin"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class BookingWindowStatus(StrEnum):
    OPEN = "open"
    WAITLIST = "waitlist"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class ReservationOutcome(StrEnum):
    PENDING = "pending"
    PAYMENT_REQUIRED = "payment_required"
    # A provider-side temporary hold, not payment completion.
    RESERVED = "reserved"
    NOT_AVAILABLE = "not_available"
    AUTH_REQUIRED = "auth_required"
    PROVIDER_BLOCKED = "provider_blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReservationResultReasonCode(StrEnum):
    """Provider-neutral reason for one persisted reservation result.

    Provider transport messages stay outside the durable/public contract.  These
    values describe only the evidence boundary that the application can safely
    present to users and operators.
    """

    RESERVATION_PENDING = "reservation_pending"
    PAYMENT_HOLD_CREATED = "payment_hold_created"
    TARGET_NOT_AVAILABLE = "target_not_available"
    TARGET_AMBIGUOUS = "target_ambiguous"
    SEAT_NOT_AVAILABLE = "seat_not_available"
    RESERVATION_CONTROL_UNAVAILABLE = "reservation_control_unavailable"
    SEAT_SELECTION_LOST = "seat_selection_lost"
    DELAY_CONSENT_REQUIRED = "delay_consent_required"
    EXISTING_RESERVATION_ACTION_REQUIRED = "existing_reservation_action_required"
    PROVIDER_NOTICE_ACTION_REQUIRED = "provider_notice_action_required"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROVIDER_BLOCKED = "provider_blocked"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_RESPONSE_INVALID = "provider_response_invalid"
    RESERVATION_REQUEST_RESULT_UNKNOWN = "reservation_request_result_unknown"
    RESERVATION_FAILED = "reservation_failed"


def reservation_result_reason_code_for_outcome(
    outcome: ReservationOutcome,
) -> ReservationResultReasonCode:
    """Return the conservative reason used when only a legacy outcome is known."""

    return {
        ReservationOutcome.PENDING: ReservationResultReasonCode.RESERVATION_PENDING,
        ReservationOutcome.PAYMENT_REQUIRED: ReservationResultReasonCode.PAYMENT_HOLD_CREATED,
        ReservationOutcome.RESERVED: ReservationResultReasonCode.PAYMENT_HOLD_CREATED,
        ReservationOutcome.NOT_AVAILABLE: ReservationResultReasonCode.TARGET_NOT_AVAILABLE,
        ReservationOutcome.AUTH_REQUIRED: ReservationResultReasonCode.AUTHENTICATION_REQUIRED,
        ReservationOutcome.PROVIDER_BLOCKED: ReservationResultReasonCode.PROVIDER_BLOCKED,
        ReservationOutcome.FAILED: ReservationResultReasonCode.RESERVATION_FAILED,
        ReservationOutcome.UNKNOWN: (
            ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
        ),
    }[outcome]


class ReservationPolicy(StrEnum):
    NOTIFY_ONLY = "notify_only"
    RESERVE_ONCE_BEFORE_PAYMENT = "reserve_once_before_payment"


class SeatObservationMode(StrEnum):
    BALANCED = "balanced"
    FOCUSED = "focused"


class ProviderCircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    # Stored for an explicit probe workflow; OPEN never auto-transitions here.
    HALF_OPEN = "half_open"
    MANUAL_HOLD = "manual_hold"


class WatchStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    WATCHING = "watching"
    OFFICIAL_WAITLIST = "official_waitlist"
    SEAT_FOUND = "seat_found"
    RESERVING = "reserving"
    PAYMENT_REQUIRED = "payment_required"
    COMPLETED = "completed"
    PAUSED = "paused"
    COOLDOWN = "cooldown"
    AUTH_REQUIRED = "auth_required"
    EXPIRED = "expired"
    FAILED = "failed"


class NotificationKind(StrEnum):
    WEB_PUSH = "web_push"
    ANDROID_FCM = "android_fcm"
    IOS_APNS = "ios_apns"
    TELEGRAM = "telegram"
    DISCORD_WEBHOOK = "discord_webhook"
    GENERIC_WEBHOOK = "generic_webhook"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


TERMINAL_STATUSES = {WatchStatus.COMPLETED, WatchStatus.EXPIRED, WatchStatus.FAILED}

ALLOWED_TRANSITIONS: dict[WatchStatus, set[WatchStatus]] = {
    WatchStatus.DRAFT: {WatchStatus.SCHEDULED, WatchStatus.EXPIRED},
    WatchStatus.SCHEDULED: {WatchStatus.WATCHING, WatchStatus.PAUSED, WatchStatus.EXPIRED},
    WatchStatus.WATCHING: {
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.COMPLETED,
        WatchStatus.PAUSED,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
        WatchStatus.EXPIRED,
        WatchStatus.FAILED,
    },
    WatchStatus.OFFICIAL_WAITLIST: {
        WatchStatus.WATCHING,
        WatchStatus.SEAT_FOUND,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.PAUSED,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
        WatchStatus.EXPIRED,
    },
    WatchStatus.SEAT_FOUND: {
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.RESERVING,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.PAUSED,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
        WatchStatus.EXPIRED,
    },
    WatchStatus.RESERVING: {
        WatchStatus.WATCHING,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.AUTH_REQUIRED,
        WatchStatus.COOLDOWN,
        WatchStatus.EXPIRED,
        WatchStatus.FAILED,
    },
    WatchStatus.PAYMENT_REQUIRED: {
        WatchStatus.WATCHING,
        WatchStatus.COMPLETED,
        WatchStatus.EXPIRED,
        WatchStatus.FAILED,
    },
    WatchStatus.PAUSED: {WatchStatus.SCHEDULED, WatchStatus.EXPIRED},
    WatchStatus.COOLDOWN: {WatchStatus.SCHEDULED, WatchStatus.PAUSED, WatchStatus.EXPIRED},
    WatchStatus.AUTH_REQUIRED: {WatchStatus.PAUSED, WatchStatus.SCHEDULED, WatchStatus.EXPIRED},
    WatchStatus.COMPLETED: set(),
    WatchStatus.EXPIRED: set(),
    WatchStatus.FAILED: set(),
}
