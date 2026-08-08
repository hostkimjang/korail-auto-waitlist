"""KORAIL sidecar confirmation transport-to-domain normalization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import ValidationError

from ...domain import Provider
from ...korail_sidecar.client import _AdapterFailure
from ...korail_sidecar.contracts import (
    KorailReservationConfirmationRequest,
    KorailReservationConfirmationResult,
)
from .contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)

ConfirmationCall = Callable[
    [KorailReservationConfirmationRequest],
    Awaitable[KorailReservationConfirmationResult],
]
TrainNumberNormalizer = Callable[[object], str]
Clock = Callable[[], datetime]

_FALLBACK_SOURCE = "korail-same-session-detail"


def _inconclusive(now: Clock) -> ReservationConfirmationResult:
    return ReservationConfirmationResult(
        provider=Provider.KORAIL,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source=_FALLBACK_SOURCE,
        observed_at=now(),
    )


async def confirm_korail_sidecar_reservation(
    *,
    enabled: bool,
    target: ReservationConfirmationTarget,
    confirm: ConfirmationCall,
    normalize_train_number: TrainNumberNormalizer,
    now: Clock,
    adapter_failure_type: type[_AdapterFailure],
) -> ReservationConfirmationResult:
    """Read and normalize one same-session confirmation without retrying or mutating."""

    if not enabled or target.provider is not Provider.KORAIL:
        return _inconclusive(now)
    try:
        result = await confirm(
            KorailReservationConfirmationRequest.model_validate(
                {
                    "attempt_id": target.attempt_id,
                    "candidate_id": target.candidate_id,
                    "train_number": normalize_train_number(target.train_number),
                    "origin": target.origin,
                    "destination": target.destination,
                    "departure_at": target.departure_at,
                    "arrival_at": target.arrival_at,
                    "seat_class": target.seat_class.value,
                    "passenger_count": target.passenger_count,
                    "credential_version": target.credential_version,
                }
            )
        )
    except adapter_failure_type as error:
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=(
                ReservationConfirmationOutcome.PROVIDER_BLOCKED
                if error.protection or error.rate_limited
                else ReservationConfirmationOutcome.INCONCLUSIVE
            ),
            source=_FALLBACK_SOURCE,
            observed_at=now(),
        )
    except (ValueError, ValidationError):
        return _inconclusive(now)
    try:
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome(result.outcome),
            source=result.source,
            observed_at=result.observed_at,
            payment_deadline=result.payment_deadline,
            official_handoff_url=result.official_handoff_url,
        )
    except ValueError:
        return _inconclusive(now)
