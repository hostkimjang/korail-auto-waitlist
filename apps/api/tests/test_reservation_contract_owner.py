from __future__ import annotations

import base64
import hashlib
import json
import pickle
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy
from rail_waitlist.domain import Provider, ReservationResultReasonCode, SeatClass
from rail_waitlist.observations.contracts import SeatObservationRequest
from rail_waitlist.provider_registry import official_url_policy
from rail_waitlist.reservations import contracts as canonical

API_ROOT = Path(__file__).resolve().parents[1]
LEGACY_REQUEST_PICKLE = (
    "gASVpgIAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMElJlc2VydmF0aW9uUmVx"
    "dWVzdJSTlCmBlH2UKIwIX19kaWN0X1+UfZQojAhwcm92aWRlcpSMFHJhaWxfd2FpdGxp"
    "c3QuZG9tYWlulIwIUHJvdmlkZXKUk5SMBG1vY2uUhZRSlIwOb3JpZ2luX25vZGVfaWSU"
    "jAxNT0NLLURBRUpFT06UjBNkZXN0aW5hdGlvbl9ub2RlX2lklIwKTU9DSy1TRU9VTJSM"
    "Bm9yaWdpbpSMB0RhZWplb26UjAtkZXN0aW5hdGlvbpSMBVNlb3VslIwMdHJhaW5fbnVt"
    "YmVylIwITU9DSy0xMTiUjAxkZXBhcnR1cmVfYXSUjAhkYXRldGltZZSMCGRhdGV0aW1l"
    "lJOUQwoH6ggHBiMAAAAAlIwccHlkYW50aWNfY29yZS5fcHlkYW50aWNfY29yZZSMBlR6"
    "SW5mb5STlE2QfoWUUpSGlFKUjApzZWF0X2NsYXNzlGgIjAlTZWF0Q2xhc3OUk5SMCHN0"
    "YW5kYXJklIWUUpSMD3Bhc3Nlbmdlcl9jb3VudJRLAYwMY2FuZGlkYXRlX2lklIwQY2Fu"
    "ZGlkYXRlLWxlZ2FjeZSMD2lkZW1wb3RlbmN5X2tleZSMEGxlZ2FjeS1hdHRlbXB0LTGU"
    "jBtleHBlY3RlZF9jcmVkZW50aWFsX3ZlcnNpb26USwOMCmFycml2YWxfYXSUaBtDCgfq"
    "CAcHMQAAAACUaB9NkH6FlFKUhpRSlHWMEl9fcHlkYW50aWNfZXh0cmFfX5ROjBdfX3B5"
    "ZGFudGljX2ZpZWxkc19zZXRfX5SPlChoL2gwaBJoFmgQaC1oFGgkaCpoB2graBhoDpCM"
    "FF9fcHlkYW50aWNfcHJpdmF0ZV9flE51Yi4="
)
LEGACY_STAGE_PICKLE = (
    "gASVHwEAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMGFJlc2VydmF0aW9uUHJv"
    "Z3Jlc3NTdGFnZZSTlCmBlH2UKIwIX19kaWN0X1+UfZQojAVzdGFnZZSMEHRhcmdldF9y"
    "ZWNoZWNrZWSUjAtvY2N1cnJlZF9hdJSMCGRhdGV0aW1llIwIZGF0ZXRpbWWUk5RDCgfq"
    "CAYVJAAAAACUjBxweWRhbnRpY19jb3JlLl9weWRhbnRpY19jb3JllIwGVHpJbmZvlJOU"
    "SwCFlFKUhpRSlHWMEl9fcHlkYW50aWNfZXh0cmFfX5ROjBdfX3B5ZGFudGljX2ZpZWxk"
    "c19zZXRfX5SPlChoB2gJkIwUX19weWRhbnRpY19wcml2YXRlX1+UTnViLg=="
)
LEGACY_RESULT_PICKLE = (
    "gASV2gIAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMEVJlc2VydmF0aW9uUmVz"
    "dWx0lJOUKYGUfZQojAhfX2RpY3RfX5R9lCiMB291dGNvbWWUjBRyYWlsX3dhaXRsaXN0"
    "LmRvbWFpbpSMElJlc2VydmF0aW9uT3V0Y29tZZSTlIwQcGF5bWVudF9yZXF1aXJlZJSF"
    "lFKUjAZzb3VyY2WUjARtb2NrlIwLb2JzZXJ2ZWRfYXSUjAhkYXRldGltZZSMCGRhdGV0"
    "aW1llJOUQwoH6ggGFSUAAAAAlIwccHlkYW50aWNfY29yZS5fcHlkYW50aWNfY29yZZSM"
    "BlR6SW5mb5STlEsAhZRSlIaUUpSMEmNyZWRlbnRpYWxfdmVyc2lvbpRLA4wQcGF5bWVu"
    "dF9kZWFkbGluZZRoE0MKB+oIBhYAAAAAAJRoF0sAhZRSlIaUUpSMFG9mZmljaWFsX2hh"
    "bmRvZmZfdXJslIwRcHlkYW50aWMubmV0d29ya3OUjApBbnlIdHRwVXJslJOUKYGUfZSM"
    "BF91cmyUjBxweWRhbnRpY19jb3JlLl9weWRhbnRpY19jb3JllIwDVXJslJOUjCRodHRw"
    "czovL2V4YW1wbGUuaW52YWxpZC9tb2NrLWJvb2tpbmeUhZSBlHNijA9wcm9ncmVzc19z"
    "dGFnZXOUaACMGFJlc2VydmF0aW9uUHJvZ3Jlc3NTdGFnZZSTlCmBlH2UKGgFfZQojAVz"
    "dGFnZZSMEHRhcmdldF9yZWNoZWNrZWSUjAtvY2N1cnJlZF9hdJRoE0MKB+oIBhUkAAAA"
    "AJRoF0sAhZRSlIaUUpR1jBJfX3B5ZGFudGljX2V4dHJhX1+UTowXX19weWRhbnRpY19m"
    "aWVsZHNfc2V0X1+Uj5QoaDZoOJCMFF9fcHlkYW50aWNfcHJpdmF0ZV9flE51YoWUdWg+"
    "Tmg/j5QoaBBoHGgOaCNoHWgwaAeQaEFOdWIu"
)


def request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": Provider.MOCK,
        "origin_node_id": "MOCK-DAEJEON",
        "destination_node_id": "MOCK-SEOUL",
        "origin": "Daejeon",
        "destination": "Seoul",
        "train_number": "MOCK-118",
        "departure_at": "2026-08-07T06:35:00+09:00",
        "seat_class": SeatClass.STANDARD,
        "passenger_count": 1,
        "candidate_id": " candidate-1 ",
        "idempotency_key": " reservation-attempt-1 ",
        "expected_credential_version": 3,
        "arrival_at": "2026-08-07T07:49:00+09:00",
    }
    payload.update(overrides)
    return payload


def result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": "payment_required",
        "source": " mock ",
        "observed_at": "2026-08-06T21:37:00Z",
        "credential_version": 3,
        "payment_deadline": "2026-08-06T22:00:00Z",
        "official_handoff_url": "https://example.invalid/mock-booking",
        "progress_stages": (
            {
                "stage": "target_rechecked",
                "occurred_at": "2026-08-06T21:36:00Z",
            },
        ),
    }
    payload.update(overrides)
    return payload


def test_central_schema_hub_preserves_exact_reservation_contract_aliases() -> None:
    assert legacy.ReservationRequest is canonical.ReservationRequest
    assert legacy.ReservationProgressStageName is canonical.ReservationProgressStageName
    assert legacy.ReservationProgressStage is canonical.ReservationProgressStage
    assert legacy.ReservationResult is canonical.ReservationResult
    assert canonical.ReservationRequest.__bases__ == (SeatObservationRequest,)
    assert canonical.ReservationProgressStage in get_args(
        canonical.ReservationResult.model_fields["progress_stages"].annotation
    )
    assert get_args(canonical.ReservationProgressStageName) == (
        "authenticated_session_ready",
        "target_rechecked",
        "seat_selected",
        "reservation_requested",
    )
    assert canonical.OFFICIAL_HOST_ROOTS is official_url_policy.OFFICIAL_HOST_ROOTS
    assert canonical.is_official_provider_host is official_url_policy.is_official_provider_host


def test_legacy_policy_reassignment_cannot_weaken_canonical_handoff_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_roots = canonical.OFFICIAL_HOST_ROOTS
    original_predicate = canonical.is_official_provider_host
    monkeypatch.setattr(legacy, "OFFICIAL_HOST_ROOTS", {Provider.MOCK: ("evil.example",)})
    monkeypatch.setattr(legacy, "is_official_provider_host", lambda *_args: True)

    assert canonical.OFFICIAL_HOST_ROOTS is original_roots
    assert canonical.is_official_provider_host is original_predicate
    with pytest.raises(ValidationError):
        canonical.ReservationResult(
            **result_payload(official_handoff_url="https://evil.example/mock-booking")
        )


def test_reservation_models_have_canonical_owners_and_unchanged_fields() -> None:
    assert canonical.ReservationRequest.__module__ == "rail_waitlist.reservations.contracts"
    assert canonical.ReservationProgressStage.__module__ == "rail_waitlist.reservations.contracts"
    assert canonical.ReservedSeat.__module__ == "rail_waitlist.reservations.contracts"
    assert canonical.ReservationResult.__module__ == "rail_waitlist.reservations.contracts"
    assert tuple(canonical.ReservationRequest.model_fields) == (
        "provider",
        "origin_node_id",
        "destination_node_id",
        "origin",
        "destination",
        "train_number",
        "departure_at",
        "seat_class",
        "passenger_count",
        "candidate_id",
        "idempotency_key",
        "expected_credential_version",
        "arrival_at",
    )
    assert tuple(canonical.ReservationProgressStage.model_fields) == ("stage", "occurred_at")
    assert tuple(canonical.ReservedSeat.model_fields) == ("car_number", "seat_number")
    assert tuple(canonical.ReservationResult.model_fields) == (
        "outcome",
        "result_reason_code",
        "source",
        "observed_at",
        "credential_version",
        "payment_deadline",
        "official_handoff_url",
        "progress_stages",
        "reserved_seats",
        "confirmation_correlation_seats",
    )
    assert canonical.ReservationResult.model_fields["progress_stages"].default == ()
    assert canonical.ReservationResult.model_fields["reserved_seats"].default == ()
    assert canonical.ReservationRequest.model_config["extra"] == "forbid"
    assert canonical.ReservationProgressStage.model_config["extra"] == "forbid"
    assert canonical.ReservedSeat.model_config["extra"] == "forbid"
    assert canonical.ReservationResult.model_config["extra"] == "forbid"


@pytest.mark.parametrize(
    ("model", "expected_sha256"),
    [
        (
            canonical.ReservationRequest,
            "a7e1fc73f390dc757e1b54d52e6eb9c25fd146137350eff0593847651d80f35a",
        ),
        (
            canonical.ReservationProgressStage,
            "0baecd0c7d4c2842b48b1cf996d2baa17aba86c430d0710a0e56b3a93560cf13",
        ),
        (
            canonical.ReservationResult,
            "8f5963fbd7c82dfd2bb5b23706651370ecef7a5edc68a97936194a4033882e3f",
        ),
    ],
)
def test_reservation_contract_json_schema_is_stable(
    model: type[canonical.ReservationRequest]
    | type[canonical.ReservationProgressStage]
    | type[canonical.ReservationResult],
    expected_sha256: str,
) -> None:
    encoded = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected_sha256


def test_reservation_request_normalization_and_validation_are_preserved() -> None:
    request = canonical.ReservationRequest(**request_payload())
    assert request.candidate_id == "candidate-1"
    assert request.idempotency_key == "reservation-attempt-1"
    assert request.expected_credential_version == 3

    for updates in (
        {"candidate_id": " "},
        {"idempotency_key": "short"},
        {"expected_credential_version": 0},
        {"arrival_at": "2026-08-07T07:49:00"},
        {"arrival_at": "2026-08-07T06:35:00+09:00"},
        {"token": "secret"},
    ):
        with pytest.raises(ValidationError):
            canonical.ReservationRequest(**request_payload(**updates))


def test_reservation_progress_and_result_fail_closed_contract_is_preserved() -> None:
    result = canonical.ReservationResult(**result_payload())
    assert result.source == "mock"
    assert result.outcome == "payment_required"
    assert result.result_reason_code is ReservationResultReasonCode.PAYMENT_HOLD_CREATED
    assert result.progress_stages[0].stage == "target_rechecked"

    invalid_payloads = (
        {"source": " "},
        {"observed_at": "2026-08-06T21:37:00"},
        {"credential_version": 0},
        {"payment_deadline": "2026-08-06T21:37:00Z"},
        {"official_handoff_url": "http://example.invalid/mock-booking"},
        {"official_handoff_url": "https://evil.example/mock-booking"},
        {"official_handoff_url": "https://www.korail.com/ticket", "source": "mock"},
        {"official_handoff_url": "https://example.invalid/mock-booking", "source": "korail"},
        {
            "outcome": "not_available",
            "payment_deadline": None,
            "official_handoff_url": None,
            "progress_stages": (
                {"stage": "target_rechecked", "occurred_at": "2026-08-06T21:36:00Z"},
                {"stage": "target_rechecked", "occurred_at": "2026-08-06T21:36:30Z"},
            ),
        },
        {
            "progress_stages": (
                {"stage": "seat_selected", "occurred_at": "2026-08-06T21:36:30Z"},
                {"stage": "target_rechecked", "occurred_at": "2026-08-06T21:36:00Z"},
            )
        },
        {
            "progress_stages": (
                {"stage": "target_rechecked", "occurred_at": "2026-08-06T21:38:00Z"},
            )
        },
    )
    for updates in invalid_payloads:
        with pytest.raises(ValidationError):
            canonical.ReservationResult(**result_payload(**updates))

    for stage_payload in (
        {"stage": "unknown", "occurred_at": "2026-08-06T21:36:00Z"},
        {"stage": "target_rechecked", "occurred_at": "2026-08-06T21:36:00"},
    ):
        with pytest.raises(ValidationError):
            canonical.ReservationProgressStage(**stage_payload)


def test_unknown_result_accepts_provider_unavailable_without_weakening_manual_fence() -> None:
    result = canonical.ReservationResult(
        outcome="unknown",
        result_reason_code=ReservationResultReasonCode.PROVIDER_UNAVAILABLE,
        source="korail-pydoll-reservation",
        observed_at="2026-08-06T21:37:00Z",
    )

    assert result.outcome == "unknown"
    assert result.result_reason_code is ReservationResultReasonCode.PROVIDER_UNAVAILABLE
    assert result.official_handoff_url is None


def test_canonical_and_pre_move_legacy_pickles_restore_exact_contract_objects() -> None:
    request = canonical.ReservationRequest(**request_payload())
    stage = canonical.ReservationProgressStage(
        stage="target_rechecked",
        occurred_at="2026-08-06T21:36:00Z",
    )
    result = canonical.ReservationResult(**result_payload())
    for value in (request, stage, result):
        assert pickle.loads(pickle.dumps(value)) == value

    legacy_request = pickle.loads(base64.b64decode(LEGACY_REQUEST_PICKLE))
    legacy_stage = pickle.loads(base64.b64decode(LEGACY_STAGE_PICKLE))
    legacy_result = pickle.loads(base64.b64decode(LEGACY_RESULT_PICKLE))
    assert isinstance(legacy_request, canonical.ReservationRequest)
    assert legacy_request.candidate_id == "candidate-legacy"
    assert isinstance(legacy_stage, canonical.ReservationProgressStage)
    assert legacy_stage.stage == "target_rechecked"
    assert isinstance(legacy_result, canonical.ReservationResult)
    assert legacy_result.outcome == "payment_required"
    assert isinstance(legacy_result.progress_stages[0], canonical.ReservationProgressStage)


@pytest.mark.parametrize(
    "imports",
    [
        "from rail_waitlist.reservations import contracts as owner",
        "from rail_waitlist import schemas; "
        "from rail_waitlist.reservations import contracts as owner",
        "import rail_waitlist.provider_contracts; "
        "from rail_waitlist.reservations import contracts as owner",
        "import rail_waitlist.korail_browser_seat_source; "
        "from rail_waitlist.reservations import contracts as owner",
        "import rail_waitlist.srt_provider_adapter_contract; "
        "from rail_waitlist.reservations import contracts as owner",
        "import rail_waitlist.reservations.attempt_result_application; "
        "from rail_waitlist.reservations import contracts as owner",
    ],
)
def test_reservation_contract_identity_is_import_order_independent(imports: str) -> None:
    script = f"""
import sys
from typing import get_args
{imports}
canonical_first = {imports!r}.startswith('from rail_waitlist.reservations')
if canonical_first:
    assert 'rail_waitlist.schemas' not in sys.modules
from rail_waitlist import schemas
from rail_waitlist.observations.contracts import SeatObservationRequest
assert schemas.ReservationRequest is owner.ReservationRequest
assert schemas.ReservationProgressStageName is owner.ReservationProgressStageName
assert schemas.ReservationProgressStage is owner.ReservationProgressStage
assert schemas.ReservationResult is owner.ReservationResult
assert owner.ReservationRequest.__bases__ == (SeatObservationRequest,)
assert owner.ReservationProgressStage in get_args(
    owner.ReservationResult.model_fields['progress_stages'].annotation
)
assert owner.ReservationRequest.__module__ == 'rail_waitlist.reservations.contracts'
assert owner.ReservationProgressStage.__module__ == 'rail_waitlist.reservations.contracts'
assert owner.ReservationResult.__module__ == 'rail_waitlist.reservations.contracts'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
