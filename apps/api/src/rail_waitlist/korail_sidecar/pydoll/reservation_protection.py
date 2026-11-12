"""Preserve the source of a reservation protection verdict without provider data."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ..browser_contracts import ProtectionTrigger
from ..browser_protection import (
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from .page_contracts import PydollPageSnapshot
from .reservation_dialog_policy import ReservationDialogPhase
from .search_response_protection import SEARCH_RESPONSE_MACRO_PROTECTION

type EvidenceSource = Literal["business_response", "network", "page_body", "page_surface"]
type ResourceKind = Literal[
    "document", "xhr", "fetch", "business_xhr", "business_fetch", "business_macro_err1", "other"
]


@dataclass(frozen=True)
class ReservationProtectionEvidence:
    trigger: ProtectionTrigger | Literal["rate_limited"]
    source: EvidenceSource
    status: int | None = None
    resource_kind: ResourceKind = "other"


def _resource_kind(value: str) -> ResourceKind:
    kinds: dict[str, ResourceKind] = {
        "document": "document",
        "xhr": "xhr",
        "fetch": "fetch",
        "business_xhr": "business_xhr",
        "business_fetch": "business_fetch",
    }
    return kinds.get(value, "other")


def classify_reservation_protection(
    snapshot: PydollPageSnapshot,
) -> ReservationProtectionEvidence | None:
    for status, resource in snapshot.network_responses:
        if resource == SEARCH_RESPONSE_MACRO_PROTECTION:
            # The bounded response observer already verified the exact top-level
            # errCode. HTTP 200 does not turn that business rejection into success.
            return ReservationProtectionEvidence(
                "marker_macro_err1", "business_response", status, "business_macro_err1"
            )
        if is_rate_limit_response(status, resource):
            return ReservationProtectionEvidence(
                "rate_limited", "network", status, _resource_kind(resource)
            )
        trigger = protection_trigger_from_http_response(status, resource)
        if trigger is not None:
            return ReservationProtectionEvidence(
                trigger, "network", status, _resource_kind(resource)
            )
    trigger = protection_trigger_from_text(snapshot.body_text)
    if trigger is not None:
        return ReservationProtectionEvidence(trigger, "page_body")
    for text in snapshot.protection_texts:
        trigger = protection_trigger_from_text(text)
        if trigger is not None:
            return ReservationProtectionEvidence(trigger, "page_surface")
    return None


def log_reservation_protection(
    evidence: ReservationProtectionEvidence,
    snapshot: PydollPageSnapshot,
    phase: ReservationDialogPhase,
    *,
    event_logger: logging.Logger,
) -> None:
    marker_surfaces = sum(
        protection_trigger_from_text(text) == evidence.trigger for text in snapshot.protection_texts
    )
    event_logger.warning(
        "KORAIL reservation protection phase=%s source=%s trigger=%s status=%s "
        "resource=%s result_rows=%d marker_surfaces=%d",
        phase.value,
        evidence.source,
        evidence.trigger,
        evidence.status,
        evidence.resource_kind,
        min(len(snapshot.rows), 9),
        min(marker_surfaces, 9),
    )
