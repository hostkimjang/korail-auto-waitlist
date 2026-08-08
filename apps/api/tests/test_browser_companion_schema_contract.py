from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy_schemas
from rail_waitlist.browser_companion import schemas as companion_schemas
from rail_waitlist.official_rail_identity import (
    contains_protection_marker,
    normalize_official_train_number,
)
from rail_waitlist.provider_schema_base import ProviderContractModel

API_ROOT = Path(__file__).resolve().parents[1]
COMPANION_SCHEMA_NAMES = (
    "KorailBrowserTrainSnapshot",
    "KorailBrowserSnapshotCreate",
    "KorailBrowserSnapshotRead",
    "KorailBrowserSnapshotRevision",
    "BrowserCompanionPairingCreate",
    "BrowserCompanionPairingRead",
    "BrowserCompanionPairingExchange",
    "BrowserCompanionPairingResult",
    "BrowserCompanionCredentialRead",
    "BrowserCompanionStatus",
    "BrowserCompanionChallengeCreate",
    "BrowserCompanionChallengeRead",
)


def _train(number: str = "00026", *, departure_at: datetime | None = None) -> dict[str, object]:
    return {
        "train_number": number,
        "departure_at": departure_at
        or datetime(2026, 8, 6, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=9))),
        "standard": "available",
        "first": "sold_out",
    }


def _snapshot(**overrides: object) -> companion_schemas.KorailBrowserSnapshotCreate:
    payload: dict[str, object] = {
        "origin": "  대전  ",
        "destination": " 서울 ",
        "travel_date": date(2026, 8, 6),
        "passenger_count": 1,
        "trains": [_train()],
    }
    payload.update(overrides)
    return companion_schemas.KorailBrowserSnapshotCreate(**payload)


def test_legacy_browser_companion_exports_are_exact_canonical_objects() -> None:
    for name in COMPANION_SCHEMA_NAMES:
        assert getattr(legacy_schemas, name) is getattr(companion_schemas, name)
    assert (
        legacy_schemas.KORAIL_BROWSER_COMPANION_SOURCE
        == companion_schemas.KORAIL_BROWSER_COMPANION_SOURCE
        == "korail-official-browser-companion"
    )
    assert legacy_schemas.KorailBrowserSeatStatus is companion_schemas.KorailBrowserSeatStatus
    assert legacy_schemas.ProviderContractModel is ProviderContractModel
    assert legacy_schemas.normalize_official_train_number is normalize_official_train_number
    assert legacy_schemas.contains_protection_marker is contains_protection_marker

    train_annotation = companion_schemas.KorailBrowserSnapshotCreate.model_fields[
        "trains"
    ].annotation
    credentials_annotation = companion_schemas.BrowserCompanionStatus.model_fields[
        "credentials"
    ].annotation
    assert get_args(train_annotation) == (companion_schemas.KorailBrowserTrainSnapshot,)
    assert get_args(credentials_annotation) == (companion_schemas.BrowserCompanionCredentialRead,)


def test_browser_snapshot_preserves_normalization_timezone_and_strict_boundary() -> None:
    snapshot = _snapshot()
    assert snapshot.origin == "대전"
    assert snapshot.destination == "서울"
    assert snapshot.trains[0].train_number == "26"
    assert snapshot.trains[0].departure_at == datetime(2026, 8, 6, 3, 0, tzinfo=UTC)
    assert snapshot.trains[0].departure_at.microsecond == 0
    assert snapshot.model_config["from_attributes"] is True
    assert snapshot.model_config["extra"] == "forbid"

    payload = {
        "origin": "대전",
        "destination": "서울",
        "travel_date": date(2026, 8, 6),
        "passenger_count": 1,
        "trains": [_train()],
        "raw_response": {"token": "must-not-cross-provider-boundary"},
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        companion_schemas.KorailBrowserSnapshotCreate(**payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"origin": "서울", "destination": "서울"},
        {"origin": "macro_err", "destination": "서울"},
        {"passenger_count": 0},
        {"trains": []},
        {"trains": [_train("026"), _train("26")]},
        {
            "trains": [
                _train(
                    departure_at=datetime(
                        2026,
                        8,
                        7,
                        0,
                        1,
                        tzinfo=timezone(timedelta(hours=9)),
                    )
                )
            ]
        },
    ],
)
def test_browser_snapshot_rejects_invalid_identity_and_bounds(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _snapshot(**overrides)


@pytest.mark.parametrize(
    "train_number",
    ["", "  ", "-8002", "-8003", "MACRO_ERR", "captcha", "NetFunnel", "blocked"],
)
def test_browser_train_rejects_blank_and_protection_marker_identity(
    train_number: str,
) -> None:
    with pytest.raises(ValidationError):
        companion_schemas.KorailBrowserTrainSnapshot(**_train(train_number))


def test_browser_companion_pairing_challenge_and_read_contracts_are_preserved() -> None:
    assert companion_schemas.BrowserCompanionPairingCreate().label == "내 브라우저"
    pairing = companion_schemas.BrowserCompanionPairingCreate(
        label="  노트북  ",
        ignored_transport_field="ignored",
    )
    assert pairing.label == "노트북"
    assert "ignored_transport_field" not in pairing.model_dump()
    with pytest.raises(ValidationError):
        companion_schemas.BrowserCompanionPairingCreate(label="   ")

    exchange = companion_schemas.BrowserCompanionPairingExchange(
        pairing_code="p" * 32,
        client_id="12345678-1234-4234-8234-123456789abc",
    )
    assert exchange.pairing_code == "p" * 32
    for invalid_client_id in (
        "12345678-1234-1234-8234-123456789abc",
        "12345678-1234-4234-8234-123456789ABC",
    ):
        with pytest.raises(ValidationError):
            companion_schemas.BrowserCompanionPairingExchange(
                pairing_code="p" * 32,
                client_id=invalid_client_id,
            )

    challenge = companion_schemas.BrowserCompanionChallengeCreate(body_sha256="a" * 64)
    assert challenge.body_sha256 == "a" * 64
    with pytest.raises(ValidationError):
        companion_schemas.BrowserCompanionChallengeCreate(body_sha256="A" * 64)

    credential = companion_schemas.BrowserCompanionCredentialRead.model_validate(
        SimpleNamespace(
            id="credential-1",
            label="내 브라우저",
            extension_origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop",
            created_at=datetime(2026, 8, 6, 0, 0, tzinfo=UTC),
            last_used_at=None,
        )
    )
    assert list(companion_schemas.BrowserCompanionCredentialRead.model_fields) == [
        "id",
        "label",
        "extension_origin",
        "created_at",
        "last_used_at",
    ]
    assert credential.last_used_at is None
    assert companion_schemas.KorailBrowserSnapshotRevision(revision=None).revision is None
    with pytest.raises(ValidationError):
        companion_schemas.KorailBrowserSnapshotRevision()


def test_official_rail_identity_helpers_preserve_normalization_and_markers() -> None:
    assert normalize_official_train_number(" 00000 ") == "0"
    assert normalize_official_train_number("  ktx-001 ") == "KTX-001"
    assert contains_protection_marker("CAPTCHA required") is True
    assert contains_protection_marker("KTX-001") is False


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_browser_companion_schema_import_orders_keep_exact_identity(
    import_order: str,
) -> None:
    script = r"""
import json
import sys
from typing import get_args

if sys.argv[1] == "canonical-first":
    from rail_waitlist.browser_companion import schemas as canonical
    from rail_waitlist import schemas as legacy
else:
    from rail_waitlist import schemas as legacy
    from rail_waitlist.browser_companion import schemas as canonical

names = (
    "KorailBrowserTrainSnapshot",
    "KorailBrowserSnapshotCreate",
    "KorailBrowserSnapshotRead",
    "KorailBrowserSnapshotRevision",
    "BrowserCompanionPairingCreate",
    "BrowserCompanionPairingRead",
    "BrowserCompanionPairingExchange",
    "BrowserCompanionPairingResult",
    "BrowserCompanionCredentialRead",
    "BrowserCompanionStatus",
    "BrowserCompanionChallengeCreate",
    "BrowserCompanionChallengeRead",
)
result = {
    "identity": all(getattr(legacy, name) is getattr(canonical, name) for name in names),
    "train_nested": get_args(
        canonical.KorailBrowserSnapshotCreate.model_fields["trains"].annotation
    )[0] is canonical.KorailBrowserTrainSnapshot,
    "credential_nested": get_args(
        canonical.BrowserCompanionStatus.model_fields["credentials"].annotation
    )[0] is canonical.BrowserCompanionCredentialRead,
    "schemas": len([getattr(canonical, name).model_json_schema() for name in names]),
}
print(json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "credential_nested": True,
        "identity": True,
        "schemas": 12,
        "train_nested": True,
    }
