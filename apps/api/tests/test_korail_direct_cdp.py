from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rail_waitlist.korail_sidecar.direct_cdp import (
    DirectCdpLaunchError,
    _stop_process,
    _wait_for_debugging_port,
    open_direct_cdp_browser,
)


class FinishedProcess:
    returncode = 0

    def __init__(self) -> None:
        self.wait_count = 0

    async def wait(self) -> int:
        self.wait_count += 1
        return 0


class TerminableProcess:
    returncode = None

    def __init__(self) -> None:
        self.terminate_count = 0
        self.kill_count = 0
        self.wait_count = 0

    def terminate(self) -> None:
        self.terminate_count += 1

    def kill(self) -> None:
        self.kill_count += 1

    async def wait(self) -> int:
        self.wait_count += 1
        self.returncode = 0
        return 0


class FakeBrowserSession:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.detach_count = 0

    async def send(self, command: str) -> None:
        self.commands.append(command)

    async def detach(self) -> None:
        self.detach_count += 1


class FakeBrowser:
    def __init__(self) -> None:
        self.session = FakeBrowserSession()
        self.close_count = 0

    async def new_browser_cdp_session(self) -> FakeBrowserSession:
        return self.session

    async def close(self) -> None:
        self.close_count += 1


class BlockingCleanupBrowser(FakeBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.cleanup_started = asyncio.Event()
        self.allow_cleanup = asyncio.Event()

    async def new_browser_cdp_session(self) -> FakeBrowserSession:
        self.cleanup_started.set()
        await self.allow_cleanup.wait()
        return self.session


class FakeChromium:
    executable_path = "fake-chromium"

    def __init__(self, browser: FakeBrowser | None = None) -> None:
        self.browser = browser or FakeBrowser()
        self.endpoints: list[str] = []

    async def connect_over_cdp(self, endpoint_url: str, *, timeout: float) -> FakeBrowser:
        self.endpoints.append(endpoint_url)
        return self.browser


@pytest.mark.asyncio
async def test_debugging_port_marker_is_read_from_fresh_profile(tmp_path: Path) -> None:
    marker = tmp_path / "DevToolsActivePort"
    marker.write_text("43210\n/devtools/browser/test\n", encoding="utf-8")

    port = await _wait_for_debugging_port(marker, TerminableProcess(), timeout_ms=1_000)

    assert port == 43210


@pytest.mark.asyncio
async def test_invalid_debugging_port_fails_closed(tmp_path: Path) -> None:
    marker = tmp_path / "DevToolsActivePort"
    marker.write_text("70000\n/devtools/browser/test\n", encoding="utf-8")

    with pytest.raises(DirectCdpLaunchError, match="invalid"):
        await _wait_for_debugging_port(marker, TerminableProcess(), timeout_ms=1_000)


@pytest.mark.asyncio
async def test_debugging_port_wait_fails_when_process_stops_first(tmp_path: Path) -> None:
    with pytest.raises(DirectCdpLaunchError, match="stopped before"):
        await _wait_for_debugging_port(
            tmp_path / "DevToolsActivePort",
            FinishedProcess(),
            timeout_ms=1_000,
        )


@pytest.mark.asyncio
async def test_non_positive_startup_timeout_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(DirectCdpLaunchError, match="startup timed out"):
        await _wait_for_debugging_port(
            tmp_path / "DevToolsActivePort",
            TerminableProcess(),
            timeout_ms=0,
        )


@pytest.mark.asyncio
async def test_process_stop_accepts_graceful_browser_close() -> None:
    process = TerminableProcess()

    await _stop_process(process)

    assert process.terminate_count == 0
    assert process.kill_count == 0
    assert process.wait_count == 1


@pytest.mark.asyncio
async def test_finished_process_is_still_reaped() -> None:
    process = FinishedProcess()

    await _stop_process(process)

    assert process.wait_count == 1


@pytest.mark.asyncio
async def test_process_stop_waits_after_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = TerminableProcess()
    calls = 0

    async def timeout_first_wait(awaitable, timeout: float):
        nonlocal calls
        calls += 1
        if calls == 1:
            awaitable.close()
            raise TimeoutError
        return await awaitable

    monkeypatch.setattr(asyncio, "wait_for", timeout_first_wait)

    await _stop_process(process)

    assert process.terminate_count == 1
    assert process.kill_count == 0
    assert process.wait_count == 1


@pytest.mark.asyncio
async def test_process_stop_kills_after_graceful_and_terminate_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = TerminableProcess()
    calls = 0

    async def timeout_first_two_waits(awaitable, timeout: float):
        nonlocal calls
        calls += 1
        if calls <= 2:
            awaitable.close()
            raise TimeoutError
        return await awaitable

    monkeypatch.setattr(asyncio, "wait_for", timeout_first_two_waits)

    await _stop_process(process)

    assert process.terminate_count == 1
    assert process.kill_count == 1
    assert process.wait_count == 1


@pytest.mark.asyncio
async def test_direct_browser_uses_sanitized_environment_and_removes_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FinishedProcess()
    chromium = FakeChromium()
    launch: dict[str, object] = {}

    async def create_process(*args: str, **kwargs: object) -> FinishedProcess:
        launch["args"] = args
        launch["env"] = kwargs["env"]
        return process

    sensitive_names = {
        "KORAIL_BROWSER_ADAPTER_TOKEN",
        "SECRET_ENCRYPTION_KEY",
        "AUTH_SESSION_SECRET",
        "DATABASE_URL",
        "REDIS_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
    }
    for name in sensitive_names:
        monkeypatch.setenv(name, f"secret-{name.lower()}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._wait_for_debugging_port",
        AsyncMock(return_value=43210),
    )
    profile_path: Path | None = None

    async with open_direct_cdp_browser(chromium, timeout_ms=1_000):
        profile_argument = next(
            value for value in launch["args"] if value.startswith("--user-data-dir=")
        )
        profile_path = Path(profile_argument.removeprefix("--user-data-dir="))
        assert profile_path.is_dir()

    environment = launch["env"]
    assert isinstance(environment, dict)
    assert sensitive_names.isdisjoint(environment)
    assert chromium.endpoints == ["http://127.0.0.1:43210"]
    assert chromium.browser.session.commands == ["Browser.close"]
    assert chromium.browser.session.detach_count == 1
    assert profile_path is not None and not profile_path.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_replace_body_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExpectedFailure(RuntimeError):
        pass

    chromium = FakeChromium()
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FinishedProcess()),
    )
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._wait_for_debugging_port",
        AsyncMock(return_value=43210),
    )
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._stop_process",
        AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )

    with pytest.raises(ExpectedFailure):
        async with open_direct_cdp_browser(chromium, timeout_ms=1_000):
            raise ExpectedFailure("protected result")


@pytest.mark.asyncio
async def test_connect_failure_still_reaps_process_and_removes_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingChromium(FakeChromium):
        async def connect_over_cdp(self, endpoint_url: str, *, timeout: float) -> FakeBrowser:
            self.endpoints.append(endpoint_url)
            raise RuntimeError("connect failed")

    process = FinishedProcess()
    launch_args: tuple[str, ...] = ()

    async def create_process(*args: str, **kwargs: object) -> FinishedProcess:
        nonlocal launch_args
        launch_args = args
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._wait_for_debugging_port",
        AsyncMock(return_value=43210),
    )

    with pytest.raises(DirectCdpLaunchError, match="launch failed"):
        async with open_direct_cdp_browser(FailingChromium(), timeout_ms=1_000):
            pytest.fail("the context must not yield after a connection failure")

    profile_argument = next(value for value in launch_args if value.startswith("--user-data-dir="))
    profile_path = Path(profile_argument.removeprefix("--user-data-dir="))
    assert process.wait_count == 1
    assert not profile_path.exists()


@pytest.mark.asyncio
async def test_cleanup_failure_without_body_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chromium = FakeChromium()
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FinishedProcess()),
    )
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._wait_for_debugging_port",
        AsyncMock(return_value=43210),
    )
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._stop_process",
        AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )

    with pytest.raises(DirectCdpLaunchError, match="cleanup failed"):
        async with open_direct_cdp_browser(chromium, timeout_ms=1_000):
            pass


@pytest.mark.asyncio
async def test_cancellation_waits_for_cleanup_and_preserves_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = TerminableProcess()
    browser = BlockingCleanupBrowser()
    chromium = FakeChromium(browser)
    launch_args: tuple[str, ...] = ()

    async def create_process(*args: str, **kwargs: object) -> TerminableProcess:
        nonlocal launch_args
        launch_args = args
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(
        "rail_waitlist.korail_sidecar.direct_cdp._wait_for_debugging_port",
        AsyncMock(return_value=43210),
    )

    async def run_browser() -> None:
        async with open_direct_cdp_browser(chromium, timeout_ms=1_000):
            pass

    task = asyncio.create_task(run_browser())
    await browser.cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    browser.allow_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    profile_argument = next(value for value in launch_args if value.startswith("--user-data-dir="))
    profile_path = Path(profile_argument.removeprefix("--user-data-dir="))
    assert browser.session.commands == ["Browser.close"]
    assert browser.close_count == 1
    assert process.wait_count == 1
    assert not profile_path.exists()
