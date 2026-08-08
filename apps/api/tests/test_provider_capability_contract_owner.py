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

from rail_waitlist import provider_contracts
from rail_waitlist import providers as providers_facade
from rail_waitlist import schemas as legacy
from rail_waitlist.domain import Provider
from rail_waitlist.provider_registry import contracts as canonical

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
LEGACY_PROVIDER_CAPABILITIES_PICKLE = (
    "gASVgAEAAAAAAACMFXJhaWxfd2FpdGxpc3Quc2NoZW1hc5SMFFByb3ZpZGVyQ2FwYWJpbGl0aWVzlJOU"
    "KYGUfZQojAhfX2RpY3RfX5R9lCiMCHByb3ZpZGVylIwUcmFpbF93YWl0bGlzdC5kb21haW6UjAhQcm92"
    "aWRlcpSTlIwGa29yYWlslIWUUpSMCXRpbWV0YWJsZZSIjBVvZmZpY2lhbF9ib29raW5nX2xpbmuUiIwW"
    "b2ZmaWNpYWxfd2FpdGxpc3RfbGlua5SJjA9zZWF0X21vbml0b3JpbmeUiIwQcmVzZXJ2YXRpb25fb25j"
    "ZZSJjAxleHBlcmltZW50YWyUiYwHZW5hYmxlZJSIjARub3RllIwRbGVnYWN5IGNhcGFiaWxpdHmUdYwS"
    "X19weWRhbnRpY19leHRyYV9flE6MF19fcHlkYW50aWNfZmllbGRzX3NldF9flI+UKGgVaBRoD2gTaBFo"
    "EGgOaAdoEpCMFF9fcHlkYW50aWNfcHJpdmF0ZV9flE51Yi4="
)


def test_provider_capabilities_has_one_canonical_class_identity() -> None:
    assert legacy.ProviderCapabilities is canonical.ProviderCapabilities
    assert providers_facade.ProviderCapabilities is canonical.ProviderCapabilities
    assert provider_contracts.ProviderCapabilities is canonical.ProviderCapabilities
    assert canonical.ProviderCapabilities.__module__ == "rail_waitlist.provider_registry.contracts"


def test_provider_capabilities_fields_requiredness_and_defaults_are_unchanged() -> None:
    fields = canonical.ProviderCapabilities.model_fields

    assert tuple(fields) == (
        "provider",
        "timetable",
        "official_booking_link",
        "official_waitlist_link",
        "seat_monitoring",
        "reservation_once",
        "experimental",
        "enabled",
        "note",
    )
    assert all(
        fields[name].is_required()
        for name in (
            "provider",
            "timetable",
            "official_booking_link",
            "official_waitlist_link",
            "seat_monitoring",
            "reservation_once",
        )
    )
    assert not fields["experimental"].is_required()
    assert not fields["enabled"].is_required()
    assert not fields["note"].is_required()
    assert fields["experimental"].default is False
    assert fields["enabled"].default is True
    assert fields["note"].default is None

    capabilities = canonical.ProviderCapabilities(
        provider=Provider.KORAIL,
        timetable=True,
        official_booking_link=True,
        official_waitlist_link=False,
        seat_monitoring=True,
        reservation_once=False,
    )
    assert capabilities.experimental is False
    assert capabilities.enabled is True
    assert capabilities.note is None


def test_provider_capabilities_json_schema_is_stable() -> None:
    encoded = json.dumps(
        canonical.ProviderCapabilities.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert len(encoded) == 862
    assert hashlib.sha256(encoded).hexdigest() == (
        "529fc42899fcab38cf8ce6e3757599f2b0a403c2bd2c32f4f1bfcd4782b15dd9"
    )


def test_pre_move_legacy_provider_capabilities_pickle_restores_canonical_contract() -> None:
    capabilities = pickle.loads(base64.b64decode(LEGACY_PROVIDER_CAPABILITIES_PICKLE))

    assert isinstance(capabilities, canonical.ProviderCapabilities)
    assert capabilities == canonical.ProviderCapabilities(
        provider=Provider.KORAIL,
        timetable=True,
        official_booking_link=True,
        official_waitlist_link=False,
        seat_monitoring=True,
        reservation_once=False,
        experimental=False,
        enabled=True,
        note="legacy capability",
    )
    assert pickle.loads(pickle.dumps(capabilities)) == capabilities


def test_legacy_facade_reassignment_does_not_propagate_to_canonical_owner() -> None:
    original = legacy.ProviderCapabilities
    replacement = object()
    try:
        legacy.ProviderCapabilities = replacement  # type: ignore[assignment]
        assert legacy.ProviderCapabilities is replacement
        assert canonical.ProviderCapabilities is original
        assert providers_facade.ProviderCapabilities is original
        assert provider_contracts.ProviderCapabilities is original
    finally:
        legacy.ProviderCapabilities = original


def test_provider_registry_package_remains_a_passive_namespace() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_registry" / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def test_provider_capabilities_owner_has_exact_definition_and_import_boundary() -> None:
    module_path = SOURCE_ROOT / "rail_waitlist" / "provider_registry" / "contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert {node.name for node in tree.body if isinstance(node, ast.ClassDef)} == {
        "ProviderCapabilities"
    }
    assert imports == {
        ("__future__", 0, "annotations", None),
        ("domain", 2, "Provider", None),
        ("schema_base", 2, "ApiModel", None),
    }


def test_compatibility_facades_only_alias_the_canonical_provider_capabilities() -> None:
    central_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    central_tree = ast.parse(
        central_path.read_text(encoding="utf-8"),
        filename=str(central_path),
    )
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ProviderCapabilities"
        for node in central_tree.body
    )
    central_aliases = {
        target.id: node.value
        for node in central_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "ProviderCapabilities"
    }
    assert set(central_aliases) == {"ProviderCapabilities"}
    central_value = central_aliases["ProviderCapabilities"]
    assert isinstance(central_value, ast.Attribute)
    assert isinstance(central_value.value, ast.Name)
    assert central_value.value.id == "provider_registry_contracts"
    assert central_value.attr == "ProviderCapabilities"

    providers_path = SOURCE_ROOT / "rail_waitlist" / "providers.py"
    providers_tree = ast.parse(
        providers_path.read_text(encoding="utf-8"),
        filename=str(providers_path),
    )
    facade_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(providers_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "ProviderCapabilities"
    }
    assert facade_imports == {
        (
            "provider_registry.contracts",
            1,
            "ProviderCapabilities",
            "ProviderCapabilities",
        )
    }


@pytest.mark.parametrize(
    "imports",
    [
        "from rail_waitlist.provider_registry import contracts as owner",
        (
            "from rail_waitlist import schemas; "
            "from rail_waitlist.provider_registry import contracts as owner"
        ),
        (
            "import rail_waitlist.providers; "
            "from rail_waitlist.provider_registry import contracts as owner"
        ),
        (
            "import rail_waitlist.provider_contracts; "
            "from rail_waitlist.provider_registry import contracts as owner"
        ),
        (
            "import rail_waitlist.provider_registry.http; "
            "from rail_waitlist.provider_registry import contracts as owner"
        ),
    ],
)
def test_provider_capabilities_identity_is_import_order_independent(imports: str) -> None:
    script = f"""
import sys
{imports}
canonical_first = {imports!r}.startswith('from rail_waitlist.provider_registry')
if canonical_first:
    assert 'rail_waitlist.schemas' not in sys.modules
    assert 'rail_waitlist.providers' not in sys.modules
from rail_waitlist import provider_contracts, providers, schemas
assert schemas.ProviderCapabilities is owner.ProviderCapabilities
assert providers.ProviderCapabilities is owner.ProviderCapabilities
assert provider_contracts.ProviderCapabilities is owner.ProviderCapabilities
assert owner.ProviderCapabilities.__module__ == 'rail_waitlist.provider_registry.contracts'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
