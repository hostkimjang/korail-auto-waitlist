from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import Provider
from rail_waitlist.provider_account_management.application import update_provider_auth_status
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.provider_account_management.reservation_runtime import (
    update_provider_auth_status_in_reservation_transaction,
)
from rail_waitlist.security import secret_box

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_reservation_auth_adapter_forwards_identity_and_forces_shared_transaction() -> None:
    session = object()
    captured: dict[str, object] = {}

    async def persist_auth_status(
        session_arg,
        provider_arg,
        status_arg,
        *,
        expected_credential_version=None,
        commit=True,
    ):
        captured.update(
            session=session_arg,
            provider=provider_arg,
            status=status_arg,
            expected_credential_version=expected_credential_version,
            commit=commit,
        )
        return object()

    result = await update_provider_auth_status_in_reservation_transaction(
        session,
        Provider.SRT,
        "auth_required",
        expected_credential_version=7,
        persist_auth_status=persist_auth_status,
    )

    assert result is None
    assert captured == {
        "session": session,
        "provider": Provider.SRT,
        "status": "auth_required",
        "expected_credential_version": 7,
        "commit": False,
    }


@pytest.mark.asyncio
async def test_reservation_auth_adapter_propagates_persistence_failure() -> None:
    expected = RuntimeError("persistence failed")

    async def persist_auth_status(*_args, **_kwargs):
        raise expected

    with pytest.raises(RuntimeError) as raised:
        await update_provider_auth_status_in_reservation_transaction(
            object(),
            Provider.KORAIL,
            "failed",
            expected_credential_version=3,
            persist_auth_status=persist_auth_status,
        )

    assert raised.value is expected


@pytest.mark.asyncio
async def test_reservation_auth_adapter_flush_is_rolled_back_with_outer_transaction(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "fixture-user",
                        "password": "fixture-only-value",
                    }
                ),
                enabled=True,
                credential_version=5,
                last_auth_status="authenticated",
                last_authenticated_at=datetime(2030, 8, 1, tzinfo=UTC),
            )
        )
        await session.commit()

    async with factory() as session:
        await update_provider_auth_status_in_reservation_transaction(
            session,
            Provider.SRT,
            "auth_required",
            expected_credential_version=5,
            persist_auth_status=update_provider_auth_status,
        )
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        assert account is not None
        assert account.last_auth_status == "auth_required"
        await session.rollback()

    async with factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        assert account is not None
        assert account.last_auth_status == "authenticated"


@pytest.mark.asyncio
async def test_worker_reservation_auth_wrapper_injects_current_persistence_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    persist_auth_status = object()
    captured: dict[str, object] = {}

    async def canonical(
        session_arg,
        provider_arg,
        status_arg,
        *,
        expected_credential_version,
        persist_auth_status,
    ) -> None:
        captured.update(
            session=session_arg,
            provider=provider_arg,
            status=status_arg,
            expected_credential_version=expected_credential_version,
            persist_auth_status=persist_auth_status,
        )

    monkeypatch.setattr(
        worker_module,
        "update_provider_auth_status_in_reservation_transaction",
        canonical,
    )
    monkeypatch.setattr(worker_module, "update_provider_auth_status", persist_auth_status)

    await worker_module._update_provider_auth_status_in_reservation_transaction(
        session,
        Provider.KORAIL,
        "provider_blocked",
        expected_credential_version=11,
    )

    assert captured == {
        "session": session,
        "provider": Provider.KORAIL,
        "status": "provider_blocked",
        "expected_credential_version": 11,
        "persist_auth_status": persist_auth_status,
    }
    assert (
        worker_module._reservation_execution_dependencies().update_provider_auth_status
        is worker_module._update_provider_auth_status_in_reservation_transaction
    )


def test_reservation_auth_adapter_import_orders_preserve_worker_wiring() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.provider_account_management.reservation_runtime import (
        update_provider_auth_status_in_reservation_transaction as Canonical,
    )
    from rail_waitlist.provider_account_management.application import (
        update_provider_auth_status as Persistence,
    )
    import rail_waitlist.worker as Worker
else:
    import rail_waitlist.worker as Worker
    from rail_waitlist.provider_account_management.application import (
        update_provider_auth_status as Persistence,
    )
    from rail_waitlist.provider_account_management.reservation_runtime import (
        update_provider_auth_status_in_reservation_transaction as Canonical,
    )

print(json.dumps({
    "canonical_identity": (
        Worker.update_provider_auth_status_in_reservation_transaction is Canonical
    ),
    "persistence_identity": Worker.update_provider_auth_status is Persistence,
    "callback_identity": (
        Worker._reservation_execution_dependencies().update_provider_auth_status
        is Worker._update_provider_auth_status_in_reservation_transaction
    ),
    "module": Canonical.__module__,
}, sort_keys=True))
"""

    for import_order in ("canonical-first", "worker-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "callback_identity": True,
            "canonical_identity": True,
            "module": "rail_waitlist.provider_account_management.reservation_runtime",
            "persistence_identity": True,
        }
