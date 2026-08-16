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
from rail_waitlist.domain import Provider, SeatClass, SeatObservationStatus
from rail_waitlist.models import OfficialPageSeatConfirmation as LegacyConfirmation
from rail_waitlist.official_page_confirmation.models import OfficialPageSeatConfirmation

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


def test_legacy_official_page_confirmation_is_the_exact_canonical_object() -> None:
    assert LegacyConfirmation is OfficialPageSeatConfirmation
    assert (
        OfficialPageSeatConfirmation.__module__ == "rail_waitlist.official_page_confirmation.models"
    )


def test_official_page_confirmation_preserves_mapper_columns_and_metadata() -> None:
    table = OfficialPageSeatConfirmation.__table__
    assert Base.metadata.tables["official_page_seat_confirmations"] is table
    assert table.metadata is Base.metadata
    assert (
        sum(mapper.class_ is OfficialPageSeatConfirmation for mapper in Base.registry.mappers) == 1
    )
    assert _column_fingerprint(OfficialPageSeatConfirmation) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False, False),
        ("batch_id", "VARCHAR(36)", False, False, None, None, False, False, False),
        ("provider", "VARCHAR(6)", False, False, None, None, False, False, False),
        ("origin_node_id", "VARCHAR(80)", False, False, None, None, False, False, False),
        (
            "destination_node_id",
            "VARCHAR(80)",
            False,
            False,
            None,
            None,
            False,
            False,
            False,
        ),
        ("train_number", "VARCHAR(40)", False, False, None, None, False, False, False),
        ("departure_at", "DATETIME", False, False, None, None, False, False, False),
        ("passenger_count", "INTEGER", False, False, None, None, False, False, False),
        ("seat_class", "VARCHAR(8)", False, False, None, None, False, False, False),
        ("status", "VARCHAR(21)", False, False, None, None, False, False, False),
        ("source", "VARCHAR(80)", False, False, None, None, False, False, False),
        ("observed_at", "DATETIME", False, False, None, None, False, False, False),
        ("fresh_until", "DATETIME", False, False, None, None, False, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False, False),
    ]
    for name in ("departure_at", "observed_at", "fresh_until", "created_at"):
        assert table.c[name].type.timezone is True


def test_official_page_confirmation_preserves_constraints_indexes_and_enums() -> None:
    table = OfficialPageSeatConfirmation.__table__
    assert {
        (constraint.name, str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        ("ck_official_page_confirmation_provider", "provider IN ('KORAIL', 'SRT')"),
        (
            "ck_official_page_confirmation_seat_class",
            "seat_class IN ('STANDARD', 'FIRST')",
        ),
        (
            "ck_official_page_confirmation_status",
            "status IN ('AVAILABLE', 'SOLD_OUT', 'WAITLIST_AVAILABLE', 'NOT_OFFERED')",
        ),
        (
            "ck_official_page_confirmation_source",
            "source = 'official-page-user-confirmation'",
        ),
        ("ck_official_page_confirmation_freshness_order", "fresh_until > observed_at"),
        (
            "ck_official_page_confirmation_passenger_count",
            "passenger_count BETWEEN 1 AND 9",
        ),
    }
    assert {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        (
            "uq_official_page_confirmation_batch_seat_class",
            ("batch_id", "seat_class"),
        )
    }
    assert {
        (index.name, index.unique, tuple(column.name for column in index.columns))
        for index in table.indexes
    } == {
        ("ix_official_page_confirmation_batch_id", False, ("batch_id",)),
        (
            "ix_official_page_confirmation_route_fresh",
            False,
            (
                "provider",
                "origin_node_id",
                "destination_node_id",
                "passenger_count",
                "departure_at",
                "fresh_until",
                "observed_at",
            ),
        ),
    }
    assert all(not column.foreign_keys for column in table.columns)
    assert list(OfficialPageSeatConfirmation.__mapper__.relationships.keys()) == []

    expected_enums = {
        "provider": (
            Provider,
            "provider",
            ("KORAIL", "SRT", "MOCK"),
        ),
        "seat_class": (
            SeatClass,
            "seatclass",
            ("STANDARD", "FIRST", "INFANT", "FREE", "WAITLIST", "ANY"),
        ),
        "status": (
            SeatObservationStatus,
            "seatobservationstatus",
            (
                "UNAVAILABLE",
                "UNKNOWN",
                "AVAILABLE",
                "LIMITED",
                "STANDING_PLUS_SEAT",
                "STANDING_ONLY",
                "NOT_ENOUGH_SEATS",
                "SOLD_OUT",
                "WAITLIST_AVAILABLE",
                "RESERVATION_COMPLETED",
                "NOT_OFFERED",
                "DEPARTED",
                "OUT_OF_SERVICE",
                "STALE",
                "ERROR",
            ),
        ),
    }
    for column_name, (enum_class, enum_name, values) in expected_enums.items():
        enum_type = table.c[column_name].type
        assert enum_type.enum_class is enum_class
        assert enum_type.name == enum_name
        assert tuple(enum_type.enums) == values
        assert enum_type.native_enum is False


def test_official_page_confirmation_preserves_python_defaults_only() -> None:
    table = OfficialPageSeatConfirmation.__table__
    id_value = table.c.id.default.arg(None)
    created_at = table.c.created_at.default.arg(None)

    assert str(UUID(id_value)) == id_value
    assert created_at.tzinfo is not None
    assert created_at.utcoffset() == timedelta(0)
    assert all(column.server_default is None for column in table.columns)
    assert all(column.onupdate is None for column in table.columns)


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_official_page_model_import_orders_register_one_mapper(import_order: str) -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.official_page_confirmation.models import (
        OfficialPageSeatConfirmation as Canonical,
    )
    configure_mappers()
    from rail_waitlist.models import OfficialPageSeatConfirmation as Legacy
else:
    from rail_waitlist.models import OfficialPageSeatConfirmation as Legacy
    from rail_waitlist.official_page_confirmation.models import (
        OfficialPageSeatConfirmation as Canonical,
    )
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["official_page_seat_confirmations"] is Canonical.__table__,
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
            "batch_id",
            "provider",
            "origin_node_id",
            "destination_node_id",
            "train_number",
            "departure_at",
            "passenger_count",
            "seat_class",
            "status",
            "source",
            "observed_at",
            "fresh_until",
            "created_at",
        ],
        "identity": True,
        "mapper_count": 1,
        "relationships": [],
        "table": True,
    }
