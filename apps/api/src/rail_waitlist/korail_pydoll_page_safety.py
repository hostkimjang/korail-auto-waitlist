"""Classify secret-free KORAIL Pydoll page evidence with fail-closed semantics."""

from __future__ import annotations

import logging

from .korail_browser_automation import (
    GENERIC_PROTECTION_TRIGGERS as AUTOMATION_GENERIC_PROTECTION_TRIGGERS,
)
from .korail_browser_automation import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from .korail_pydoll_contracts import PydollPageSnapshot

GENERIC_PROTECTION_TRIGGERS = AUTOMATION_GENERIC_PROTECTION_TRIGGERS


def assert_pydoll_response_allowed(
    snapshot: PydollPageSnapshot,
    stage: str,
    *,
    event_logger: logging.Logger,
) -> None:
    """Raise only the established sanitized adapter errors for blocked page evidence."""

    for status, resource_type in snapshot.network_responses:
        if is_rate_limit_response(status, resource_type):
            raise BrowserRateLimited()
        trigger = protection_trigger_from_http_response(status, resource_type)
        if trigger == "http_403_main":
            _log_protection_snapshot(snapshot, stage, trigger, event_logger=event_logger)
            raise BrowserProtectionDetected(trigger, stage)

    trigger = protection_trigger_from_text(snapshot.body_text)
    if trigger is None:
        return
    if trigger not in GENERIC_PROTECTION_TRIGGERS:
        _log_protection_snapshot(snapshot, stage, trigger, event_logger=event_logger)
        raise BrowserProtectionDetected(trigger, stage)
    if (
        any(
            protection_trigger_from_text(text) in GENERIC_PROTECTION_TRIGGERS
            for text in snapshot.protection_texts
        )
        or not snapshot.rows
    ):
        _log_protection_snapshot(snapshot, stage, trigger, event_logger=event_logger)
        raise BrowserProtectionDetected(trigger, stage)


def _log_protection_snapshot(
    snapshot: PydollPageSnapshot,
    stage: str,
    trigger: str,
    *,
    event_logger: logging.Logger,
) -> None:
    marker_surface_count = sum(
        protection_trigger_from_text(text) == trigger for text in snapshot.protection_texts
    )
    event_logger.warning(
        "KORAIL Pydoll protection evidence stage=%s trigger=%s rows=%d "
        "visible_surfaces=%d marker_surfaces=%d network=%s",
        stage,
        trigger,
        len(snapshot.rows),
        len(snapshot.protection_texts),
        marker_surface_count,
        snapshot.network_responses,
    )
