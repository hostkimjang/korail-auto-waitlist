"""Own Pydoll Chromium, tab, and network-listener resources transactionally."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from ..browser_contracts import BrowserSourceUnavailable
from ..chromium_launch import isolated_test_chromium_arguments

logger = logging.getLogger(__name__)

DEDICATED_PROFILE_MARKER_FILENAME = ".rail-waitlist-korail-profile"
DEDICATED_PROFILE_MARKER_CONTENT = "rail-waitlist-korail-browser-profile-v1\n"
DEDICATED_PROFILE_SINGLETON_NAMES = (
    "SingletonLock",
    "SingletonSocket",
    "SingletonCookie",
)


class PydollBrowserProfileMode(StrEnum):
    """Choose disposable state or an app-owned persistent browser profile."""

    EPHEMERAL = "ephemeral"
    DEDICATED_AUTO = "dedicated_auto"
    DEDICATED_MANUAL = "dedicated_manual"


class PydollChromiumPhase(StrEnum):
    """Explicit lifecycle state for one single-use Pydoll browser session."""

    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    ROTATING = "rotating"
    CLOSING = "closing"
    FAILED = "failed"
    CLOSED = "closed"


def browser_profile_mode_setting() -> PydollBrowserProfileMode:
    """Read the closed profile mode without accepting browser-default directories."""

    raw_value = os.environ.get(
        "KORAIL_BROWSER_PROFILE_MODE",
        PydollBrowserProfileMode.EPHEMERAL.value,
    )
    try:
        return PydollBrowserProfileMode(raw_value.strip().lower())
    except ValueError as error:
        raise BrowserSourceUnavailable("browser_profile") from error


def dedicated_browser_profile_directory(*, headless: bool) -> Path | None:
    """Return a validated app-owned profile or keep Pydoll's temporary profile."""

    if browser_profile_mode_setting() is PydollBrowserProfileMode.EPHEMERAL:
        return None
    if headless:
        raise BrowserSourceUnavailable("browser_profile")

    configured = os.environ.get("KORAIL_BROWSER_PROFILE_DIR", "").strip()
    if not configured:
        raise BrowserSourceUnavailable("browser_profile")
    profile = Path(configured)
    marker = profile / DEDICATED_PROFILE_MARKER_FILENAME
    try:
        if not profile.is_absolute() or profile.is_symlink() or not profile.is_dir():
            raise BrowserSourceUnavailable("browser_profile")
        resolved = profile.resolve(strict=True)
        if marker.is_symlink() or not marker.is_file():
            raise BrowserSourceUnavailable("browser_profile")
        if marker.read_text(encoding="ascii") != DEDICATED_PROFILE_MARKER_CONTENT:
            raise BrowserSourceUnavailable("browser_profile")
        if not os.access(resolved, os.R_OK | os.W_OK | os.X_OK):
            raise BrowserSourceUnavailable("browser_profile")
        if os.name != "nt" and resolved.stat().st_mode & 0o077:
            raise BrowserSourceUnavailable("browser_profile")
    except BrowserSourceUnavailable:
        raise
    except (OSError, UnicodeError) as error:
        raise BrowserSourceUnavailable("browser_profile") from error
    return resolved


def cleanup_dedicated_profile_singletons(*, headless: bool) -> None:
    """Remove only stale Chrome symlinks after this owner has stopped its process."""

    profile = dedicated_browser_profile_directory(headless=headless)
    if profile is None:
        return
    try:
        for name in DEDICATED_PROFILE_SINGLETON_NAMES:
            singleton = profile / name
            if singleton.is_symlink():
                singleton.unlink()
            elif singleton.exists():
                raise BrowserSourceUnavailable("browser_profile")
    except BrowserSourceUnavailable:
        raise
    except OSError as error:
        raise BrowserSourceUnavailable("browser_profile") from error


class _ChromiumOptions(Protocol):
    headless: bool
    browser_preferences: dict[str, bool]
    binary_location: str

    def add_argument(self, argument: str) -> None: ...


class _PydollTab(Protocol):
    network_events_enabled: bool

    async def enable_network_events(self) -> object: ...

    async def disable_network_events(self) -> object: ...

    async def on(
        self,
        event: object,
        callback: Callable[[dict[str, Any]], None],
    ) -> int: ...

    async def remove_callback(self, callback_id: int) -> object: ...

    async def close(self) -> object: ...


class _PydollBrowser(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> object: ...

    async def start(self) -> _PydollTab: ...

    async def new_tab(self) -> _PydollTab: ...

    async def stop(self) -> object: ...

    async def close(self) -> object: ...


class _PydollBrowserFactory(Protocol):
    def __call__(self, *, options: _ChromiumOptions) -> _PydollBrowser: ...


class _ChromiumOptionsConfigurer(Protocol):
    def __call__(self, options: object, *, headless: bool) -> None: ...


@dataclass(frozen=True)
class PydollChromiumRuntime:
    """Lazily imported Pydoll factories needed to start Chromium."""

    browser_factory: _PydollBrowserFactory
    options_factory: Callable[[], _ChromiumOptions]


PydollRuntimeLoader = Callable[[], PydollChromiumRuntime]
PydollResponseEventLoader = Callable[[], object]
PydollResponseCallback = Callable[[dict[str, Any]], None]


def _load_pydoll_runtime() -> PydollChromiumRuntime:
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.managers import BrowserProcessManager
        from pydoll.browser.options import ChromiumOptions
    except ImportError as error:
        raise BrowserSourceUnavailable("browser_import") from error

    def create_process(command: list[str]) -> subprocess.Popen[bytes]:
        # Pydoll 2.23.1 captures both streams but never drains them. Chromium can
        # block on a full stderr pipe before answering CDP. Raw browser output
        # is not an application diagnostic and may contain provider material.
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def create_browser(*, options: _ChromiumOptions) -> _PydollBrowser:
        # Pydoll's default chooses an unchecked port from a small fixed range and
        # may attach to another browser. Ask the OS for a free loopback port;
        # releasing it before Chrome binds still leaves a small allocation race.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            connection_port = reservation.getsockname()[1]
        browser = Chrome(options=cast(ChromiumOptions, options), connection_port=connection_port)
        # The pinned Chrome constructor does not expose the base-class manager
        # argument. Replace its unused manager before starting this instance.
        browser._browser_process_manager = BrowserProcessManager(process_creator=create_process)
        return cast(_PydollBrowser, browser)

    return PydollChromiumRuntime(
        browser_factory=create_browser,
        options_factory=cast(Callable[[], _ChromiumOptions], ChromiumOptions),
    )


def _load_response_received_event() -> object:
    from pydoll.protocol.network.events import NetworkEvent

    return NetworkEvent.RESPONSE_RECEIVED


def set_chromium_binary(options: object) -> None:
    """Apply the explicit or bundled Chromium binary without logging its path."""
    chromium_options = cast(_ChromiumOptions, options)
    configured = os.environ.get("KORAIL_BROWSER_CHROMIUM_EXECUTABLE_PATH", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise BrowserSourceUnavailable("browser_binary")
        chromium_options.binary_location = str(path)
        return
    playwright_root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright"))
    candidates = sorted(playwright_root.glob("chromium-*/chrome-linux/chrome"), reverse=True)
    if candidates:
        chromium_options.binary_location = str(candidates[0])


def configure_chromium_options(
    options: object,
    *,
    headless: bool,
    allow_dedicated_profile: bool = True,
) -> None:
    """Apply the shared safe Chromium defaults used by probe and real sessions."""
    chromium_options = cast(_ChromiumOptions, options)
    chromium_options.headless = headless
    chromium_options.browser_preferences = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.password_manager_leak_detection": False,
    }
    chromium_options.add_argument("--disable-save-password-bubble")
    profile = (
        dedicated_browser_profile_directory(headless=headless) if allow_dedicated_profile else None
    )
    if profile is not None:
        chromium_options.add_argument(f"--user-data-dir={profile}")
        chromium_options.add_argument("--profile-directory=Default")
        chromium_options.add_argument("--disable-sync")
        chromium_options.add_argument("--restore-last-session")
    for argument in isolated_test_chromium_arguments():
        chromium_options.add_argument(argument)
    set_chromium_binary(chromium_options)


async def finish_owned_cleanup(cleanup: Awaitable[object]) -> None:
    """Finish owned cleanup even when the awaiting task is cancelled repeatedly."""
    pending_cancellation: asyncio.CancelledError | None = None
    cleanup_task: asyncio.Future[object] = asyncio.ensure_future(cleanup)
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as error:
            if pending_cancellation is None:
                pending_cancellation = error
    cleanup_task.result()
    if pending_cancellation is not None:
        raise pending_cancellation


async def cleanup_pydoll_tab_listener(
    tab: object | None,
    callback_id: int | None,
    network_events_enabled_by_owner: bool,
    *,
    event_logger: logging.Logger = logger,
) -> None:
    """Release one listener binding without exposing backend or request details."""
    if tab is None:
        return
    pydoll_tab = cast(_PydollTab, tab)
    if callback_id is not None:
        try:
            await pydoll_tab.remove_callback(callback_id)
        except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
            event_logger.warning("KORAIL Pydoll network callback cleanup failed")
    if network_events_enabled_by_owner:
        try:
            await pydoll_tab.disable_network_events()
        except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
            event_logger.warning("KORAIL Pydoll network event cleanup failed")


class PydollChromiumLifecycle:
    """Single-use owner for one Pydoll browser and its active tab binding."""

    def __init__(
        self,
        *,
        headless: bool,
        on_response: PydollResponseCallback | None,
        attach_network_listener: bool = True,
        runtime_loader: PydollRuntimeLoader | None = None,
        response_event_loader: PydollResponseEventLoader | None = None,
        options_configurer: _ChromiumOptionsConfigurer = configure_chromium_options,
        owns_dedicated_profile: bool = True,
        event_logger: logging.Logger = logger,
    ) -> None:
        if attach_network_listener and on_response is None:
            raise ValueError("on_response is required when network listener attachment is enabled")
        self._headless = headless
        self._on_response = on_response
        self._attach_listener_on_start = attach_network_listener
        self._runtime_loader = runtime_loader or _load_pydoll_runtime
        self._response_event_loader = response_event_loader or _load_response_received_event
        self._options_configurer = options_configurer
        self._owns_dedicated_profile = owns_dedicated_profile
        self._event_logger = event_logger
        self._phase = PydollChromiumPhase.NEW
        self._browser: _PydollBrowser | None = None
        self._tab: _PydollTab | None = None
        self._callback_id: int | None = None
        self._network_events_enabled_by_owner = False
        self._retired_tabs: list[_PydollTab] = []
        self._close_task: asyncio.Task[None] | None = None

    @property
    def phase(self) -> PydollChromiumPhase:
        return self._phase

    @property
    def browser(self) -> object | None:
        return self._browser

    @browser.setter
    def browser(self, value: object | None) -> None:
        self._browser = cast(_PydollBrowser | None, value)
        self._promote_compat_binding_if_ready()

    @property
    def tab(self) -> object | None:
        return self._tab

    @tab.setter
    def tab(self, value: object | None) -> None:
        self._tab = cast(_PydollTab | None, value)
        self._promote_compat_binding_if_ready()

    @property
    def callback_id(self) -> int | None:
        return self._callback_id

    @callback_id.setter
    def callback_id(self, value: int | None) -> None:
        self._callback_id = value

    @property
    def network_events_enabled_by_owner(self) -> bool:
        return self._network_events_enabled_by_owner

    @network_events_enabled_by_owner.setter
    def network_events_enabled_by_owner(self, value: bool) -> None:
        self._network_events_enabled_by_owner = value

    async def start(self) -> object:
        """Start Chromium and commit the first tab only after listener attachment succeeds."""
        if self._phase is not PydollChromiumPhase.NEW:
            raise BrowserSourceUnavailable("browser_lifecycle")
        self._phase = PydollChromiumPhase.STARTING
        try:
            runtime = self._runtime_loader()
            options = runtime.options_factory()
            self._options_configurer(options, headless=self._headless)
            browser = runtime.browser_factory(options=options)
            self._browser = browser
            await browser.__aenter__()
            tab = await browser.start()
            callback_id: int | None = None
            enabled_by_owner = False
            if self._attach_listener_on_start:
                callback_id, enabled_by_owner = await self.attach_network_listener(tab)
            self._tab = tab
            self._callback_id = callback_id
            self._network_events_enabled_by_owner = enabled_by_owner
            self._phase = PydollChromiumPhase.READY
            return tab
        except BaseException as error:
            await self.close(raise_on_failure=False)
            if isinstance(error, BrowserSourceUnavailable):
                raise
            if not isinstance(error, Exception):
                raise
            raise BrowserSourceUnavailable("browser_launch") from error

    async def replace_tab(self) -> object:
        """Prepare a replacement binding before swapping and retiring the previous tab."""
        browser = self._browser
        old_tab = self._tab
        if self._phase is not PydollChromiumPhase.READY or browser is None or old_tab is None:
            raise BrowserSourceUnavailable("browser_lifecycle")
        self._phase = PydollChromiumPhase.ROTATING
        new_tab: _PydollTab | None = None
        callback_id: int | None = None
        enabled_by_owner = False
        try:
            new_tab = await browser.new_tab()
            callback_id, enabled_by_owner = await self.attach_network_listener(new_tab)
        except BaseException:
            try:
                if new_tab is not None:
                    await finish_owned_cleanup(
                        self._retire_tab(new_tab, callback_id, enabled_by_owner)
                    )
            finally:
                self._phase = PydollChromiumPhase.READY
            raise

        old_callback_id = self._callback_id
        old_enabled_by_owner = self._network_events_enabled_by_owner
        self._tab = new_tab
        self._callback_id = callback_id
        self._network_events_enabled_by_owner = enabled_by_owner
        self._phase = PydollChromiumPhase.READY
        await finish_owned_cleanup(self._retire_tab(old_tab, old_callback_id, old_enabled_by_owner))
        return new_tab

    async def attach_network_listener(self, tab: object) -> tuple[int, bool]:
        """Attach one callback transactionally and return ownership metadata."""
        pydoll_tab = cast(_PydollTab, tab)
        enabled_by_owner = False
        callback_id: int | None = None
        try:
            if not pydoll_tab.network_events_enabled:
                enabled_by_owner = True
                await pydoll_tab.enable_network_events()
            response_event = self._response_event_loader()
            callback = self._on_response
            if callback is None:
                raise BrowserSourceUnavailable("browser_lifecycle")
            callback_id = await pydoll_tab.on(response_event, callback)
            return callback_id, enabled_by_owner
        except BaseException:
            await finish_owned_cleanup(
                cleanup_pydoll_tab_listener(
                    pydoll_tab,
                    callback_id,
                    enabled_by_owner,
                    event_logger=self._event_logger,
                )
            )
            raise

    async def close(self, *, raise_on_failure: bool = True) -> None:
        """Finish each close attempt under cancellation and retry one total fallback failure."""
        for _attempt in range(2):
            close_task = self._close_task
            if close_task is None or (
                close_task.done() and self._phase is PydollChromiumPhase.FAILED
            ):
                self._phase = PydollChromiumPhase.CLOSING
                close_task = asyncio.create_task(self._close_resources())
                self._close_task = close_task
            await finish_owned_cleanup(close_task)
            if self._phase is not PydollChromiumPhase.FAILED:
                return
        if raise_on_failure:
            raise BrowserSourceUnavailable("browser_close")

    async def _close_resources(self) -> None:
        browser = self._browser
        tab = self._tab
        callback_id = self._callback_id
        enabled_by_owner = self._network_events_enabled_by_owner
        try:
            await cleanup_pydoll_tab_listener(
                tab,
                callback_id,
                enabled_by_owner,
                event_logger=self._event_logger,
            )
            await self._retry_retired_tabs()
            browser_closed = browser is None or await self._close_browser(browser)
            if browser_closed and self._owns_dedicated_profile:
                cleanup_dedicated_profile_singletons(headless=self._headless)
        except BaseException:
            self._phase = PydollChromiumPhase.FAILED
            raise
        if browser_closed:
            self._browser = None
            self._tab = None
            self._callback_id = None
            self._network_events_enabled_by_owner = False
            self._retired_tabs.clear()
            self._phase = PydollChromiumPhase.CLOSED
        else:
            self._phase = PydollChromiumPhase.FAILED

    async def _retire_tab(
        self,
        tab: _PydollTab,
        callback_id: int | None,
        enabled_by_owner: bool,
    ) -> None:
        await cleanup_pydoll_tab_listener(
            tab,
            callback_id,
            enabled_by_owner,
            event_logger=self._event_logger,
        )
        try:
            await tab.close()
        except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
            self._event_logger.warning("KORAIL Pydoll tab cleanup failed")
            if all(retired is not tab for retired in self._retired_tabs):
                self._retired_tabs.append(tab)

    async def _retry_retired_tabs(self) -> None:
        still_open: list[_PydollTab] = []
        for tab in self._retired_tabs:
            try:
                await tab.close()
            except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                self._event_logger.warning("KORAIL Pydoll retired tab cleanup failed")
                still_open.append(tab)
        self._retired_tabs = still_open

    async def _close_browser(self, browser: _PydollBrowser) -> bool:
        connection_closed = True
        try:
            await browser.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
            try:
                await browser.stop()
            except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
                    self._event_logger.warning("KORAIL Pydoll browser cleanup failed")
                    connection_closed = False
        process_stopped = await self._stop_owned_process_if_running(browser)
        return connection_closed and process_stopped

    async def _stop_owned_process_if_running(self, browser: _PydollBrowser) -> bool:
        # Pydoll 2.23.1 __aexit__ skips stop() when CDP ping fails, even if its
        # owned Chrome process is still alive. Its close() only closes the socket.
        manager = getattr(browser, "_browser_process_manager", None)
        process = getattr(manager, "_process", None)
        poll = getattr(process, "poll", None)
        stop = getattr(manager, "stop_process", None)
        if not callable(poll) or not callable(stop) or poll() is not None:
            return True
        self._event_logger.warning("KORAIL Pydoll event=browser_process_cleanup_fallback")
        try:
            await finish_owned_cleanup(asyncio.to_thread(stop))
            if poll() is None:
                raise RuntimeError("owned browser process survived cleanup")
            temp_manager = getattr(browser, "_temp_directory_manager", None)
            cleanup = getattr(temp_manager, "cleanup", None)
            if callable(cleanup):
                await finish_owned_cleanup(asyncio.to_thread(cleanup))
        except Exception:  # noqa: BLE001 -- optional backend exceptions are not stable.
            self._event_logger.warning("KORAIL Pydoll browser process cleanup failed")
            return False
        return True

    def _promote_compat_binding_if_ready(self) -> None:
        if (
            self._phase is PydollChromiumPhase.NEW
            and self._browser is not None
            and self._tab is not None
        ):
            self._phase = PydollChromiumPhase.READY


async def probe_pydoll_chromium(*, headless: bool = True) -> None:
    """Probe an isolated Chromium without locking the operator's live auth profile."""
    lifecycle = PydollChromiumLifecycle(
        headless=headless,
        on_response=None,
        attach_network_listener=False,
        owns_dedicated_profile=False,
        options_configurer=lambda options, *, headless: configure_chromium_options(
            options,
            headless=headless,
            allow_dedicated_profile=False,
        ),
    )
    primary_error: BaseException | None = None
    try:
        await lifecycle.start()
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if primary_error is None:
            await lifecycle.close()
