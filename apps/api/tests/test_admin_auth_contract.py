from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from rail_waitlist.admin_auth.models import (
    AdminAccount as CanonicalAdminAccount,
)
from rail_waitlist.admin_auth.models import (
    AdminSession as CanonicalAdminSession,
)
from rail_waitlist.admin_auth.schemas import AuthStatus as CanonicalAuthStatus
from rail_waitlist.admin_auth.schemas import LoginResult as CanonicalLoginResult
from rail_waitlist.admin_auth.schemas import (
    UsernamePasswordCredentials as CanonicalUsernamePasswordCredentials,
)
from rail_waitlist.database import Base
from rail_waitlist.models import AdminAccount, AdminSession
from rail_waitlist.schemas import AuthStatus, LoginResult, UsernamePasswordCredentials

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


def test_legacy_admin_auth_exports_are_exact_canonical_objects() -> None:
    assert AdminAccount is CanonicalAdminAccount
    assert AdminSession is CanonicalAdminSession
    assert AuthStatus is CanonicalAuthStatus
    assert LoginResult is CanonicalLoginResult
    assert UsernamePasswordCredentials is CanonicalUsernamePasswordCredentials


def test_admin_auth_schema_contract_is_preserved() -> None:
    credentials = CanonicalUsernamePasswordCredentials(
        username="  Rail.Admin  ",
        password="correct horse battery staple",
    )
    assert credentials.username == "rail.admin"
    assert credentials.password == "correct horse battery staple"

    naive_expiry = datetime(2026, 8, 6, 12, 30)  # noqa: DTZ001 - naive compatibility case
    status = CanonicalAuthStatus.model_validate(
        SimpleNamespace(
            configured=True,
            authenticated=True,
            registration_allowed=False,
            session_expires_at=naive_expiry,
        )
    )
    assert status.session_expires_at == naive_expiry.replace(tzinfo=UTC)

    seoul = timezone(timedelta(hours=9))
    aware_expiry = datetime(2026, 8, 6, 21, 30, tzinfo=seoul)
    assert (
        CanonicalAuthStatus(
            configured=True,
            authenticated=True,
            registration_allowed=False,
            session_expires_at=aware_expiry,
        ).session_expires_at
        == aware_expiry
    )

    login_expiry = CanonicalLoginResult(
        authenticated=True,
        expires_at=naive_expiry,
    ).expires_at
    assert login_expiry.tzinfo is None


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("ab", "correct horse battery staple"),
        ("a" * 65, "correct horse battery staple"),
        ("관리자", "correct horse battery staple"),
        ("admin", "too-short"),
        ("admin", "x" * 129),
    ],
)
def test_admin_credentials_reject_values_outside_the_existing_bounds(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValidationError):
        CanonicalUsernamePasswordCredentials(username=username, password=password)


def test_admin_models_keep_the_existing_mapper_and_metadata_contract() -> None:
    assert Base.metadata.tables["admin_accounts"] is CanonicalAdminAccount.__table__
    assert Base.metadata.tables["admin_sessions"] is CanonicalAdminSession.__table__
    assert CanonicalAdminAccount.__table__.metadata is Base.metadata
    assert CanonicalAdminSession.__table__.metadata is Base.metadata
    assert sum(mapper.class_ is CanonicalAdminAccount for mapper in Base.registry.mappers) == 1
    assert sum(mapper.class_ is CanonicalAdminSession for mapper in Base.registry.mappers) == 1

    assert _column_fingerprint(CanonicalAdminAccount) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("singleton_slot", "INTEGER", False, False, True, None, True, False),
        ("username", "VARCHAR(64)", False, False, True, None, False, False),
        ("password_hash", "VARCHAR(512)", False, False, None, None, False, False),
        (
            "timetable_refresh_interval_seconds",
            "INTEGER",
            False,
            False,
            None,
            None,
            True,
            True,
        ),
        (
            "observation_interval_seconds",
            "INTEGER",
            False,
            False,
            None,
            None,
            True,
            True,
        ),
        ("preferences_updated_at", "DATETIME", False, False, None, None, True, True),
        ("created_at", "DATETIME", False, False, None, None, True, False),
        ("password_changed_at", "DATETIME", False, False, None, None, True, False),
        ("last_login_at", "DATETIME", True, False, None, None, False, False),
    ]
    assert _column_fingerprint(CanonicalAdminSession) == [
        ("id", "VARCHAR(36)", False, True, None, None, True, False),
        ("token_hash", "VARCHAR(64)", False, False, True, True, False, False),
        ("csrf_hash", "VARCHAR(64)", False, False, None, None, False, False),
        ("expires_at", "DATETIME", False, False, None, True, False, False),
        ("revoked_at", "DATETIME", True, False, None, None, False, False),
        ("created_at", "DATETIME", False, False, None, None, True, False),
        ("last_seen_at", "DATETIME", False, False, None, None, True, False),
    ]

    check_constraints = {
        (constraint.name, str(constraint.sqltext))
        for constraint in CanonicalAdminAccount.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_constraints == {
        ("ck_admin_account_singleton_slot", "singleton_slot = 1"),
        ("ck_admin_account_username_nonempty", "length(trim(username)) >= 3"),
        (
            "ck_admin_account_timetable_refresh_interval_seconds",
            "timetable_refresh_interval_seconds BETWEEN 5 AND 300",
        ),
        (
            "ck_admin_account_observation_interval_seconds",
            "observation_interval_seconds BETWEEN 1 AND 600",
        ),
    }
    assert {
        (index.name, index.unique, tuple(column.name for column in index.columns))
        for index in CanonicalAdminSession.__table__.indexes
    } == {
        ("ix_admin_sessions_expires_at", False, ("expires_at",)),
        ("ix_admin_sessions_token_hash", True, ("token_hash",)),
    }


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_admin_model_import_orders_register_each_mapper_once(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.admin_auth.models import AdminAccount as CanonicalAdminAccount
    from rail_waitlist.admin_auth.models import AdminSession as CanonicalAdminSession
    from rail_waitlist.models import AdminAccount, AdminSession
else:
    from rail_waitlist.models import AdminAccount, AdminSession
    from rail_waitlist.admin_auth.models import AdminAccount as CanonicalAdminAccount
    from rail_waitlist.admin_auth.models import AdminSession as CanonicalAdminSession

from rail_waitlist.database import Base

result = {
    "identity": [
        AdminAccount is CanonicalAdminAccount,
        AdminSession is CanonicalAdminSession,
    ],
    "tables": [
        Base.metadata.tables["admin_accounts"] is CanonicalAdminAccount.__table__,
        Base.metadata.tables["admin_sessions"] is CanonicalAdminSession.__table__,
    ],
    "mappers": [
        sum(mapper.class_ is CanonicalAdminAccount for mapper in Base.registry.mappers),
        sum(mapper.class_ is CanonicalAdminSession for mapper in Base.registry.mappers),
    ],
    "columns": {
        model.__tablename__: [column.name for column in model.__table__.columns]
        for model in (CanonicalAdminAccount, CanonicalAdminSession)
    },
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
        "columns": {
            "admin_accounts": [
                "id",
                "singleton_slot",
                "username",
                "password_hash",
                "timetable_refresh_interval_seconds",
                "observation_interval_seconds",
                "preferences_updated_at",
                "created_at",
                "password_changed_at",
                "last_login_at",
            ],
            "admin_sessions": [
                "id",
                "token_hash",
                "csrf_hash",
                "expires_at",
                "revoked_at",
                "created_at",
                "last_seen_at",
            ],
        },
        "identity": [True, True],
        "mappers": [1, 1],
        "tables": [True, True],
    }
