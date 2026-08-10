from __future__ import annotations

import ast
import base64
import hashlib
import json
import pickle
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rail_waitlist import policy as legacy_policy
from rail_waitlist import schemas as legacy_schemas
from rail_waitlist.domain import WatchStatus
from rail_waitlist.main import app
from rail_waitlist.watch_management import provider_failure_policy as canonical

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
LEGACY_ERROR_POLICY_RESULT_PICKLE = (
    "gASVVAEAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMEUVycm9yUG9saWN5UmVzdWx0lJOU"
    "KYGUfZQojAhfX2RpY3RfX5R9lCiMBnN0YXR1c5SMFHJhaWxfd2FpdGxpc3QuZG9tYWlulIwLV2F0"
    "Y2hTdGF0dXOUk5SMDWF1dGhfcmVxdWlyZWSUhZRSlIwQY29vbGRvd25fc2Vjb25kc5RNLAGMFnJl"
    "cXVpcmVzX21hbnVhbF9yZXN1bWWUiIwZb2ZmaWNpYWxfaGFuZG9mZl9yZXF1aXJlZJSIjAZyZWFz"
    "b26UjBtwcm92aWRlcl9ibG9ja19vcl9jaGFsbGVuZ2WUdYwSX19weWRhbnRpY19leHRyYV9flE6M"
    "F19fcHlkYW50aWNfZmllbGRzX3NldF9flI+UKGgPaBBoEWgOaAeQjBRfX3B5ZGFudGljX3ByaXZh"
    "dGVfX5ROdWIu"
)
EXPECTED_PROTECTION_SIGNALS = frozenset(
    {
        "-8002",
        "-8003",
        "403",
        "abnormal_access",
        "access_denied",
        "automation_detected",
        "bot_challenge",
        "captcha",
        "code_-8002",
        "code_-8003",
        "korail_-8002",
        "korail_-8003",
        "macro_err1",
        "netfunnel",
        "queue_challenge",
    }
)


def _expected_result(
    status: str,
    cooldown_seconds: int | None,
    requires_manual_resume: bool,
    official_handoff_required: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "status": status,
        "cooldown_seconds": cooldown_seconds,
        "requires_manual_resume": requires_manual_resume,
        "official_handoff_required": official_handoff_required,
        "reason": reason,
    }


RATE_LIMIT_RESULT = _expected_result(
    "cooldown",
    1800,
    False,
    False,
    "provider_rate_limited",
)
PROTECTION_RESULT = _expected_result(
    "auth_required",
    300,
    True,
    True,
    "provider_block_or_challenge",
)
AUTH_RESULT = _expected_result(
    "auth_required",
    None,
    True,
    False,
    "provider_authentication_required",
)
UNKNOWN_RESULT = _expected_result(
    "failed",
    None,
    True,
    False,
    "provider_request_failed",
)


def test_provider_failure_policy_has_one_canonical_identity() -> None:
    assert legacy_schemas.ErrorPolicyResult is canonical.ErrorPolicyResult
    assert legacy_policy.ErrorPolicyResult is canonical.ErrorPolicyResult
    assert canonical.ErrorPolicyResult.__module__ == (
        "rail_waitlist.watch_management.provider_failure_policy"
    )

    for symbol in (
        "RATE_LIMIT_COOLDOWN",
        "BLOCK_COOLDOWN",
        "PROTECTION_SIGNALS",
        "classify_provider_failure",
        "cooldown_until",
    ):
        assert getattr(legacy_policy, symbol) is getattr(canonical, symbol)


def test_error_policy_result_fields_requiredness_and_defaults_are_unchanged() -> None:
    fields = canonical.ErrorPolicyResult.model_fields

    assert tuple(fields) == (
        "status",
        "cooldown_seconds",
        "requires_manual_resume",
        "official_handoff_required",
        "reason",
    )
    assert all(
        fields[name].is_required()
        for name in (
            "status",
            "cooldown_seconds",
            "requires_manual_resume",
            "reason",
        )
    )
    assert not fields["official_handoff_required"].is_required()
    assert fields["official_handoff_required"].default is False

    result = canonical.ErrorPolicyResult(
        status=WatchStatus.FAILED,
        cooldown_seconds=None,
        requires_manual_resume=True,
        reason="provider_request_failed",
    )
    assert result.official_handoff_required is False


def test_error_policy_result_json_schema_is_stable() -> None:
    encoded = json.dumps(
        canonical.ErrorPolicyResult.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert len(encoded) == 723
    assert hashlib.sha256(encoded).hexdigest() == (
        "d21af6b8f892cebc281fcdc5920ed73782e010daccdcaa3e17a1eec721c54f6c"
    )


def test_pre_move_legacy_error_policy_pickle_restores_canonical_contract() -> None:
    result = pickle.loads(base64.b64decode(LEGACY_ERROR_POLICY_RESULT_PICKLE))

    assert isinstance(result, canonical.ErrorPolicyResult)
    assert result == canonical.ErrorPolicyResult(
        status=WatchStatus.AUTH_REQUIRED,
        cooldown_seconds=300,
        requires_manual_resume=True,
        official_handoff_required=True,
        reason="provider_block_or_challenge",
    )
    assert pickle.loads(pickle.dumps(result)) == result


def test_legacy_facade_reassignment_does_not_propagate_to_canonical_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_model = canonical.ErrorPolicyResult
    original_classifier = canonical.classify_provider_failure
    original_signals = canonical.PROTECTION_SIGNALS
    replacement = object()

    monkeypatch.setattr(legacy_schemas, "ErrorPolicyResult", replacement)
    monkeypatch.setattr(legacy_policy, "classify_provider_failure", replacement)
    monkeypatch.setattr(legacy_policy, "PROTECTION_SIGNALS", frozenset())

    assert canonical.ErrorPolicyResult is original_model
    assert legacy_policy.ErrorPolicyResult is original_model
    assert canonical.classify_provider_failure is original_classifier
    assert canonical.PROTECTION_SIGNALS is original_signals


def test_watch_management_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "schemas-first", "policy-first"],
)
def test_provider_failure_policy_identity_is_import_order_independent(
    import_order: str,
) -> None:
    script = r"""
import sys

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.watch_management import provider_failure_policy as owner
    assert "rail_waitlist.schemas" not in sys.modules
    assert "rail_waitlist.policy" not in sys.modules
elif order == "schemas-first":
    from rail_waitlist import schemas
    from rail_waitlist.watch_management import provider_failure_policy as owner
else:
    from rail_waitlist import policy
    from rail_waitlist.watch_management import provider_failure_policy as owner

from rail_waitlist import policy, schemas

assert schemas.ErrorPolicyResult is owner.ErrorPolicyResult
for name in (
    "ErrorPolicyResult",
    "RATE_LIMIT_COOLDOWN",
    "BLOCK_COOLDOWN",
    "PROTECTION_SIGNALS",
    "classify_provider_failure",
    "cooldown_until",
):
    assert getattr(policy, name) is getattr(owner, name)
assert owner.ErrorPolicyResult.__module__ == (
    "rail_waitlist.watch_management.provider_failure_policy"
)
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_provider_failure_policy_constants_are_exact() -> None:
    assert canonical.RATE_LIMIT_COOLDOWN == timedelta(minutes=30)
    assert canonical.BLOCK_COOLDOWN == timedelta(minutes=5)
    assert canonical.PROTECTION_SIGNALS == EXPECTED_PROTECTION_SIGNALS


POLICY_CASES: list[tuple[int | str, dict[str, object]]] = [
    (429, RATE_LIMIT_RESULT),
    (" 429 ", RATE_LIMIT_RESULT),
    *((signal, PROTECTION_RESULT) for signal in sorted(EXPECTED_PROTECTION_SIGNALS)),
    (-8002, PROTECTION_RESULT),
    (-8003, PROTECTION_RESULT),
    (403, PROTECTION_RESULT),
    (" CODE -8002 ", PROTECTION_RESULT),
    ("CODE -8003", PROTECTION_RESULT),
    ("Abnormal Access", PROTECTION_RESULT),
    ("NetFunnel", PROTECTION_RESULT),
    (401, AUTH_RESULT),
    (" 401 ", AUTH_RESULT),
    (" AUTH ", AUTH_RESULT),
    ("login_failed", AUTH_RESULT),
    (500, UNKNOWN_RESULT),
    ("unexpected_response", UNKNOWN_RESULT),
]


@pytest.mark.parametrize(("signal", "expected"), POLICY_CASES)
def test_provider_failure_policy_matrix_is_unchanged(
    signal: int | str,
    expected: dict[str, object],
) -> None:
    now = datetime(2026, 8, 7, tzinfo=UTC)

    result = canonical.classify_provider_failure(signal, now)

    assert result.model_dump(mode="json") == expected


@pytest.mark.parametrize(
    ("signal", "expected_seconds"),
    [
        (429, 1800),
        ("captcha", 300),
        ("login_failed", None),
        ("unexpected_response", None),
    ],
)
def test_cooldown_until_uses_the_classified_duration(
    signal: int | str,
    expected_seconds: int | None,
) -> None:
    now = datetime(2026, 8, 7, 12, 30, tzinfo=UTC)
    result = canonical.classify_provider_failure(signal, now)

    actual = canonical.cooldown_until(result, now)

    expected = None if expected_seconds is None else now + timedelta(seconds=expected_seconds)
    assert actual == expected


def test_provider_failure_owner_move_does_not_change_openapi() -> None:
    schema = app.openapi()
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()

    assert len(schema["paths"]) == 35
    assert len(schema["components"]["schemas"]) == 70
    assert "ErrorPolicyResult" not in schema["components"]["schemas"]
    assert len(encoded) == 83500
    assert hashlib.sha256(encoded).hexdigest() == (
        "a3bfcc336b728ed8fe3641a8139a6abbcc8475dc85e3f42b012fa4747c6aa662"
    )
