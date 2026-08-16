from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy import inspect as sqlalchemy_inspect

from rail_waitlist import models as legacy_models
from rail_waitlist.browser_companion import models as companion_models
from rail_waitlist.database import Base

API_ROOT = Path(__file__).resolve().parents[1]
MODEL_NAMES = (
    "KorailBrowserSnapshotBatch",
    "KorailBrowserSeatSnapshot",
    "BrowserCompanionPairing",
    "BrowserCompanionCredential",
    "BrowserCompanionChallenge",
)


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


EXPECTED_COLUMNS = {
    "korail_browser_snapshot_batches": [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("credential_id", "VARCHAR(36)", True, False, None, None, False, False),
        ("challenge_id", "VARCHAR(36)", True, False, True, None, False, False),
        ("origin", "VARCHAR(40)", False, False, None, None, False, False),
        ("destination", "VARCHAR(40)", False, False, None, None, False, False),
        ("travel_date", "DATE", False, False, None, None, False, False),
        ("passenger_count", "INTEGER", False, False, None, None, False, False),
        ("source", "VARCHAR(80)", False, False, None, None, False, False),
        ("observed_at", "DATETIME", False, False, None, None, False, False),
        ("fresh_until", "DATETIME", False, False, None, None, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
    ],
    "korail_browser_seat_snapshots": [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("batch_id", "VARCHAR(36)", False, False, None, None, False, False),
        ("train_number", "VARCHAR(40)", False, False, None, None, False, False),
        ("departure_at", "DATETIME", False, False, None, None, False, False),
        ("seat_class", "VARCHAR(8)", False, False, None, None, False, False),
        ("status", "VARCHAR(21)", False, False, None, None, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
    ],
    "browser_companion_pairings": [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("code_hash", "VARCHAR(64)", False, False, True, True, False, False),
        ("label", "VARCHAR(80)", False, False, None, None, False, False),
        ("expires_at", "DATETIME", False, False, None, True, False, False),
        ("consumed_at", "DATETIME", True, False, None, None, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
    ],
    "browser_companion_credentials": [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("token_hash", "VARCHAR(64)", False, False, True, True, False, False),
        ("extension_origin", "VARCHAR(100)", False, False, None, None, False, False),
        ("client_id", "VARCHAR(36)", False, False, None, None, False, False),
        ("label", "VARCHAR(80)", False, False, None, None, False, False),
        ("window_started_at", "DATETIME", True, False, None, None, False, False),
        ("accepted_in_window", "INTEGER", False, False, None, None, True, False),
        ("last_used_at", "DATETIME", True, False, None, None, False, False),
        ("revoked_at", "DATETIME", True, False, None, True, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
    ],
    "browser_companion_challenges": [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("credential_id", "VARCHAR(36)", False, False, None, None, False, False),
        ("challenge_hash", "VARCHAR(64)", False, False, True, True, False, False),
        ("method", "VARCHAR(8)", False, False, None, None, False, False),
        ("path", "VARCHAR(160)", False, False, None, None, False, False),
        ("body_sha256", "VARCHAR(64)", False, False, None, None, False, False),
        ("expires_at", "DATETIME", False, False, None, True, False, False),
        ("consumed_at", "DATETIME", True, False, None, None, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
    ],
}


def test_legacy_browser_companion_models_are_exact_canonical_objects() -> None:
    for name in MODEL_NAMES:
        assert getattr(legacy_models, name) is getattr(companion_models, name)


def test_browser_companion_models_preserve_metadata_columns_and_single_mappers() -> None:
    for name in MODEL_NAMES:
        model = getattr(companion_models, name)
        table = model.__table__
        assert Base.metadata.tables[table.name] is table
        assert table.metadata is Base.metadata
        assert sum(mapper.class_ is model for mapper in Base.registry.mappers) == 1
        assert _column_fingerprint(model) == EXPECTED_COLUMNS[table.name]


def test_browser_companion_constraints_indexes_foreign_keys_and_relationships() -> None:
    batch = companion_models.KorailBrowserSnapshotBatch.__table__
    seat = companion_models.KorailBrowserSeatSnapshot.__table__
    pairing = companion_models.BrowserCompanionPairing.__table__
    credential = companion_models.BrowserCompanionCredential.__table__
    challenge = companion_models.BrowserCompanionChallenge.__table__

    assert {
        (item.name, str(item.sqltext))
        for item in batch.constraints
        if isinstance(item, CheckConstraint)
    } == {
        ("ck_korail_browser_snapshot_batch_source", "source = 'korail-official-browser-companion'"),
        ("ck_korail_browser_snapshot_batch_passenger_count", "passenger_count BETWEEN 1 AND 9"),
        ("ck_korail_browser_snapshot_batch_freshness_order", "fresh_until > observed_at"),
    }
    assert {
        (item.name, str(item.sqltext))
        for item in seat.constraints
        if isinstance(item, CheckConstraint)
    } == {
        ("ck_korail_browser_snapshot_seat_class", "seat_class IN ('STANDARD', 'FIRST')"),
        (
            "ck_korail_browser_snapshot_status",
            (
                "status IN ('AVAILABLE', 'LIMITED', 'STANDING_PLUS_SEAT', 'STANDING_ONLY', "
                "'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED')"
            ),
        ),
    }
    assert {
        (item.name, tuple(column.name for column in item.columns))
        for item in seat.constraints
        if isinstance(item, UniqueConstraint)
    } == {
        (
            "uq_korail_browser_snapshot_batch_train_seat",
            ("batch_id", "train_number", "seat_class"),
        )
    }
    assert {
        table.name: {
            (index.name, index.unique, tuple(column.name for column in index.columns))
            for index in table.indexes
        }
        for table in (batch, seat, pairing, credential, challenge)
    } == {
        "korail_browser_snapshot_batches": {
            (
                "ix_korail_browser_snapshot_batch_route_fresh",
                False,
                (
                    "origin",
                    "destination",
                    "travel_date",
                    "passenger_count",
                    "fresh_until",
                    "observed_at",
                ),
            )
        },
        "korail_browser_seat_snapshots": {
            (
                "ix_korail_browser_snapshot_identity",
                False,
                ("train_number", "departure_at", "seat_class"),
            )
        },
        "browser_companion_pairings": {
            ("ix_browser_companion_pairings_code_hash", True, ("code_hash",)),
            ("ix_browser_companion_pairings_expires_at", False, ("expires_at",)),
        },
        "browser_companion_credentials": {
            (
                "ix_browser_companion_credential_installation",
                False,
                ("extension_origin", "client_id"),
            ),
            ("ix_browser_companion_credentials_revoked_at", False, ("revoked_at",)),
            ("ix_browser_companion_credentials_token_hash", True, ("token_hash",)),
        },
        "browser_companion_challenges": {
            (
                "ix_browser_companion_challenge_active",
                False,
                ("credential_id", "expires_at", "consumed_at"),
            ),
            ("ix_browser_companion_challenges_challenge_hash", True, ("challenge_hash",)),
            ("ix_browser_companion_challenges_expires_at", False, ("expires_at",)),
        },
    }

    assert {
        (column.name, fk.target_fullname, fk.ondelete)
        for table in (batch, seat, pairing, credential, challenge)
        for column in table.columns
        for fk in column.foreign_keys
    } == {
        ("credential_id", "browser_companion_credentials.id", "SET NULL"),
        ("batch_id", "korail_browser_snapshot_batches.id", "CASCADE"),
        ("credential_id", "browser_companion_credentials.id", "CASCADE"),
    }

    snapshots = sqlalchemy_inspect(companion_models.KorailBrowserSnapshotBatch).relationships[
        "snapshots"
    ]
    batch_relation = sqlalchemy_inspect(companion_models.KorailBrowserSeatSnapshot).relationships[
        "batch"
    ]
    assert snapshots.back_populates == "batch"
    assert snapshots.passive_deletes is True
    assert "delete-orphan" in snapshots.cascade
    assert batch_relation.back_populates == "snapshots"


def test_browser_companion_python_defaults_preserve_uuid_utc_and_no_server_default() -> None:
    for name in MODEL_NAMES:
        table = getattr(companion_models, name).__table__
        id_value = table.c.id.default.arg(None)
        created_at = table.c.created_at.default.arg(None)
        assert str(UUID(id_value)) == id_value
        assert created_at.tzinfo is not None
        assert created_at.utcoffset() == timedelta(0)
        assert table.c.created_at.server_default is None

    accepted = companion_models.BrowserCompanionCredential.__table__.c.accepted_in_window
    assert accepted.default.arg == 0
    assert accepted.server_default is None


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_browser_companion_model_import_orders_configure_each_mapper_once(
    import_order: str,
) -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.browser_companion import models as canonical
    configure_mappers()
    from rail_waitlist import models as legacy
else:
    from rail_waitlist import models as legacy
    from rail_waitlist.browser_companion import models as canonical
configure_mappers()

from rail_waitlist.database import Base
names = (
    "KorailBrowserSnapshotBatch",
    "KorailBrowserSeatSnapshot",
    "BrowserCompanionPairing",
    "BrowserCompanionCredential",
    "BrowserCompanionChallenge",
)
result = {
    "identity": all(getattr(legacy, name) is getattr(canonical, name) for name in names),
    "tables": all(
        Base.metadata.tables[getattr(canonical, name).__table__.name]
        is getattr(canonical, name).__table__
        for name in names
    ),
    "mappers": [
        sum(mapper.class_ is getattr(canonical, name) for mapper in Base.registry.mappers)
        for name in names
    ],
    "relationships": sorted(
        relationship.key
        for name in names
        for relationship in getattr(canonical, name).__mapper__.relationships
    ),
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
        "identity": True,
        "mappers": [1, 1, 1, 1, 1],
        "relationships": ["batch", "snapshots"],
        "tables": True,
    }
