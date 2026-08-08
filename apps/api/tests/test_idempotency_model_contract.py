from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import UniqueConstraint

from rail_waitlist.database import Base
from rail_waitlist.idempotency.models import (
    IdempotencyRecord as CanonicalIdempotencyRecord,
)
from rail_waitlist.models import IdempotencyRecord

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
        )
        for column in model.__table__.columns
    ]


def test_legacy_idempotency_record_export_is_the_exact_canonical_object() -> None:
    assert IdempotencyRecord is CanonicalIdempotencyRecord


def test_idempotency_record_preserves_mapper_and_metadata_contract() -> None:
    table = CanonicalIdempotencyRecord.__table__
    assert Base.metadata.tables["idempotency_records"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is CanonicalIdempotencyRecord for mapper in Base.registry.mappers) == 1
    assert _column_fingerprint(CanonicalIdempotencyRecord) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("scope", "VARCHAR(100)", False, False, None, None, False, False),
        ("key", "VARCHAR(100)", False, False, None, None, False, False),
        ("resource_id", "VARCHAR(36)", False, False, None, None, False, False),
        ("request_hash", "VARCHAR(64)", False, False, None, None, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
    ]
    assert table.indexes == set()
    assert {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {("uq_idempotency_scope_key", ("scope", "key"))}
    assert all(not column.foreign_keys for column in table.columns)


def test_idempotency_record_defaults_keep_uuid_and_aware_utc_semantics() -> None:
    table = CanonicalIdempotencyRecord.__table__
    id_value = table.c.id.default.arg(None)
    created_at = table.c.created_at.default.arg(None)

    assert str(UUID(id_value)) == id_value
    assert created_at.tzinfo is not None
    assert created_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_idempotency_model_import_orders_register_one_mapper(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.idempotency.models import IdempotencyRecord as Canonical
    from rail_waitlist.models import IdempotencyRecord as Legacy
else:
    from rail_waitlist.models import IdempotencyRecord as Legacy
    from rail_waitlist.idempotency.models import IdempotencyRecord as Canonical

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["idempotency_records"] is Canonical.__table__,
    "mapper_count": sum(mapper.class_ is Canonical for mapper in Base.registry.mappers),
    "columns": [column.name for column in Canonical.__table__.columns],
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
            "scope",
            "key",
            "resource_id",
            "request_hash",
            "created_at",
        ],
        "identity": True,
        "mapper_count": 1,
        "table": True,
    }
