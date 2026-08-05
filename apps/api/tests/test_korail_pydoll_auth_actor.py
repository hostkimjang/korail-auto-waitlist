from __future__ import annotations

import ast
from pathlib import Path

import pytest

import rail_waitlist.korail_pydoll_auth_actor as auth_actor_module
import rail_waitlist.korail_pydoll_browser as browser_module
from rail_waitlist.korail_pydoll_auth_actor import KorailSessionActorState
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    PydollKorailBrowserClient,
    PydollPageSnapshot,
)


class _AuthSession:
    def __init__(self) -> None:
        self.open_count = 0
        self.authentication_count = 0
        self.closed = 0

    async def open(self) -> PydollPageSnapshot:
        self.open_count += 1
        return PydollPageSnapshot("CODE -8003", ())

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        assert credential.login_id == "fixture-account"
        self.authentication_count += 1
        return True


class _AuthContext:
    def __init__(self, session: _AuthSession) -> None:
        self.session = session

    async def __aenter__(self) -> _AuthSession:
        return self.session

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        self.session.closed += 1


def _credential() -> KorailCredentialInput:
    return KorailCredentialInput(
        login_id="fixture-account",
        password="fixture-password",
        version="credential-v1",
    )


@pytest.mark.asyncio
async def test_auth_guard_patched_before_client_construction_is_used_by_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_stages: list[str] = []

    def patched_guard(_snapshot: PydollPageSnapshot, stage: str) -> None:
        checked_stages.append(stage)

    monkeypatch.setattr(
        PydollKorailBrowserClient,
        "_assert_response_allowed",
        staticmethod(patched_guard),
    )
    session = _AuthSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _AuthContext(session),  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )

    assert await client.verify_credentials(_credential()) is True

    assert checked_stages == ["load_page"]
    assert session.open_count == 1
    assert session.authentication_count == 1
    assert client.session_snapshot().state is KorailSessionActorState.READY
    await client.close()
    assert session.closed == 1


def test_browser_keeps_authentication_contract_compatibility_exports() -> None:
    assert browser_module.KorailCredentialInput is auth_actor_module.KorailCredentialInput
    assert browser_module.KorailLoginMethod is auth_actor_module.KorailLoginMethod
    assert browser_module.KorailSessionActorSnapshot is auth_actor_module.KorailSessionActorSnapshot
    assert browser_module.KorailSessionActorState is auth_actor_module.KorailSessionActorState


def test_auth_actor_has_no_reverse_or_peer_actor_dependencies() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_pydoll_auth_actor.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules.isdisjoint(
        {
            "korail_pydoll_browser",
            "korail_pydoll_confirmation_reader",
            "korail_pydoll_http_replay",
            "korail_pydoll_page_safety",
            "korail_pydoll_search_actor",
        }
    )
