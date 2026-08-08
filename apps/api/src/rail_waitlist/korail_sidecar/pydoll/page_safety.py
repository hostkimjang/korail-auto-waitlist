"""Classify secret-free KORAIL Pydoll page evidence with fail-closed semantics."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    ProtectionTrigger,
)
from ..browser_protection import (
    GENERIC_PROTECTION_TRIGGERS as BROWSER_GENERIC_PROTECTION_TRIGGERS,
)
from ..browser_protection import (
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from .page_contracts import PydollPageSnapshot

AUTOMATION_GENERIC_PROTECTION_TRIGGERS = BROWSER_GENERIC_PROTECTION_TRIGGERS
GENERIC_PROTECTION_TRIGGERS = AUTOMATION_GENERIC_PROTECTION_TRIGGERS


@dataclass(frozen=True)
class PydollRateLimitBlock:
    kind: Literal["rate_limited"] = "rate_limited"
    trigger: None = None


@dataclass(frozen=True)
class PydollProtectionBlock:
    trigger: ProtectionTrigger
    kind: Literal["protection"] = "protection"


type PydollPageBlock = PydollRateLimitBlock | PydollProtectionBlock


def classify_pydoll_page_block(snapshot: PydollPageSnapshot) -> PydollPageBlock | None:
    """Return the first blocking page evidence without logging or raising."""

    for status, resource_type in snapshot.network_responses:
        if is_rate_limit_response(status, resource_type):
            return PydollRateLimitBlock()
        trigger = protection_trigger_from_http_response(status, resource_type)
        if trigger == "http_403_main":
            return PydollProtectionBlock(trigger)

    trigger = protection_trigger_from_text(snapshot.body_text)
    if trigger is None:
        return None
    if trigger not in GENERIC_PROTECTION_TRIGGERS:
        return PydollProtectionBlock(trigger)
    if not snapshot.rows or any(
        protection_trigger_from_text(text) in GENERIC_PROTECTION_TRIGGERS
        for text in snapshot.protection_texts
    ):
        return PydollProtectionBlock(trigger)
    return None


def assert_pydoll_response_allowed(
    snapshot: PydollPageSnapshot,
    stage: str,
    *,
    event_logger: logging.Logger,
) -> None:
    """Raise only the established sanitized adapter errors for blocked page evidence."""

    block = classify_pydoll_page_block(snapshot)
    if block is None:
        return
    if block.kind == "rate_limited":
        raise BrowserRateLimited()
    _log_protection_snapshot(snapshot, stage, block.trigger, event_logger=event_logger)
    raise BrowserProtectionDetected(block.trigger, stage)


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
