from __future__ import annotations

import ast
import asyncio
import inspect
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
from rail_waitlist.korail_sidecar.provider_cooldown import (
    MemoryProviderCooldown,
    ProviderCooldownDeferred,
)
from rail_waitlist.korail_sidecar.pydoll import login_submission_context as observation_module
from rail_waitlist.korail_sidecar.pydoll.login_driver import login_step
from rail_waitlist.korail_sidecar.pydoll.login_submission import PydollLoginResponseUnavailable
from rail_waitlist.korail_sidecar.pydoll.login_submission_pacing import (
    LoginSubmissionPurpose,
    login_submission_pacing,
)
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
        (
            200,
            "text/html",
            '{"errCode":"macro_err1","errMsg":"fixture-private-message"}',
            "source_unavailable",
            1,
        ),
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
        "protection-error-code-is-observed",
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
    caplog: pytest.LogCaptureFixture,
    status: int,
    mime: str,
    body: str,
    expected: str,
    body_reads: int,
) -> None:
    """Execute the production browser script with fixture fetch, without network I/O."""

    caplog.set_level("INFO")
    expected_value: dict[str, object] = {"outcome": expected}
    if "errCode" in body:
        expected_value["err_code"] = "macro_err1"
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
        completed = await asyncio.to_thread(
            subprocess.run,
            [node, "-e", harness],
            input=json.dumps({"script": script, "status": status, "mime": mime, "body": body}),
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        result = json.loads(completed.stdout)
        assert result == {"value": expected_value, "calls": 1, "reads": body_reads}
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
    probe_logs = [
        row.getMessage()
        for row in caplog.records
        if "login session probe response" in row.getMessage()
    ]
    if "err_code" in expected_value:
        assert probe_logs == [
            "KORAIL login session probe response outcome=source_unavailable "
            "login_err_codes=('macro_err1',)"
        ]
    else:
        assert probe_logs == []
    assert "fixture-private-message" not in caplog.text
    completed_logs = [
        row.getMessage()
        for row in caplog.records
        if "login session probe completed" in row.getMessage()
    ]
    assert completed_logs == [f"KORAIL login session probe completed outcome={expected}"]


def _credential() -> KorailCredentialInput:
    return KorailCredentialInput(
        login_id="fixture-account",
        password="fixture-password",
        version="credential-v1",
    )


def _login_post_data() -> str:
    return json.dumps(
        {
            "UserId": "fixture-account",
            "selInputFlg": "2",
            "hidMemberFlg": "1",
            "txtDv": "2",
            "encUserId": "fixture-private-encrypted-account",
            "keyname": "fixture-private-key",
            "encUserPwd": "fixture-private-encrypted-password",
            "useKeySec": "N",
            "deviceId": "fixture-private-device",
        }
    )


class ObservedLoginTab:
    def __init__(self) -> None:
        self.network_events_enabled = True
        self.callbacks: dict[int, tuple[object, Callable[[dict[str, object]], object]]] = {}
        self.removed: list[int] = []
        self.go_to = AsyncMock()
        self.command_calls: list[dict[str, object]] = []
        self.response_bodies: dict[str, object] = {}
        self.stack_traces: dict[str, dict[str, object]] = {}

    async def on(self, event: object, callback: Callable[[dict[str, object]], object]) -> int:
        callback_id = len(self.callbacks) + 1
        self.callbacks[callback_id] = (event, callback)
        return callback_id

    async def remove_callback(self, callback_id: int) -> None:
        self.removed.append(callback_id)
        del self.callbacks[callback_id]

    def emit(self, event: str, params: dict[str, object]) -> None:
        for binding, callback in tuple(self.callbacks.values()):
            if binding == event:
                result = callback({"params": params})
                if asyncio.iscoroutine(result):
                    try:
                        result.send(None)
                    except StopIteration:
                        pass
                    else:
                        result.close()
                        raise AssertionError("fixture callback unexpectedly suspended")

    async def emit_async(self, event: str, params: dict[str, object]) -> None:
        for binding, callback in tuple(self.callbacks.values()):
            if binding == event:
                result = callback({"params": params})
                if inspect.isawaitable(result):
                    await result

    def request(
        self,
        request_id: str = "login",
        *,
        url: str = "https://www.korail.com/dynamic-login",
        method: str = "POST",
        resource_type: str = "XHR",
        post_data: object = None,
        initiator: object = None,
    ) -> None:
        request_data: dict[str, object] = {"method": method, "url": url}
        if post_data is not None:
            request_data["postData"] = post_data
        params: dict[str, object] = {
            "requestId": request_id,
            "type": resource_type,
            "request": request_data,
        }
        if initiator is not None:
            params["initiator"] = initiator
        self.emit(
            "request",
            params,
        )

    def response(
        self,
        status: int = 200,
        *,
        request_id: str = "login",
        url: str = "https://www.korail.com/dynamic-login",
        resource_type: str = "XHR",
        mime: object = None,
    ) -> None:
        response_data: dict[str, object] = {"status": status, "url": url}
        if mime is not None:
            response_data["mimeType"] = mime
        self.emit(
            "response",
            {
                "requestId": request_id,
                "type": resource_type,
                "response": response_data,
            },
        )

    def finish(self, request_id: str = "login", *, encoded_data_length: object = 512) -> None:
        self.emit(
            "finished",
            {"requestId": request_id, "encodedDataLength": encoded_data_length},
        )

    async def _execute_command(self, command: dict[str, object]) -> object:
        self.command_calls.append(command)
        params = command.get("params")
        stack_trace_id = params.get("stackTraceId") if isinstance(params, dict) else None
        identifier = stack_trace_id.get("id") if isinstance(stack_trace_id, dict) else None
        if command.get("method") == "Debugger.getStackTrace":
            stack = self.stack_traces.get(str(identifier))
            return {"result": {"stackTrace": stack}} if stack is not None else {"result": {}}
        request_id = params.get("requestId") if isinstance(params, dict) else None
        return self.response_bodies.get(
            request_id if isinstance(request_id, str) else "",
            {"result": {}},
        )


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
    input_control = SimpleNamespace(
        clear=AsyncMock(),
        type_text=AsyncMock(),
        execute_script=AsyncMock(return_value={"result": {"result": {"value": True}}}),
    )
    submit = AsyncMock()
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    monkeypatch.setattr(session._login_driver, "_find_login_controls", AsyncMock(return_value=None))
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
@pytest.mark.parametrize("in_place", [False, True])
@pytest.mark.parametrize("authenticated", [False, True])
async def test_terminal_login_log_keeps_only_closed_request_facts(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    in_place: bool,
    authenticated: bool,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=authenticated),
    )
    caplog.set_level("INFO")

    async def submitted() -> None:
        url = "https://www.korail.com/ebizweb/common/loginProcess?fixture-private-query"
        tab.request(
            "fixture-private-request-id",
            url=url,
            post_data=_login_post_data(),
        )
        tab.response(
            200,
            request_id="fixture-private-request-id",
            url=url,
            mime="application/json",
        )
        tab.finish("fixture-private-request-id")
        tab.response_bodies["fixture-private-request-id"] = {
            "result": {
                "body": json.dumps(
                    {
                        "strResult": "FAIL",
                        "h_msg_cd": "fixture-private-response-code",
                        "h_msg_txt": "fixture-private-response-message",
                    }
                ),
                "base64Encoded": False,
            }
        }

    submit.side_effect = submitted
    if not authenticated:
        ticks = iter((0.0, 0.0, 2.0))
        session._login_driver._monotonic = lambda: next(ticks, 2.0)
        session._login_driver._sleep = AsyncMock()

    result = (
        await session._authenticate_in_place(_credential())
        if in_place
        else await session.ensure_authenticated(_credential())
    )

    assert result is authenticated
    assert tab.callbacks == {}
    observations = [
        row.getMessage()
        for row in caplog.records
        if "login submission terminal observation" in row.getMessage()
    ]
    assert len(observations) == 1
    expected_outcome = "authenticated" if authenticated else "auth_required"
    assert f"outcome={expected_outcome}" in observations[0]
    rows = json.loads(observations[0].partition("requests=")[2])
    assert rows[0]["request_role"] == "login_submission"
    assert rows[0]["payload_role"] == "login_submission"
    assert rows[0]["status"] == 200
    assert rows[0]["terminal"] == "completed"
    assert rows[0]["response_auth_outcome"] == ("not_observed" if authenticated else "rejected")
    body_commands = [
        command
        for command in tab.command_calls
        if command.get("method") == "Network.getResponseBody"
    ]
    assert len(body_commands) == (0 if authenticated else 1)
    for private_value in (
        "fixture-account",
        "fixture-password",
        "fixture-private-encrypted-account",
        "fixture-private-encrypted-password",
        "fixture-private-query",
        "fixture-private-request-id",
        "fixture-private-response-code",
        "fixture-private-response-message",
        "https://",
    ):
        assert private_value not in observations[0]


@pytest.mark.asyncio
async def test_public_key_bootstrap_is_not_mistaken_for_protected_login_transport(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    caplog.set_level("INFO")

    async def submitted() -> None:
        key_url = "https://www.korail.com/ebizweb/tour/mypage/pwd_action/pblk"
        tab.request("key", url=key_url, method="GET", resource_type="Fetch")
        tab.response(200, request_id="key", url=key_url, resource_type="Fetch")
        tab.finish("key")
        assert session._login_driver._submission is not None
        assert session._login_driver._submission.snapshot().state == "missing"

        bundle_url = "https://cdn.korail.com/bundle/bundle.fcfd32491c1f6e0b2541.js?v=1.0.221"
        tab.stack_traces["bootstrap-parent"] = {
            "callFrames": [
                {
                    "functionName": "callApi",
                    "url": bundle_url,
                    "lineNumber": 2,
                    "columnNumber": 30054,
                }
            ]
        }
        bootstrap_url = "https://www.korail.com/fixture-bootstrap-transport"
        await tab.emit_async(
            "request",
            {
                "requestId": "bootstrap",
                "type": "Fetch",
                "request": {"method": "POST", "url": bootstrap_url},
                "initiator": {
                    "type": "script",
                    "stack": {
                        "callFrames": [
                            {
                                "functionName": "requestWrapper",
                                "url": bundle_url,
                                "lineNumber": 403,
                                "columnNumber": 3377,
                            }
                        ]
                        * 9,
                        "parentId": {"id": "bootstrap-parent"},
                    },
                },
            },
        )
        tab.response(
            200,
            request_id="bootstrap",
            url=bootstrap_url,
            resource_type="Fetch",
            mime="application/json",
        )
        tab.finish("bootstrap", encoded_data_length=64)
        assert session._login_driver._submission.snapshot().state == "missing"

        tab.stack_traces["login-parent"] = {
            "callFrames": [
                {
                    "functionName": "callApi",
                    "url": bundle_url,
                    "lineNumber": 2,
                    "columnNumber": 30620,
                }
            ]
        }
        login_url = "https://www.korail.com/fixture-protection-transport"
        await tab.emit_async(
            "request",
            {
                "requestId": "login",
                "type": "Fetch",
                "request": {"method": "POST", "url": login_url},
                "initiator": {
                    "type": "script",
                    "stack": {
                        "callFrames": [
                            {
                                "functionName": "requestWrapper",
                                "url": bundle_url,
                                "lineNumber": 403,
                                "columnNumber": 3511,
                            }
                        ],
                        "parentId": {"id": "login-parent"},
                    },
                },
            },
        )
        tab.response(
            200,
            request_id="login",
            url=login_url,
            resource_type="Fetch",
            mime="application/json",
        )
        tab.finish("login", encoded_data_length=64)
        tab.response_bodies["login"] = {
            "result": {
                "body": '{"transport":"fixture-private-envelope"}',
                "base64Encoded": False,
            }
        }

    submit.side_effect = submitted
    ticks = iter((0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    session._login_driver._sleep = AsyncMock()

    assert await session.ensure_authenticated(_credential()) is False
    probe.assert_awaited_once()
    observation = next(
        row.getMessage()
        for row in caplog.records
        if "login submission terminal observation" in row.getMessage()
    )
    rows = json.loads(observation.partition("requests=")[2])
    assert [(row["path_family"], row["request_role"]) for row in rows] == [
        ("public_key", "login_bootstrap"),
        ("official_other", "login_bootstrap"),
        ("official_other", "login_submission"),
    ]
    assert rows[0]["response_auth_outcome"] == "not_observed"
    assert rows[1]["response_auth_outcome"] == "not_observed"
    assert rows[2]["response_auth_outcome"] == "not_login_result"
    assert rows[2]["response_schema_fields"] == ["transport"]
    assert [row["initiator_completion_reason"] for row in rows[1:]] == [
        "complete",
        "complete",
    ]
    assert rows[1]["initiator_public_frames_truncated"] is True
    assert len(rows[1]["initiator_public_frames"]) == 8
    assert rows[2]["initiator_public_frames_truncated"] is False
    assert "fixture-private-envelope" not in observation


@pytest.mark.asyncio
async def test_diagnostic_accepted_body_cannot_override_negative_session_probe(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )
    caplog.set_level("INFO")

    async def submitted() -> None:
        url = "https://www.korail.com/ebizweb/common/loginProcess"
        tab.request(post_data=_login_post_data(), url=url)
        tab.response(url=url, mime="application/json")
        tab.finish()
        tab.response_bodies["login"] = {
            "result": {
                "body": json.dumps(
                    {
                        "strResult": "SUCC",
                        "h_msg_cd": "IRZ000001",
                        "hc14100En": {"strCustNo": "fixture-private-customer"},
                    }
                ),
                "base64Encoded": False,
            }
        }

    submit.side_effect = submitted
    ticks = iter((0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    session._login_driver._sleep = AsyncMock()

    assert await session.ensure_authenticated(_credential()) is False
    observation = next(
        row.getMessage()
        for row in caplog.records
        if "login submission terminal observation" in row.getMessage()
    )
    rows = json.loads(observation.partition("requests=")[2])
    assert rows[0]["response_auth_outcome"] == "accepted"
    assert [
        command.get("method")
        for command in tab.command_calls
        if command.get("method") == "Network.getResponseBody"
    ] == ["Network.getResponseBody"]
    assert "fixture-private-customer" not in observation


@pytest.mark.asyncio
async def test_macro_response_is_promoted_to_provider_protection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )
    caplog.set_level("INFO")

    async def submitted() -> None:
        url = "https://www.korail.com/fixture-protection-transport"
        tab.request(post_data=_login_post_data(), url=url, resource_type="Fetch")
        tab.response(200, url=url, resource_type="Fetch", mime="application/json")
        tab.finish()
        tab.response_bodies["login"] = {
            "result": {
                "body": json.dumps(
                    {
                        "dynaPathResultCode": "fixture-private-result",
                        "errCode": "macro_err1",
                        "errEngMsg": "fixture-private-english",
                        "errMsg": "fixture-private-message",
                        "globalFlag": "fixture-private-flag",
                        "requestId": "fixture-private-request",
                    }
                ),
                "base64Encoded": False,
            }
        }

    submit.side_effect = submitted
    ticks = iter((0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    session._login_driver._sleep = AsyncMock()

    with pytest.raises(BrowserProtectionDetected) as captured:
        await session.ensure_authenticated(_credential())
    assert (captured.value.trigger, captured.value.stage) == (
        "marker_macro_err1",
        "login_response",
    )
    observation = next(
        row.getMessage()
        for row in caplog.records
        if "login submission failure observation" in row.getMessage()
    )
    rows = json.loads(observation.partition("requests=")[2])
    assert rows[0]["payload_role"] == "login_submission"
    assert rows[0]["request_payload_shape"] == "exact_login"
    assert rows[0]["response_auth_outcome"] == "protection"
    # The closed error code is the only response value that may be logged.
    assert rows[0]["response_err_codes"] == ["macro_err1"]
    assert (
        "KORAIL login protection response stage=login_response trigger=marker_macro_err1 "
        "response_auth_outcome=protection login_err_codes=('macro_err1',)"
    ) in caplog.text
    assert rows[0]["response_schema_fields"] == [
        "dynaPathResultCode",
        "errCode",
        "errEngMsg",
        "errMsg",
        "globalFlag",
        "requestId",
    ]
    for private_value in (
        "fixture-private-result",
        "fixture-private-english",
        "fixture-private-message",
        "fixture-private-flag",
        "fixture-private-request",
    ):
        assert private_value not in caplog.text
    assert tab.callbacks == {}


@pytest.mark.asyncio
async def test_page_marker_protection_still_records_login_response_error_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "macro_err1", (), url="https://www.korail.com/ticket/login"
            )
        ),
    )
    session._login_driver._sleep = AsyncMock()
    caplog.set_level("INFO")

    async def submitted() -> None:
        url = "https://www.korail.com/ebizweb/common/loginProcess"
        tab.request(post_data=_login_post_data(), url=url)
        tab.response(200, url=url, mime="application/json")
        tab.finish()
        tab.response_bodies["login"] = {
            "result": {
                "body": json.dumps({"errCode": "macro_err1", "errMsg": "fixture-private-message"}),
                "base64Encoded": False,
            }
        }

    submit.side_effect = submitted
    with pytest.raises(BrowserProtectionDetected) as captured:
        await session.ensure_authenticated(_credential())

    # The page marker wins before any session probe, as before; only diagnostics change.
    assert captured.value.stage == "authenticate"
    probe.assert_not_awaited()
    assert (
        "KORAIL login protection response stage=authenticate "
        f"trigger={captured.value.trigger} response_auth_outcome=protection "
        "login_err_codes=('macro_err1',)"
    ) in caplog.text
    assert "fixture-private-message" not in caplog.text
    assert tab.callbacks == {}


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
@pytest.mark.parametrize("positive_evidence", ["header", "official_probe", "none"])
async def test_two_completed_login_posts_still_require_independent_authentication_evidence(
    monkeypatch: pytest.MonkeyPatch,
    positive_evidence: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    header = AsyncMock(side_effect=(False, positive_evidence == "header"))
    monkeypatch.setattr(session, "_has_authenticated_header", header)
    probe = AsyncMock(return_value=positive_evidence == "official_probe")
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def submitted() -> None:
        tab.request()
        tab.response()
        tab.request("second")
        tab.finish()

    submit.side_effect = submitted

    async def finish_second(_: float) -> None:
        if positive_evidence == "none" and probe.await_count:
            return
        assert header.await_count == 1
        probe.assert_not_awaited()
        assert session._login_driver._submission is not None
        assert session._login_driver._submission.snapshot().safe_to_probe is False
        tab.response(request_id="second")
        tab.finish("second")

    session._login_driver._sleep = finish_second
    if positive_evidence == "none":
        ticks = iter((0.0, 0.0, 0.0, 2.0))
        session._login_driver._monotonic = lambda: next(ticks, 2.0)
    assert await session.ensure_authenticated(_credential()) is (positive_evidence != "none")
    submit.assert_awaited_once()
    if positive_evidence == "header":
        probe.assert_not_awaited()
    else:
        probe.assert_awaited_once()
    assert tab.callbacks == {}
    assert tab.removed == [1, 2, 3, 4]
    tab.request("late-after-close")
    tab.response(500, request_id="late-after-close")
    assert session._login_driver._submission is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival_during", ["header", "official_probe"])
async def test_late_post_during_authentication_evidence_blocks_success_until_finished(
    monkeypatch: pytest.MonkeyPatch,
    arrival_during: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    header_reads = 0

    async def read_header() -> bool:
        nonlocal header_reads
        header_reads += 1
        if header_reads == 1:
            return False
        if header_reads == 2:
            if arrival_during == "header":
                tab.request("second")
                return True
            return False
        return True

    header = AsyncMock(side_effect=read_header)
    monkeypatch.setattr(session, "_has_authenticated_header", header)

    async def read_probe() -> bool:
        tab.request("second")
        return True

    probe = AsyncMock(side_effect=read_probe)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def submitted() -> None:
        tab.request()
        tab.response()
        tab.finish()

    submit.side_effect = submitted
    waits = 0

    async def finish_late_post(_: float) -> None:
        nonlocal waits
        waits += 1
        assert header.await_count == 2
        assert probe.await_count == (arrival_during == "official_probe")
        assert session._login_driver._submission is not None
        assert session._login_driver._submission.snapshot().safe_to_probe is False
        tab.response(request_id="second")
        tab.finish("second")

    session._login_driver._sleep = finish_late_post
    assert await session.ensure_authenticated(_credential()) is True
    assert waits == 1
    submit.assert_awaited_once()
    assert probe.await_count == (arrival_during == "official_probe")
    assert tab.callbacks == {}
    assert tab.removed == [1, 2, 3, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_result", [False, True])
@pytest.mark.parametrize("completion", ["during_probe", "after_probe"])
async def test_changed_probe_group_never_attests_authentication_or_rejection(
    monkeypatch: pytest.MonkeyPatch,
    probe_result: bool,
    completion: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)

    async def submitted() -> None:
        tab.request()
        tab.response()
        tab.finish()

    submit.side_effect = submitted

    async def probe_session() -> bool:
        tab.request("second")
        if completion == "during_probe":
            tab.response(request_id="second")
            tab.finish("second")
        return probe_result

    probe = AsyncMock(side_effect=probe_session)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def finish_pending(_: float) -> None:
        if completion == "after_probe":
            tab.response(request_id="second")
            tab.finish("second")

    session._login_driver._sleep = finish_pending
    ticks = iter((0.0, 0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    with pytest.raises(PydollLoginResponseUnavailable) as error:
        await session.ensure_authenticated(_credential())
    assert error.value.stage == "login_response"
    assert error.value.retry_after_seconds == 300
    # 전송은 완료됐지만 바뀐 그룹의 과거 probe는 인증의 양성·음성 근거가 아니다.
    assert error.value.submission_state == "completed"
    assert error.value.submission_status == 200
    probe.assert_awaited_once()
    submit.assert_awaited_once()
    assert tab.callbacks == {}
    assert tab.removed == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_late_completed_group_after_negative_probe_cannot_reuse_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def submitted() -> None:
        tab.request()
        tab.response()
        tab.finish()

    submit.side_effect = submitted

    async def arrive_after_probe(_: float) -> None:
        probe.assert_awaited_once()
        tab.request("second")
        tab.response(request_id="second")
        tab.finish("second")

    session._login_driver._sleep = arrive_after_probe
    ticks = iter((0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    with pytest.raises(PydollLoginResponseUnavailable):
        await session.ensure_authenticated(_credential())
    probe.assert_awaited_once()
    submit.assert_awaited_once()
    assert tab.callbacks == {}


@pytest.mark.asyncio
async def test_completed_late_post_during_header_read_invalidates_old_positive_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    reads = 0

    async def header() -> bool:
        nonlocal reads
        reads += 1
        if reads == 2:
            tab.request("second")
            tab.response(request_id="second")
            tab.finish("second")
            return True
        return False

    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(side_effect=header))
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def submitted() -> None:
        tab.request()
        tab.response()
        tab.finish()

    submit.side_effect = submitted
    session._login_driver._sleep = AsyncMock()
    ticks = iter((0.0, 0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)
    assert await session.ensure_authenticated(_credential()) is False
    assert reads == 3
    probe.assert_awaited_once()
    submit.assert_awaited_once()
    assert tab.callbacks == {}
    assert tab.removed == [1, 2, 3, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 403, 429])
async def test_second_login_post_failure_blocks_probe_and_cleans_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)

    async def submitted() -> None:
        tab.request()
        tab.response()
        tab.finish()
        tab.request("second")
        tab.response(status, request_id="second")

    submit.side_effect = submitted
    expected = {
        500: PydollLoginResponseUnavailable,
        403: BrowserProtectionDetected,
        429: BrowserRateLimited,
    }[status]
    with pytest.raises(expected):
        await session.ensure_authenticated(_credential())
    probe.assert_not_awaited()
    submit.assert_awaited_once()
    assert tab.callbacks == {}
    assert tab.removed == [1, 2, 3, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header_present", "probe_outcome", "expected"),
    [
        (True, True, "authenticated"),
        (True, False, "unavailable"),
        (False, True, "unavailable"),
    ],
)
async def test_confirmed_integration_failure_requires_two_independent_auth_signals(
    monkeypatch: pytest.MonkeyPatch,
    header_present: bool,
    probe_outcome: bool,
    expected: str,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    header = AsyncMock(side_effect=(False, header_present))
    probe = AsyncMock(return_value=probe_outcome)
    monkeypatch.setattr(session, "_has_authenticated_header", header)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
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
    login_url = "https://www.korail.com/ebizweb/common/loginProcess"
    integration_url = "https://www.korail.com/ebizweb/integrate/srCheck.do"

    async def submitted() -> None:
        tab.request(url=login_url)
        tab.response(url=login_url)
        tab.finish()
        tab.request("integration", url=integration_url)
        tab.response(500, request_id="integration", url=integration_url)
        tab.finish("integration")

    submit.side_effect = submitted
    session._login_driver._sleep = AsyncMock()
    ticks = iter((0.0, 0.0, 2.0))
    session._login_driver._monotonic = lambda: next(ticks, 2.0)

    if expected == "authenticated":
        assert await session.ensure_authenticated(_credential()) is True
        probe.assert_awaited_once()
    else:
        with pytest.raises(PydollLoginResponseUnavailable):
            await session.ensure_authenticated(_credential())
        if header_present:
            probe.assert_awaited_once()
        else:
            probe.assert_not_awaited()
    submit.assert_awaited_once()
    assert tab.callbacks == {}


@pytest.mark.asyncio
async def test_confirmed_login_failure_cannot_be_overridden_by_positive_auth_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    header = AsyncMock(side_effect=(False, True))
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_has_authenticated_header", header)
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
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
    login_url = "https://www.korail.com/ebizweb/common/loginProcess"

    async def submitted() -> None:
        tab.request(url=login_url)
        tab.response(500, url=login_url)
        tab.finish()

    submit.side_effect = submitted
    with pytest.raises(PydollLoginResponseUnavailable):
        await session.ensure_authenticated(_credential())
    probe.assert_not_awaited()
    submit.assert_awaited_once()
    assert tab.callbacks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http_500", "network", "missing", "incomplete_multi"])
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
            # 두 번째 정상 요청은 허용하지만 미완료 제출 창은 timeout으로 닫힌다.
            assert session._login_driver._submission is not None
            session._login_driver._submission._monotonic = lambda: float("inf")

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
            "incomplete_multi": ("failed", "timeout", None),
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
@pytest.mark.parametrize("header_outcome", ["present", "absent", "unavailable", "timeout"])
@pytest.mark.parametrize("in_place", [False, True])
async def test_failure_observes_existing_header_before_disposal_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    header_outcome: str,
    in_place: bool,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock()
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    session._login_driver._timeout_seconds = 0.05
    caplog.set_level("INFO")

    async def existing_header() -> bool:
        assert tab.callbacks, "Failure evidence must be read before the observer is disposed"
        if header_outcome == "unavailable":
            raise RuntimeError("fixture-private-dom-error")
        if header_outcome == "timeout":
            await asyncio.sleep(60)
        return header_outcome == "present"

    header = AsyncMock(side_effect=existing_header)

    async def failed() -> None:
        tab.request()
        tab.response(500)
        tab.finish()
        monkeypatch.setattr(session, "_has_authenticated_header", header)

    submit.side_effect = failed
    with pytest.raises(PydollLoginResponseUnavailable) as error:
        if in_place:
            await session._authenticate_in_place(_credential())
        else:
            await session.ensure_authenticated(_credential())
    assert error.value.submission_status == 500
    header.assert_awaited_once()
    submit.assert_awaited_once()
    probe.assert_not_awaited()
    assert tab.go_to.await_count == (0 if in_place else 1)
    assert tab.callbacks == {}
    expected_header = "unavailable" if header_outcome == "timeout" else header_outcome
    observations = [
        row.getMessage()
        for row in caplog.records
        if "login submission failure observation" in row.getMessage()
    ]
    assert len(observations) == 1
    assert f"header={expected_header}" in observations[0]
    assert '"status": 500' in observations[0]
    for private_value in (
        "fixture-account",
        "fixture-password",
        "fixture-private-dom-error",
        "dynamic-login",
    ):
        assert private_value not in observations[0]


@pytest.mark.asyncio
async def test_failure_log_serializes_only_verified_public_frame_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    probe = AsyncMock()
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    caplog.set_level("INFO")

    async def failed() -> None:
        tab.emit(
            "request",
            {
                "requestId": "fixture-private-request-id",
                "type": "XHR",
                "request": {
                    "method": "POST",
                    "url": "https://www.korail.com/fixture-private-path?fixture-private-query",
                },
                "initiator": {
                    "stack": {
                        "callFrames": [
                            {
                                "functionName": "fixture-private-function",
                                "url": "https://cdn.korail.com/bundle/"
                                "bundle.fcfd32491c1f6e0b2541.js?fixture-private-script-query",
                                "lineNumber": 2,
                                "columnNumber": 30620,
                            }
                        ]
                    }
                },
            },
        )
        tab.response(500, request_id="fixture-private-request-id")
        tab.finish("fixture-private-request-id")

    submit.side_effect = failed
    with pytest.raises(PydollLoginResponseUnavailable):
        await session.ensure_authenticated(_credential())
    observation = next(
        row.getMessage()
        for row in caplog.records
        if "login submission failure observation" in row.getMessage()
    )
    rows = json.loads(observation.partition("requests=")[2])
    assert rows[0]["public_callsite"] == "login_submit"
    assert rows[0]["initiator_kind"] == "unknown"
    assert rows[0]["initiator_frame_count"] == 1
    assert rows[0]["initiator_source_scopes"] == ["verified_main_bundle"]
    assert rows[0]["response_media"] == "unknown"
    assert rows[0]["initiator_public_frames"] == [
        {
            "source_id": "verified_main_bundle",
            "source_query_present": True,
            "line_0based": 2,
            "utf16_column_0based": 30620,
        }
    ]
    assert "fixture-private" not in observation
    assert "https://" not in observation
    probe.assert_not_awaited()
    submit.assert_awaited_once()
    assert tab.callbacks == {}


@pytest.mark.asyncio
async def test_failure_observation_cancellation_still_releases_owned_listeners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tab, submit = prepare_observed_login(monkeypatch)
    reading = asyncio.Event()

    async def existing_header() -> bool:
        reading.set()
        await asyncio.Event().wait()
        return False

    async def failed() -> None:
        tab.request()
        tab.response(500)
        monkeypatch.setattr(session, "_has_authenticated_header", existing_header)

    submit.side_effect = failed
    task = asyncio.create_task(session.ensure_authenticated(_credential()))
    await asyncio.wait_for(reading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.callbacks == {}
    submit.assert_awaited_once()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("in_place", [False, True])
async def test_manual_session_mode_never_navigates_or_submits_when_logged_out(
    monkeypatch: pytest.MonkeyPatch,
    in_place: bool,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        False,
        manual_session_only=True,
    )
    go_to = AsyncMock()
    session._tab = SimpleNamespace(go_to=go_to)
    has_authenticated_header = AsyncMock(return_value=False)
    probe_authenticated_session = AsyncMock(return_value=False)
    submit_login_form = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_has_authenticated_header", has_authenticated_header)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        probe_authenticated_session,
    )
    monkeypatch.setattr(session, "_submit_login_form", submit_login_form)

    with pytest.raises(BrowserSourceUnavailable) as raised:
        if in_place:
            await session._authenticate_in_place(_credential())
        else:
            await session.ensure_authenticated(_credential())

    assert raised.value.stage == "manual_login_required"
    has_authenticated_header.assert_awaited_once_with()
    probe_authenticated_session.assert_awaited_once_with()
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("in_place", [False, True])
async def test_manual_session_mode_accepts_official_session_from_another_tab(
    monkeypatch: pytest.MonkeyPatch,
    in_place: bool,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        False,
        manual_session_only=True,
    )
    go_to = AsyncMock()
    session._tab = SimpleNamespace(go_to=go_to)
    has_authenticated_header = AsyncMock(return_value=False)
    probe_authenticated_session = AsyncMock(return_value=True)
    confirm_authenticated_search = AsyncMock(return_value=True)
    submit_login_form = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_has_authenticated_header", has_authenticated_header)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        probe_authenticated_session,
    )
    monkeypatch.setattr(session, "_confirm_authenticated_search", confirm_authenticated_search)
    monkeypatch.setattr(session, "_submit_login_form", submit_login_form)

    if in_place:
        assert await session._authenticate_in_place(_credential()) is True
        confirm_authenticated_search.assert_not_awaited()
    else:
        assert await session.ensure_authenticated(_credential()) is True
        confirm_authenticated_search.assert_awaited_once()
        attempt = confirm_authenticated_search.await_args.args[0]
        assert attempt.post_submit_check_attempted is True
        assert attempt.post_submit_authenticated is True

    has_authenticated_header.assert_awaited_once_with()
    probe_authenticated_session.assert_awaited_once_with()
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("in_place", [False, True])
async def test_manual_session_mode_retains_handoff_when_official_probe_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    in_place: bool,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        False,
        manual_session_only=True,
    )
    go_to = AsyncMock()
    session._tab = SimpleNamespace(go_to=go_to)
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    probe_authenticated_session = AsyncMock(
        side_effect=BrowserSourceUnavailable("session_keepalive")
    )
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        probe_authenticated_session,
    )
    submit_login_form = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_submit_login_form", submit_login_form)

    with pytest.raises(BrowserSourceUnavailable) as raised:
        if in_place:
            await session._authenticate_in_place(_credential())
        else:
            await session.ensure_authenticated(_credential())

    assert raised.value.stage == "manual_login_required"
    probe_authenticated_session.assert_awaited_once_with()
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("in_place", [False, True])
@pytest.mark.parametrize(
    "error",
    [BrowserProtectionDetected(), BrowserRateLimited()],
    ids=("protected", "rate-limited"),
)
async def test_manual_session_mode_preserves_provider_limits_from_official_probe(
    monkeypatch: pytest.MonkeyPatch,
    in_place: bool,
    error: Exception,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        False,
        manual_session_only=True,
    )
    go_to = AsyncMock()
    session._tab = SimpleNamespace(go_to=go_to)
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    probe_authenticated_session = AsyncMock(side_effect=error)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        probe_authenticated_session,
    )
    submit_login_form = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_submit_login_form", submit_login_form)

    with pytest.raises(type(error)) as raised:
        if in_place:
            await session._authenticate_in_place(_credential())
        else:
            await session.ensure_authenticated(_credential())

    assert raised.value is error
    probe_authenticated_session.assert_awaited_once_with()
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_session_mode_accepts_existing_authenticated_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        False,
        manual_session_only=True,
    )
    session._tab = SimpleNamespace(go_to=AsyncMock())
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=True))
    confirm_authenticated_search = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_confirm_authenticated_search", confirm_authenticated_search)

    assert await session.ensure_authenticated(_credential()) is True

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


def _restored_profile_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    probe: AsyncMock,
    probe_restored_session: bool = True,
) -> tuple[_PydollSession, AsyncMock, AsyncMock, AsyncMock]:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        False,
        probe_restored_session=probe_restored_session,
    )
    go_to = AsyncMock()
    session._tab = SimpleNamespace(go_to=go_to)
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    monkeypatch.setattr(session, "_probe_official_authenticated_session", probe)
    submit_login_form = AsyncMock(return_value=True)
    confirm_authenticated_search = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_submit_login_form", submit_login_form)
    monkeypatch.setattr(session, "_wait_for_login_authentication", AsyncMock(return_value=True))
    monkeypatch.setattr(session, "_confirm_authenticated_search", confirm_authenticated_search)
    return session, go_to, submit_login_form, confirm_authenticated_search


@pytest.mark.asyncio
async def test_restored_profile_session_is_reused_before_submitting_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    probe = AsyncMock(return_value=True)
    session, go_to, submit_login_form, confirm = _restored_profile_session(monkeypatch, probe=probe)

    with caplog.at_level("INFO"):
        assert await session.ensure_authenticated(_credential()) is True

    probe.assert_awaited_once_with()
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()
    attempt = confirm.await_args.args[0]
    assert attempt.post_submit_check_attempted is True
    assert attempt.post_submit_authenticated is True
    assert "stage=restored_session present=true" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe_restored_session", "probe_result"),
    [
        (True, False),
        (True, BrowserSourceUnavailable("session_keepalive")),
        (False, True),
    ],
    ids=("logged-out", "probe-unavailable", "ephemeral-profile-skips-probe"),
)
async def test_restored_profile_probe_falls_back_to_the_existing_login(
    monkeypatch: pytest.MonkeyPatch,
    probe_restored_session: bool,
    probe_result: object,
) -> None:
    probe = (
        AsyncMock(side_effect=probe_result)
        if isinstance(probe_result, Exception)
        else AsyncMock(return_value=probe_result)
    )
    session, go_to, submit_login_form, _confirm = _restored_profile_session(
        monkeypatch, probe=probe, probe_restored_session=probe_restored_session
    )

    assert await session.ensure_authenticated(_credential()) is True

    assert probe.await_count == (1 if probe_restored_session else 0)
    go_to.assert_awaited_once_with("https://www.korail.com/ticket/login", timeout=1)
    submit_login_form.assert_awaited_once_with(_credential())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [BrowserProtectionDetected(), BrowserRateLimited()],
    ids=("protected", "rate-limited"),
)
async def test_restored_profile_probe_preserves_provider_limits(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    session, go_to, submit_login_form, _confirm = _restored_profile_session(
        monkeypatch, probe=AsyncMock(side_effect=error)
    )

    with pytest.raises(type(error)) as raised:
        await session.ensure_authenticated(_credential())

    assert raised.value is error
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_login_inside_the_submission_interval_never_opens_the_login_page(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = [1_700_000_000.0]
    pacer = MemoryProviderCooldown(clock=lambda: now[0], login_submission_min_interval_seconds=1800)
    await pacer.record_login_submission()
    now[0] += 1777
    probe = AsyncMock(return_value=False)
    session, go_to, submit_login_form, _confirm = _restored_profile_session(
        monkeypatch, probe=probe
    )

    with (
        caplog.at_level("INFO"),
        login_submission_pacing(pacer, LoginSubmissionPurpose.BACKGROUND),
        pytest.raises(ProviderCooldownDeferred) as caught,
    ):
        await session.ensure_authenticated(_credential())

    assert caught.value.retry_after_seconds == 23
    # The credential-free official probe still ran; only the submission was deferred.
    probe.assert_awaited_once_with()
    go_to.assert_not_awaited()
    submit_login_form.assert_not_awaited()
    assert "login submission deferred reason=provider_access_restricted" in caplog.text

    with login_submission_pacing(pacer, LoginSubmissionPurpose.REQUIRED):
        assert await session.ensure_authenticated(_credential()) is True
    submit_login_form.assert_awaited_once_with(_credential())
