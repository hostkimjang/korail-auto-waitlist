from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable
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
from rail_waitlist.korail_sidecar.pydoll.auth_actor import PydollAuthenticationSessionActor


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


async def _finish_cleanup(awaitable: Awaitable[object]) -> None:
    await awaitable


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


@pytest.mark.asyncio
async def test_auth_actor_replaces_the_persistent_session_at_the_max_use_boundary() -> None:
    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(_AuthSession())
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[_AuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=factory,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=2,
        monotonic=lambda: 0.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
    )

    first = await actor.acquire_session(credential_version="credential-v1")
    second = await actor.acquire_session(credential_version="credential-v1")
    third = await actor.acquire_session(credential_version="credential-v1")

    assert first.session is second.session
    assert third.session is not first.session
    assert [context.session.closed for context in contexts] == [1, 0]

    await actor.close_locked()
    assert [context.session.closed for context in contexts] == [1, 1]


@pytest.mark.asyncio
async def test_auth_actor_cancellation_discards_the_active_context_and_marks_it_stale() -> None:
    started = asyncio.Event()

    class BlockingAuthSession(_AuthSession):
        async def open(self) -> PydollPageSnapshot:
            self.open_count += 1
            return PydollPageSnapshot("로그인 처리", ())

        async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
            assert credential.login_id == "fixture-account"
            self.authentication_count += 1
            started.set()
            await asyncio.Event().wait()
            return True

    session = BlockingAuthSession()
    actor = PydollAuthenticationSessionActor[BlockingAuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=lambda *_args: _AuthContext(session),  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 0.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
    )

    verification = asyncio.create_task(actor.verify_credentials(_credential()))
    await started.wait()
    verification.cancel()

    with pytest.raises(asyncio.CancelledError):
        await verification

    assert actor.active_session is None
    assert actor.state is KorailSessionActorState.STALE
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
        / "korail_sidecar"
        / "pydoll"
        / "auth_actor.py"
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
