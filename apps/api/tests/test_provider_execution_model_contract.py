from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import CheckConstraint

import rail_waitlist.provider_execution_lease as legacy_lease_module
from rail_waitlist.database import Base
from rail_waitlist.domain import Provider
from rail_waitlist.models import ProviderExecutionLease as LegacyProviderExecutionLease
from rail_waitlist.provider_execution import contracts as canonical_contracts
from rail_waitlist.provider_execution import lease_application as canonical_application
from rail_waitlist.provider_execution.models import ProviderExecutionLease

API_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_provider_execution_symbols_are_exact_canonical_objects() -> None:
    assert LegacyProviderExecutionLease is ProviderExecutionLease
    assert ProviderExecutionLease.__module__ == "rail_waitlist.provider_execution.models"
    assert legacy_lease_module.ExecutionLeaseGrant is canonical_contracts.ExecutionLeaseGrant
    assert (
        legacy_lease_module.ProviderExecutionLeaseService
        is canonical_application.ProviderExecutionLeaseService
    )
    assert (
        legacy_lease_module.lock_execution_lease_current
        is canonical_application.lock_execution_lease_current
    )
    assert (
        legacy_lease_module.acquire_anonymous_public_execution_lease
        is canonical_application.acquire_anonymous_public_execution_lease
    )
    assert (
        legacy_lease_module.ExecutionLeaseAcquisitionDependencies
        is canonical_application.ExecutionLeaseAcquisitionDependencies
    )


def test_provider_execution_lease_preserves_mapper_columns_and_metadata() -> None:
    table = ProviderExecutionLease.__table__
    assert Base.metadata.tables["provider_execution_leases"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is ProviderExecutionLease for mapper in Base.registry.mappers) == 1
    assert [
        (
            column.name,
            str(column.type),
            column.nullable,
            column.primary_key,
            column.default is not None,
            column.server_default is not None,
        )
        for column in table.columns
    ] == [
        ("provider", "VARCHAR(6)", False, True, False, False),
        ("account_scope", "VARCHAR(128)", False, True, False, False),
        ("owner_token", "VARCHAR(128)", True, False, False, False),
        ("fencing_token", "BIGINT", False, False, False, False),
        ("expires_at", "DATETIME", True, False, False, False),
        ("updated_at", "DATETIME", False, False, True, False),
    ]
    assert tuple(column.name for column in table.primary_key.columns) == (
        "provider",
        "account_scope",
    )
    assert table.c.expires_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True


def test_provider_execution_lease_preserves_checks_index_and_enum_storage() -> None:
    table = ProviderExecutionLease.__table__
    assert {
        (constraint.name, str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        (
            "ck_provider_execution_lease_provider_allowed",
            "provider IN ('KORAIL', 'SRT')",
        ),
        (
            "ck_provider_execution_lease_scope_nonempty",
            "length(trim(account_scope)) > 0",
        ),
        ("ck_provider_execution_lease_fencing_positive", "fencing_token >= 1"),
        (
            "ck_provider_execution_lease_owner_expiry_shape",
            "((owner_token IS NULL AND expires_at IS NULL) OR "
            "(owner_token IS NOT NULL AND expires_at IS NOT NULL))",
        ),
    }
    assert {
        (index.name, index.unique, tuple(column.name for column in index.columns))
        for index in table.indexes
    } == {("ix_provider_execution_leases_expires_at", False, ("expires_at",))}
    provider_type = table.c.provider.type
    assert provider_type.enum_class is Provider
    assert provider_type.name == "provider"
    assert tuple(provider_type.enums) == ("KORAIL", "SRT", "MOCK")
    assert provider_type.native_enum is False
    assert all(not column.foreign_keys for column in table.columns)
    assert list(ProviderExecutionLease.__mapper__.relationships.keys()) == []


def test_provider_execution_lease_preserves_aware_updated_at_default() -> None:
    table = ProviderExecutionLease.__table__
    updated_at = table.c.updated_at.default.arg(None)

    assert updated_at.tzinfo is not None
    assert updated_at.utcoffset() == timedelta(0)
    assert all(column.onupdate is None for column in table.columns)


def test_execution_lease_grant_hides_owner_token_from_repr() -> None:
    grant = canonical_contracts.ExecutionLeaseGrant(
        provider=Provider.KORAIL,
        account_scope="anonymous/public",
        owner_token="must-not-appear",
        fencing_token=1,
        expires_at=datetime(2026, 8, 6, 1, tzinfo=UTC),
    )

    assert "must-not-appear" not in repr(grant)


def test_provider_execution_import_orders_register_one_mapper() -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.provider_execution.models import ProviderExecutionLease as Canonical
    configure_mappers()
    from rail_waitlist.models import ProviderExecutionLease as Legacy
else:
    from rail_waitlist.models import ProviderExecutionLease as Legacy
    from rail_waitlist.provider_execution.models import ProviderExecutionLease as Canonical
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["provider_execution_leases"] is Canonical.__table__,
    "mapper_count": sum(mapper.class_ is Canonical for mapper in Base.registry.mappers),
    "columns": [column.name for column in Canonical.__table__.columns],
    "relationships": list(Canonical.__mapper__.relationships.keys()),
}, sort_keys=True))
"""

    for import_order in ("canonical-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "columns": [
                "provider",
                "account_scope",
                "owner_token",
                "fencing_token",
                "expires_at",
                "updated_at",
            ],
            "identity": True,
            "mapper_count": 1,
            "relationships": [],
            "table": True,
        }
