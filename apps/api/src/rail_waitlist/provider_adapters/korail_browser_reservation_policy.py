from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl, SecretStr

from ..domain import Provider, ReservationOutcome, ReservationResultReasonCode, SeatClass
from ..korail_sidecar.contracts import (
    KorailCredentialRequest,
    KorailReserveOnceRequest,
    KorailReserveOnceResult,
)
from ..provider_account_management.contracts import ProviderCredentials
from ..reservations.contracts import (
    ReservationProgressStage,
    ReservationProgressStageName,
    ReservationRequest,
    ReservationResult,
    ReservedSeat,
)
from ..reservations.progress_timing_policy import normalize_reservation_terminal_time

RESERVATION_SOURCE = "korail-pydoll-reservation"
PAYMENT_HANDOFF_URL = AnyHttpUrl("https://www.korail.com/ticket/mypage/mykorail")
KOREA = ZoneInfo("Asia/Seoul")

TrainNumberNormalizer = Callable[[object], str]

__all__ = (
    "build_reservation_request",
    "normalize_reservation_terminal_time",
    "project_reservation_failure",
    "project_reservation_result",
)


def build_reservation_request(
    request: ReservationRequest,
    credentials: ProviderCredentials,
    *,
    enabled: bool,
    normalize_train_number: TrainNumberNormalizer,
) -> KorailReserveOnceRequest | None:
    """Build one exact sidecar command only for the supported reservation shape."""

    if (
        not enabled
        or request.provider != Provider.KORAIL
        or request.arrival_at is None
        or request.passenger_count != 1
        or request.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}
    ):
        return None
    departure = request.departure_at.astimezone(KOREA)
    arrival = request.arrival_at.astimezone(KOREA)
    return KorailReserveOnceRequest(
        origin=request.origin,
        destination=request.destination,
        travel_date=departure.date(),
        train_number=normalize_train_number(request.train_number),
        train_type=None,
        departure_time=departure.time().replace(tzinfo=None),
        arrival_time=arrival.time().replace(tzinfo=None),
        seat_class=("general" if request.seat_class == SeatClass.STANDARD else "special"),
        credential=KorailCredentialRequest(
            login_method=credentials.login_method,
            login_id=SecretStr(credentials.login_id),
            password=SecretStr(credentials.password),
            version=str(credentials.credential_version),
        ),
    )


def project_reservation_failure(
    observed_at: datetime,
    *,
    provider_blocked: bool = False,
) -> ReservationResult:
    return ReservationResult(
        outcome=(
            ReservationOutcome.PROVIDER_BLOCKED if provider_blocked else ReservationOutcome.FAILED
        ),
        result_reason_code=(
            ReservationResultReasonCode.PROVIDER_BLOCKED
            if provider_blocked
            else ReservationResultReasonCode.PROVIDER_UNAVAILABLE
        ),
        source=RESERVATION_SOURCE,
        observed_at=observed_at,
    )


def project_reservation_result(
    result: KorailReserveOnceResult,
    *,
    observed_at: datetime,
) -> ReservationResult:
    progress_values: tuple[tuple[ReservationProgressStageName, datetime | None], ...] = (
        ("authenticated_session_ready", result.session_ready_at),
        ("target_rechecked", result.target_rechecked_at),
        ("seat_selected", result.seat_selected_at),
        ("reservation_requested", result.reservation_requested_at),
    )
    progress_stages = tuple(
        ReservationProgressStage(stage=stage, occurred_at=occurred_at)
        for stage, occurred_at in progress_values
        if occurred_at is not None
    )
    observed_at = normalize_reservation_terminal_time(
        observed_at,
        (progress.occurred_at for progress in progress_stages),
    )
    provider_unavailable = (
        result.reason.startswith(("source_unavailable:", "browser_error:"))
        or result.reason == "reservation_backend_error"
    )

    post_request_command_may_have_been_issued = (
        result.reservation_clicked or result.reservation_requested_at is not None
    )
    if result.outcome == "payment_required":
        return ReservationResult(
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            result_reason_code=ReservationResultReasonCode.PAYMENT_HOLD_CREATED,
            source=RESERVATION_SOURCE,
            observed_at=observed_at,
            official_handoff_url=PAYMENT_HANDOFF_URL,
            progress_stages=progress_stages,
            reserved_seats=tuple(
                ReservedSeat(
                    car_number=seat.car_number,
                    seat_number=seat.seat_number,
                )
                for seat in result.reserved_seats
            ),
        )
    if result.outcome == "auth_required":
        if post_request_command_may_have_been_issued:
            outcome = ReservationOutcome.UNKNOWN
            reason_code = ReservationResultReasonCode.AUTHENTICATION_REQUIRED
        else:
            outcome = ReservationOutcome.AUTH_REQUIRED
            reason_code = ReservationResultReasonCode.AUTHENTICATION_REQUIRED
    elif result.outcome in {"consent_required", "action_required"}:
        # Reaching an official manual-intervention boundary does not prove that the
        # saved credentials or provider session are invalid.
        outcome = ReservationOutcome.UNKNOWN
        if result.reservation_clicked and provider_unavailable:
            reason_code = ReservationResultReasonCode.PROVIDER_UNAVAILABLE
        elif result.reservation_clicked or result.reason.endswith("_result_unknown"):
            reason_code = ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
        elif result.reason.startswith("delay_consent_"):
            reason_code = ReservationResultReasonCode.DELAY_CONSENT_REQUIRED
        elif result.reason == "existing_reservation_action_required":
            reason_code = ReservationResultReasonCode.EXISTING_RESERVATION_ACTION_REQUIRED
        else:
            reason_code = ReservationResultReasonCode.PROVIDER_NOTICE_ACTION_REQUIRED
    elif result.outcome == "provider_blocked":
        if post_request_command_may_have_been_issued:
            outcome = ReservationOutcome.UNKNOWN
            reason_code = ReservationResultReasonCode.PROVIDER_BLOCKED
        else:
            outcome = ReservationOutcome.PROVIDER_BLOCKED
            reason_code = ReservationResultReasonCode.PROVIDER_BLOCKED
    elif result.outcome == "unavailable":
        if post_request_command_may_have_been_issued:
            outcome = ReservationOutcome.UNKNOWN
            reason_code = ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
        else:
            outcome = (
                ReservationOutcome.UNKNOWN
                if result.reason == "target_not_unique"
                else ReservationOutcome.NOT_AVAILABLE
            )
            reason_code = {
                "target_not_unique": ReservationResultReasonCode.TARGET_AMBIGUOUS,
                "seat_not_available": ReservationResultReasonCode.SEAT_NOT_AVAILABLE,
                "seat_control_not_unique": (
                    ReservationResultReasonCode.RESERVATION_CONTROL_UNAVAILABLE
                ),
                "reservation_control_ambiguous": (
                    ReservationResultReasonCode.RESERVATION_CONTROL_UNAVAILABLE
                ),
                "reservation_control_disabled": (
                    ReservationResultReasonCode.RESERVATION_CONTROL_UNAVAILABLE
                ),
            }.get(result.reason, ReservationResultReasonCode.TARGET_NOT_AVAILABLE)
    elif (
        result.outcome == "failed"
        and result.reason == "reservation_result_unknown:reservation_click_error"
    ):
        # The browser control did not confirm that click dispatch completed. Keep the
        # command behind the UNKNOWN/manual-confirmation fence without claiming that
        # the provider request stage itself was completed.
        outcome = ReservationOutcome.UNKNOWN
        reason_code = ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
    elif result.reservation_clicked:
        # A final click with no authoritative terminal state is never replayed.
        outcome = ReservationOutcome.UNKNOWN
        reason_code = (
            ReservationResultReasonCode.PROVIDER_UNAVAILABLE
            if provider_unavailable
            else ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
        )
    else:
        outcome = ReservationOutcome.FAILED
        if provider_unavailable:
            reason_code = ReservationResultReasonCode.PROVIDER_UNAVAILABLE
        else:
            reason_code = {
                "reservation_selection_not_preserved": (
                    ReservationResultReasonCode.SEAT_SELECTION_LOST
                ),
                "reservation_control_timeout": (
                    ReservationResultReasonCode.RESERVATION_CONTROL_UNAVAILABLE
                ),
            }.get(result.reason, ReservationResultReasonCode.RESERVATION_FAILED)
    return ReservationResult(
        outcome=outcome,
        result_reason_code=reason_code,
        source=RESERVATION_SOURCE,
        observed_at=observed_at,
        progress_stages=progress_stages,
        confirmation_correlation_seats=(
            tuple(
                ReservedSeat(
                    car_number=seat.car_number,
                    seat_number=seat.seat_number,
                )
                for seat in result.confirmation_correlation_seats
            )
            if outcome is ReservationOutcome.UNKNOWN
            else ()
        ),
    )
