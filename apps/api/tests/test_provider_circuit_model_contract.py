from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from rail_waitlist.database import Base
from rail_waitlist.domain import Provider, ProviderCircuitState
from rail_waitlist.models import ProviderCircuit as LegacyProviderCircuit
from rail_waitlist.provider_circuit.models import ProviderCircuit

API_ROOT = Path(__file__).resolve().parents[1]


def _column_fingerprint(model: type[object]) -> list[tuple[object, ...]]:
    return [
        (
            column.name,
            str(column.type),
            column.nullable,
            column.primary_key,
            column.unique,
            column.index,
            column.default is not None,
            column.server_default is not None,
            column.onupdate is not None,
        )
        for column in model.__table__.columns
    ]


def test_legacy_provider_circuit_export_is_the_exact_canonical_object() -> None:
    assert LegacyProviderCircuit is ProviderCircuit
    assert ProviderCircuit.__module__ == "rail_waitlist.provider_circuit.models"


def test_provider_circuit_preserves_mapper_columns_and_metadata() -> None:
    table = ProviderCircuit.__table__
    assert Base.metadata.tables["provider_circuits"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is ProviderCircuit for mapper in Base.registry.mappers) == 1
    assert _column_fingerprint(ProviderCircuit) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False, False),
        ("provider", "VARCHAR(6)", False, False, True, None, False, False, False),
        ("state", "VARCHAR(11)", False, False, None, None, True, False, False),
        ("reason", "VARCHAR(160)", True, False, None, None, False, False, False),
        ("opened_at", "DATETIME", True, False, None, None, False, False, False),
        ("cooldown_until", "DATETIME", True, False, None, None, False, False, False),
        ("manual_resume_required", "BOOLEAN", False, False, None, None, True, False, False),
        ("generation", "INTEGER", False, False, None, None, True, False, False),
        ("updated_at", "DATETIME", False, False, None, None, True, False, True),
    ]


def test_provider_circuit_preserves_constraints_index_and_enums() -> None:
    table = ProviderCircuit.__table__
    assert {
        (constraint.name, str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        (
            "ck_provider_circuit_reason_nonempty",
            "reason IS NULL OR length(trim(reason)) > 0",
        ),
        (
            "ck_provider_circuit_cooldown_order",
            "cooldown_until IS NULL OR opened_at IS NULL OR cooldown_until >= opened_at",
        ),
        ("ck_provider_circuit_generation_nonnegative", "generation >= 0"),
        (
            "ck_provider_circuit_provider_allowed",
            "provider IN ('KORAIL', 'SRT', 'MOCK')",
        ),
        (
            "ck_provider_circuit_state_allowed",
            "state IN ('CLOSED', 'OPEN', 'HALF_OPEN', 'MANUAL_HOLD')",
        ),
    }
    assert {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {(None, ("provider",))}
    assert {
        (index.name, index.unique, tuple(column.name for column in index.columns))
        for index in table.indexes
    } == {("ix_provider_circuits_state_cooldown", False, ("state", "cooldown_until"))}
    assert all(not column.foreign_keys for column in table.columns)
    assert table.c.provider.type.enum_class is Provider
    assert tuple(table.c.provider.type.enums) == ("KORAIL", "SRT", "MOCK")
    assert table.c.provider.type.name == "provider"
    assert table.c.provider.type.native_enum is False
    assert table.c.state.type.enum_class is ProviderCircuitState
    assert tuple(table.c.state.type.enums) == ("CLOSED", "OPEN", "HALF_OPEN", "MANUAL_HOLD")
    assert table.c.state.type.name == "providercircuitstate"
    assert table.c.state.type.native_enum is False


def test_provider_circuit_preserves_python_defaults_and_utc_onupdate() -> None:
    table = ProviderCircuit.__table__
    id_value = table.c.id.default.arg(None)
    updated_at = table.c.updated_at.default.arg(None)
    updated_on_change = table.c.updated_at.onupdate.arg(None)

    assert str(UUID(id_value)) == id_value
    assert table.c.state.default.arg is ProviderCircuitState.CLOSED
    assert table.c.manual_resume_required.default.arg is False
    assert table.c.generation.default.arg == 0
    assert updated_at.tzinfo is not None
    assert updated_at.utcoffset() == timedelta(0)
    assert updated_on_change.tzinfo is not None
    assert updated_on_change.utcoffset() == timedelta(0)
    assert all(column.server_default is None for column in table.columns)


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_provider_circuit_import_orders_register_one_mapper(import_order: str) -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.provider_circuit.models import ProviderCircuit as Canonical
    configure_mappers()
    from rail_waitlist.models import ProviderCircuit as Legacy
else:
    from rail_waitlist.models import ProviderCircuit as Legacy
    from rail_waitlist.provider_circuit.models import ProviderCircuit as Canonical
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["provider_circuits"] is Canonical.__table__,
    "mapper_count": sum(mapper.class_ is Canonical for mapper in Base.registry.mappers),
    "columns": [column.name for column in Canonical.__table__.columns],
    "relationships": list(Canonical.__mapper__.relationships.keys()),
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
        "columns": [
            "id",
            "provider",
            "state",
            "reason",
            "opened_at",
            "cooldown_until",
            "manual_resume_required",
            "generation",
            "updated_at",
        ],
        "identity": True,
        "mapper_count": 1,
        "relationships": [],
        "table": True,
    }
