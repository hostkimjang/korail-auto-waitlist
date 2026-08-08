from __future__ import annotations

import ast
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, Integer, String, UniqueConstraint

import rail_waitlist.models as legacy
from rail_waitlist.database import Base
from rail_waitlist.domain import OutboxStatus
from rail_waitlist.outbox_management import models as canonical

API_ROOT = Path(__file__).resolve().parents[1]


def test_outbox_model_legacy_alias_has_one_canonical_mapper_and_table() -> None:
    assert legacy.OutboxEvent is canonical.OutboxEvent
    assert canonical.OutboxEvent.__module__ == "rail_waitlist.outbox_management.models"
    assert Base.metadata.tables["outbox_events"] is canonical.OutboxEvent.__table__
    assert sum(mapper.class_ is canonical.OutboxEvent for mapper in Base.registry.mappers) == 1


def test_outbox_model_column_order_and_types_are_preserved() -> None:
    table = canonical.OutboxEvent.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "aggregate_type",
        "aggregate_id",
        "event_type",
        "payload",
        "dedupe_key",
        "status",
        "attempts",
        "available_at",
        "processed_at",
        "last_error",
        "created_at",
    ]
    assert isinstance(table.c.id.type, String) and table.c.id.type.length == 36
    assert isinstance(table.c.aggregate_type.type, String)
    assert table.c.aggregate_type.type.length == 40
    assert isinstance(table.c.aggregate_id.type, String)
    assert table.c.aggregate_id.type.length == 36
    assert isinstance(table.c.event_type.type, String) and table.c.event_type.type.length == 80
    assert isinstance(table.c.payload.type, JSON)
    assert isinstance(table.c.dedupe_key.type, String) and table.c.dedupe_key.type.length == 128
    assert isinstance(table.c.status.type, Enum)
    assert table.c.status.type.enum_class is OutboxStatus
    assert not table.c.status.type.native_enum
    assert table.c.status.type.enums == ["PENDING", "SENT", "FAILED"]
    assert table.c.status.type.name == "outboxstatus"
    assert isinstance(table.c.attempts.type, Integer)
    assert isinstance(table.c.available_at.type, DateTime)
    assert table.c.available_at.type.timezone
    assert isinstance(table.c.processed_at.type, DateTime)
    assert table.c.processed_at.type.timezone
    assert isinstance(table.c.last_error.type, String) and table.c.last_error.type.length == 240
    assert isinstance(table.c.created_at.type, DateTime)
    assert table.c.created_at.type.timezone


def test_outbox_model_nullability_keys_and_indexes_are_preserved() -> None:
    table = canonical.OutboxEvent.__table__
    assert {column.name for column in table.columns if column.nullable} == {
        "processed_at",
        "last_error",
    }
    assert table.c.id.primary_key
    assert table.c.dedupe_key.unique
    assert {
        (index.name, index.unique, tuple(column.name for column in index.columns))
        for index in table.indexes
    } == {
        ("ix_outbox_events_aggregate_id", False, ("aggregate_id",)),
        ("ix_outbox_events_created_at", False, ("created_at",)),
        ("ix_outbox_events_event_type", False, ("event_type",)),
        ("ix_outbox_events_processed_at", False, ("processed_at",)),
        ("ix_outbox_events_status", False, ("status",)),
    }
    unique_constraints = [
        constraint for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert unique_constraints[0].name is None
    assert [column.name for column in unique_constraints[0].columns] == ["dedupe_key"]
    assert not any(isinstance(constraint, CheckConstraint) for constraint in table.constraints)
    assert not any(column.foreign_keys for column in table.columns)
    assert list(canonical.OutboxEvent.__mapper__.relationships) == []


def test_outbox_model_python_defaults_have_no_server_default() -> None:
    table = canonical.OutboxEvent.__table__
    assert all(column.server_default is None for column in table.columns)
    assert {column.name for column in table.columns if column.default is not None} == {
        "id",
        "status",
        "attempts",
        "available_at",
        "created_at",
    }
    assert table.c.id.default is not None and table.c.id.default.is_callable
    assert uuid.UUID(table.c.id.default.arg(None))
    assert table.c.status.default is not None
    assert table.c.status.default.is_scalar
    assert table.c.status.default.arg is OutboxStatus.PENDING
    assert table.c.attempts.default is not None
    assert table.c.attempts.default.is_scalar
    assert table.c.attempts.default.arg == 0
    for column_name in ("available_at", "created_at"):
        default = table.c[column_name].default
        assert default is not None and default.is_callable
        value = default.arg(None)
        assert isinstance(value, datetime)
        assert value.tzinfo is UTC


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "legacy-first", "outbox-first", "operations-first"],
)
def test_outbox_model_import_orders_keep_one_mapper(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.outbox_management import models as canonical
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import models as legacy
elif sys.argv[1] == "outbox-first":
    from rail_waitlist import outbox
else:
    from rail_waitlist import operations

from rail_waitlist import models as legacy
from rail_waitlist.database import Base
from rail_waitlist.outbox_management import models as canonical

print(json.dumps({
    "identity": legacy.OutboxEvent is canonical.OutboxEvent,
    "metadata": Base.metadata.tables["outbox_events"] is canonical.OutboxEvent.__table__,
    "module": canonical.OutboxEvent.__module__,
    "mapper_count": sum(
        mapper.class_ is canonical.OutboxEvent for mapper in Base.registry.mappers
    ),
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
        "identity": True,
        "mapper_count": 1,
        "metadata": True,
        "module": "rail_waitlist.outbox_management.models",
    }


def test_alembic_bootstrap_still_imports_central_metadata_module() -> None:
    module_path = API_ROOT / "migrations" / "env.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert ("rail_waitlist", 0, "models", None) in imports
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "target_metadata"
    ]
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Attribute) and value.attr == "metadata"
    assert isinstance(value.value, ast.Name) and value.value.id == "Base"
