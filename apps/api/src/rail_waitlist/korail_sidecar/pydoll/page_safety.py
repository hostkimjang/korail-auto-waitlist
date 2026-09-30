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
from ..browser_service_availability import (
    BrowserProviderUnavailable,
    ProviderUnavailableTrigger,
    provider_unavailable_trigger_from_page,
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


@dataclass(frozen=True)
class PydollProviderUnavailableBlock:
    trigger: ProviderUnavailableTrigger
    kind: Literal["provider_unavailable"] = "provider_unavailable"


type PydollPageBlock = PydollRateLimitBlock | PydollProtectionBlock | PydollProviderUnavailableBlock


def classify_pydoll_page_block(snapshot: PydollPageSnapshot) -> PydollPageBlock | None:
    """Return the first blocking page evidence without logging or raising."""

    for status, resource_type in snapshot.network_responses:
        if is_rate_limit_response(status, resource_type):
            return PydollRateLimitBlock()
        trigger = protection_trigger_from_http_response(status, resource_type)
        if trigger == "http_403_main":
            return PydollProtectionBlock(trigger)

    unavailable_trigger = provider_unavailable_trigger_from_page(
        snapshot.url,
        snapshot.body_text,
        has_result_rows=bool(snapshot.rows),
    )
    if unavailable_trigger is not None:
        return PydollProviderUnavailableBlock(unavailable_trigger)

    trigger = protection_trigger_from_text(snapshot.body_text)
    if trigger is not None:
        if trigger not in GENERIC_PROTECTION_TRIGGERS:
            return PydollProtectionBlock(trigger)
        if not snapshot.rows or any(
            protection_trigger_from_text(text) in GENERIC_PROTECTION_TRIGGERS
            for text in snapshot.protection_texts
        ):
            return PydollProtectionBlock(trigger)
    for status, resource_type in snapshot.network_responses:
        if resource_type in {"business_xhr", "business_fetch"} and 500 <= status <= 599:
            return PydollProviderUnavailableBlock("business_server_error")
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
    if block.kind == "provider_unavailable":
        _log_provider_unavailable_snapshot(
            snapshot, stage, block.trigger, event_logger=event_logger
        )
        raise BrowserProviderUnavailable(
            block.trigger,
            "business_response" if block.trigger == "business_server_error" else stage,
        )
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


def _log_provider_unavailable_snapshot(
    snapshot: PydollPageSnapshot,
    stage: str,
    trigger: str,
    *,
    event_logger: logging.Logger,
) -> None:
    if trigger == "business_server_error":
        status, resource_type = next(
            (status, resource_type)
            for status, resource_type in snapshot.network_responses
            if resource_type in {"business_xhr", "business_fetch"} and 500 <= status <= 599
        )
        event_logger.warning(
            "KORAIL Pydoll business response failed stage=%s trigger=%s "
            "status=%d resource_type=%s rows=%d",
            stage,
            trigger,
            status,
            resource_type,
            len(snapshot.rows),
        )
        return
    event_logger.warning(
        "KORAIL Pydoll service unavailable evidence stage=%s trigger=%s rows=%d network_count=%d",
        stage,
        trigger,
        len(snapshot.rows),
        len(snapshot.network_responses),
    )
