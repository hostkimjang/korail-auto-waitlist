from __future__ import annotations

import base64
import hashlib
import json
import pickle
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.observations import contracts as canonical

API_ROOT = Path(__file__).resolve().parents[1]
LEGACY_REQUEST_PICKLE = (
    "gASVEgIAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMFlNlYXRPYnNlcnZhdGlv"
    "blJlcXVlc3SUk5QpgZR9lCiMCF9fZGljdF9flH2UKIwIcHJvdmlkZXKUjBRyYWlsX3dh"
    "aXRsaXN0LmRvbWFpbpSMCFByb3ZpZGVylJOUjARtb2NrlIWUUpSMDm9yaWdpbl9ub2Rl"
    "X2lklIwMTU9DSy1EQUVKRU9OlIwTZGVzdGluYXRpb25fbm9kZV9pZJSMCk1PQ0stU0VP"
    "VUyUjAZvcmlnaW6UjAdEYWVqZW9ulIwLZGVzdGluYXRpb26UjAVTZW91bJSMDHRyYWlu"
    "X251bWJlcpSMCE1PQ0stMTE4lIwMZGVwYXJ0dXJlX2F0lIwIZGF0ZXRpbWWUjAhkYXRl"
    "dGltZZSTlEMKB+oIBwYjAAAAAJSMHHB5ZGFudGljX2NvcmUuX3B5ZGFudGljX2NvcmWU"
    "jAZUekluZm+Uk5RNkH6FlFKUhpRSlIwKc2VhdF9jbGFzc5RoCIwJU2VhdENsYXNzlJOU"
    "jAhzdGFuZGFyZJSFlFKUjA9wYXNzZW5nZXJfY291bnSUSwF1jBJfX3B5ZGFudGljX2V4"
    "dHJhX1+UTowXX19weWRhbnRpY19maWVsZHNfc2V0X1+Uj5QoaBBoJGgSaAdoFGgqaBZo"
    "DmgYkIwUX19weWRhbnRpY19wcml2YXRlX1+UTnViLg=="
)
LEGACY_RESULT_PICKLE = (
    "gASV+wEAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMFVNlYXRPYnNlcnZhdGlv"
    "blJlc3VsdJSTlCmBlH2UKIwIX19kaWN0X1+UfZQojApzZWF0X2NsYXNzlIwUcmFpbF93"
    "YWl0bGlzdC5kb21haW6UjAlTZWF0Q2xhc3OUk5SMBWZpcnN0lIWUUpSMBnN0YXR1c5Ro"
    "CIwVU2VhdE9ic2VydmF0aW9uU3RhdHVzlJOUjAVlcnJvcpSFlFKUjAZzb3VyY2WUjBJs"
    "ZWdhY3ktb2JzZXJ2YXRpb26UjAtvYnNlcnZlZF9hdJSMCGRhdGV0aW1llIwIZGF0ZXRp"
    "bWWUk5RDCgfqCAcAAAAAAACUjBxweWRhbnRpY19jb3JlLl9weWRhbnRpY19jb3JllIwG"
    "VHpJbmZvlJOUSwCFlFKUhpRSlIwLZnJlc2hfdW50aWyUaBlDCgfqCAcAAAAAAACUaB1L"
    "AIWUUpSGlFKUjA5lcnJvcl9jYXRlZ29yeZSMFHByb3ZpZGVyX3VuYXZhaWxhYmxllIwN"
    "ZGVsYXlfbWludXRlc5RLEXWMEl9fcHlkYW50aWNfZXh0cmFfX5ROjBdfX3B5ZGFudGlj"
    "X2ZpZWxkc19zZXRfX5SPlChoKmgWaAdoImgOaBRoKJCMFF9fcHlkYW50aWNfcHJpdmF0"
    "ZV9flE51Yi4="
)


def request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": Provider.MOCK,
        "origin_node_id": " MOCK-DAEJEON ",
        "destination_node_id": " MOCK-SEOUL ",
        "origin": " Daejeon ",
        "destination": " Seoul ",
        "train_number": " MOCK-118 ",
        "departure_at": "2026-08-07T06:35:00+09:00",
        "seat_class": SeatClass.STANDARD,
        "passenger_count": 1,
    }
    payload.update(overrides)
    return payload


def result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "seat_class": SeatClass.FIRST,
        "status": "error",
        "source": " legacy-observation ",
        "observed_at": "2026-08-07T00:00:00Z",
        "fresh_until": "2026-08-07T00:00:00Z",
        "error_category": "provider_unavailable",
        "delay_minutes": 17,
    }
    payload.update(overrides)
    return payload


def test_central_schema_hub_preserves_exact_observation_contract_aliases() -> None:
    assert legacy.ObservationErrorCategory is canonical.ObservationErrorCategory
    assert legacy.SeatObservationRequest is canonical.SeatObservationRequest
    assert legacy.SeatObservationResult is canonical.SeatObservationResult
    assert legacy.ReservationRequest.__bases__ == (canonical.SeatObservationRequest,)
    latest_error_annotation = legacy.WatchCandidateLatestObservationRead.model_fields[
        "error_category"
    ].annotation
    assert canonical.ObservationErrorCategory in get_args(latest_error_annotation)


def test_srt_sidecar_nested_models_use_the_canonical_observation_contracts() -> None:
    from rail_waitlist.srt_provider_adapter_contract import SrtObserveRequest, SrtObserveResult

    assert SrtObserveRequest.model_fields["request"].annotation is canonical.SeatObservationRequest
    assert canonical.SeatObservationResult in get_args(
        SrtObserveResult.model_fields["observations"].annotation
    )


def test_observation_models_have_the_canonical_owner_and_unchanged_field_contract() -> None:
    assert canonical.SeatObservationRequest.__module__ == "rail_waitlist.observations.contracts"
    assert canonical.SeatObservationResult.__module__ == "rail_waitlist.observations.contracts"
    assert tuple(canonical.SeatObservationRequest.model_fields) == (
        "provider",
        "origin_node_id",
        "destination_node_id",
        "origin",
        "destination",
        "train_number",
        "departure_at",
        "seat_class",
        "passenger_count",
    )
    assert tuple(canonical.SeatObservationResult.model_fields) == (
        "seat_class",
        "status",
        "source",
        "observed_at",
        "fresh_until",
        "error_category",
        "delay_minutes",
    )
    assert canonical.SeatObservationResult.model_fields["error_category"].default is None
    assert canonical.SeatObservationResult.model_fields["delay_minutes"].default is None
    assert canonical.SeatObservationRequest.model_config["extra"] == "forbid"
    assert canonical.SeatObservationResult.model_config["extra"] == "forbid"


@pytest.mark.parametrize(
    ("model", "expected_sha256"),
    [
        (
            canonical.SeatObservationRequest,
            "18280886a4c9405adc9655885d760d1d286e4fc00f9cf6448e5db56180bb2152",
        ),
        (
            canonical.SeatObservationResult,
            "23ff628c55a4fd371af033d64a7cf3f0a6f122688ffc6f71e18bfa61ff830113",
        ),
    ],
)
def test_observation_contract_json_schema_is_stable(
    model: type[canonical.SeatObservationRequest] | type[canonical.SeatObservationResult],
    expected_sha256: str,
) -> None:
    encoded = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected_sha256


def test_observation_request_normalization_and_fail_closed_validation_are_preserved() -> None:
    request = canonical.SeatObservationRequest(**request_payload())
    assert request.origin_node_id == "MOCK-DAEJEON"
    assert request.destination_node_id == "MOCK-SEOUL"
    assert request.origin == "Daejeon"
    assert request.destination == "Seoul"
    assert request.train_number == "MOCK-118"
    assert request.departure_at == datetime(2026, 8, 6, 21, 35, tzinfo=UTC)

    invalid_payloads = [
        {"origin_node_id": " "},
        {"destination_node_id": "MOCK-DAEJEON"},
        {"destination": "Daejeon"},
        {"train_number": ""},
        {"departure_at": "2026-08-07T06:35:00"},
        {"seat_class": SeatClass.ANY},
        {"passenger_count": 0},
        {"passenger_count": 10},
        {"token": "secret"},
    ]
    for updates in invalid_payloads:
        with pytest.raises(ValidationError):
            canonical.SeatObservationRequest(**request_payload(**updates))


def test_observation_result_normalization_and_fail_closed_validation_are_preserved() -> None:
    result = canonical.SeatObservationResult(**result_payload())
    assert result.source == "legacy-observation"
    assert result.error_category == "provider_unavailable"
    assert result.delay_minutes == 17

    invalid_payloads = [
        {"seat_class": SeatClass.ANY},
        {"status": "available", "error_category": "partial_failure"},
        {"status": "error", "error_category": None},
        {"status": "maybe"},
        {"source": " "},
        {"source": "https://private.example/source"},
        {"observed_at": "2026-08-07T00:00:00"},
        {"fresh_until": "2026-08-06T23:59:59Z"},
        {"delay_minutes": 0},
        {"delay_minutes": 1000},
    ]
    for updates in invalid_payloads:
        with pytest.raises(ValidationError):
            canonical.SeatObservationResult(**result_payload(**updates))


def test_canonical_and_pre_move_legacy_pickles_restore_exact_contract_objects() -> None:
    request = canonical.SeatObservationRequest(**request_payload())
    result = canonical.SeatObservationResult(**result_payload())
    assert pickle.loads(pickle.dumps(request)) == request
    assert pickle.loads(pickle.dumps(result)) == result

    legacy_request = pickle.loads(base64.b64decode(LEGACY_REQUEST_PICKLE))
    legacy_result = pickle.loads(base64.b64decode(LEGACY_RESULT_PICKLE))
    assert isinstance(legacy_request, canonical.SeatObservationRequest)
    assert legacy_request.train_number == "MOCK-118"
    assert isinstance(legacy_result, canonical.SeatObservationResult)
    assert legacy_result.error_category == "provider_unavailable"


@pytest.mark.parametrize(
    "imports",
    [
        "from rail_waitlist.observations import contracts as owner",
        "from rail_waitlist import schemas; "
        "from rail_waitlist.observations import contracts as owner",
        "import rail_waitlist.provider_contracts; "
        "from rail_waitlist.observations import contracts as owner",
        "import rail_waitlist.korail_browser_seat_source; "
        "from rail_waitlist.observations import contracts as owner",
        "import rail_waitlist.srt_seat_source; "
        "from rail_waitlist.observations import contracts as owner",
    ],
)
def test_observation_contract_identity_is_import_order_independent(imports: str) -> None:
    script = f"""
import sys
{imports}
canonical_first = {imports!r}.startswith(
    'from rail_waitlist.observations'
)
if canonical_first:
    assert 'rail_waitlist.schemas' not in sys.modules
from rail_waitlist import schemas
assert schemas.ObservationErrorCategory is owner.ObservationErrorCategory
assert schemas.SeatObservationRequest is owner.SeatObservationRequest
assert schemas.SeatObservationResult is owner.SeatObservationResult
assert schemas.ReservationRequest.__bases__ == (owner.SeatObservationRequest,)
assert owner.SeatObservationRequest.__module__ == 'rail_waitlist.observations.contracts'
assert owner.SeatObservationResult.__module__ == 'rail_waitlist.observations.contracts'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
