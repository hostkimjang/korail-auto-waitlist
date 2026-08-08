from __future__ import annotations

import base64
import inspect
import json
import pickle
import re
import subprocess
import sys
from dataclasses import MISSING, FrozenInstanceError, dataclass, fields
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import pytest

import rail_waitlist.reservation_confirmation as legacy
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.provider_registry.official_url_policy import OFFICIAL_HOST_ROOTS
from rail_waitlist.reservations.provider_confirmation import contracts as canonical

API_ROOT = Path(__file__).resolve().parents[1]
LEGACY_TARGET_PICKLE = (
    "gASVTwEAAAAAAACMJnJhaWxfd2FpdGxpc3QucmVzZXJ2YXRpb25fY29uZmlybWF0aW9ulIwd"
    "UmVzZXJ2YXRpb25Db25maXJtYXRpb25UYXJnZXSUk5QpgZRdlCiMDmF0dGVtcHQtbGVnYWN5"
    "lIwQY2FuZGlkYXRlLWxlZ2FjeZSMFHJhaWxfd2FpdGxpc3QuZG9tYWlulIwIUHJvdmlkZXKU"
    "k5SMBmtvcmFpbJSFlFKUjAMxMTiUjAdEYWVqZW9ulIwFU2VvdWyUjAhkYXRldGltZZSMCGRh"
    "dGV0aW1llJOUQwoH6ggHBiMAAAAAlGgQjAh0aW1lem9uZZSTlGgQjAl0aW1lZGVsdGGUk5RL"
    "AEsASwCHlFKUhZRSlIaUUpRoB4wJU2VhdENsYXNzlJOUjAhzdGFuZGFyZJSFlFKUSwFLA2gS"
    "QwoH6ggHBzEAAAAAlGgbhpRSlGViLg=="
)
LEGACY_RESULT_PICKLE = (
    "gASVYAEAAAAAAACMJnJhaWxfd2FpdGxpc3QucmVzZXJ2YXRpb25fY29uZmlybWF0aW9ulIwd"
    "UmVzZXJ2YXRpb25Db25maXJtYXRpb25SZXN1bHSUk5QpgZRdlCiMFHJhaWxfd2FpdGxpc3Qu"
    "ZG9tYWlulIwIUHJvdmlkZXKUk5SMA3NydJSFlFKUjDpyYWlsX3dhaXRsaXN0LnJlc2VydmF0"
    "aW9ucy5wcm92aWRlcl9jb25maXJtYXRpb24uY29udHJhY3RzlIweUmVzZXJ2YXRpb25Db25m"
    "aXJtYXRpb25PdXRjb21llJOUjAlub3RfZm91bmSUhZRSlIwTbGVnYWN5LWNvbmZpcm1hdGlv"
    "bpSMCGRhdGV0aW1llIwIZGF0ZXRpbWWUk5RDCgfqCAcAAAAAAACUaBKMCHRpbWV6b25llJOU"
    "aBKMCXRpbWVkZWx0YZSTlEsASwBLAIeUUpSFlFKUhpRSlE5OZWIu"
)
LEGACY_OUTCOME_PICKLE = (
    "gASVXQAAAAAAAACMJnJhaWxfd2FpdGxpc3QucmVzZXJ2YXRpb25fY29uZmlybWF0aW9ulIwe"
    "UmVzZXJ2YXRpb25Db25maXJtYXRpb25PdXRjb21llJOUjAlub3RfZm91bmSUhZRSlC4="
)
SEMANTIC_SYMBOLS = {
    "ReservationConfirmationAdapter",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "require_official_handoff_url",
}
LEGACY_PUBLIC_SURFACE = {
    "OFFICIAL_HOST_ROOTS",
    "Protocol",
    "Provider",
    "ReservationConfirmationAdapter",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "SeatClass",
    "StrEnum",
    "annotations",
    "dataclass",
    "datetime",
    "re",
    "require_official_handoff_url",
    "urlsplit",
}


def target_kwargs() -> dict[str, object]:
    return {
        "attempt_id": "attempt-1",
        "candidate_id": "candidate-1",
        "provider": Provider.KORAIL,
        "train_number": "118",
        "origin": "Daejeon",
        "destination": "Seoul",
        "departure_at": datetime(2026, 8, 7, 6, 35, tzinfo=UTC),
        "seat_class": SeatClass.STANDARD,
        "passenger_count": 1,
        "credential_version": 3,
        "arrival_at": datetime(2026, 8, 7, 7, 49, tzinfo=UTC),
    }


def result_kwargs() -> dict[str, object]:
    return {
        "provider": Provider.KORAIL,
        "outcome": canonical.ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        "source": "korail-reservation-list",
        "observed_at": datetime(2026, 8, 7, 6, 40, tzinfo=UTC),
        "payment_deadline": datetime(2026, 8, 7, 7, 30, tzinfo=UTC),
        "official_handoff_url": "https://www.korail.com/ticket/reservation/list",
    }


def test_legacy_facade_preserves_semantic_identity_and_accidental_public_surface() -> None:
    assert {name for name in vars(legacy) if not name.startswith("_")} == LEGACY_PUBLIC_SURFACE
    for symbol in SEMANTIC_SYMBOLS - {"require_official_handoff_url"}:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)
    assert legacy.require_official_handoff_url is canonical.require_official_handoff_url
    assert legacy.OFFICIAL_HOST_ROOTS is OFFICIAL_HOST_ROOTS
    assert legacy.re is re
    assert legacy.dataclass is dataclass
    assert legacy.datetime is datetime
    assert legacy.Protocol is Protocol
    assert legacy.urlsplit is urlsplit
    assert legacy.Provider is Provider
    assert legacy.SeatClass is SeatClass
    assert legacy.StrEnum is StrEnum


def test_confirmation_classes_are_canonical_frozen_slot_contracts() -> None:
    target_fields = fields(canonical.ReservationConfirmationTarget)
    result_fields = fields(canonical.ReservationConfirmationResult)

    assert [field.name for field in target_fields] == [
        "attempt_id",
        "candidate_id",
        "provider",
        "train_number",
        "origin",
        "destination",
        "departure_at",
        "seat_class",
        "passenger_count",
        "credential_version",
        "arrival_at",
    ]
    assert [field.default for field in target_fields[:-1]] == [MISSING] * 10
    assert target_fields[-1].default is None
    assert [field.name for field in result_fields] == [
        "provider",
        "outcome",
        "source",
        "observed_at",
        "payment_deadline",
        "official_handoff_url",
    ]
    assert [field.default for field in result_fields[:4]] == [MISSING] * 4
    assert [field.default for field in result_fields[4:]] == [None, None]
    assert canonical.ReservationConfirmationTarget.__slots__ == tuple(
        field.name for field in target_fields
    )
    assert canonical.ReservationConfirmationResult.__slots__ == tuple(
        field.name for field in result_fields
    )
    assert canonical.ReservationConfirmationTarget.__module__ == (
        "rail_waitlist.reservations.provider_confirmation.contracts"
    )
    assert canonical.ReservationConfirmationResult.__module__ == (
        "rail_waitlist.reservations.provider_confirmation.contracts"
    )
    assert canonical.ReservationConfirmationAdapter.__module__ == (
        "rail_waitlist.reservations.provider_confirmation.contracts"
    )

    first = canonical.ReservationConfirmationTarget(**target_kwargs())
    second = canonical.ReservationConfirmationTarget(**target_kwargs())
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises(FrozenInstanceError):
        first.attempt_id = "changed"


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"attempt_id": " "}, "attempt_id cannot be blank"),
        ({"candidate_id": ""}, "candidate_id cannot be blank"),
        ({"train_number": " "}, "train_number cannot be blank"),
        ({"origin": " "}, "origin cannot be blank"),
        ({"destination": ""}, "destination cannot be blank"),
        ({"provider": Provider.MOCK}, "only KORAIL or SRT"),
        ({"destination": " Daejeon "}, "distinct stations"),
        ({"departure_at": datetime(2026, 8, 7, 6, 35)}, "departure_at.*timezone"),
        ({"arrival_at": datetime(2026, 8, 7, 7, 49)}, "arrival_at.*timezone"),
        ({"arrival_at": datetime(2026, 8, 7, 6, 35, tzinfo=UTC)}, "later"),
        ({"seat_class": SeatClass.ANY}, "concrete supported seat class"),
        ({"passenger_count": 0}, "between 1 and 9"),
        ({"passenger_count": 10}, "between 1 and 9"),
        ({"credential_version": 0}, "positive"),
    ],
)
def test_target_validation_contract_is_preserved(
    updates: dict[str, object],
    message: str,
) -> None:
    values = {**target_kwargs(), **updates}
    with pytest.raises(ValueError, match=message):
        canonical.ReservationConfirmationTarget(**values)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"source": "bad source"}, "stable sanitized identifier"),
        ({"observed_at": datetime(2026, 8, 7, 6, 40)}, "observed_at.*timezone"),
        ({"payment_deadline": datetime(2026, 8, 7, 7, 30)}, "payment_deadline.*timezone"),
        ({"provider": Provider.MOCK}, "only KORAIL or SRT"),
        ({"official_handoff_url": None}, "only a confirmed payment hold"),
        (
            {
                "outcome": canonical.ReservationConfirmationOutcome.NOT_FOUND,
                "official_handoff_url": "https://www.korail.com/ticket/reservation/list",
            },
            "only a confirmed payment hold",
        ),
        (
            {
                "outcome": canonical.ReservationConfirmationOutcome.NOT_FOUND,
                "payment_deadline": datetime(2026, 8, 7, 7, 30, tzinfo=UTC),
                "official_handoff_url": None,
            },
            "only a confirmed payment hold",
        ),
        (
            {"official_handoff_url": "https://etk.srail.kr/hpg/hra/02/list"},
            "provider allowlist",
        ),
    ],
)
def test_result_validation_contract_is_preserved(
    updates: dict[str, object],
    message: str,
) -> None:
    values = {**result_kwargs(), **updates}
    with pytest.raises(ValueError, match=message):
        canonical.ReservationConfirmationResult(**values)


def test_confirmation_result_never_authorizes_an_automatic_retry() -> None:
    result = canonical.ReservationConfirmationResult(**result_kwargs())
    assert not result.permits_automatic_reservation_retry


def test_confirmation_adapter_keeps_the_async_structural_signature() -> None:
    method = canonical.ReservationConfirmationAdapter.confirm
    signature = inspect.signature(method)

    assert inspect.iscoroutinefunction(method)
    assert tuple(signature.parameters) == ("self", "target")
    assert signature.parameters["target"].annotation == "ReservationConfirmationTarget"
    assert signature.return_annotation == "ReservationConfirmationResult"


def test_canonical_and_pre_move_legacy_pickles_restore_exact_contract_objects() -> None:
    target = canonical.ReservationConfirmationTarget(**target_kwargs())
    result = canonical.ReservationConfirmationResult(**result_kwargs())
    assert pickle.loads(pickle.dumps(target)) == target
    assert pickle.loads(pickle.dumps(result)) == result

    legacy_target = pickle.loads(base64.b64decode(LEGACY_TARGET_PICKLE))
    legacy_result = pickle.loads(base64.b64decode(LEGACY_RESULT_PICKLE))
    legacy_outcome = pickle.loads(base64.b64decode(LEGACY_OUTCOME_PICKLE))
    assert isinstance(legacy_target, canonical.ReservationConfirmationTarget)
    assert legacy_target.attempt_id == "attempt-legacy"
    assert legacy_target.provider is Provider.KORAIL
    assert isinstance(legacy_result, canonical.ReservationConfirmationResult)
    assert legacy_result.outcome is canonical.ReservationConfirmationOutcome.NOT_FOUND
    assert legacy_outcome is canonical.ReservationConfirmationOutcome.NOT_FOUND


def test_legacy_facade_reassignment_does_not_bypass_canonical_url_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(legacy, "require_official_handoff_url", lambda _provider, value: value)
    values = {
        **result_kwargs(),
        "official_handoff_url": "https://etk.srail.kr/hpg/hra/02/list",
    }

    with pytest.raises(ValueError, match="provider allowlist"):
        canonical.ReservationConfirmationResult(**values)


@pytest.mark.parametrize(
    "order",
    (
        "canonical-first",
        "legacy-first",
        "schemas-first",
        "watch-models-first",
        "korail-first",
        "srt-first",
        "provider-contracts-first",
    ),
)
def test_confirmation_contract_import_orders_keep_exact_identity(order: str) -> None:
    script = """
import json
import sys

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.reservations.provider_confirmation import contracts as canonical
elif order == "legacy-first":
    import rail_waitlist.reservation_confirmation
elif order == "schemas-first":
    import rail_waitlist.schemas
elif order == "watch-models-first":
    from rail_waitlist.watch_management import models
elif order == "korail-first":
    from rail_waitlist.reservations.provider_confirmation import korail
elif order == "srt-first":
    from rail_waitlist.reservations.provider_confirmation import srt
else:
    import rail_waitlist.provider_contracts

from rail_waitlist import reservation_confirmation as legacy
from rail_waitlist.provider_registry import official_url_policy
from rail_waitlist.reservations.provider_confirmation import contracts as canonical
from rail_waitlist.reservations.provider_confirmation import korail, srt
from rail_waitlist import schemas
semantic = (
    "ReservationConfirmationAdapter",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
)
print(json.dumps({
    "identity": all(getattr(legacy, name) is getattr(canonical, name) for name in semantic),
    "modules": {name: getattr(canonical, name).__module__ for name in semantic},
    "roots": schemas.OFFICIAL_HOST_ROOTS is official_url_policy.OFFICIAL_HOST_ROOTS,
    "schema_host": (
        schemas.is_official_provider_host
        is official_url_policy.is_official_provider_host
    ),
    "legacy_url": (
        legacy.require_official_handoff_url
        is official_url_policy.require_official_handoff_url
    ),
    "korail_url": (
        korail.require_official_handoff_url
        is official_url_policy.require_official_handoff_url
    ),
    "srt_url": srt.require_official_handoff_url is official_url_policy.require_official_handoff_url,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, order],
        cwd=API_ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    expected_module = "rail_waitlist.reservations.provider_confirmation.contracts"
    assert json.loads(completed.stdout) == {
        "identity": True,
        "modules": {
            "ReservationConfirmationAdapter": expected_module,
            "ReservationConfirmationOutcome": expected_module,
            "ReservationConfirmationResult": expected_module,
            "ReservationConfirmationTarget": expected_module,
        },
        "roots": True,
        "schema_host": True,
        "legacy_url": True,
        "korail_url": True,
        "srt_url": True,
    }
