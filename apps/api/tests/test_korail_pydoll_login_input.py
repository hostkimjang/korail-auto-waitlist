"""Login preparation preserves fresh official controls and guards submission."""

from __future__ import annotations

import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rail_waitlist.korail_pydoll_browser import KorailCredentialInput, _PydollSession
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.auth_contracts import KorailLoginMethod


def input_control(results: list[bool]) -> SimpleNamespace:
    return SimpleNamespace(
        clear=AsyncMock(),
        type_text=AsyncMock(),
        execute_script=AsyncMock(
            side_effect=[{"result": {"result": {"value": value}}} for value in results]
        ),
    )


def prepare(monkeypatch, *, selected: bool, identity: list[bool], password: list[bool]):
    session = _PydollSession("https://www.korail.com/ticket/login", 1000, True)
    method = SimpleNamespace(click=AsyncMock())
    controls = input_control(identity), input_control(password), SimpleNamespace(click=AsyncMock())
    monkeypatch.setattr(
        session, "_wait_for_unique_login_method_tab", AsyncMock(return_value=method)
    )
    monkeypatch.setattr(session, "_wait_for_login_controls", AsyncMock(return_value=controls))
    monkeypatch.setattr(
        session._login_driver,
        "_find_login_controls",
        AsyncMock(return_value=controls if selected else None),
    )
    submission = SimpleNamespace(arm=lambda: None)

    @asynccontextmanager
    async def observe():
        yield submission

    monkeypatch.setattr(session._login_driver, "_observe_submission", observe)
    return session, method, controls


@pytest.mark.asyncio
@pytest.mark.parametrize("login_method", list(KorailLoginMethod))
@pytest.mark.parametrize("selected", [True, False])
async def test_fresh_form_preserves_inputs_and_submits_once(monkeypatch, login_method, selected):
    session, method, controls = prepare(
        monkeypatch, selected=selected, identity=[True, True], password=[True, True]
    )
    credential = KorailCredentialInput("fixture-id", "fixture-password", "v4", login_method)
    try:
        assert await session._submit_login_form(credential) is True
        assert method.click.await_count == (0 if selected else 1)
        controls[0].clear.assert_not_awaited()
        controls[1].clear.assert_not_awaited()
        controls[0].type_text.assert_awaited_once_with(credential.login_id)
        controls[1].type_text.assert_awaited_once_with(credential.password)
        controls[2].click.assert_awaited_once()
    finally:
        await session._login_driver.close_submission_observer()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity", "password", "stage"),
    [
        ([False], [True], "login_identity_clear"),
        ([True], [False], "login_password_clear"),
        ([True, False], [True], "login_input_mismatch"),
        ([True, True], [True, False], "login_input_mismatch"),
    ],
)
async def test_changed_or_mismatched_input_stops_before_credential_post(
    monkeypatch, identity, password, stage
):
    session, _, controls = prepare(monkeypatch, selected=True, identity=identity, password=password)
    with pytest.raises(BrowserSourceUnavailable) as error:
        await session._submit_login_form(
            KorailCredentialInput("fixture-id", "fixture-password", "v4")
        )
    assert error.value.stage == stage
    controls[2].click.assert_not_awaited()
    controls[0].clear.assert_not_awaited()
    controls[1].clear.assert_not_awaited()
    assert session._login_driver._submission is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, {}, {"result": None}, {"result": {"value": "true"}}])
async def test_input_attestation_requires_boolean_true(monkeypatch, raw):
    session, _, controls = prepare(monkeypatch, selected=True, identity=[True], password=[True])
    controls[0].execute_script.side_effect = None
    controls[0].execute_script.return_value = raw
    with pytest.raises(BrowserSourceUnavailable):
        await session._submit_login_form(
            KorailCredentialInput("fixture-id", "fixture-password", "v4")
        )
    controls[2].click.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"value": "unexpected"}, False),
        ({"connected": False}, False),
        ({"disabled": True}, False),
        ({"readonly": True}, False),
        ({"hidden": True}, False),
        ({"other_target": True}, False),
        ({"duplicate_input": True}, False),
        ({"duplicate_panel": True}, False),
    ],
)
async def test_live_input_script_rejects_changed_or_inactive_controls(changes, expected):
    from playwright._impl._driver import compute_driver_executable

    node, _ = compute_driver_executable()
    assert Path(node).is_file()
    harness = """
      const fs = require('node:fs'), vm = require('node:vm');
      const f = JSON.parse(fs.readFileSync(0, 'utf8'));
      const control = {value:f.value ?? '', isConnected:f.connected ?? true,
        disabled:f.disabled ?? false, readOnly:f.readonly ?? false,
        getBoundingClientRect:()=>({width:f.hidden ? 0 : 100,height:44})};
      const panel = {getBoundingClientRect:()=>({width:100,height:100}),
        querySelectorAll:()=>f.duplicate_input ? [control,control]
          : [f.other_target ? {...control} : control]};
      const context = {document:{querySelectorAll:()=>f.duplicate_panel ? [panel,panel] : [panel]},
        getComputedStyle:()=>({display:'block',visibility:'visible'})};
      const fn = vm.runInNewContext('(' + f.script + ')', context);
      process.stdout.write(JSON.stringify(fn.call(control, 'input', '')));
    """
    captured = []

    async def execute(script, **kwargs):
        assert kwargs == {"arguments": [{"value": "input"}, {"value": ""}], "return_by_value": True}
        completed = subprocess.run(
            [node, "-e", harness],
            input=json.dumps({"script": script, **changes}),
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        value = json.loads(completed.stdout)
        captured.append(value)
        return {"result": {"result": {"value": value}}}

    session = _PydollSession("https://www.korail.com/ticket/login", 1000, True)
    control = SimpleNamespace(execute_script=execute)
    if expected:
        await session._login_driver._require_input_value(
            control, "input", "", "login_identity_clear"
        )
    else:
        with pytest.raises(BrowserSourceUnavailable):
            await session._login_driver._require_input_value(
                control, "input", "", "login_identity_clear"
            )
    assert captured == [expected]
