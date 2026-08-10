from __future__ import annotations

import ast
import base64
import hashlib
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic_core import PydanticUndefined

from rail_waitlist import schemas as legacy
from rail_waitlist.health import schemas as canonical
from rail_waitlist.main import app as production_app

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
LEGACY_HEALTH_RESPONSE_PICKLE = (
    "gASVvgAAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMDkhlYWx0aFJlc3BvbnNllJOU"
    "KYGUfZQojAhfX2RpY3RfX5R9lCiMBnN0YXR1c5SMAm9rlIwZZXhwZXJpbWVudGFsX3JhaWxf"
    "ZW5hYmxlZJSIdYwSX19weWRhbnRpY19leHRyYV9flE6MF19fcHlkYW50aWNfZmllbGRzX3Nl"
    "dF9flI+UKGgJaAeQjBRfX3B5ZGFudGljX3ByaXZhdGVfX5ROdWIu"
)


def test_health_response_has_one_canonical_class_identity() -> None:
    assert legacy.HealthResponse is canonical.HealthResponse
    assert canonical.HealthResponse.__module__ == "rail_waitlist.health.schemas"


def test_health_response_fields_defaults_and_model_config_are_unchanged() -> None:
    fields = canonical.HealthResponse.model_fields

    assert tuple(fields) == ("status", "experimental_rail_enabled")
    assert fields["status"].annotation is str
    assert fields["experimental_rail_enabled"].annotation is bool
    assert fields["status"].is_required()
    assert fields["experimental_rail_enabled"].is_required()
    assert fields["status"].default is PydanticUndefined
    assert fields["experimental_rail_enabled"].default is PydanticUndefined
    assert canonical.HealthResponse.model_config == {"from_attributes": True}

    response = canonical.HealthResponse(status="ok", experimental_rail_enabled=True)
    assert response.model_dump() == {
        "status": "ok",
        "experimental_rail_enabled": True,
    }


def test_health_response_json_schema_is_stable() -> None:
    encoded = json.dumps(
        canonical.HealthResponse.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert len(encoded) == 234
    assert hashlib.sha256(encoded).hexdigest() == (
        "88d6782be9ff2711a6fd7ef9e513dfc47271446cf01a72e17cd5f2c91261b020"
    )


def test_pre_move_legacy_health_response_pickle_restores_canonical_contract() -> None:
    response = pickle.loads(base64.b64decode(LEGACY_HEALTH_RESPONSE_PICKLE))

    assert isinstance(response, canonical.HealthResponse)
    assert response == canonical.HealthResponse(
        status="ok",
        experimental_rail_enabled=True,
    )
    assert pickle.loads(pickle.dumps(response)) == response


def test_legacy_facade_reassignment_does_not_propagate_to_health_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = canonical.HealthResponse
    replacement = object()

    monkeypatch.setattr(legacy, "HealthResponse", replacement)

    assert legacy.HealthResponse is replacement
    assert canonical.HealthResponse is original


def test_health_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "health" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def test_health_owner_and_central_facade_have_exact_definition_boundaries() -> None:
    owner_path = SOURCE_ROOT / "rail_waitlist" / "health" / "schemas.py"
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert {node.name for node in owner_tree.body if isinstance(node, ast.ClassDef)} == {
        "HealthResponse"
    }
    assert owner_imports == {
        ("__future__", 0, "annotations", None),
        ("schema_base", 2, "ApiModel", None),
    }

    facade_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    facade_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in facade_tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if node.module in {"health", "schema_base"}
    }
    aliases = {
        target.id: node.value
        for node in facade_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "HealthResponse"
    }

    assert not any(isinstance(node, ast.ClassDef) for node in facade_tree.body)
    assert facade_imports == {("health", 1, "schemas", "health_schemas")}
    assert set(aliases) == {"HealthResponse"}
    value = aliases["HealthResponse"]
    assert isinstance(value, ast.Attribute)
    assert value.attr == "HealthResponse"
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "health_schemas"


@pytest.mark.parametrize(
    "import_order",
    ("canonical-first", "schemas-first", "main-first"),
)
def test_health_response_identity_is_import_order_independent(import_order: str) -> None:
    script = r"""
import sys

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.health import schemas as owner
    assert "rail_waitlist.schemas" not in sys.modules
elif order == "schemas-first":
    from rail_waitlist import schemas
    from rail_waitlist.health import schemas as owner
else:
    from rail_waitlist import main
    from rail_waitlist.health import schemas as owner

from rail_waitlist import main, schemas

assert schemas.HealthResponse is owner.HealthResponse
assert main.HealthResponse is owner.HealthResponse
assert owner.HealthResponse.__module__ == "rail_waitlist.health.schemas"
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, import_order],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_health_owner_move_does_not_change_openapi() -> None:
    schema = production_app.openapi()
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()

    assert len(schema["paths"]) == 35
    assert len(schema["components"]["schemas"]) == 69
    assert schema["components"]["schemas"]["HealthResponse"] == (
        canonical.HealthResponse.model_json_schema()
    )
    assert schema["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/HealthResponse"}
    assert len(encoded) == 83000
    assert hashlib.sha256(encoded).hexdigest() == (
        "45abe6354812e213d57ad3e703b9a023bd94598efc3c5504226da51bbbf03b22"
    )


async def test_health_endpoint_payload_contract_is_unchanged(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "experimental_rail_enabled": False,
    }
