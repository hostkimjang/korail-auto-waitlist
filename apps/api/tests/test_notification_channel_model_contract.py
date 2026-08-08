from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import CheckConstraint, DateTime, Enum, UniqueConstraint

from rail_waitlist.database import Base
from rail_waitlist.domain import NotificationKind
from rail_waitlist.models import NativePushCredential as LegacyNativePushCredential
from rail_waitlist.models import NativePushPairing as LegacyNativePushPairing
from rail_waitlist.models import NotificationChannel as LegacyNotificationChannel
from rail_waitlist.notification_management.models import (
    NativePushCredential,
    NativePushPairing,
    NotificationChannel,
)

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


def test_legacy_notification_channel_is_the_exact_canonical_object() -> None:
    assert LegacyNotificationChannel is NotificationChannel
    assert NotificationChannel.__module__ == "rail_waitlist.notification_management.models"


def test_retired_native_push_models_are_exact_canonical_objects() -> None:
    assert LegacyNativePushPairing is NativePushPairing
    assert LegacyNativePushCredential is NativePushCredential
    assert NativePushPairing.__module__ == "rail_waitlist.notification_management.models"
    assert NativePushCredential.__module__ == "rail_waitlist.notification_management.models"


def test_retired_native_push_models_preserve_migration_metadata() -> None:
    pairing_table = NativePushPairing.__table__
    credential_table = NativePushCredential.__table__
    assert Base.metadata.tables["native_push_pairings"] is pairing_table
    assert Base.metadata.tables["native_push_credentials"] is credential_table
    assert sum(mapper.class_ is NativePushPairing for mapper in Base.registry.mappers) == 1
    assert sum(mapper.class_ is NativePushCredential for mapper in Base.registry.mappers) == 1

    assert _column_fingerprint(NativePushPairing) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False, False),
        ("code_hash", "VARCHAR(64)", False, False, None, None, False, False, False),
        ("kind", "VARCHAR(15)", False, False, None, None, False, False, False),
        ("expires_at", "DATETIME", False, False, None, None, False, False, False),
        ("consumed_at", "DATETIME", True, False, None, None, False, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False, False),
    ]
    assert _column_fingerprint(NativePushCredential) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False, False),
        ("token_hash", "VARCHAR(64)", False, False, None, None, False, False, False),
        ("channel_id", "VARCHAR(36)", False, False, None, None, False, False, False),
        ("kind", "VARCHAR(15)", False, False, None, None, False, False, False),
        ("last_used_at", "DATETIME", True, False, None, None, False, False, False),
        ("revoked_at", "DATETIME", True, False, None, None, False, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False, False),
    ]

    assert {
        index.name: (index.unique, tuple(column.name for column in index.columns))
        for index in pairing_table.indexes
    } == {
        "ix_native_push_pairings_code_hash": (True, ("code_hash",)),
        "ix_native_push_pairings_expires_at": (False, ("expires_at",)),
    }
    assert {
        index.name: (index.unique, tuple(column.name for column in index.columns))
        for index in credential_table.indexes
    } == {
        "ix_native_push_credentials_token_hash": (True, ("token_hash",)),
        "ix_native_push_credentials_channel_id": (True, ("channel_id",)),
        "ix_native_push_credentials_revoked_at": (False, ("revoked_at",)),
    }
    foreign_key = next(iter(credential_table.c.channel_id.foreign_keys))
    assert foreign_key.target_fullname == "notification_channels.id"
    assert foreign_key.ondelete == "RESTRICT"
    assert list(NativePushPairing.__mapper__.relationships.keys()) == []
    assert list(NativePushCredential.__mapper__.relationships.keys()) == []

    for table in (pairing_table, credential_table):
        kind_type = table.c.kind.type
        assert isinstance(kind_type, Enum)
        assert kind_type.enum_class is NotificationKind
        assert kind_type.name == "notificationkind"
        assert tuple(kind_type.enums) == (
            "WEB_PUSH",
            "ANDROID_FCM",
            "IOS_APNS",
            "TELEGRAM",
            "DISCORD_WEBHOOK",
            "GENERIC_WEBHOOK",
        )
        assert kind_type.native_enum is False
        assert all(column.server_default is None for column in table.columns)

    for table, datetime_columns in (
        (pairing_table, ("expires_at", "consumed_at", "created_at")),
        (credential_table, ("last_used_at", "revoked_at", "created_at")),
    ):
        for name in datetime_columns:
            date_type = table.c[name].type
            assert isinstance(date_type, DateTime)
            assert date_type.timezone is True


def test_notification_channel_preserves_mapper_columns_and_metadata() -> None:
    table = NotificationChannel.__table__
    assert Base.metadata.tables["notification_channels"] is table
    assert table.metadata is Base.metadata
    assert sum(mapper.class_ is NotificationChannel for mapper in Base.registry.mappers) == 1
    assert _column_fingerprint(NotificationChannel) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False, False),
        ("kind", "VARCHAR(15)", False, False, None, None, False, False, False),
        ("name", "VARCHAR(80)", False, False, None, None, False, False, False),
        ("config_ciphertext", "TEXT", False, False, None, None, False, False, False),
        ("web_push_device_key", "VARCHAR(43)", True, False, None, None, False, False, False),
        ("enabled", "BOOLEAN", False, False, None, None, True, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False, False),
        ("updated_at", "DATETIME", False, False, None, None, True, False, True),
    ]
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone is True


def test_notification_channel_preserves_enum_and_absent_relational_constraints() -> None:
    table = NotificationChannel.__table__
    kind_type = table.c.kind.type
    assert kind_type.enum_class is NotificationKind
    assert kind_type.name == "notificationkind"
    assert tuple(kind_type.enums) == (
        "WEB_PUSH",
        "ANDROID_FCM",
        "IOS_APNS",
        "TELEGRAM",
        "DISCORD_WEBHOOK",
        "GENERIC_WEBHOOK",
    )
    assert kind_type.native_enum is False
    assert {
        constraint for constraint in table.constraints if isinstance(constraint, CheckConstraint)
    } == set()
    assert {
        constraint for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    } == set()
    assert {
        index.name: (index.unique, tuple(column.name for column in index.columns))
        for index in table.indexes
    } == {
        "ix_notification_channels_web_push_device_key": (
            True,
            ("web_push_device_key",),
        )
    }
    assert all(not column.foreign_keys for column in table.columns)
    assert list(NotificationChannel.__mapper__.relationships.keys()) == []


def test_notification_channel_preserves_python_defaults_and_onupdate() -> None:
    table = NotificationChannel.__table__
    id_value = table.c.id.default.arg(None)
    created_at = table.c.created_at.default.arg(None)
    updated_at = table.c.updated_at.default.arg(None)
    updated_onupdate = table.c.updated_at.onupdate.arg(None)

    assert str(UUID(id_value)) == id_value
    assert table.c.enabled.default.arg is True
    for value in (created_at, updated_at, updated_onupdate):
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)
    assert all(column.server_default is None for column in table.columns)
    assert all(column.onupdate is None for column in table.columns if column.name != "updated_at")


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_notification_model_import_orders_register_one_mapper(import_order: str) -> None:
    script = r"""
import json
import sys
from sqlalchemy.orm import configure_mappers

if sys.argv[1] == "canonical-first":
    from rail_waitlist.notification_management.models import (
        NativePushCredential as CanonicalCredential,
        NativePushPairing as CanonicalPairing,
        NotificationChannel as CanonicalChannel,
    )
    configure_mappers()
    from rail_waitlist.models import (
        NativePushCredential as LegacyCredential,
        NativePushPairing as LegacyPairing,
        NotificationChannel as LegacyChannel,
    )
else:
    from rail_waitlist.models import (
        NativePushCredential as LegacyCredential,
        NativePushPairing as LegacyPairing,
        NotificationChannel as LegacyChannel,
    )
    from rail_waitlist.notification_management.models import (
        NativePushCredential as CanonicalCredential,
        NativePushPairing as CanonicalPairing,
        NotificationChannel as CanonicalChannel,
    )
configure_mappers()

from rail_waitlist.database import Base

print(json.dumps({
    "identities": [
        LegacyChannel is CanonicalChannel,
        LegacyPairing is CanonicalPairing,
        LegacyCredential is CanonicalCredential,
    ],
    "tables": [
        Base.metadata.tables["notification_channels"] is CanonicalChannel.__table__,
        Base.metadata.tables["native_push_pairings"] is CanonicalPairing.__table__,
        Base.metadata.tables["native_push_credentials"] is CanonicalCredential.__table__,
    ],
    "mapper_counts": [
        sum(mapper.class_ is model for mapper in Base.registry.mappers)
        for model in (CanonicalChannel, CanonicalPairing, CanonicalCredential)
    ],
    "relationships": [
        list(model.__mapper__.relationships.keys())
        for model in (CanonicalChannel, CanonicalPairing, CanonicalCredential)
    ],
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
        "identities": [True, True, True],
        "mapper_counts": [1, 1, 1],
        "relationships": [[], [], []],
        "tables": [True, True, True],
    }
