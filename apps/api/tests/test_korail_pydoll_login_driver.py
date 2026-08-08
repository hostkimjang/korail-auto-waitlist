from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import rail_waitlist.korail_pydoll_auth_actor as auth_actor_module
import rail_waitlist.korail_pydoll_auth_contracts as auth_contracts_module
import rail_waitlist.korail_pydoll_browser as browser_module
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    PydollKorailBrowserClient,
    _PydollSession,
)
from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_sidecar.pydoll.login_driver import login_step


def _credential() -> KorailCredentialInput:
    return KorailCredentialInput(
        login_id="fixture-account",
        password="fixture-password",
        version="credential-v1",
    )


@pytest.mark.asyncio
async def test_login_driver_resolves_session_monkeypatch_seams_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    go_to = AsyncMock()
    session._tab = SimpleNamespace(go_to=go_to)
    has_authenticated_header = AsyncMock(return_value=False)
    submit_login_form = AsyncMock(return_value=True)
    wait_for_authentication = AsyncMock(return_value=True)
    confirm_authenticated_search = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_has_authenticated_header", has_authenticated_header)
    monkeypatch.setattr(session, "_submit_login_form", submit_login_form)
    monkeypatch.setattr(session, "_wait_for_login_authentication", wait_for_authentication)
    monkeypatch.setattr(session, "_confirm_authenticated_search", confirm_authenticated_search)

    assert await session.ensure_authenticated(_credential()) is True

    has_authenticated_header.assert_awaited_once_with()
    go_to.assert_awaited_once_with(
        "https://www.korail.com/ticket/login",
        timeout=1,
    )
    submit_login_form.assert_awaited_once_with(_credential())
    wait_for_authentication.assert_awaited_once()
    confirm_authenticated_search.assert_awaited_once()


def test_auth_contract_identity_remains_compatible_across_public_facades() -> None:
    assert browser_module.KorailCredentialInput is auth_contracts_module.KorailCredentialInput
    assert auth_actor_module.KorailCredentialInput is auth_contracts_module.KorailCredentialInput
    assert browser_module.KorailLoginMethod is auth_contracts_module.KorailLoginMethod
    assert auth_actor_module.KorailLoginMethod is auth_contracts_module.KorailLoginMethod
    assert PydollKorailBrowserClient is browser_module.PydollKorailBrowserClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        BrowserProtectionDetected(),
        BrowserRateLimited(),
        BrowserSourceUnavailable("existing_stage"),
    ],
    ids=("cancelled", "protection", "rate_limited", "source_unavailable"),
)
async def test_login_step_preserves_cancellation_and_classified_browser_errors(
    error: BaseException,
) -> None:
    async def fail() -> None:
        raise error

    with pytest.raises(type(error)) as captured:
        await login_step("new_stage", fail())

    assert captured.value is error


def test_login_driver_has_no_browser_or_lifecycle_actor_dependencies() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_sidecar"
        / "pydoll"
        / "login_driver.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules.isdisjoint(
        {
            "korail_pydoll_auth_actor",
            "korail_pydoll_browser",
            "korail_pydoll_confirmation_reader",
            "korail_pydoll_http_replay",
            "korail_pydoll_page_safety",
            "korail_pydoll_reservation_actor",
            "korail_pydoll_search_actor",
        }
    )
