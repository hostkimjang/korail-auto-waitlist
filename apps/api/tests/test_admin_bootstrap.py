from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.admin_auth import bootstrap
from rail_waitlist.admin_auth.account_application import password_hasher
from rail_waitlist.admin_auth.models import AdminAccount, AdminSession
from rail_waitlist.admin_auth.schemas import UsernamePasswordCredentials
from rail_waitlist.config import get_settings

CREDENTIALS = {"username": "  Rail.Admin  ", "password": "correct horse battery staple"}
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def run_bootstrap(
    payload: bytes, database_url: str, cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(SOURCE_ROOT),
        "DATABASE_URL": database_url,
        "AUTH_INITIAL_REGISTRATION_ENABLED": "false",
        "AUTO_CREATE_SCHEMA": "true",
        "EXPERIMENTAL_RAIL_ENABLED": "true",
    }
    return subprocess.run(
        [sys.executable, "-m", "rail_waitlist.admin_auth.bootstrap"],
        input=payload,
        capture_output=True,
        cwd=cwd,
        env=environment,
        timeout=20,
        check=False,
    )


async def test_module_creates_admin_with_registration_closed_and_no_session(db_engine, tmp_path):
    result = await asyncio.to_thread(
        run_bootstrap, json.dumps(CREDENTIALS).encode(), str(db_engine.url), tmp_path
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "created"}
    assert result.stderr == b""
    probe = await asyncio.to_thread(
        run_bootstrap, b'{"check_only":true}', str(db_engine.url), tmp_path
    )
    assert probe.returncode == 0
    assert json.loads(probe.stdout) == {"status": "already_configured"}
    assert probe.stderr == b""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        account = await session.scalar(select(AdminAccount))
        assert account is not None
        assert account.username == "rail.admin"
        assert account.singleton_slot == 1
        assert account.password_hash.startswith("$argon2id$")
        assert password_hasher.verify(account.password_hash, CREDENTIALS["password"])
        assert await session.scalar(select(func.count()).select_from(AdminSession)) == 0
    assert CREDENTIALS["password"].encode() not in result.stdout + result.stderr
    assert b"rail.admin" not in result.stdout + result.stderr


async def test_existing_admin_is_preserved_on_rerun(db_engine, tmp_path):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert (
            await bootstrap.bootstrap_account(
                session, UsernamePasswordCredentials.model_validate(CREDENTIALS)
            )
            == "created"
        )
        account = await session.scalar(select(AdminAccount))
        original = (
            account.id,
            account.username,
            account.password_hash,
            account.password_changed_at,
        )

    result = await asyncio.to_thread(
        run_bootstrap,
        json.dumps({"username": "replacement", "password": "another valid password"}).encode(),
        str(db_engine.url),
        tmp_path,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "already_configured"}
    assert result.stderr == b""
    async with factory() as session:
        accounts = list((await session.scalars(select(AdminAccount))).all())
        assert len(accounts) == 1
        account = accounts[0]
        assert (
            account.id,
            account.username,
            account.password_hash,
            account.password_changed_at,
        ) == (original)


async def test_concurrent_bootstrap_creates_one_administrator(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def attempt(username: str):
        async with factory() as session:
            return await bootstrap.bootstrap_account(
                session,
                UsernamePasswordCredentials(username=username, password=CREDENTIALS["password"]),
            )

    assert sorted(await asyncio.gather(attempt("first-admin"), attempt("second-admin"))) == [
        "already_configured",
        "created",
    ]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AdminAccount)) == 1
        assert await session.scalar(select(func.count()).select_from(AdminSession)) == 0


async def test_probe_reports_unconfigured_without_creating_accounts_or_sessions(
    db_engine, tmp_path
):
    result = await asyncio.to_thread(
        run_bootstrap, b'{"check_only":true}', str(db_engine.url), tmp_path
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "not_configured"}
    assert result.stderr == b""
    async with async_sessionmaker(db_engine)() as session:
        assert await session.scalar(select(func.count()).select_from(AdminAccount)) == 0
        assert await session.scalar(select(func.count()).select_from(AdminSession)) == 0


async def test_bootstrap_and_public_registration_share_the_singleton(app, registration_enabled):
    from httpx2 import ASGITransport, AsyncClient

    async def install():
        async with app.state.test_session_factory() as session:
            return await bootstrap.bootstrap_account(
                session, UsernamePasswordCredentials.model_validate(CREDENTIALS)
            )

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("198.51.100.95", 1234)),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        installed, registered = await asyncio.gather(
            install(), client.post("/api/v1/auth/register", json=CREDENTIALS)
        )
    assert (installed, registered.status_code) in {
        ("created", 409),
        ("already_configured", 200),
    }
    async with app.state.test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AdminAccount)) == 1
        assert await session.scalar(select(func.count()).select_from(AdminSession)) == (
            1 if registered.status_code == 200 else 0
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-json",
        b"[]",
        b'{"username":"admin","password":"short"}',
        b'{"username":"bad username","password":"a valid long password"}',
        b'{"username":"admin"}',
        b'{"username":"admin","password":"a valid long password","extra":"ignored?"}',
        b'{"username":"admin","password":"a valid long password","check_only":true}',
        b'{"check_only":false}',
        b'{"check_only":1}',
        b'{"check_only":"true"}',
        b'{"check_only":true,"extra":"ignored?"}',
        b"{}",
        b"\xff",
        b" " * (bootstrap.MAX_INPUT_BYTES + 1),
    ],
)
def test_invalid_input_is_rejected_without_echoing_values(payload, monkeypatch):
    database_accessed = False

    async def forbidden_database_access(credentials):
        nonlocal database_accessed
        database_accessed = True
        raise AssertionError("invalid input must not access the database")

    monkeypatch.setattr(bootstrap, "_run", forbidden_database_access)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert bootstrap.main(io.BytesIO(payload), stdout, stderr) == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == bootstrap.ERROR_MESSAGE
    assert database_accessed is False


def test_stdin_read_is_bounded():
    class BoundedInput(io.BytesIO):
        def read(self, size=-1):
            assert size == bootstrap.MAX_INPUT_BYTES + 1
            return super().read(size)

    stdout, stderr = io.StringIO(), io.StringIO()
    assert bootstrap.main(BoundedInput(b" " * (bootstrap.MAX_INPUT_BYTES * 2)), stdout, stderr) == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == bootstrap.ERROR_MESSAGE


def test_database_errors_are_redacted(monkeypatch):
    async def fail(credentials):
        raise RuntimeError(f"postgresql://secret-user:{credentials.password}@db/private")

    monkeypatch.setattr(bootstrap, "_run", fail)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert bootstrap.main(io.BytesIO(json.dumps(CREDENTIALS).encode()), stdout, stderr) == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == bootstrap.ERROR_MESSAGE


@pytest.mark.parametrize("payload", [json.dumps(CREDENTIALS).encode(), b'{"check_only":true}'])
def test_missing_migrations_fail_without_creating_schema(tmp_path, payload):
    import sqlite3

    database_path = tmp_path / "unmigrated.db"
    result = run_bootstrap(payload, f"sqlite+aiosqlite:///{database_path}", tmp_path)

    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr.decode() == bootstrap.ERROR_MESSAGE
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )


async def test_bootstrap_does_not_change_public_registration_gate(db_engine):
    settings = get_settings()
    previous = settings.auth_initial_registration_enabled
    settings.auth_initial_registration_enabled = False
    try:
        async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
            assert (
                await bootstrap.bootstrap_account(
                    session, UsernamePasswordCredentials.model_validate(CREDENTIALS)
                )
                == "created"
            )
        assert settings.auth_initial_registration_enabled is False
    finally:
        settings.auth_initial_registration_enabled = previous
