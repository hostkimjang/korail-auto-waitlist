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
from rail_waitlist.domain import Provider
from rail_waitlist.provider_registry import official_url_policy
from rail_waitlist.timetable_management import schemas as canonical

API_ROOT = Path(__file__).resolve().parents[1]
LEGACY_TIMETABLE_ITEM_PICKLE = (
    "gASVbAUAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMDVRpbWV0YWJsZUl0ZW2U"
    "k5QpgZR9lCiMCF9fZGljdF9flH2UKIwIcHJvdmlkZXKUjBRyYWlsX3dhaXRsaXN0LmRv"
    "bWFpbpSMCFByb3ZpZGVylJOUjARtb2NrlIWUUpSMDHRyYWluX251bWJlcpSMCE1PQ0st"
    "MTE4lIwKdHJhaW5fdHlwZZSMBE1PQ0uUjAZvcmlnaW6UjAdEYWVqZW9ulIwLZGVzdGlu"
    "YXRpb26UjAVTZW91bJSMDGRlcGFydHVyZV9hdJSMCGRhdGV0aW1llIwIZGF0ZXRpbWWU"
    "k5RDCgfqCAcGIwAAAACUjBxweWRhbnRpY19jb3JlLl9weWRhbnRpY19jb3JllIwGVHpJ"
    "bmZvlJOUTZB+hZRSlIaUUpSMCmFycml2YWxfYXSUaBlDCgfqCAcHMQAAAACUaB1NkH6F"
    "lFKUhpRSlIwKYWR1bHRfZmFyZZRNlFyMDWZhcmVfY3VycmVuY3mUjANLUleUjBB0aW1l"
    "dGFibGVfc291cmNllGgLjBZ0aW1ldGFibGVfcmV0cmlldmVkX2F0lGgZQwoH6ggHAAAA"
    "AAAAlGgdSwCFlFKUhpRSlIwMYXZhaWxhYmlsaXR5lGgAjBBTZWF0QXZhaWxhYmlsaXR5"
    "lJOUKYGUfZQoaAV9lCiMBnN0YXR1c5SMCWF2YWlsYWJsZZSMBnNvdXJjZZRoC4wLb2Jz"
    "ZXJ2ZWRfYXSUaBlDCgfqCAcAAAAAAACUaB1LAIWUUpSGlFKUdYwSX19weWRhbnRpY19l"
    "eHRyYV9flE6MF19fcHlkYW50aWNfZmllbGRzX3NldF9flI+UKGg4aDtoOpCMFF9fcHlk"
    "YW50aWNfcHJpdmF0ZV9flE51YowMc2VhdF9jbGFzc2VzlF2UaACMFVNlYXRDbGFzc0F2"
    "YWlsYWJpbGl0eZSTlCmBlH2UKGgFfZQojApzZWF0X2NsYXNzlGgIjAlTZWF0Q2xhc3OU"
    "k5SMCHN0YW5kYXJklIWUUpRoOGg5jApwcm92ZW5hbmNllGgAjBpTZWF0QXZhaWxhYmls"
    "aXR5UHJvdmVuYW5jZZSTlCmBlH2UKGgFfZQojARraW5klGgLaDpoC2g7aBlDCgfqCAcA"
    "AAAAAACUaB1LAIWUUpSGlFKUjAtmcmVzaF91bnRpbJRoGUMKB+oIBwAFAAAAAJRoHUsA"
    "hZRSlIaUUpSMBnJlYXNvbpROdWhBTmhCj5QoaFhoO2g6aF6QaEROdWKMBGZhcmWUTZRc"
    "aCloKowHYWN0aW9uc5RdlGgAjBZTZWF0QXZhaWxhYmlsaXR5QWN0aW9ulJOUKYGUfZQo"
    "aAV9lChoWIwOb2ZmaWNpYWxfY2hlY2uUjAN1cmyUjBFweWRhbnRpYy5uZXR3b3Jrc5SM"
    "CkFueUh0dHBVcmyUk5QpgZR9lIwEX3VybJSMHHB5ZGFudGljX2NvcmUuX3B5ZGFudGlj"
    "X2NvcmWUjANVcmyUk5SMJGh0dHBzOi8vZXhhbXBsZS5pbnZhbGlkL21vY2stYm9va2lu"
    "Z5SFlIGUc2J1aEFOaEKPlChob2hYkGhETnViYYwYcmVnaXN0cmF0aW9uX2V2aWRlbmNl"
    "X2lklE51aEFOaEKPlChoUmhMaGdoZmg4kGhETnViYYwUb2ZmaWNpYWxfYm9va2luZ191"
    "cmyUaHIpgZR9lGh1aHiMJGh0dHBzOi8vZXhhbXBsZS5pbnZhbGlkL21vY2stYm9va2lu"
    "Z5SFlIGUc2KME29mZmljaWFsX3NlYXJjaF91cmyUTnVoQU5oQo+UKGgiaAdof2gOaCxo"
    "FGgSaBZoKGgraDJoRWgQkGhETnViLg=="
)
LEGACY_TIMETABLE_EVIDENCE_PICKLE = (
    "gASVMQIAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMGVRpbWV0YWJsZVNlYXRF"
    "dmlkZW5jZVJlYWSUk5QpgZR9lCiMCF9fZGljdF9flH2UKIwCaWSUjA9ldmlkZW5jZS1s"
    "ZWdhY3mUjAZzdGF0dXOUjAlhdmFpbGFibGWUjApwcm92ZW5hbmNllGgAjBpTZWF0QXZh"
    "aWxhYmlsaXR5UHJvdmVuYW5jZZSTlCmBlH2UKGgFfZQojARraW5klIwEbW9ja5SMBnNv"
    "dXJjZZRoEowLb2JzZXJ2ZWRfYXSUjAhkYXRldGltZZSMCGRhdGV0aW1llJOUQwoH6ggH"
    "AAAAAAAAlIwccHlkYW50aWNfY29yZS5fcHlkYW50aWNfY29yZZSMBlR6SW5mb5STlEsA"
    "hZRSlIaUUpSMC2ZyZXNoX3VudGlslGgXQwoH6ggHAAUAAAAAlGgbSwCFlFKUhpRSlIwG"
    "cmVhc29ulE51jBJfX3B5ZGFudGljX2V4dHJhX1+UTowXX19weWRhbnRpY19maWVsZHNf"
    "c2V0X1+Uj5QoaBFoFGgTaCCQjBRfX3B5ZGFudGljX3ByaXZhdGVfX5ROdWKMCmNyZWF0"
    "ZWRfYXSUaBdDCgfqCAcAAAAAAACUaBtLAIWUUpSGlFKUjBhyZWdpc3RyYXRpb25fdmFs"
    "aWRfdW50aWyUaBdDCgfqCAcACgAAAACUaBtLAIWUUpSGlFKUdWgnTmgoj5QoaAtoK2gH"
    "aAloMZBoKk51Yi4="
)


def test_central_schema_hub_preserves_exact_timetable_contract_aliases() -> None:
    symbols = (
        "SeatAvailabilityStatus",
        "SeatAvailability",
        "SeatAvailabilityNotObservedReason",
        "SeatAvailabilityProvenance",
        "SeatAvailabilityAction",
        "SeatClassAvailability",
        "TimetableSeatEvidenceRead",
        "TimetableItem",
    )
    for symbol in symbols:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)


def test_timetable_contracts_have_canonical_owners_and_nested_identities() -> None:
    models = (
        canonical.SeatAvailability,
        canonical.SeatAvailabilityProvenance,
        canonical.SeatAvailabilityAction,
        canonical.SeatClassAvailability,
        canonical.TimetableSeatEvidenceRead,
        canonical.TimetableItem,
    )
    assert {model.__module__ for model in models} == {"rail_waitlist.timetable_management.schemas"}
    assert canonical.TimetableItem.model_fields["availability"].default_factory is (
        canonical.SeatAvailability
    )
    assert (
        canonical.SeatAvailabilityProvenance
        is canonical.SeatClassAvailability.model_fields["provenance"].annotation
    )
    assert (
        canonical.TimetableSeatEvidenceRead.model_fields["status"].annotation
        is canonical.SeatAvailabilityStatus
    )
    assert (
        canonical.SeatAvailabilityProvenance
        is canonical.TimetableSeatEvidenceRead.model_fields["provenance"].annotation
    )
    seat_class_annotation = canonical.TimetableItem.model_fields["seat_classes"].annotation
    assert canonical.SeatClassAvailability in get_args(seat_class_annotation)


def test_timetable_literal_contracts_are_unchanged() -> None:
    assert get_args(canonical.SeatAvailabilityStatus) == (
        "unavailable",
        "unknown",
        "available",
        "limited",
        "standing_plus_seat",
        "not_enough_seats",
        "sold_out",
        "waitlist_available",
        "reservation_completed",
        "not_offered",
        "departed",
        "out_of_service",
        "stale",
        "error",
    )
    assert get_args(canonical.SeatAvailabilityNotObservedReason) == (
        "public_api_not_available",
        "source_not_configured",
        "provider_access_restricted",
        "unsupported_route",
        "passenger_count_not_supported",
        "departure_window_elapsed",
        "no_exact_match",
        "source_unavailable",
    )


@pytest.mark.parametrize(
    ("model", "expected_sha256"),
    [
        (
            canonical.SeatAvailability,
            "0f5e1f68db4379e07e18ea3683b3e0a2ca988a09e0b398d2720e49c628c9ba09",
        ),
        (
            canonical.SeatAvailabilityProvenance,
            "bc3861c4a0370f59224436d6a093aa9a48655b070a6720fee6e8c8637fb7e4ff",
        ),
        (
            canonical.SeatAvailabilityAction,
            "cd9d4994ce7fa58e6297016ef0fec66b1ee1f53e20bfff40b3986479ef532865",
        ),
        (
            canonical.SeatClassAvailability,
            "0ecebb4b97181c81f7cda68b3a559ca46667afcced4562de3564e10eb70e5a57",
        ),
        (
            canonical.TimetableSeatEvidenceRead,
            "3813275f7384824c6e1291187d873b09a86cc8404b3d370041678d6300d0ded1",
        ),
        (
            canonical.TimetableItem,
            "724293b4c2dc692f3da55ab796d1c7adf79c882f3f5868646f948a79b0201a82",
        ),
    ],
)
def test_timetable_contract_json_schema_is_stable(
    model: type[canonical.SeatAvailability]
    | type[canonical.SeatAvailabilityProvenance]
    | type[canonical.SeatAvailabilityAction]
    | type[canonical.SeatClassAvailability]
    | type[canonical.TimetableSeatEvidenceRead]
    | type[canonical.TimetableItem],
    expected_sha256: str,
) -> None:
    encoded = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected_sha256


def _timetable_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": Provider.MOCK,
        "train_number": "MOCK-118",
        "train_type": "MOCK",
        "origin": "Daejeon",
        "destination": "Seoul",
        "departure_at": "2026-08-07T06:35:00+09:00",
        "arrival_at": "2026-08-07T07:49:00+09:00",
        "timetable_source": "mock",
        "timetable_retrieved_at": "2026-08-07T00:00:00Z",
        "official_booking_url": "https://example.invalid/mock-booking",
    }
    payload.update(overrides)
    return payload


def test_legacy_host_policy_reassignment_cannot_weaken_canonical_timetable_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_predicate = canonical.is_official_provider_host
    monkeypatch.setattr(legacy, "is_official_provider_host", lambda *_args: True)

    assert canonical.is_official_provider_host is original_predicate
    with pytest.raises(ValidationError):
        canonical.TimetableItem(**_timetable_payload(official_booking_url="https://evil.example"))


def test_legacy_search_validator_reassignment_cannot_weaken_canonical_korail_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_validator = canonical.validate_korail_general_search_url
    monkeypatch.setattr(legacy, "validate_korail_general_search_url", lambda value: value)

    assert canonical.validate_korail_general_search_url is original_validator
    with pytest.raises(ValidationError):
        canonical.TimetableItem(
            **_timetable_payload(
                provider=Provider.KORAIL,
                timetable_source="official_provider",
                official_booking_url="https://www.korail.com/ticket/search/list",
                official_search_url=(
                    "https://www.korail.com/ticket/search/list?searchType=GENERAL"
                ),
            )
        )


def test_canonical_and_pre_move_legacy_pickles_restore_exact_timetable_objects() -> None:
    legacy_item = pickle.loads(base64.b64decode(LEGACY_TIMETABLE_ITEM_PICKLE))
    legacy_evidence = pickle.loads(base64.b64decode(LEGACY_TIMETABLE_EVIDENCE_PICKLE))

    assert isinstance(legacy_item, canonical.TimetableItem)
    assert isinstance(legacy_item.availability, canonical.SeatAvailability)
    assert isinstance(legacy_item.seat_classes[0], canonical.SeatClassAvailability)
    assert isinstance(
        legacy_item.seat_classes[0].provenance,
        canonical.SeatAvailabilityProvenance,
    )
    assert isinstance(legacy_item.seat_classes[0].actions[0], canonical.SeatAvailabilityAction)
    assert isinstance(legacy_evidence, canonical.TimetableSeatEvidenceRead)
    assert isinstance(legacy_evidence.provenance, canonical.SeatAvailabilityProvenance)
    for value in (legacy_item, legacy_evidence):
        assert pickle.loads(pickle.dumps(value)) == value


@pytest.mark.parametrize(
    "imports",
    [
        "from rail_waitlist.timetable_management import schemas as owner",
        "from rail_waitlist import schemas; "
        "from rail_waitlist.timetable_management import schemas as owner",
        "import rail_waitlist.providers; "
        "from rail_waitlist.timetable_management import schemas as owner",
        "import rail_waitlist.korail_browser_seat_source; "
        "from rail_waitlist.timetable_management import schemas as owner",
        "import rail_waitlist.srt_provider_adapter_contract; "
        "from rail_waitlist.timetable_management import schemas as owner",
        "import rail_waitlist.watch_registration_policy; "
        "from rail_waitlist.timetable_management import schemas as owner",
        "import rail_waitlist.timetable_management.http; "
        "from rail_waitlist.timetable_management import schemas as owner",
    ],
)
def test_timetable_contract_identity_is_import_order_independent(imports: str) -> None:
    script = f"""
import sys
{imports}
canonical_first = {imports!r}.startswith('from rail_waitlist.timetable_management')
if canonical_first:
    assert 'rail_waitlist.schemas' not in sys.modules
from rail_waitlist import schemas
assert schemas.SeatAvailabilityStatus is owner.SeatAvailabilityStatus
assert schemas.SeatAvailability is owner.SeatAvailability
assert schemas.SeatAvailabilityNotObservedReason is owner.SeatAvailabilityNotObservedReason
assert schemas.SeatAvailabilityProvenance is owner.SeatAvailabilityProvenance
assert schemas.SeatAvailabilityAction is owner.SeatAvailabilityAction
assert schemas.SeatClassAvailability is owner.SeatClassAvailability
assert schemas.TimetableSeatEvidenceRead is owner.TimetableSeatEvidenceRead
assert schemas.TimetableItem is owner.TimetableItem
assert owner.TimetableItem.model_fields['availability'].default_factory is owner.SeatAvailability
assert owner.SeatAvailability.__module__ == 'rail_waitlist.timetable_management.schemas'
assert owner.TimetableItem.__module__ == 'rail_waitlist.timetable_management.schemas'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_timetable_owner_uses_the_canonical_official_host_policy() -> None:
    assert canonical.is_official_provider_host is official_url_policy.is_official_provider_host
