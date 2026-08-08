from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol, TypeVar

from .chromium_launch import isolated_test_chromium_arguments

# Preserve the established operational logger name across the owner relocation.
logger = logging.getLogger("rail_waitlist.korail_direct_cdp")


class _BrowserCdpSession(Protocol):
    async def send(self, command: str) -> object: ...

    async def detach(self) -> object: ...


class _DirectCdpBrowser(Protocol):
    async def new_browser_cdp_session(self) -> _BrowserCdpSession: ...

    async def close(self) -> object: ...


_DirectCdpBrowserT_co = TypeVar(
    "_DirectCdpBrowserT_co",
    bound=_DirectCdpBrowser,
    covariant=True,
)


class ChromiumBrowserType(Protocol[_DirectCdpBrowserT_co]):
    @property
    def executable_path(self) -> str: ...

    async def connect_over_cdp(
        self,
        endpoint_url: str,
        *,
        timeout: float,
    ) -> _DirectCdpBrowserT_co: ...


class DirectCdpLaunchError(RuntimeError):
    pass


@asynccontextmanager
async def open_direct_cdp_browser[BrowserT: _DirectCdpBrowser](
    chromium: ChromiumBrowserType[BrowserT],
    *,
    timeout_ms: int,
) -> AsyncIterator[BrowserT]:
    """Launch a fresh Chromium process and attach Playwright over loopback CDP."""
    profile = tempfile.TemporaryDirectory(prefix="railwait-korail-cdp-")
    process: asyncio.subprocess.Process | None = None
    browser: BrowserT | None = None
    body_error: BaseException | None = None
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                chromium.executable_path,
                *isolated_test_chromium_arguments(),
                "--headless=new",
                "--window-size=1440,1000",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile.name}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=_chromium_environment(Path(profile.name)),
            )
            port = await _wait_for_debugging_port(
                Path(profile.name) / "DevToolsActivePort",
                process,
                timeout_ms=timeout_ms,
            )
            browser = await chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                timeout=timeout_ms,
            )
        except DirectCdpLaunchError:
            raise
        except Exception as error:
            raise DirectCdpLaunchError("direct Chromium CDP launch failed") from error
        try:
            yield browser
        except BaseException as error:
            body_error = error
            raise
    finally:
        cleanup_cancelled: asyncio.CancelledError | None = None
        cleanup_error: BaseException | None = None
        cleanup_task = asyncio.create_task(_cleanup_browser_process(browser, process))
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as error:
                # Repeated cancellation must not reach the owned cleanup task. Record
                # the first request and keep shielding until Chromium is reaped.
                if cleanup_cancelled is None:
                    cleanup_cancelled = error
            except BaseException as error:  # noqa: BLE001 - cleanup must report any failure
                cleanup_error = error
                break
        if cleanup_error is None:
            try:
                cleanup_task.result()
            except BaseException as error:  # noqa: BLE001 - preserve cleanup outcome
                cleanup_error = error
        if cleanup_error is not None:
            logger.warning("Direct Chromium cleanup incomplete at stage=process")
        profile_error: OSError | None = None
        try:
            profile.cleanup()
        except OSError as error:
            profile_error = error
            logger.warning("Direct Chromium cleanup incomplete at stage=profile")
        if body_error is None:
            if cleanup_cancelled is not None:
                raise cleanup_cancelled
            if cleanup_error is not None:
                raise DirectCdpLaunchError("direct Chromium cleanup failed") from cleanup_error
            if profile_error is not None:
                raise DirectCdpLaunchError(
                    "direct Chromium profile cleanup failed"
                ) from profile_error


def _chromium_environment(profile_path: Path) -> dict[str, str]:
    allowed_names = {
        "COMSPEC",
        "DISPLAY",
        "FONTCONFIG_PATH",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "WINDIR",
        "XDG_RUNTIME_DIR",
    }
    environment = {
        name: value for name, value in os.environ.items() if name.upper() in allowed_names
    }
    environment["HOME"] = str(profile_path)
    environment["USERPROFILE"] = str(profile_path)
    environment["XDG_CACHE_HOME"] = str(profile_path / "cache")
    environment["XDG_CONFIG_HOME"] = str(profile_path / "config")
    return environment


async def _cleanup_browser_process(
    browser: _DirectCdpBrowser | None,
    process: asyncio.subprocess.Process | None,
) -> None:
    if browser is not None:
        try:
            session = await asyncio.wait_for(
                browser.new_browser_cdp_session(),
                timeout=1,
            )
            try:
                await asyncio.wait_for(session.send("Browser.close"), timeout=2)
            finally:
                try:
                    await asyncio.wait_for(session.detach(), timeout=1)
                except Exception:  # noqa: BLE001, S110 - process reap is the final cleanup fence
                    pass
        except Exception:  # noqa: BLE001, S110 - continue to browser/process cleanup
            pass
        try:
            await asyncio.wait_for(browser.close(), timeout=2)
        except Exception:  # noqa: BLE001, S110 - continue to process cleanup
            pass
    if process is not None:
        await _stop_process(process)


async def _wait_for_debugging_port(
    marker_path: Path,
    process: asyncio.subprocess.Process,
    *,
    timeout_ms: int,
) -> int:
    deadline = time.monotonic() + min(timeout_ms / 1000, 10)
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise DirectCdpLaunchError("Chromium stopped before exposing loopback CDP")
        try:
            first_line = marker_path.read_text(encoding="utf-8").splitlines()[0]
            port = int(first_line)
        except (FileNotFoundError, IndexError, OSError, UnicodeError, ValueError):
            await asyncio.sleep(0.05)
            continue
        if 1 <= port <= 65535:
            return port
        raise DirectCdpLaunchError("Chromium exposed an invalid loopback CDP port")
    raise DirectCdpLaunchError("Chromium loopback CDP startup timed out")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        await asyncio.wait_for(process.wait(), timeout=1)
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
        return
    except TimeoutError:
        pass
    try:
        process.terminate()
    except ProcessLookupError:
        await asyncio.wait_for(process.wait(), timeout=1)
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.wait(), timeout=3)
