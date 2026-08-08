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
from rail_waitlist.domain import Provider
from rail_waitlist.models import RailProviderAccount as LegacyRailProviderAccount
from rail_waitlist.provider_account_management.models import RailProviderAccount

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


def test_legacy_provider_account_is_the_exact_canonical_object() -> None:
    assert LegacyRailProviderAccount is RailProviderAccount
    assert RailProviderAccount.__module__ == "rail_waitlist.provider_account_management.models"


def test_provider_account_preserves_mapper_columns_and_metadata() -> None:
    table = RailProviderAccount.__table__
    assert Base.metadata.tables["rail_provider_accounts"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is RailProviderAccount for mapper in Base.registry.mappers) == 1
    assert _column_fingerprint(RailProviderAccount) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False, False),
        ("provider", "VARCHAR(6)", False, False, True, True, False, False, False),
        ("credentials_ciphertext", "TEXT", False, False, None, None, False, False, False),
        ("enabled", "BOOLEAN", False, False, None, None, True, True, False),
        ("credential_version", "INTEGER", False, False, None, None, True, True, False),
        ("last_auth_status", "VARCHAR(32)", False, False, None, None, True, True, False),
        ("last_authenticated_at", "DATETIME", True, False, None, None, False, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False, False),
        ("updated_at", "DATETIME", False, False, None, None, True, False, True),
    ]
    for name in ("last_authenticated_at", "created_at", "updated_at"):
        assert table.c[name].type.timezone is True


def test_provider_account_preserves_checks_index_and_enum_storage() -> None:
    table = RailProviderAccount.__table__
    assert {
        (constraint.name, str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        (
            "ck_rail_provider_account_provider_allowed",
            "provider IN ('KORAIL', 'SRT')",
        ),
        (
            "ck_rail_provider_account_ciphertext_nonempty",
            "length(trim(credentials_ciphertext)) > 0",
        ),
        ("ck_rail_provider_account_version_positive", "credential_version >= 1"),
        (
            "ck_rail_provider_account_auth_status_allowed",
            "last_auth_status IN ('not_checked', 'authenticated', 'auth_required', "
            "'provider_blocked', 'failed')",
        ),
    }
    assert {
        constraint for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    } == set()
    assert {
        (index.name, index.unique, tuple(column.name for column in index.columns))
        for index in table.indexes
    } == {("ix_rail_provider_accounts_provider", True, ("provider",))}
    provider_type = table.c.provider.type
    assert provider_type.enum_class is Provider
    assert provider_type.name == "provider"
    assert tuple(provider_type.enums) == ("KORAIL", "SRT", "MOCK")
    assert provider_type.native_enum is False
    assert all(not column.foreign_keys for column in table.columns)
    assert list(RailProviderAccount.__mapper__.relationships.keys()) == []


def test_provider_account_preserves_python_and_server_defaults() -> None:
    table = RailProviderAccount.__table__
    id_value = table.c.id.default.arg(None)
    created_at = table.c.created_at.default.arg(None)
    updated_at = table.c.updated_at.default.arg(None)
    updated_onupdate = table.c.updated_at.onupdate.arg(None)

    assert str(UUID(id_value)) == id_value
    assert table.c.enabled.default.arg is True
    assert table.c.credential_version.default.arg == 1
    assert table.c.last_auth_status.default.arg == "not_checked"
    assert table.c.enabled.server_default.arg == "1"
    assert table.c.credential_version.server_default.arg == "1"
    assert table.c.last_auth_status.server_default.arg == "not_checked"
    assert table.c.created_at.server_default is None
    assert table.c.updated_at.server_default is None
    for value in (created_at, updated_at, updated_onupdate):
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)
    assert all(column.onupdate is None for column in table.columns if column.name != "updated_at")


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_provider_account_import_orders_register_one_mapper(import_order: str) -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.provider_account_management.models import RailProviderAccount as Canonical
    configure_mappers()
    from rail_waitlist.models import RailProviderAccount as Legacy
else:
    from rail_waitlist.models import RailProviderAccount as Legacy
    from rail_waitlist.provider_account_management.models import RailProviderAccount as Canonical
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["rail_provider_accounts"] is Canonical.__table__,
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
            "credentials_ciphertext",
            "enabled",
            "credential_version",
            "last_auth_status",
            "last_authenticated_at",
            "created_at",
            "updated_at",
        ],
        "identity": True,
        "mapper_count": 1,
        "relationships": [],
        "table": True,
    }
