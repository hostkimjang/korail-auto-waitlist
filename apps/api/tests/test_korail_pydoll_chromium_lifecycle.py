from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

import rail_waitlist.korail_pydoll_browser as browser_facade
import rail_waitlist.korail_sidecar.pydoll.chromium_lifecycle as lifecycle_module
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll.chromium_lifecycle import (
    PydollChromiumLifecycle,
    PydollChromiumPhase,
    PydollChromiumRuntime,
    configure_chromium_options,
    finish_owned_cleanup,
    probe_pydoll_chromium,
    set_chromium_binary,
)


@dataclass
class _Options:
    headless: bool = True
    browser_preferences: dict[str, bool] = field(default_factory=dict)
    binary_location: str = ""
    arguments: list[str] = field(default_factory=list)

    def add_argument(self, argument: str) -> None:
        self.arguments.append(argument)


@dataclass
class _Tab:
    name: str
    events: list[str]
    network_events_enabled: bool = False
    on_error: BaseException | None = None
    close_started: asyncio.Event | None = None
    close_continue: asyncio.Event | None = None

    async def enable_network_events(self) -> None:
        self.events.append(f"{self.name}:enable")
        self.network_events_enabled = True

    async def disable_network_events(self) -> None:
        self.events.append(f"{self.name}:disable")
        self.network_events_enabled = False

    async def on(self, _event: object, _callback: Callable[[dict[str, object]], None]) -> int:
        self.events.append(f"{self.name}:on")
        if self.on_error is not None:
            raise self.on_error
        return 41 if self.name == "old" else 42

    async def remove_callback(self, callback_id: int) -> None:
        self.events.append(f"{self.name}:remove:{callback_id}")

    async def close(self) -> None:
        self.events.append(f"{self.name}:close")
        if self.close_started is not None and self.close_continue is not None:
            self.close_started.set()
            await self.close_continue.wait()


class _Browser:
    def __init__(
        self,
        events: list[str],
        first_tab: _Tab,
        *,
        enter_error: BaseException | None = None,
        start_error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.first_tab = first_tab
        self.next_tab: _Tab | None = None
        self.enter_error = enter_error
        self.start_error = start_error
        self.exit_started: asyncio.Event | None = None
        self.exit_continue: asyncio.Event | None = None
        self.exit_error: Exception | None = None
        self.stop_error: Exception | None = None
        self.close_error: Exception | None = None

    async def __aenter__(self) -> object:
        self.events.append("browser:enter")
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.events.append("browser:exit")
        if self.exit_started is not None and self.exit_continue is not None:
            self.exit_started.set()
            await self.exit_continue.wait()
        if self.exit_error is not None:
            raise self.exit_error

    async def start(self) -> _Tab:
        self.events.append("browser:start")
        if self.start_error is not None:
            raise self.start_error
        return self.first_tab

    async def new_tab(self) -> _Tab:
        self.events.append("browser:new_tab")
        assert self.next_tab is not None
        return self.next_tab

    async def stop(self) -> None:
        self.events.append("browser:stop")
        if self.stop_error is not None:
            raise self.stop_error

    async def close(self) -> None:
        self.events.append("browser:close")
        if self.close_error is not None:
            raise self.close_error


def _runtime(browser: _Browser, options: _Options | None = None) -> PydollChromiumRuntime:
    selected_options = options or _Options()
    return PydollChromiumRuntime(
        browser_factory=lambda *, options: browser,
        options_factory=lambda: selected_options,
    )


def _lifecycle(browser: _Browser) -> PydollChromiumLifecycle:
    return PydollChromiumLifecycle(
        headless=True,
        on_response=lambda _event: None,
        runtime_loader=lambda: _runtime(browser),
        response_event_loader=lambda: "response_received",
    )


def test_lifecycle_module_is_passive_without_importing_optional_pydoll() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import rail_waitlist.korail_sidecar.pydoll.chromium_lifecycle; "
                "assert not any(name == 'pydoll' or name.startswith('pydoll.') "
                "for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("sandbox_value", "expected_sandbox"),
    [("true", True), ("yes", False)],
)
def test_canonical_options_preserve_gui_and_explicit_sandbox_policy(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_value: str,
    expected_sandbox: bool,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_TEST_DISABLE_SANDBOX", sandbox_value)
    monkeypatch.delenv("KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH", raising=False)
    options = _Options()

    configure_chromium_options(options, headless=False)

    assert options.headless is False
    assert ("--no-sandbox" in options.arguments) is expected_sandbox
    assert options.arguments.count("--disable-save-password-bubble") == 1
    assert browser_facade._configure_chromium_options is configure_chromium_options
    assert browser_facade._set_chromium_binary is set_chromium_binary
    assert browser_facade._finish_owned_cleanup is finish_owned_cleanup
    assert browser_facade.probe_pydoll_chromium is probe_pydoll_chromium


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_point", "failure"),
    [
        ("enter", RuntimeError("enter")),
        ("start", asyncio.CancelledError()),
        ("attach", asyncio.CancelledError()),
    ],
)
async def test_start_failure_or_cancellation_closes_owned_browser(
    failure_point: str,
    failure: BaseException,
) -> None:
    events: list[str] = []
    tab = _Tab(
        "old",
        events,
        on_error=failure if failure_point == "attach" else None,
    )
    browser = _Browser(
        events,
        tab,
        enter_error=failure if failure_point == "enter" else None,
        start_error=failure if failure_point == "start" else None,
    )
    if failure_point == "enter":
        browser.exit_error = RuntimeError("cleanup exit")
        browser.stop_error = RuntimeError("cleanup stop")
    lifecycle = _lifecycle(browser)

    expected_error = (
        asyncio.CancelledError
        if isinstance(failure, asyncio.CancelledError)
        else BrowserSourceUnavailable
    )
    with pytest.raises(expected_error) as raised:
        await lifecycle.start()

    if isinstance(raised.value, BrowserSourceUnavailable):
        assert raised.value.stage == "browser_launch"
        assert str(raised.value.__cause__) == "enter"
    assert events.count("browser:exit") == 1
    assert lifecycle.phase is PydollChromiumPhase.CLOSED
    assert lifecycle.browser is None
    assert lifecycle.tab is None
    if failure_point == "attach":
        assert events.index("old:disable") < events.index("browser:exit")
    if failure_point == "enter":
        assert events[-2:] == ["browser:stop", "browser:close"]


@pytest.mark.asyncio
async def test_replace_tab_rolls_back_failure_then_commits_before_retiring_old() -> None:
    events: list[str] = []
    old = _Tab("old", events)
    browser = _Browser(events, old)
    lifecycle = _lifecycle(browser)
    await lifecycle.start()
    events.clear()

    rejected = _Tab("rejected", events, on_error=RuntimeError("attach"))
    browser.next_tab = rejected
    with pytest.raises(RuntimeError, match="attach"):
        await lifecycle.replace_tab()

    assert lifecycle.tab is old
    assert lifecycle.callback_id == 41
    assert events == [
        "browser:new_tab",
        "rejected:enable",
        "rejected:on",
        "rejected:disable",
        "rejected:close",
    ]

    events.clear()
    replacement = _Tab("new", events)
    browser.next_tab = replacement
    await lifecycle.replace_tab()

    assert lifecycle.tab is replacement
    assert lifecycle.callback_id == 42
    assert events == [
        "browser:new_tab",
        "new:enable",
        "new:on",
        "old:remove:41",
        "old:disable",
        "old:close",
    ]
    await lifecycle.close()


@pytest.mark.asyncio
async def test_replace_rollback_restores_ready_after_repeated_cancellation() -> None:
    events: list[str] = []
    old = _Tab("old", events)
    browser = _Browser(events, old)
    lifecycle = _lifecycle(browser)
    await lifecycle.start()
    close_started = asyncio.Event()
    close_continue = asyncio.Event()
    browser.next_tab = _Tab(
        "rejected",
        events,
        on_error=asyncio.CancelledError(),
        close_started=close_started,
        close_continue=close_continue,
    )

    replace_task = asyncio.create_task(lifecycle.replace_tab())
    await close_started.wait()
    replace_task.cancel()
    await asyncio.sleep(0)
    replace_task.cancel()
    close_continue.set()

    with pytest.raises(asyncio.CancelledError):
        await replace_task
    assert lifecycle.phase is PydollChromiumPhase.READY
    assert lifecycle.tab is old
    await lifecycle.close()


@pytest.mark.asyncio
async def test_close_is_single_run_and_finishes_after_repeated_cancellation() -> None:
    events: list[str] = []
    tab = _Tab("old", events)
    browser = _Browser(events, tab)
    browser.exit_started = asyncio.Event()
    browser.exit_continue = asyncio.Event()
    lifecycle = _lifecycle(browser)
    await lifecycle.start()

    close_task = asyncio.create_task(lifecycle.close())
    await browser.exit_started.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    close_task.cancel()
    browser.exit_continue.set()

    with pytest.raises(asyncio.CancelledError):
        await close_task
    await lifecycle.close()

    assert events.count("browser:exit") == 1
    assert lifecycle.phase is PydollChromiumPhase.CLOSED
    assert lifecycle.browser is None
    assert lifecycle.tab is None


@pytest.mark.asyncio
async def test_close_failure_retains_handles_and_allows_a_bounded_retry() -> None:
    events: list[str] = []
    tab = _Tab("old", events)
    browser = _Browser(events, tab)
    browser.exit_error = RuntimeError("exit")
    browser.stop_error = RuntimeError("stop")
    browser.close_error = RuntimeError("close")
    lifecycle = _lifecycle(browser)
    await lifecycle.start()

    with pytest.raises(BrowserSourceUnavailable) as raised:
        await lifecycle.close()

    assert raised.value.stage == "browser_close"
    assert lifecycle.phase is PydollChromiumPhase.FAILED
    assert lifecycle.browser is browser
    assert lifecycle.tab is tab
    assert events.count("browser:exit") == 2

    browser.exit_error = None
    browser.stop_error = None
    browser.close_error = None
    await lifecycle.close()

    assert lifecycle.phase is PydollChromiumPhase.CLOSED
    assert lifecycle.browser is None
    assert lifecycle.tab is None
    assert events.count("browser:exit") == 3


@pytest.mark.asyncio
async def test_probe_uses_same_gui_options_and_cleanup_without_network_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    options = _Options()
    browser = _Browser(events, _Tab("probe", events))
    monkeypatch.setattr(
        lifecycle_module, "_load_pydoll_runtime", lambda: _runtime(browser, options)
    )
    monkeypatch.delenv("KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH", raising=False)

    await probe_pydoll_chromium(headless=False)

    assert options.headless is False
    assert events == ["browser:enter", "browser:start", "browser:exit"]
