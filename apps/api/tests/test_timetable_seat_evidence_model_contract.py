from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint

from rail_waitlist.database import Base
from rail_waitlist.domain import Provider, SeatClass, SeatObservationStatus
from rail_waitlist.models import TimetableSeatEvidence as LegacyTimetableSeatEvidence
from rail_waitlist.models import WatchCandidate
from rail_waitlist.timetable_management.models import TimetableSeatEvidence

API_ROOT = Path(__file__).resolve().parents[1]


def test_timetable_seat_evidence_legacy_symbol_is_exact_canonical_object() -> None:
    assert LegacyTimetableSeatEvidence is TimetableSeatEvidence
    assert TimetableSeatEvidence.__module__ == "rail_waitlist.timetable_management.models"


def test_timetable_seat_evidence_preserves_mapper_columns_and_metadata() -> None:
    table = TimetableSeatEvidence.__table__

    assert Base.metadata.tables["timetable_seat_evidence"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is TimetableSeatEvidence for mapper in Base.registry.mappers) == 1
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
        ("id", "VARCHAR(36)", False, True, True, False),
        ("evidence_hash", "VARCHAR(64)", False, False, False, False),
        ("provider", "VARCHAR(6)", False, False, False, False),
        ("origin_node_id", "VARCHAR(80)", False, False, False, False),
        ("destination_node_id", "VARCHAR(80)", False, False, False, False),
        ("canonical_train_number", "VARCHAR(40)", False, False, False, False),
        ("departure_at", "DATETIME", False, False, False, False),
        ("passenger_count", "INTEGER", False, False, False, False),
        ("seat_class", "VARCHAR(8)", False, False, False, False),
        ("status", "VARCHAR(21)", False, False, False, False),
        ("provenance_kind", "VARCHAR(40)", False, False, False, False),
        ("source", "VARCHAR(80)", True, False, False, False),
        ("observed_at", "DATETIME", True, False, False, False),
        ("fresh_until", "DATETIME", True, False, False, False),
        ("reason", "VARCHAR(80)", True, False, False, False),
        ("registration_allowed", "BOOLEAN", False, False, True, True),
        ("created_at", "DATETIME", False, False, True, False),
        ("registration_valid_until", "DATETIME", False, False, False, False),
    ]
    assert tuple(column.name for column in table.primary_key.columns) == ("id",)
    assert table.c.provider.type.enum_class is Provider
    assert table.c.seat_class.type.enum_class is SeatClass
    assert table.c.status.type.enum_class is SeatObservationStatus
    assert table.c.registration_allowed.default.arg is False
    assert str(table.c.registration_allowed.server_default.arg) == "false"
    assert {column.name for column in table.columns if getattr(column.type, "timezone", False)} == {
        "departure_at",
        "observed_at",
        "fresh_until",
        "created_at",
        "registration_valid_until",
    }


def test_timetable_seat_evidence_preserves_constraints_index_and_relationship_shape() -> None:
    table = TimetableSeatEvidence.__table__

    assert {
        (constraint.name, str(constraint.sqltext))
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        (
            "ck_timetable_seat_evidence_provider",
            "provider IN ('KORAIL', 'SRT')",
        ),
        (
            "ck_timetable_seat_evidence_seat_class",
            "seat_class IN ('STANDARD', 'FIRST')",
        ),
        (
            "ck_timetable_seat_evidence_passenger_count",
            "passenger_count BETWEEN 1 AND 9",
        ),
        (
            "ck_timetable_seat_evidence_provenance_kind",
            "provenance_kind IN ('not_observed', 'official_provider', "
            "'official_page_browser_companion', 'user_confirmed_official_page')",
        ),
        (
            "ck_timetable_seat_evidence_provenance_shape",
            "((provenance_kind = 'not_observed' AND status = 'UNKNOWN' "
            "AND reason IS NOT NULL AND source IS NULL AND observed_at IS NULL "
            "AND fresh_until IS NULL) OR (provenance_kind <> 'not_observed' "
            "AND source IS NOT NULL AND observed_at IS NOT NULL AND reason IS NULL))",
        ),
        (
            "ck_timetable_seat_evidence_user_confirmation",
            "(provenance_kind <> 'user_confirmed_official_page' OR "
            "(source = 'official-page-user-confirmation' AND fresh_until IS NOT NULL "
            "AND fresh_until > observed_at))",
        ),
        (
            "ck_timetable_seat_evidence_browser_companion",
            "(provenance_kind <> 'official_page_browser_companion' OR "
            "(source = 'korail-official-browser-companion' AND fresh_until IS NOT NULL "
            "AND fresh_until > observed_at))",
        ),
        (
            "ck_timetable_seat_evidence_registration_window",
            "registration_valid_until > created_at",
        ),
    }
    unique_constraints = {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints == {("uq_timetable_seat_evidence_hash", ("evidence_hash",))}
    assert {
        (index.name, tuple(column.name for column in index.columns), index.unique)
        for index in table.indexes
    } == {
        (
            "ix_timetable_seat_evidence_identity",
            (
                "provider",
                "origin_node_id",
                "destination_node_id",
                "departure_at",
                "passenger_count",
                "seat_class",
            ),
            False,
        )
    }
    assert all(not column.foreign_keys for column in table.columns)
    assert list(TimetableSeatEvidence.__mapper__.relationships.keys()) == []

    relationship = WatchCandidate.__mapper__.relationships["registration_evidence"]
    assert relationship.mapper.class_ is TimetableSeatEvidence
    registration_evidence_id = WatchCandidate.__table__.c.registration_evidence_id
    assert {
        foreign_key.target_fullname for foreign_key in registration_evidence_id.foreign_keys
    } == {"timetable_seat_evidence.id"}


def test_timetable_seat_evidence_preserves_aware_defaults_and_provenance_projection() -> None:
    created_at = TimetableSeatEvidence.__table__.c.created_at.default.arg(None)
    assert created_at.tzinfo is not None
    assert created_at.utcoffset() == timedelta(0)

    naive_observed_at = datetime(2030, 8, 1, 0, 0)
    aware_fresh_until = datetime(2030, 8, 1, 0, 5, tzinfo=UTC)
    evidence = TimetableSeatEvidence(
        evidence_hash="e" * 64,
        provider=Provider.KORAIL,
        origin_node_id="0010",
        destination_node_id="0001",
        canonical_train_number="26",
        departure_at=datetime(2030, 8, 1, 8, 30, tzinfo=UTC),
        passenger_count=1,
        seat_class=SeatClass.STANDARD,
        status=SeatObservationStatus.AVAILABLE,
        provenance_kind="official_provider",
        source="authorized-provider",
        observed_at=naive_observed_at,
        fresh_until=aware_fresh_until,
        reason=None,
        registration_allowed=True,
        registration_valid_until=datetime(2030, 8, 1, 0, 5, tzinfo=UTC),
    )

    assert evidence.provenance == {
        "kind": "official_provider",
        "source": "authorized-provider",
        "observed_at": naive_observed_at.replace(tzinfo=UTC),
        "fresh_until": aware_fresh_until,
        "reason": None,
    }


def test_timetable_seat_evidence_import_orders_register_one_mapper() -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management.models import TimetableSeatEvidence as Canonical
    configure_mappers()
    from rail_waitlist.models import TimetableSeatEvidence as Legacy, WatchCandidate
else:
    from rail_waitlist.models import TimetableSeatEvidence as Legacy, WatchCandidate
    from rail_waitlist.timetable_management.models import TimetableSeatEvidence as Canonical
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identity": Legacy is Canonical,
    "table": Base.metadata.tables["timetable_seat_evidence"] is Canonical.__table__,
    "mapper_count": sum(mapper.class_ is Canonical for mapper in Base.registry.mappers),
    "columns": [column.name for column in Canonical.__table__.columns],
    "candidate_relationship": (
        WatchCandidate.__mapper__.relationships["registration_evidence"].mapper.class_
        is Canonical
    ),
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
            "candidate_relationship": True,
            "columns": [
                "id",
                "evidence_hash",
                "provider",
                "origin_node_id",
                "destination_node_id",
                "canonical_train_number",
                "departure_at",
                "passenger_count",
                "seat_class",
                "status",
                "provenance_kind",
                "source",
                "observed_at",
                "fresh_until",
                "reason",
                "registration_allowed",
                "created_at",
                "registration_valid_until",
            ],
            "identity": True,
            "mapper_count": 1,
            "table": True,
        }
