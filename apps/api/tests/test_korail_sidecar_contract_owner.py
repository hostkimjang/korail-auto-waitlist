from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

import rail_waitlist.korail_reservation_contract as legacy
from rail_waitlist.korail_sidecar import contracts as canonical

API_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SYMBOLS = {
    "KorailConfirmationPurposeValue",
    "KorailCredentialRequest",
    "KorailLoginMethodValue",
    "KorailLoginVerificationOutcomeValue",
    "KorailLoginVerifyRequest",
    "KorailLoginVerifyResult",
    "KorailReservationConfirmationRequest",
    "KorailReservationConfirmationResult",
    "KorailReservationOutcomeValue",
    "KorailReservationSeatClassValue",
    "KorailReservedSeat",
    "KorailReserveOnceRequest",
    "KorailReserveOnceResult",
    "KorailSessionActorStateValue",
    "KorailSessionStateResult",
}
MODEL_SYMBOLS = {
    "KorailCredentialRequest",
    "KorailLoginVerifyRequest",
    "KorailLoginVerifyResult",
    "KorailSessionStateResult",
    "KorailReservationConfirmationRequest",
    "KorailReservationConfirmationResult",
    "KorailReservedSeat",
    "KorailReserveOnceRequest",
    "KorailReserveOnceResult",
}


def credential() -> canonical.KorailCredentialRequest:
    return canonical.KorailCredentialRequest(
        login_method="membership_number",
        login_id="membership-secret",
        password="password-secret",
        version="credential:7",
    )


def confirmation_request() -> canonical.KorailReservationConfirmationRequest:
    departure = datetime(2026, 8, 7, 12, tzinfo=UTC)
    return canonical.KorailReservationConfirmationRequest(
        attempt_id="attempt-1",
        candidate_id="candidate-1",
        train_number="43",
        origin="서울",
        destination="부산",
        departure_at=departure,
        arrival_at=departure + timedelta(hours=2),
        seat_class="standard",
        passenger_count=1,
        credential_version=7,
    )


def test_legacy_contract_symbols_are_exact_canonical_aliases() -> None:
    for symbol in PUBLIC_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)
    for symbol in MODEL_SYMBOLS:
        assert getattr(canonical, symbol).__module__ == "rail_waitlist.korail_sidecar.contracts"
    assert legacy._InternalModel is canonical._InternalModel


def test_facade_preserves_pre_move_dependency_attributes() -> None:
    assert legacy.date is date
    assert legacy.datetime is datetime
    assert legacy.clock_time is time
    assert legacy.BaseModel is BaseModel
    assert legacy.SecretStr is SecretStr
    assert {name for name in vars(legacy) if not name.startswith("_")} == {
        *PUBLIC_SYMBOLS,
        "BaseModel",
        "ConfigDict",
        "Field",
        "Literal",
        "SecretStr",
        "clock_time",
        "date",
        "datetime",
        "field_validator",
        "model_validator",
    }


def test_contract_models_forbid_unknown_fields_and_redact_credentials() -> None:
    value = credential()
    assert value.login_id.get_secret_value() == "membership-secret"
    assert value.password.get_secret_value() == "password-secret"
    rendered = f"{value!r}\n{value.model_dump_json()}"
    assert "membership-secret" not in rendered
    assert "password-secret" not in rendered
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        canonical.KorailCredentialRequest.model_validate(
            {
                "login_id": "id",
                "password": "password",
                "version": "v1",
                "unexpected": True,
            }
        )


def test_confirmation_request_preserves_time_route_and_arrival_validation() -> None:
    valid = confirmation_request()
    assert valid.departure_at.tzinfo is UTC
    assert valid.purpose == "initial"
    assert valid.reserved_seats == []
    assert valid.confirmation_correlation_seats == []
    payload = valid.model_dump()
    payload["departure_at"] = valid.departure_at.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="reservation times must include a timezone"):
        canonical.KorailReservationConfirmationRequest.model_validate(payload)
    with pytest.raises(ValidationError, match="origin and destination must differ"):
        canonical.KorailReservationConfirmationRequest.model_validate(
            {**valid.model_dump(), "destination": " 서울 "}
        )
    with pytest.raises(ValidationError, match="arrival_at must be later"):
        canonical.KorailReservationConfirmationRequest.model_validate(
            {**valid.model_dump(), "arrival_at": valid.departure_at}
        )
    follow_up = canonical.KorailReservationConfirmationRequest.model_validate(
        {
            **valid.model_dump(),
            "purpose": "payment_follow_up",
            "reserved_seats": [{"car_number": "4", "seat_number": "8A"}],
        }
    )
    assert follow_up.reserved_seats[0].seat_number == "8A"
    with pytest.raises(ValidationError, match="initial confirmation cannot carry seat identity"):
        canonical.KorailReservationConfirmationRequest.model_validate(
            {
                **valid.model_dump(),
                "reserved_seats": [{"car_number": "4", "seat_number": "8A"}],
            }
        )
    empty_unknown_follow_up = canonical.KorailReservationConfirmationRequest.model_validate(
        {
            **valid.model_dump(),
            "purpose": "unknown_result_follow_up",
        }
    )
    assert empty_unknown_follow_up.reserved_seats == []
    assert empty_unknown_follow_up.confirmation_correlation_seats == []
    correlated_unknown_follow_up = canonical.KorailReservationConfirmationRequest.model_validate(
        {
            **valid.model_dump(),
            "purpose": "unknown_result_follow_up",
            "confirmation_correlation_seats": [{"car_number": "4", "seat_number": "8A"}],
        }
    )
    assert correlated_unknown_follow_up.reserved_seats == []
    assert correlated_unknown_follow_up.confirmation_correlation_seats[0].seat_number == "8A"


def test_confirmation_result_preserves_handoff_and_deadline_shape() -> None:
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    confirmed = canonical.KorailReservationConfirmationResult(
        outcome="confirmed_payment_required",
        source="korail-same-session-detail",
        observed_at=now,
        payment_deadline=now + timedelta(minutes=10),
        official_handoff_url="opaque-existing-handoff",
    )
    assert confirmed.official_handoff_url == "opaque-existing-handoff"
    paid = canonical.KorailReservationConfirmationResult(
        outcome="confirmed_paid",
        source="korail-issued-ticket-list",
        observed_at=now,
    )
    assert paid.payment_deadline is None
    assert paid.official_handoff_url is None
    inconclusive = canonical.KorailReservationConfirmationResult(
        outcome="inconclusive",
        diagnostic_code="official_record_ambiguous",
        source="korail-reservation-list",
        observed_at=now,
    )
    assert inconclusive.model_dump(mode="json")["diagnostic_code"] == ("official_record_ambiguous")
    with pytest.raises(ValidationError, match="requires a diagnostic code"):
        canonical.KorailReservationConfirmationResult(
            outcome="inconclusive",
            source="korail-reservation-list",
            observed_at=now,
        )
    with pytest.raises(ValidationError, match="requires a diagnostic code"):
        canonical.KorailReservationConfirmationResult(
            outcome="not_found",
            diagnostic_code="official_evidence_insufficient",
            source="korail-reservation-list",
            observed_at=now,
        )
    with pytest.raises(ValidationError, match="confirmed paid requires issued-ticket"):
        canonical.KorailReservationConfirmationResult(
            outcome="confirmed_paid",
            source="korail-reservation-list",
            observed_at=now,
        )
    with pytest.raises(ValidationError, match="only confirmed payment holds"):
        canonical.KorailReservationConfirmationResult(
            outcome="confirmed_payment_required",
            source="korail-same-session-detail",
            observed_at=now,
        )
    with pytest.raises(ValidationError, match="only confirmed payment holds"):
        canonical.KorailReservationConfirmationResult(
            outcome="not_found",
            source="korail-reservation-list",
            observed_at=now,
            payment_deadline=now + timedelta(minutes=10),
        )


def test_reserve_once_request_keeps_normalization_and_route_time_rules() -> None:
    request = canonical.KorailReserveOnceRequest(
        origin="  서울 역 ",
        destination=" 부산역 ",
        travel_date=date(2026, 8, 7),
        train_number="43",
        train_type=" KTX  산천 ",
        departure_time=time(12),
        arrival_time=time(14),
        seat_class="general",
        credential=credential(),
    )
    assert (request.origin, request.destination, request.train_type) == (
        "서울 ",
        "부산",
        "KTX 산천",
    )
    with pytest.raises(ValidationError, match="origin and destination must differ"):
        canonical.KorailReserveOnceRequest.model_validate(
            {**request.model_dump(), "origin": "서울역", "destination": "서울역"}
        )
    with pytest.raises(ValidationError, match="departure_time and arrival_time must differ"):
        canonical.KorailReserveOnceRequest.model_validate(
            {**request.model_dump(), "arrival_time": request.departure_time}
        )


def test_reserve_result_preserves_progress_evidence_contract() -> None:
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    result = canonical.KorailReserveOnceResult(
        outcome="payment_required",
        reason="payment_required",
        seat_clicked=True,
        reservation_clicked=True,
        session_ready_at=now,
        target_rechecked_at=now + timedelta(seconds=1),
        seat_selected_at=now + timedelta(seconds=2),
        reservation_requested_at=now + timedelta(seconds=3),
        reserved_seats=[{"car_number": "4", "seat_number": "8A"}],
    )
    assert result.reservation_requested_at == now + timedelta(seconds=3)
    assert result.reserved_seats[0].seat_number == "8A"
    with pytest.raises(ValidationError, match="seat_selected_at requires seat_clicked"):
        canonical.KorailReserveOnceResult.model_validate(
            {**result.model_dump(), "seat_clicked": False}
        )
    with pytest.raises(ValidationError, match="reservation progress times must be chronological"):
        canonical.KorailReserveOnceResult.model_validate(
            {
                **result.model_dump(),
                "target_rechecked_at": now + timedelta(seconds=4),
            }
        )
    with pytest.raises(ValidationError):
        canonical.KorailReserveOnceResult.model_validate(
            {
                **result.model_dump(),
                "reserved_seats": [
                    {"car_number": "4", "seat_number": "8A"},
                    {"car_number": "5", "seat_number": "9B"},
                ],
            }
        )


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "legacy-first", "seat-source-first", "http-first", "login-first"],
)
def test_contract_import_orders_preserve_one_canonical_identity(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.korail_sidecar import contracts as canonical
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import korail_reservation_contract as legacy
elif sys.argv[1] == "seat-source-first":
    from rail_waitlist import korail_browser_seat_source
elif sys.argv[1] == "http-first":
    from rail_waitlist.korail_sidecar import http
else:
    from rail_waitlist import provider_login_verification

from rail_waitlist import korail_reservation_contract as legacy
from rail_waitlist.korail_sidecar import contracts as canonical

symbols = {
    "KorailCredentialRequest",
    "KorailLoginMethodValue",
    "KorailLoginVerificationOutcomeValue",
    "KorailLoginVerifyRequest",
    "KorailLoginVerifyResult",
    "KorailReservationConfirmationRequest",
    "KorailReservationConfirmationResult",
    "KorailReservationOutcomeValue",
    "KorailReservationSeatClassValue",
    "KorailReservedSeat",
    "KorailReserveOnceRequest",
    "KorailReserveOnceResult",
    "KorailSessionActorStateValue",
    "KorailSessionStateResult",
}
print(json.dumps({
    "identity": all(getattr(legacy, name) is getattr(canonical, name) for name in symbols),
    "modules": sorted({
        getattr(canonical, name).__module__
        for name in symbols
        if isinstance(getattr(canonical, name), type)
    }),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "identity": True,
        "modules": ["rail_waitlist.korail_sidecar.contracts"],
    }
