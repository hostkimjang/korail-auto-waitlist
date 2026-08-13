from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI

from .file_logging import configure_service_console_logging as _configure_service_console_logging
from .file_logging import configure_service_file_logging
from .korail_sidecar.http import AdapterHttpDependencies as _AdapterHttpDependencies
from .korail_sidecar.http import create_adapter_app as _create_adapter_http_app
from .korail_sidecar.playwright.client import probe_chromium as probe_chromium
from .korail_sidecar.runtime import (
    KorailBrowserEngine as KorailBrowserEngine,
)
from .korail_sidecar.runtime import ReadinessGate as _ReadinessGate
from .korail_sidecar.runtime import browser_engine_setting as _browser_engine_setting
from .korail_sidecar.runtime import build_automation as build_automation
from .korail_sidecar.runtime import build_browser_client as _build_browser_client
from .korail_sidecar.runtime import float_setting as _float_setting
from .korail_sidecar.runtime import integer_setting as integer_setting
from .korail_sidecar.runtime import readiness_probe_for_engine as _readiness_probe_for_engine
from .korail_sidecar.search_coordinator import KorailBrowserAutomation

if TYPE_CHECKING:
    from .korail_sidecar.http import _ReservationClient

logger = logging.getLogger(__name__)
configure_service_file_logging()
_configure_service_console_logging(logging.getLogger("rail_waitlist"))

_integer_setting = integer_setting


def create_adapter_app(
    automation: KorailBrowserAutomation | None = None,
    token: str | None = None,
    readiness_probe: Callable[[], Awaitable[None]] | None = None,
    reservation_client: _ReservationClient | None = None,
    *,
    readiness_retry_interval_seconds: float = 5,
    readiness_probe_timeout_seconds: float = 30,
) -> FastAPI:
    """Build the canonical HTTP app with compatibility globals captured at call time."""

    dependencies = _AdapterHttpDependencies(
        browser_engine_setting=_browser_engine_setting,
        build_browser_client=_build_browser_client,
        float_setting=_float_setting,
        build_automation=build_automation,
        readiness_factory=_ReadinessGate,
        readiness_probe_for_engine=_readiness_probe_for_engine,
        getenv=os.getenv,
        monotonic=time.monotonic,
        logger=logger,
    )
    return _create_adapter_http_app(
        automation,
        token,
        readiness_probe,
        reservation_client,
        readiness_retry_interval_seconds=readiness_retry_interval_seconds,
        readiness_probe_timeout_seconds=readiness_probe_timeout_seconds,
        dependencies=dependencies,
    )


app = create_adapter_app()
