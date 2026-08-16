"""KORAIL read-only reservation evidence normalization.

The authenticated detail page is the fast path. The official reservation list is
the fallback when the current SPA tab no longer contains the attempted reservation.
Neither surface may click payment, cancellation, or another reservation action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ...domain import Provider
from ...provider_registry.official_url_policy import require_official_handoff_url
from .contracts import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)

KORAIL_RESERVATION_HANDOFF_URL = "https://www.korail.com/ticket/reservation/list"
KORAIL_CONFIRMATION_SOURCE = "korail-same-session-detail"
KORAIL_RESERVATION_LIST_SOURCE = "korail-reservation-list"
KORAIL_ISSUED_TICKET_LIST_SOURCE = "korail-issued-ticket-list"


@dataclass(frozen=True, slots=True)
class KorailSameSessionDetailEvidence:
    """Redacted outcome of one current-tab detail-page read.

    ``exact_identity_matched`` is source-specific. Detail evidence includes the
    expected seat class; the reservation list proves a unique hold with route,
    service date, train number, departure/arrival time, and ticket count because
    that surface does not expose the seat class. No DOM text is carried forward.
    """

    observed_at: datetime
    credential_version: int | None
    exact_identity_matched: bool
    payment_pending_markers_present: bool
    seat_class_matched: bool = False
    passenger_count_matched: bool = False
    seat_class_match_required: bool = True
    official_list_read_completed: bool = False
    official_list_target_absent: bool = False
    inconclusive_diagnostic_code: ReservationConfirmationDiagnosticCode = (
        ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    auth_required: bool = False
    provider_blocked: bool = False
    issued_ticket_exact_match: bool = False
    payment_deadline: datetime | None = None
    source: str = KORAIL_CONFIRMATION_SOURCE

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        if self.payment_deadline is not None and (
            self.payment_deadline.tzinfo is None or self.payment_deadline.utcoffset() is None
        ):
            raise ValueError("payment_deadline must include a timezone")
        if self.source not in {
            KORAIL_CONFIRMATION_SOURCE,
            KORAIL_RESERVATION_LIST_SOURCE,
            KORAIL_ISSUED_TICKET_LIST_SOURCE,
        }:
            raise ValueError("unsupported KORAIL confirmation evidence source")
        if self.official_list_target_absent and (
            self.source != KORAIL_RESERVATION_LIST_SOURCE or not self.official_list_read_completed
        ):
            raise ValueError("official list target absence requires a completed official list read")
        if self.inconclusive_diagnostic_code not in {
            ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE,
            ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS,
            ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT,
        }:
            raise ValueError("unsupported KORAIL inconclusive diagnostic code")
        if self.issued_ticket_exact_match and (
            self.source != KORAIL_ISSUED_TICKET_LIST_SOURCE
            or not self.exact_identity_matched
            or not self.seat_class_matched
            or not self.passenger_count_matched
            or self.payment_pending_markers_present
            or self.payment_deadline is not None
        ):
            raise ValueError("issued-ticket match requires complete redacted paid evidence")


class KorailSameSessionDetailProbe(Protocol):
    """Read the already-open official detail page without taking an action."""

    async def read_detail(
        self,
        target: ReservationConfirmationTarget,
    ) -> KorailSameSessionDetailEvidence: ...


def normalize_korail_same_session_detail(
    target: ReservationConfirmationTarget,
    evidence: KorailSameSessionDetailEvidence,
) -> ReservationConfirmationResult:
    if target.provider is not Provider.KORAIL:
        raise ValueError("KORAIL detail confirmation received a non-KORAIL target")
    if evidence.provider_blocked:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.PROVIDER_BLOCKED,
            source=evidence.source,
            observed_at=evidence.observed_at,
        )
    if evidence.auth_required:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source=evidence.source,
            observed_at=evidence.observed_at,
        )
    if evidence.credential_version != target.credential_version:
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            diagnostic_code=(ReservationConfirmationDiagnosticCode.CREDENTIAL_CONTEXT_MISMATCH),
            source=evidence.source,
            observed_at=evidence.observed_at,
        )
    paid_confirmation_seats = (
        target.confirmation_correlation_seats
        if target.purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
        else target.reserved_seats
    )
    if (
        evidence.source == KORAIL_ISSUED_TICKET_LIST_SOURCE
        and evidence.issued_ticket_exact_match
        and target.purpose
        in {
            ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP,
            ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP,
        }
        and target.passenger_count == 1
        and len(paid_confirmation_seats) == 1
    ):
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
            source=evidence.source,
            observed_at=evidence.observed_at,
        )
    if (
        target.purpose is not ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
        and evidence.exact_identity_matched
        and (not evidence.seat_class_match_required or evidence.seat_class_matched)
        and evidence.passenger_count_matched
        and evidence.payment_pending_markers_present
    ):
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source=evidence.source,
            observed_at=evidence.observed_at,
            payment_deadline=evidence.payment_deadline,
            official_handoff_url=require_official_handoff_url(
                Provider.KORAIL,
                KORAIL_RESERVATION_HANDOFF_URL,
            ),
        )
    if (
        evidence.source == KORAIL_RESERVATION_LIST_SOURCE
        and evidence.official_list_read_completed
        and evidence.official_list_target_absent
        and target.arrival_at is not None
    ):
        # A successfully loaded authenticated official reservation list is the one
        # surface where absence of the exact target is meaningful. This still does
        # not authorize another reservation by itself; the worker only uses bounded
        # negative reads to remove a stale payment handoff behind the existing fence.
        return ReservationConfirmationResult(
            provider=target.provider,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source=evidence.source,
            observed_at=evidence.observed_at,
        )
    # Absence from a same-tab detail page or an uncertain list load is not an
    # official negative result.
    complete_identity = (
        evidence.exact_identity_matched
        and (not evidence.seat_class_match_required or evidence.seat_class_matched)
        and evidence.passenger_count_matched
    )
    return ReservationConfirmationResult(
        provider=target.provider,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        diagnostic_code=(
            ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS
            if (
                evidence.inconclusive_diagnostic_code
                is ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS
                or complete_identity
            )
            else evidence.inconclusive_diagnostic_code
        ),
        source=evidence.source,
        observed_at=evidence.observed_at,
    )


@dataclass(slots=True)
class KorailSameSessionDetailConfirmationAdapter:
    probe: KorailSameSessionDetailProbe

    async def confirm(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return normalize_korail_same_session_detail(target, await self.probe.read_detail(target))
