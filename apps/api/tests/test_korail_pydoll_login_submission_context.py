from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import rail_waitlist.korail_sidecar.pydoll.login_submission_context as context_module
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.login_submission_context import (
    observe_login_submission,
)


class Tab:
    def __init__(self, *, enabled: bool = False) -> None:
        self.network_events_enabled = enabled
        self.callbacks: dict[int, Callable[[dict[str, Any]], None]] = {}
        self.events: list[str] = []
        self.fail_on: int | None = None
        self.enable_error = False
        self.remove_error: int | None = None
        self.disable_error = False
        self.remove_started: asyncio.Event | None = None
        self.remove_continue: asyncio.Event | None = None

    async def enable_network_events(self) -> None:
        self.events.append("enable")
        self.network_events_enabled = True
        if self.enable_error:
            raise RuntimeError("fixture-sensitive-browser-error")

    async def disable_network_events(self) -> None:
        self.events.append("disable")
        self.network_events_enabled = False
        if self.disable_error:
            raise RuntimeError("fixture-sensitive-browser-error")

    async def on(self, event: object, callback: Callable[[dict[str, Any]], None]) -> int:
        callback_id = len(self.callbacks) + 1
        self.events.append(f"attach:{event}")
        if callback_id == self.fail_on:
            raise RuntimeError("fixture-sensitive-browser-error")
        self.callbacks[callback_id] = callback
        return callback_id

    async def remove_callback(self, callback_id: int) -> None:
        self.events.append(f"remove:{callback_id}")
        if self.remove_started is not None and self.remove_continue is not None:
            self.remove_started.set()
            await self.remove_continue.wait()
        if callback_id == self.remove_error:
            raise RuntimeError("fixture-sensitive-browser-error")
        del self.callbacks[callback_id]


@pytest.fixture(autouse=True)
def stub_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module, "_submission_events", lambda: ("request", "response", "finished", "failed")
    )


@pytest.mark.parametrize("enabled", [False, True])
async def test_observer_is_unarmed_and_releases_only_its_network_ownership(enabled: bool) -> None:
    tab = Tab(enabled=enabled)
    async with observe_login_submission(tab, 10) as owner:
        assert len(tab.callbacks) == 4
        assert owner.snapshot().state == "missing"
        tab.callbacks[1](
            {
                "params": {
                    "requestId": "ignored",
                    "type": "XHR",
                    "request": {"method": "POST", "url": "https://www.korail.com/opaque-login"},
                }
            }
        )
        assert owner.snapshot().state == "missing"
        owner.arm()
    assert owner.snapshot().failure == "missing"
    assert tab.callbacks == {}
    assert tab.network_events_enabled is enabled
    assert ("enable" in tab.events) is not enabled
    assert ("disable" in tab.events) is not enabled


async def test_callbacks_observe_completion_without_sending_requests() -> None:
    tab = Tab()
    async with observe_login_submission(tab, 10) as owner:
        owner.arm()
        tab.callbacks[1](
            {
                "params": {
                    "requestId": "login",
                    "type": "XHR",
                    "request": {"method": "POST", "url": "https://www.korail.com/opaque-login"},
                }
            }
        )
        tab.callbacks[2](
            {
                "params": {
                    "requestId": "login",
                    "type": "XHR",
                    "response": {"status": 200, "url": "https://www.korail.com/opaque-login"},
                }
            }
        )
        assert owner.snapshot().safe_to_probe is False
        tab.callbacks[3]({"params": {"requestId": "login"}})
        assert owner.snapshot().safe_to_probe is True
    assert owner.snapshot().safe_to_probe is True
    assert tab.callbacks == {}


@pytest.mark.parametrize("fail_on", [1, 2, 3, 4])
async def test_partial_attachment_failure_is_closed_and_secret_safe(fail_on: int) -> None:
    tab = Tab()
    tab.fail_on = fail_on
    with pytest.raises(BrowserSourceUnavailable) as error:
        async with observe_login_submission(tab, 10):
            pytest.fail("An incompletely attached observer must not be yielded")
    assert error.value.stage == "login_response"
    assert error.value.__cause__ is None
    assert str(error.value) == "source_unavailable"
    assert tab.callbacks == {}
    assert tab.network_events_enabled is False


async def test_partial_network_enable_failure_restores_previous_disabled_state() -> None:
    tab = Tab()
    tab.enable_error = True
    with pytest.raises(BrowserSourceUnavailable, match="source_unavailable"):
        async with observe_login_submission(tab, 10):
            pytest.fail("Enable failure must not yield an observer")
    assert tab.events == ["enable", "disable"]
    assert tab.network_events_enabled is False


async def test_cancellation_during_attachment_releases_registered_callbacks() -> None:
    attaching = asyncio.Event()

    class AttachingTab(Tab):
        async def on(self, event: object, callback: Callable[[dict[str, Any]], None]) -> int:
            if len(self.callbacks) == 2:
                attaching.set()
                await asyncio.Event().wait()
            return await super().on(event, callback)

    tab = AttachingTab()

    async def operation() -> None:
        async with observe_login_submission(tab, 10):
            pytest.fail("Attachment cancellation must not yield the observer")

    task = asyncio.create_task(operation())
    await attaching.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.callbacks == {}
    assert tab.network_events_enabled is False
    assert "remove:1" in tab.events and "remove:2" in tab.events


async def test_body_failure_releases_original_tab_even_if_caller_rotates() -> None:
    original = Tab(enabled=True)
    rotated = Tab(enabled=True)
    selected = original
    with pytest.raises(ValueError, match="body failure"):
        async with observe_login_submission(selected, 10) as owner:
            owner.arm()
            selected = rotated
            raise ValueError("body failure")
    assert original.callbacks == {}
    assert original.network_events_enabled is True
    assert rotated.events == []
    assert owner.snapshot().state == "failed"


async def test_repeated_cancellation_finishes_cleanup_before_propagating() -> None:
    tab = Tab()
    tab.remove_started = asyncio.Event()
    tab.remove_continue = asyncio.Event()
    entered = asyncio.Event()
    owners = []

    async def operation() -> None:
        async with observe_login_submission(tab, 10) as owner:
            owners.append(owner)
            owner.arm()
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    await entered.wait()
    task.cancel()
    await tab.remove_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    tab.remove_continue.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.callbacks == {}
    assert tab.network_events_enabled is False
    assert owners[0].snapshot().state == "failed"


async def test_cleanup_failures_do_not_skip_other_callbacks_or_expose_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tab = Tab()
    tab.remove_error = 2
    tab.disable_error = True
    async with observe_login_submission(tab, 10) as owner:
        pass
    assert all(f"remove:{index}" in tab.events for index in range(1, 5))
    assert "disable" in tab.events
    assert len(caplog.records) == 2
    assert "fixture-sensitive-browser-error" not in caplog.text
    assert "login observation callback cleanup failed" in caplog.text
    assert "login observation network cleanup failed" in caplog.text
    # A listener that the backend failed to remove is still inert after owner close.
    tab.callbacks[2]({"params": {"requestId": "late", "type": "XHR", "response": {"status": 200}}})
    assert owner.snapshot().state == "failed"


def test_module_import_does_not_load_optional_pydoll_runtime() -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    script = (
        "import sys;"
        f"sys.path.insert(0, {str(source)!r});"
        "import rail_waitlist.korail_sidecar.pydoll.login_submission_context;"
        "assert not any(name == 'pydoll' or name.startswith('pydoll.') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode()
