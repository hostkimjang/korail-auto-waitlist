from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl, SecretStr

from ..domain import Provider, ReservationOutcome, SeatClass
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

RESERVATION_SOURCE = "korail-pydoll-reservation"
PAYMENT_HANDOFF_URL = AnyHttpUrl("https://www.korail.com/ticket/mypage/mykorail")
KOREA = ZoneInfo("Asia/Seoul")

TrainNumberNormalizer = Callable[[object], str]

__all__ = (
    "build_reservation_request",
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

    if result.outcome == "payment_required":
        return ReservationResult(
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
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
        outcome = ReservationOutcome.AUTH_REQUIRED
    elif result.outcome in {"consent_required", "action_required"}:
        # Reaching an official manual-intervention boundary does not prove that the
        # saved credentials or provider session are invalid.
        outcome = ReservationOutcome.UNKNOWN
    elif result.outcome == "provider_blocked":
        outcome = ReservationOutcome.PROVIDER_BLOCKED
    elif result.outcome == "unavailable":
        outcome = ReservationOutcome.NOT_AVAILABLE
    elif result.reservation_clicked:
        # A final click with no authoritative terminal state is never replayed.
        outcome = ReservationOutcome.UNKNOWN
    else:
        outcome = ReservationOutcome.FAILED
    return ReservationResult(
        outcome=outcome,
        source=RESERVATION_SOURCE,
        observed_at=observed_at,
        progress_stages=progress_stages,
    )
