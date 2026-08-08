"""Compatibility facade for the KORAIL sidecar direct-CDP owner."""

from __future__ import annotations

from .korail_sidecar import direct_cdp as _owner

asyncio = _owner.asyncio
logging = _owner.logging
os = _owner.os
tempfile = _owner.tempfile
time = _owner.time
AsyncIterator = _owner.AsyncIterator
asynccontextmanager = _owner.asynccontextmanager
Path = _owner.Path
Protocol = _owner.Protocol
ChromiumBrowserType = _owner.ChromiumBrowserType
DirectCdpLaunchError = _owner.DirectCdpLaunchError
_chromium_environment = _owner._chromium_environment
_cleanup_browser_process = _owner._cleanup_browser_process
_stop_process = _owner._stop_process
_wait_for_debugging_port = _owner._wait_for_debugging_port
isolated_test_chromium_arguments = _owner.isolated_test_chromium_arguments
logger = _owner.logger
open_direct_cdp_browser = _owner.open_direct_cdp_browser
