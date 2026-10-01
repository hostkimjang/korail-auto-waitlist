from __future__ import annotations

import ast
import asyncio
import json
import shutil
import subprocess
from collections.abc import Callable
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
from rail_waitlist.korail_sidecar.browser_service_availability import BrowserProviderUnavailable
from rail_waitlist.korail_sidecar.pydoll import login_submission_context as observation_module
from rail_waitlist.korail_sidecar.pydoll.login_driver import login_step
from rail_waitlist.korail_sidecar.pydoll.login_submission import PydollLoginResponseUnavailable
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollPageSnapshot


@pytest.mark.parametrize(
    ("status", "mime", "body", "expected", "body_reads"),
    [
        (200, "application/json", '{"strResult":"SUCC"}', "authenticated", 1),
        (200, "text/html; charset=utf-8", '{"strResult":"SUCC"}', "authenticated", 1),
        (200, " TEXT/HTML ; charset=UTF-8", '{"strResult":"SUCC"}', "authenticated", 1),
        (200, "text/html", '{"strResult":"SUCC","h_msg_cd":""}', "authenticated", 1),
        (200, "text/html", '{"strResult":"SUCC","h_msg_cd":null}', "authenticated", 1),
        (
            200,
            "text/html",
            '{"strResult":"SUCC","h_msg_cd":"WRT300004"}',
            "logged_out",
            1,
        ),
        (
            200,
            "application/json",
            '{"strResult":"SUCC","h_msg_cd":"WRT300004"}',
            "logged_out",
            1,
        ),
        (200, "text/html", '{"strResult":"FAIL"}', "logged_out", 1),
        (200, "text/html", "<html>Temporary error</html>", "source_unavailable", 1),
        (200, "text/html", '{"strResult":', "source_unavailable", 1),
        (200, "application/json", '{"strResult":', "source_unavailable", 1),
        (200, "text/html", "null", "source_unavailable", 1),
        (200, "text/html", "[]", "source_unavailable", 1),
        (200, "text/html", '"SUCC"', "source_unavailable", 1),
        (200, "text/html", "{}", "source_unavailable", 1),
        (200, "text/html", '{"strResult":1}', "source_unavailable", 1),
        (200, "text/html", '{"strResult":"SUCC","h_msg_cd":0}', "source_unavailable", 1),
        (200, "text/html", '{"strResult":"SUCC","h_msg_cd":{}}', "source_unavailable", 1),
        (200, "text/plain", '{"strResult":"SUCC"}', "source_unavailable", 0),
        (200, "", '{"strResult":"SUCC"}', "source_unavailable", 0),
        (200, "text/html-other", '{"strResult":"SUCC"}', "source_unavailable", 0),
        (429, "text/html", '{"strResult":"SUCC"}', "rate_limited", 0),
        (403, "text/html", '{"strResult":"SUCC"}', "protected", 0),
        (500, "text/html", '{"strResult":"SUCC"}', "source_unavailable", 0),
    ],
    ids=(
        "json-authenticated",
        "official-html-json-authenticated",
        "mime-case-and-parameters",
        "empty-message-code-authenticated",
        "null-message-code-authenticated",
        "official-html-json-logged-out",
        "json-logged-out",
        "explicit-failure",
        "html-error-page",
        "invalid-html-json",
        "invalid-json",
        "null-payload",
        "array-payload",
        "scalar-payload",
        "missing-result",
        "non-string-result",
        "numeric-message-code",
        "object-message-code",
        "unsupported-mime",
        "missing-mime",
        "mime-prefix-is-not-html",
        "rate-limit-precedes-body",
        "protection-precedes-body",
        "server-error-precedes-body",
    ),
)
@pytest.mark.asyncio
async def test_official_session_probe_executes_json_mime_and_failure_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    mime: str,
    body: str,
    expected: str,
    body_reads: int,
) -> None:
    """Execute the production browser script with fixture fetch, without network I/O."""

    node = shutil.which("node")
    if node is None:
        # make verify-api installs the browser extra, whose pinned Playwright
        # runtime already includes Node. This regression must not silently skip.
        from playwright._impl._driver import compute_driver_executable

        node, _ = compute_driver_executable()
        assert Path(node).is_file(), "The browser verification runtime must include Node.js"
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1000, True)
    harness = """
        const fs = require('node:fs');
        const vm = require('node:vm');
        const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
        let calls = 0;
        let reads = 0;
        const context = {
          fetch: async (url, options) => {
            calls++;
            if (url !== '/ebizweb/common/loginCheck?Device=BH&Version=999999999'
                || options.method !== 'GET' || options.credentials !== 'same-origin'
                || options.cache !== 'no-store') throw new Error('Unexpected probe');
            return {
              status: fixture.status,
              ok: fixture.status >= 200 && fixture.status < 300,
              headers: { get: name => name === 'content-type' ? fixture.mime : null },
              json: async () => { reads++; return JSON.parse(fixture.body); },
            };
          },
        };
        Promise.resolve(vm.runInNewContext(fixture.script, context, { timeout: 1000 }))
          .then(value => process.stdout.write(JSON.stringify({ value, calls, reads })))
          .catch(() => { process.exitCode = 1; });
    """
    executions: list[str] = []

    async def execute(script: str, **kwargs: object) -> dict[str, object]:
        assert kwargs == {"return_by_value": True, "await_promise": True, "timeout": 1000}
        completed = subprocess.run(
            [node, "-e", harness],
            input=json.dumps({"script": script, "status": status, "mime": mime, "body": body}),
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        result = json.loads(completed.stdout)
        assert result == {"value": {"outcome": expected}, "calls": 1, "reads": body_reads}
        executions.append(expected)
        return {"result": {"result": {"value": result["value"]}}}

    monkeypatch.setattr(session._login_driver, "_execute_script", execute)
    if expected in {"authenticated", "logged_out"}:
        assert await session._probe_official_authenticated_session() is (
            expected == "authenticated"
        )
    else:
        error_type = {
            "source_unavailable": BrowserSourceUnavailable,
            "rate_limited": BrowserRateLimited,
            "protected": BrowserProtectionDetected,
        }[expected]
        with pytest.raises(error_type):
            await session._probe_official_authenticated_session()
    assert executions == [expected]


def _credential() -> KorailCredentialInput:
    return KorailCredentialInput(
        login_id="fixture-account",
        password="fixture-password",
        version="credential-v1",
    )


class ObservedLoginTab:
    def __init__(self) -> None:
        self.network_events_enabled = True
        self.callbacks: dict[int, tuple[object, Callable[[dict[str, object]], None]]] = {}
        self.removed: list[int] = []
        self.go_to = AsyncMock()

    async def on(self, event: object, callback: Callable[[dict[str, object]], None]) -> int:
        callback_id = len(self.callbacks) + 1
        self.callbacks[callback_id] = (event, callback)
        return callback_id

    async def remove_callback(self, callback_id: int) -> None:
        self.removed.append(callback_id)
        del self.callbacks[callback_id]

    def emit(self, event: str, params: dict[str, object]) -> None:
        for binding, callback in tuple(self.callbacks.values()):
            if binding == event:
                callback({"params": params})

    def request(self, request_id: str = "login") -> None:
        self.emit(
            "request",
            {
                "requestId": request_id,
                "type": "XHR",
                "request": {"method": "POST", "url": "https://www.korail.com/dynamic-login"},
            },
        )

    def response(self, status: int = 200) -> None:
        self.emit(
            "response",
            {
                "requestId": "login",
                "type": "XHR",
                "response": {"status": status, "url": "https://www.korail.com/dynamic-login"},
            },
        )

    def finish(self) -> None:
        self.emit("finished", {"requestId": "login"})


def prepare_observed_login(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout_ms: int = 1000,
) -> tuple[_PydollSession, ObservedLoginTab, AsyncMock]:
    monkeypatch.setattr(
        observation_module,
        "_submission_events",
        lambda: ("request", "response", "finished", "failed"),
    )
    session = _PydollSession("https://www.korail.com/ticket/search/general", timeout_ms, True)
    tab = ObservedLoginTab()
    session._tab = tab
    input_control = SimpleNamespace(clear=AsyncMock(), type_text=AsyncMock())
    submit = AsyncMock()
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    monkeypatch.setattr(
        session,
        "_wait_for_unique_login_method_tab",
        AsyncMock(return_value=SimpleNamespace(click=AsyncMock())),
    )
    monkeypatch.setattr(
        session,
        "_wait_for_login_controls",
        AsyncMock(return_value=(input_control, input_control, SimpleNamespace(click=submit))),
    )
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리", (), url="https://www.korail.com/ticket/login"
            )
        ),
    )
    monkeypatch.setattr(session, "_confirm_authenticated_search", AsyncMock(return_value=True))
    return session, tab, submit


@pytest.mark.asyncio
async def test_normal_login_never_probes_until_submission_loading_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    submit.side_effect = tab.request
    stages: list[str] = []

    async def advance(_: float) -> None:
        probe.assert_not_awaited()
        assert session._login_driver._submission is not None
        stages.append(session._login_driver._submission.snapshot().state)
        if len(stages) == 1:
            tab.response()
        else:
            tab.finish()

    session._login_driver._sleep = advance
    assert await session.ensure_authenticated(_credential()) is True
    assert stages == ["posted", "in_flight"]
    submit.assert_awaited_once()
    probe.assert_awaited_once()
    assert tab.callbacks == {}
    assert tab.removed == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_existing_header_is_not_accepted_while_submission_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    header = AsyncMock(side_effect=(False, True))
    monkeypatch.setattr(session, "_has_authenticated_header", header)
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    submit.side_effect = tab.request

    async def finish(_: float) -> None:
        assert header.await_count == 1
        probe.assert_not_awaited()
        tab.response()
        tab.finish()

    session._login_driver._sleep = finish
    assert await session.ensure_authenticated(_credential()) is True
    probe.assert_not_awaited()
    assert tab.callbacks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http_500", "network", "missing", "ambiguous"])
@pytest.mark.parametrize("in_place", [False, True])
async def test_submission_failure_is_source_unavailable_and_never_auth_required(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    in_place: bool,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def fail() -> None:
        if failure == "missing":
            return
        tab.request()
        if failure == "http_500":
            tab.response(500)
        elif failure == "network":
            tab.emit("failed", {"requestId": "login", "errorText": "fixture-error"})
        else:
            tab.request("second")

    submit.side_effect = fail
    if failure == "missing":
        ticks = iter((0.0, 0.0, 2.0))
        session._login_driver._monotonic = lambda: next(ticks, 2.0)
    with pytest.raises(BrowserSourceUnavailable) as error:
        if in_place:
            await session._authenticate_in_place(_credential())
        else:
            await session.ensure_authenticated(_credential())
    assert error.value.stage == "login_response"
    if failure == "missing":
        assert type(error.value) is BrowserSourceUnavailable
    else:
        assert isinstance(error.value, PydollLoginResponseUnavailable)
        assert error.value.failure_kind == "provider_submission_failed"
        assert error.value.retry_after_seconds == 300
        diagnostics = {
            "http_500": ("failed", "http_error", 500),
            "network": ("failed", "network_error", None),
            "ambiguous": ("ambiguous", "ambiguous", None),
        }
        assert (
            error.value.submission_state,
            error.value.submission_failure,
            error.value.submission_status,
        ) == diagnostics[failure]
    submit.assert_awaited_once()
    probe.assert_not_awaited()
    assert tab.callbacks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["posted", "in_flight", "unavailable_probe", "logged_out"])
async def test_login_timeout_requires_explicit_negative_proof_to_return_auth_required(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(
        side_effect=BrowserSourceUnavailable("session_keepalive")
        if phase == "unavailable_probe"
        else None,
        return_value=False,
    )
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def submitted() -> None:
        tab.request()
        if phase != "posted":
            tab.response()
        if phase in {"unavailable_probe", "logged_out"}:
            tab.finish()

    submit.side_effect = submitted
    ticks = iter((0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    session._login_driver._sleep = AsyncMock()
    if phase == "logged_out":
        assert await session.ensure_authenticated(_credential()) is False
        probe.assert_awaited_once()
    else:
        with pytest.raises(BrowserSourceUnavailable) as error:
            await session.ensure_authenticated(_credential())
        assert error.value.stage == "login_response"
        assert isinstance(error.value, PydollLoginResponseUnavailable)
        if phase == "unavailable_probe":
            probe.assert_awaited_once()
        else:
            probe.assert_not_awaited()
    assert tab.callbacks == {}


@pytest.mark.asyncio
async def test_login_business_500_is_classified_at_login_response_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)

    async def failed() -> None:
        tab.request()
        tab.response(500)

    submit.side_effect = failed
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "통신 오류",
                (),
                network_responses=((500, "business_xhr"),),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )
    with pytest.raises(BrowserSourceUnavailable) as error:
        await session.ensure_authenticated(_credential())
    assert isinstance(error.value, PydollLoginResponseUnavailable)
    assert error.value.stage == "login_response"
    assert tab.callbacks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "step"), [("posted", "snapshot"), ("completed", "snapshot"), ("completed", "header")]
)
async def test_dom_observation_failure_after_dispatch_keeps_provider_retry_metadata(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    step: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)

    async def submitted() -> None:
        tab.request()
        if phase == "completed":
            tab.response()
            tab.finish()
        if step == "header":
            monkeypatch.setattr(
                session,
                "_has_authenticated_header",
                AsyncMock(side_effect=BrowserSourceUnavailable("fixture_dom")),
            )

    submit.side_effect = submitted
    if step == "snapshot":
        monkeypatch.setattr(
            session, "_snapshot", AsyncMock(side_effect=BrowserSourceUnavailable("fixture_dom"))
        )
    with pytest.raises(PydollLoginResponseUnavailable) as error:
        await session.ensure_authenticated(_credential())
    assert error.value.failure_kind == "provider_submission_failed"
    assert error.value.retry_after_seconds == 300
    assert error.value.stage == "login_response"
    assert tab.callbacks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_submission_preserves_explicit_http_protection_before_source_failure(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)

    async def blocked() -> None:
        tab.request()
        tab.response(status)

    submit.side_effect = blocked
    expected = BrowserProtectionDetected if status == 403 else BrowserRateLimited
    with pytest.raises(expected):
        await session.ensure_authenticated(_credential())
    assert tab.callbacks == {}


@pytest.mark.asyncio
async def test_provider_maintenance_has_priority_over_login_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)

    async def failed() -> None:
        tab.request()
        tab.response(500)

    submit.side_effect = failed
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "서비스 점검", (), url="https://www.korail.com/rejectservice_job.html"
            )
        ),
    )
    with pytest.raises(BrowserProviderUnavailable) as error:
        await session.ensure_authenticated(_credential())
    assert error.value.trigger == "maintenance_page"
    assert tab.callbacks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("in_place", [False, True])
async def test_cancellation_releases_login_observer_for_both_authentication_routes(
    monkeypatch: pytest.MonkeyPatch,
    in_place: bool,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    submit.side_effect = tab.request
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        if in_place:
            await session._authenticate_in_place(_credential())
        else:
            await session.ensure_authenticated(_credential())
    assert tab.callbacks == {}
    assert session._login_driver._submission is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rotate", "close", "exit"])
async def test_browser_releases_submission_listeners_before_tab_or_browser_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    submit.side_effect = tab.request
    assert await session._submit_login_form(_credential()) is True
    assert len(tab.callbacks) == 4

    async def lifecycle_cleanup(**_: object) -> None:
        assert tab.callbacks == {}

    monkeypatch.setattr(session._chromium_lifecycle, "replace_tab", lifecycle_cleanup)
    monkeypatch.setattr(session._chromium_lifecycle, "close", lifecycle_cleanup)
    if operation == "rotate":
        await session._replace_tab()
    elif operation == "close":
        await session._close()
    else:
        await session.__aexit__(None, None, None)
    assert tab.callbacks == {}


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
