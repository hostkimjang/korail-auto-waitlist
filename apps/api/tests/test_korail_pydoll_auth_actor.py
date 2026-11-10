from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable
from pathlib import Path

import pytest

import rail_waitlist.korail_pydoll_auth_actor as auth_actor_module
import rail_waitlist.korail_pydoll_browser as browser_module
from rail_waitlist.korail_pydoll_auth_actor import (
    BrowserProtectionDetected,
    BrowserSourceUnavailable,
    KorailSessionActorState,
)
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    PydollKorailBrowserClient,
    PydollPageSnapshot,
)
from rail_waitlist.korail_sidecar.browser_service_availability import BrowserProviderUnavailable
from rail_waitlist.korail_sidecar.provider_cooldown import (
    MemoryProviderCooldown,
    ProviderCooldownDeferred,
)
from rail_waitlist.korail_sidecar.pydoll.auth_actor import PydollAuthenticationSessionActor
from rail_waitlist.korail_sidecar.pydoll.login_submission import (
    LoginSubmissionSnapshot,
    PydollLoginResponseUnavailable,
)
from rail_waitlist.korail_sidecar.pydoll.login_submission_pacing import (
    admit_login_submission,
    record_login_submission,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["verify_credentials", "prewarm_credentials"])
async def test_shared_hold_rechecked_after_auth_lock_without_session_mutation(operation) -> None:
    hold = MemoryProviderCooldown()
    session = _AuthSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _AuthContext(session),
        provider_cooldown=hold,
    )
    await client._session_lock.acquire()
    task = asyncio.create_task(getattr(client, operation)(_credential()))
    await asyncio.sleep(0)
    await hold.query_failed()
    client._session_lock.release()
    with pytest.raises(ProviderCooldownDeferred):
        await task
    assert session.open_count == session.authentication_count == session.probe_count == 0
    assert client.session_snapshot().state is KorailSessionActorState.COLD


@pytest.mark.asyncio
async def test_actual_login_http_error_opens_shared_hold_once() -> None:
    class FailedLogin(_AuthSession):
        async def open(self):
            self.open_count += 1
            return PydollPageSnapshot("fixture login", ())

        async def ensure_authenticated(self, credential):
            self.authentication_count += 1
            raise PydollLoginResponseUnavailable(
                LoginSubmissionSnapshot("failed", 500, "http_error")
            )

    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0])
    session = FailedLogin()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _AuthContext(session), provider_cooldown=hold
    )
    with pytest.raises(PydollLoginResponseUnavailable):
        await client.verify_credentials(_credential())
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await client.prewarm_credentials(_credential())
    assert caught.value.retry_after_seconds == 300
    assert session.authentication_count == 1
    assert hold._login_failures == 1


@pytest.mark.asyncio
async def test_failed_official_probe_cannot_reauthenticate_through_new_hold() -> None:
    class OutageProbe(_AuthSession):
        async def open(self):
            self.open_count += 1
            return PydollPageSnapshot("fixture login", ())

        async def probe_authenticated_session(self):
            self.probe_count += 1
            raise BrowserProviderUnavailable("business_server_error", "session_keepalive")

    hold = MemoryProviderCooldown()
    session = OutageProbe()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: _AuthContext(session),
        provider_cooldown=hold,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )
    assert await client.verify_credentials(_credential())
    with pytest.raises(ProviderCooldownDeferred):
        await client.prewarm_credentials(_credential())
    assert session.authentication_count == session.probe_count == 1
    await client.close()


class _AuthSession:
    def __init__(self) -> None:
        self.open_count = 0
        self.authentication_count = 0
        self.probe_count = 0
        self.probe_result = True
        self.closed = 0

    async def open(self) -> PydollPageSnapshot:
        self.open_count += 1
        return PydollPageSnapshot("CODE -8003", ())

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        assert credential.login_id == "fixture-account"
        self.authentication_count += 1
        return True

    async def probe_authenticated_session(self) -> bool:
        self.probe_count += 1
        return self.probe_result


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


def _credential(version: str = "credential-v1") -> KorailCredentialInput:
    return KorailCredentialInput(
        login_id="fixture-account",
        password="fixture-password",
        version=version,
    )


async def _finish_cleanup(awaitable: Awaitable[object]) -> None:
    await awaitable


@pytest.mark.asyncio
async def test_manual_session_handoff_keeps_the_same_browser_until_user_login() -> None:
    class ManualSession(_AuthSession):
        def __init__(self) -> None:
            super().__init__()
            self.user_logged_in = False

        async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
            assert credential.login_id == "fixture-account"
            self.authentication_count += 1
            if not self.user_logged_in:
                raise BrowserSourceUnavailable("manual_login_required")
            return True

    session = ManualSession()
    actor = PydollAuthenticationSessionActor[ManualSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=False,
        session_factory=lambda *_args: _AuthContext(session),  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 10.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        retain_auth_required_session=True,
    )

    with pytest.raises(BrowserSourceUnavailable) as first_attempt:
        await actor.verify_credentials(_credential())

    assert first_attempt.value.stage == "manual_login_required"
    assert actor.active_session is not None
    assert actor.active_session.session is session
    assert actor.snapshot().state is KorailSessionActorState.AUTH_REQUIRED
    assert session.closed == 0

    session.user_logged_in = True

    assert await actor.verify_credentials(_credential()) is True
    assert session.open_count == 1
    assert session.authentication_count == 2
    assert session.closed == 0
    assert actor.snapshot().state is KorailSessionActorState.READY

    await actor.close_locked()
    assert session.closed == 1


@pytest.mark.asyncio
async def test_manual_session_handoff_keeps_live_browser_after_reuse_ttl() -> None:
    class ManualSession(_AuthSession):
        def __init__(self) -> None:
            super().__init__()
            self.user_logged_in = False

        async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
            assert credential.login_id == "fixture-account"
            self.authentication_count += 1
            if not self.user_logged_in:
                raise BrowserSourceUnavailable("manual_login_required")
            return True

    now = [10.0]
    session = ManualSession()
    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(session)
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[ManualSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=False,
        session_factory=factory,  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=2,
        monotonic=lambda: now[0],
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        retain_auth_required_session=True,
    )

    with pytest.raises(BrowserSourceUnavailable) as first_attempt:
        await actor.verify_credentials(_credential())

    assert first_attempt.value.stage == "manual_login_required"
    now[0] = 71.0
    session.user_logged_in = True

    assert await actor.verify_credentials(_credential()) is True
    assert len(contexts) == 1
    assert session.open_count == 1
    assert session.authentication_count == 2
    assert session.closed == 0
    snapshot = actor.snapshot()
    assert snapshot.state is KorailSessionActorState.READY
    assert snapshot.locally_reusable is True

    await actor.close_locked()
    assert session.closed == 1


@pytest.mark.asyncio
async def test_manual_authenticated_session_refreshes_after_reuse_ttl_without_relaunch() -> None:
    now = [10.0]
    session = _AuthSession()
    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(session)
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[_AuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=False,
        session_factory=factory,  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: now[0],
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        retain_auth_required_session=True,
    )

    assert await actor.verify_credentials(_credential()) is True
    now[0] = 71.0
    assert actor.snapshot().locally_reusable is False

    assert await actor.prewarm_credentials(_credential()) is True

    assert len(contexts) == 1
    assert session.open_count == 1
    assert session.authentication_count == 2
    assert session.closed == 0
    snapshot = actor.snapshot()
    assert snapshot.state is KorailSessionActorState.READY
    assert snapshot.locally_reusable is True

    await actor.close_locked()
    assert session.closed == 1


@pytest.mark.asyncio
async def test_manual_authenticated_session_rebinds_same_secret_generation_without_relaunch() -> (
    None
):
    session = _AuthSession()
    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(session)
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[_AuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=False,
        session_factory=factory,  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 10.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        retain_auth_required_session=True,
    )

    assert await actor.verify_credentials(_credential("credential-v1")) is True
    assert await actor.verify_credentials(_credential("credential-v2")) is True

    assert len(contexts) == 1
    assert session.open_count == 1
    assert session.authentication_count == 2
    assert session.closed == 0
    snapshot = actor.snapshot()
    assert snapshot.state is KorailSessionActorState.READY
    assert snapshot.credential_generation == "credential-v2"

    await actor.close_locked()
    assert session.closed == 1


@pytest.mark.asyncio
async def test_manual_authenticated_session_relaunches_when_secret_changes() -> None:
    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(_AuthSession())
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[_AuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=False,
        session_factory=factory,  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 10.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        retain_auth_required_session=True,
    )

    assert await actor.verify_credentials(_credential("credential-v1")) is True
    changed = KorailCredentialInput(
        login_id="fixture-account",
        password="different-fixture-password",
        version="credential-v2",
    )
    assert await actor.verify_credentials(changed) is True

    assert len(contexts) == 2
    assert contexts[0].session.closed == 1
    assert contexts[1].session.closed == 0
    assert actor.snapshot().credential_generation == "credential-v2"

    await actor.close_locked()
    assert contexts[1].session.closed == 1


@pytest.mark.asyncio
async def test_manual_session_handoff_discards_a_pending_different_generation() -> None:
    class ManualSession(_AuthSession):
        async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
            self.authentication_count += 1
            raise BrowserSourceUnavailable("manual_login_required")

    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(ManualSession())
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[ManualSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=False,
        session_factory=factory,  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 10.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
        retain_auth_required_session=True,
    )

    with pytest.raises(BrowserSourceUnavailable) as first_attempt:
        await actor.verify_credentials(_credential("credential-v1"))
    with pytest.raises(BrowserSourceUnavailable) as second_attempt:
        await actor.verify_credentials(_credential("credential-v2"))

    assert first_attempt.value.stage == "manual_login_required"
    assert second_attempt.value.stage == "manual_login_required"
    assert len(contexts) == 2
    assert contexts[0].session.closed == 1
    assert contexts[1].session.closed == 0
    assert actor.snapshot().credential_generation == "credential-v2"

    await actor.close_locked()
    assert contexts[1].session.closed == 1


@pytest.mark.parametrize("manual_session_only", [False, True])
def test_client_reserves_the_dedicated_profile_for_authentication_only(
    monkeypatch: pytest.MonkeyPatch,
    manual_session_only: bool,
) -> None:
    calls: list[tuple[bool, bool]] = []

    def factory(
        _page_url: str,
        _timeout_ms: int,
        _headless: bool,
        *,
        auto_handle_dialogs: bool,
        manual_session_only: bool,
        allow_dedicated_profile: bool,
        probe_restored_session: bool = False,
        notice_report_sink: object | None = None,
    ) -> object:
        assert auto_handle_dialogs is False
        assert probe_restored_session is False
        calls.append((manual_session_only, allow_dedicated_profile))
        return object()

    monkeypatch.setattr(browser_module, "_default_pydoll_session_factory", factory)
    client = PydollKorailBrowserClient(
        manual_session_only=manual_session_only,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )

    client._auth_actor._session_factory(client.page_url, client.timeout_ms, client.headless)
    client._search_actor._session_factory(client.page_url, client.timeout_ms, client.headless)

    assert calls == [(manual_session_only, True), (False, False)]


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
async def test_prewarm_probes_the_official_session_before_extending_local_reuse() -> None:
    now = [10.0]
    session = _AuthSession()
    actor = PydollAuthenticationSessionActor[_AuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=lambda *_args: _AuthContext(session),
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: now[0],
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
    )

    assert await actor.verify_credentials(_credential()) is True
    now[0] = 30.0

    assert await actor.prewarm_credentials(_credential()) is True

    snapshot = actor.snapshot()
    assert session.probe_count == 1
    assert session.authentication_count == 1
    assert snapshot.last_verified_at_monotonic == 30.0
    assert snapshot.last_used_at_monotonic == 30.0
    assert snapshot.local_reuse_until_monotonic == 90.0
    await actor.close_locked()


@pytest.mark.asyncio
async def test_unverified_use_cannot_extend_an_authenticated_generation_past_its_ttl() -> None:
    now = [10.0]
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
        session_reuse_max_searches=10,
        monotonic=lambda: now[0],
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
    )

    assert await actor.verify_credentials(_credential()) is True
    first_session = contexts[0].session
    for used_at in (50.0, 69.0):
        now[0] = used_at
        lease = await actor.acquire_session(credential_version=_credential().version)
        assert lease.session is first_session
        assert await actor.ensure_authenticated_session(lease.session, _credential()) is True

    snapshot = actor.snapshot()
    assert snapshot.last_verified_at_monotonic == 10.0
    assert snapshot.last_used_at_monotonic == 69.0
    assert snapshot.local_reuse_until_monotonic == 70.0

    now[0] = 71.0
    replacement = await actor.acquire_session(credential_version=_credential().version)

    assert replacement.session is not first_session
    assert first_session.closed == 1
    await actor.close_locked()


@pytest.mark.asyncio
async def test_prewarm_reauthenticates_once_when_the_official_session_is_gone() -> None:
    sessions = [_AuthSession(), _AuthSession()]
    contexts: list[_AuthContext] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        context = _AuthContext(sessions[len(contexts)])
        contexts.append(context)
        return context

    actor = PydollAuthenticationSessionActor[_AuthSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=factory,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 0.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
    )

    assert await actor.verify_credentials(_credential()) is True
    sessions[0].probe_result = False

    assert await actor.prewarm_credentials(_credential()) is True

    assert sessions[0].probe_count == 1
    assert sessions[0].closed == 1
    assert sessions[1].authentication_count == 1
    assert actor.active_session is not None
    assert actor.active_session.session is sessions[1]
    assert actor.state is KorailSessionActorState.READY
    await actor.close_locked()


@pytest.mark.asyncio
async def test_prewarm_cancellation_retires_the_uncertain_session() -> None:
    started = asyncio.Event()

    class BlockingProbeSession(_AuthSession):
        async def probe_authenticated_session(self) -> bool:
            self.probe_count += 1
            started.set()
            await asyncio.Event().wait()
            return True

    session = BlockingProbeSession()
    actor = PydollAuthenticationSessionActor[BlockingProbeSession](
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
    assert await actor.verify_credentials(_credential()) is True

    prewarm = asyncio.create_task(actor.prewarm_credentials(_credential()))
    await started.wait()
    assert actor.lock.locked() is True
    prewarm.cancel()

    with pytest.raises(asyncio.CancelledError):
        await prewarm

    assert actor.active_session is None
    assert actor.state is KorailSessionActorState.STALE
    assert session.authentication_count == 1
    assert session.probe_count == 1
    assert session.closed == 1


@pytest.mark.asyncio
async def test_prewarm_protection_retires_the_session_and_marks_it_blocked() -> None:
    class ProtectedProbeSession(_AuthSession):
        async def probe_authenticated_session(self) -> bool:
            self.probe_count += 1
            raise BrowserProtectionDetected(stage="session_keepalive")

    session = ProtectedProbeSession()
    actor = PydollAuthenticationSessionActor[ProtectedProbeSession](
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
    assert await actor.verify_credentials(_credential()) is True

    with pytest.raises(BrowserProtectionDetected):
        await actor.prewarm_credentials(_credential())

    assert actor.active_session is None
    assert actor.state is KorailSessionActorState.BLOCKED
    assert session.authentication_count == 1
    assert session.probe_count == 1
    assert session.closed == 1


@pytest.mark.asyncio
async def test_prewarm_unavailable_probe_reauthenticates_in_the_same_call() -> None:
    class UnavailableProbeSession(_AuthSession):
        async def probe_authenticated_session(self) -> bool:
            self.probe_count += 1
            raise BrowserSourceUnavailable("session_keepalive")

    session = UnavailableProbeSession()
    actor = PydollAuthenticationSessionActor[UnavailableProbeSession](
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
    assert await actor.verify_credentials(_credential()) is True

    # An inconclusive official probe must not leave the account unauthenticated until the
    # next maintenance tick. The retired generation is replaced by a fresh login here.
    assert await actor.prewarm_credentials(_credential()) is True

    assert actor.active_session is not None
    assert actor.state is KorailSessionActorState.READY
    assert session.probe_count == 1
    assert session.authentication_count == 2
    assert session.open_count == 2
    assert session.closed == 1


@pytest.mark.asyncio
async def test_prewarm_unavailable_probe_replaces_the_retired_generation() -> None:
    sessions: list[_AuthSession] = []

    class UnavailableProbeSession(_AuthSession):
        async def probe_authenticated_session(self) -> bool:
            self.probe_count += 1
            raise BrowserSourceUnavailable("session_keepalive")

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        session = UnavailableProbeSession()
        sessions.append(session)
        return _AuthContext(session)

    actor = PydollAuthenticationSessionActor[UnavailableProbeSession](
        page_url="https://www.korail.com/ticket/search/general",
        timeout_ms=1_000,
        headless=True,
        session_factory=factory,  # type: ignore[arg-type]
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
        monotonic=lambda: 0.0,
        cleanup=_finish_cleanup,
        response_safety_guard=lambda _snapshot, _stage: None,
    )
    assert await actor.verify_credentials(_credential()) is True

    assert await actor.prewarm_credentials(_credential()) is True

    assert len(sessions) == 2
    assert sessions[0].probe_count == 1
    assert sessions[0].closed == 1
    assert sessions[1].authentication_count == 1
    assert actor.active_session is not None
    assert actor.active_session.session is sessions[1]
    assert actor.state is KorailSessionActorState.READY


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


class _PacedLoginSession(_AuthSession):
    """Mirror the driver: background admission precedes the recorded submission."""

    def __init__(self) -> None:
        super().__init__()
        self.submissions = 0

    async def open(self) -> PydollPageSnapshot:
        self.open_count += 1
        return PydollPageSnapshot("fixture search", ())

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        self.authentication_count += 1
        await admit_login_submission()
        await record_login_submission()
        self.submissions += 1
        return True


def _paced_client(
    hold: MemoryProviderCooldown,
    sessions: list[_PacedLoginSession],
) -> PydollKorailBrowserClient:
    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        session = _PacedLoginSession()
        sessions.append(session)
        return _AuthContext(session)

    return PydollKorailBrowserClient(
        session_factory=factory,
        provider_cooldown=hold,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )


@pytest.mark.asyncio
async def test_restart_prewarm_inside_submission_interval_defers_and_retires_browser() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0], login_submission_min_interval_seconds=1800)
    sessions: list[_PacedLoginSession] = []
    before_restart = _paced_client(hold, sessions)
    assert await before_restart.verify_credentials(_credential()) is True
    await before_restart.close()

    # A restarted adapter shares the persisted ledger but not the in-memory session.
    restarted = _paced_client(hold, sessions)
    now[0] += 600
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await restarted.prewarm_credentials(_credential())

    assert (caught.value.reason, caught.value.retry_after_seconds) == (
        "provider_access_restricted",
        1200,
    )
    snapshot = restarted.session_snapshot()
    assert snapshot.state is KorailSessionActorState.STALE
    assert snapshot.locally_reusable is False
    assert restarted._auth_actor.active_session is None
    assert sessions[1].submissions == 0
    assert sessions[1].closed == 1

    now[0] += 1200
    assert await restarted.prewarm_credentials(_credential()) is True
    assert sessions[2].submissions == 1
    assert restarted.session_snapshot().state is KorailSessionActorState.READY
    await restarted.close()


@pytest.mark.asyncio
async def test_required_logins_inside_submission_interval_are_recorded_not_deferred() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0], login_submission_min_interval_seconds=1800)
    sessions: list[_PacedLoginSession] = []
    client = _paced_client(hold, sessions)

    assert await client.verify_credentials(_credential()) is True
    now[0] += 60
    assert await client.verify_credentials(_credential()) is True
    assert [session.submissions for session in sessions] == [1, 1]

    # A reservation login reaches the actor without a background scope.
    now[0] += 60
    lease = await client._auth_actor.acquire_session(credential_version="credential-v2")
    assert await client._auth_actor.ensure_authenticated_session(
        lease.session, _credential("credential-v2")
    )
    assert sum(session.submissions for session in sessions) == 3
    assert hold.login_submission_remaining() == 1800
    await client.close()


@pytest.mark.asyncio
async def test_reservation_entry_records_in_place_login_without_deferring(monkeypatch) -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0], login_submission_min_interval_seconds=1800)
    await hold.record_login_submission()
    now[0] += 30
    client = _paced_client(hold, [])

    async def reserve_with_in_place_login(_request: object) -> str:
        await admit_login_submission()
        await record_login_submission()
        return "reserved"

    monkeypatch.setattr(client._reservation_actor, "reserve_once", reserve_with_in_place_login)
    assert await client.reserve_once(object()) == "reserved"
    assert hold.login_submission_remaining() == 1800
    await client.close()


@pytest.mark.asyncio
async def test_keepalive_probe_logout_inside_submission_interval_defers_the_login() -> None:
    now = [1_700_000_000.0]
    hold = MemoryProviderCooldown(clock=lambda: now[0], login_submission_min_interval_seconds=1800)
    sessions: list[_PacedLoginSession] = []

    def factory(_page_url: str, _timeout_ms: int, _headless: bool) -> _AuthContext:
        session = _PacedLoginSession()
        sessions.append(session)
        return _AuthContext(session)

    client = PydollKorailBrowserClient(
        session_factory=factory,
        provider_cooldown=hold,
        session_reuse_ttl_seconds=7200,
        session_reuse_max_searches=5,
    )
    assert await client.verify_credentials(_credential()) is True

    # A keepalive inside the submission interval only probes; a positive answer keeps
    # the same browser and submits nothing.
    now[0] += 600
    assert await client.prewarm_credentials(_credential()) is True
    assert sessions[0].probe_count == 1
    assert [session.submissions for session in sessions] == [1]
    assert client.session_snapshot().state is KorailSessionActorState.READY

    # An official logout retires the session, and its replacement is a background login
    # that waits out the shared interval instead of submitting 20 minutes after the last.
    now[0] += 600
    sessions[0].probe_result = False
    with pytest.raises(ProviderCooldownDeferred) as caught:
        await client.prewarm_credentials(_credential())

    assert caught.value.retry_after_seconds == 600
    assert sessions[0].probe_count == 2
    assert sessions[0].closed == 1
    assert [session.submissions for session in sessions] == [1, 0]
    assert client.session_snapshot().state is KorailSessionActorState.STALE
    await client.close()
