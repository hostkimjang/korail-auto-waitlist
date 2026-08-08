from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy_schemas
from rail_waitlist import services
from rail_waitlist.schema_base import ApiModel
from rail_waitlist.watch_management import schemas

API_ROOT = Path(__file__).resolve().parents[1]


def test_registration_conflict_has_one_canonical_schema_object() -> None:
    assert (
        legacy_schemas.RegistrationEvidenceConflictDetail
        is schemas.RegistrationEvidenceConflictDetail
    )
    assert services.RegistrationEvidenceConflictDetail is schemas.RegistrationEvidenceConflictDetail
    assert schemas.RegistrationEvidenceConflictDetail.__module__ == (
        "rail_waitlist.watch_management.schemas"
    )
    assert schemas.RegistrationEvidenceConflictDetail.__bases__ == (ApiModel,)
    assert list(schemas.RegistrationEvidenceConflictDetail.model_fields) == [
        "code",
        "reason",
        "message",
    ]


def test_registration_conflict_json_schema_contract_is_preserved() -> None:
    schema = schemas.RegistrationEvidenceConflictDetail.model_json_schema()

    assert schema == {
        "properties": {
            "code": {
                "const": "registration_evidence_conflict",
                "default": "registration_evidence_conflict",
                "title": "Code",
                "type": "string",
            },
            "reason": {
                "const": "expired",
                "title": "Reason",
                "type": "string",
            },
            "message": {
                "maxLength": 240,
                "minLength": 1,
                "title": "Message",
                "type": "string",
            },
        },
        "required": ["reason", "message"],
        "title": "RegistrationEvidenceConflictDetail",
        "type": "object",
    }


def test_registration_conflict_preserves_default_and_permissive_values() -> None:
    conflict = schemas.RegistrationEvidenceConflictDetail(
        reason="expired",
        message=" ",
        ignored="legacy-extra",
    )
    assert conflict.model_dump(mode="json") == {
        "code": "registration_evidence_conflict",
        "reason": "expired",
        "message": " ",
    }
    assert not hasattr(conflict, "ignored")

    explicit = schemas.RegistrationEvidenceConflictDetail(
        code="registration_evidence_conflict",
        reason="expired",
        message="x" * 240,
    )
    assert len(explicit.message) == 240

    bytes_message = schemas.RegistrationEvidenceConflictDetail.model_validate(
        {"reason": "expired", "message": b"expired"}
    )
    assert bytes_message.message == "expired"

    @dataclass
    class ConflictAttributes:
        reason: str = "expired"
        message: str = "from attributes"

    assert (
        schemas.RegistrationEvidenceConflictDetail.model_validate(ConflictAttributes()).message
        == "from attributes"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "wrong", "reason": "expired", "message": "expired"},
        {"code": None, "reason": "expired", "message": "expired"},
        {"message": "expired"},
        {"reason": " expired ", "message": "expired"},
        {"reason": "expired"},
        {"reason": "expired", "message": None},
        {"reason": "expired", "message": ""},
        {"reason": "expired", "message": "x" * 241},
        {"reason": "expired", "message": 7},
    ],
)
def test_registration_conflict_preserves_existing_rejections(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schemas.RegistrationEvidenceConflictDetail.model_validate(payload)


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first", "services-first"])
def test_registration_conflict_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.watch_management import schemas as canonical
    from rail_waitlist import schemas as legacy
    from rail_waitlist import services
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import schemas as legacy
    from rail_waitlist.watch_management import schemas as canonical
    from rail_waitlist import services
else:
    from rail_waitlist import services
    from rail_waitlist.watch_management import schemas as canonical
    from rail_waitlist import schemas as legacy

print(json.dumps({
    "legacy": (
        legacy.RegistrationEvidenceConflictDetail
        is canonical.RegistrationEvidenceConflictDetail
    ),
    "services": (
        services.RegistrationEvidenceConflictDetail
        is canonical.RegistrationEvidenceConflictDetail
    ),
    "module": canonical.RegistrationEvidenceConflictDetail.__module__,
    "title": canonical.RegistrationEvidenceConflictDetail.model_json_schema()["title"],
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
        "legacy": True,
        "module": "rail_waitlist.watch_management.schemas",
        "services": True,
        "title": "RegistrationEvidenceConflictDetail",
    }
