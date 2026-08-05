from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_acceptance_module() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check_reservation_credential_fencing_postgres.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_reservation_credential_fencing_postgres_for_test",
        script,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("acceptance script could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acceptance = _load_acceptance_module()


@pytest.mark.parametrize(
    ("opt_in", "database_url"),
    [
        (
            None,
            "postgresql+asyncpg://acceptance:fixture@localhost/rail_waitlist_acceptance_test",
        ),
        (
            "false",
            "postgresql+asyncpg://acceptance:fixture@localhost/rail_waitlist_acceptance_test",
        ),
        (
            "true",
            "postgresql+asyncpg://acceptance:fixture@localhost/rail_waitlist_production",
        ),
        (
            "true",
            "sqlite+aiosqlite:///rail_waitlist_acceptance_test",
        ),
    ],
)
def test_acceptance_rejects_unsafe_database_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch,
    opt_in: str | None,
    database_url: str,
) -> None:
    if opt_in is None:
        monkeypatch.delenv("POSTGRES_ACCEPTANCE_ISOLATED", raising=False)
    else:
        monkeypatch.setenv("POSTGRES_ACCEPTANCE_ISOLATED", opt_in)
    monkeypatch.setattr(
        acceptance,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url),
    )
    engine_created = False

    def forbidden_engine(*args: object, **kwargs: object) -> None:
        nonlocal engine_created
        del args, kwargs
        engine_created = True

    monkeypatch.setattr(acceptance, "create_async_engine", forbidden_engine)

    with pytest.raises(RuntimeError, match="PostgreSQL acceptance|requires PostgreSQL"):
        acceptance._engine_and_factory()

    assert engine_created is False


def test_acceptance_accepts_only_prefixed_effective_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = (
        "postgresql+asyncpg://acceptance:fixture@localhost/rail_waitlist_acceptance_local"
    )
    monkeypatch.setenv("POSTGRES_ACCEPTANCE_ISOLATED", "true")
    monkeypatch.setattr(
        acceptance,
        "get_settings",
        lambda: SimpleNamespace(database_url=database_url),
    )

    assert acceptance._require_isolated_database() == database_url
