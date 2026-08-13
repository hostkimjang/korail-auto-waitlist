"""Read fail-closed KORAIL reservation evidence from an authenticated Pydoll session.

This module deliberately knows only the semantic read surface needed for a
same-session confirmation.  Browser lifecycle, credential generation and the
session lock remain owned by :mod:`korail_pydoll_browser`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from datetime import time as clock_time
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ...reservations.provider_confirmation.contracts import (
    ReservationConfirmationPurpose,
    ReservationConfirmationTarget,
)
from ...reservations.provider_confirmation.korail import (
    KORAIL_CONFIRMATION_SOURCE,
    KORAIL_ISSUED_TICKET_LIST_SOURCE,
    KORAIL_RESERVATION_LIST_SOURCE,
    KorailSameSessionDetailEvidence,
)
from ..browser_protection import (
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)


class KorailConfirmationSnapshot(Protocol):
    """Secret-free fields exposed by an official read-only reservation surface."""

    @property
    def body_text(self) -> str: ...

    @property
    def protection_texts(self) -> tuple[str, ...]: ...

    @property
    def network_responses(self) -> tuple[tuple[int, str], ...]: ...

    @property
    def url(self) -> str: ...

    @property
    def reservation_rows(self) -> tuple[str, ...]: ...


class KorailReservationListSnapshot(Protocol):
    url: str
    reservation_rows: tuple[str, ...]
    rendered_card_count: int
    malformed_card_count: int
    page_marker_visible: bool
    explicit_empty_visible: bool
    loading_visible: bool
    protection_detected: bool
    network_responses: tuple[tuple[int, str], ...]

    @property
    def page_ready(self) -> bool: ...

    @property
    def official_read_completed(self) -> bool: ...


@runtime_checkable
class KorailConfirmationSession(Protocol):
    """Read-only same-session operations required to confirm a payment hold."""

    async def _snapshot(self) -> KorailConfirmationSnapshot: ...

    async def _probe_official_authenticated_session(self) -> bool: ...

    async def _has_authenticated_header(self) -> bool: ...


@runtime_checkable
class KorailReservationListSession(KorailConfirmationSession, Protocol):
    async def read_reservation_list(self) -> KorailReservationListSnapshot: ...


class KorailIssuedTicketSummary(Protocol):
    service_date: date
    train_number: str
    origin: str
    destination: str
    departure_time: clock_time
    arrival_time: clock_time
    seat_class: str
    passenger_count: int
    car_number: str
    seat_number: str
    returned: bool
    operation_stopped: bool
    transferred: bool


class KorailIssuedTicketListSnapshot(Protocol):
    url: str
    tickets: tuple[KorailIssuedTicketSummary, ...]
    rendered_card_count: int
    malformed_card_count: int
    empty_state_visible: bool
    protection_detected: bool
    network_responses: tuple[tuple[int, str], ...]

    @property
    def page_ready(self) -> bool: ...


@runtime_checkable
class KorailIssuedTicketListSession(KorailConfirmationSession, Protocol):
    async def read_issued_ticket_list(self) -> KorailIssuedTicketListSnapshot: ...


PaymentDeadlineParser = Callable[[str], datetime | None]


async def read_korail_same_session_confirmation(
    *,
    session: KorailConfirmationSession,
    target: ReservationConfirmationTarget,
    credential_version: int | None,
    payment_deadline_parser: PaymentDeadlineParser | None = None,
) -> KorailSameSessionDetailEvidence:
    """Read detail then list fallback without taking any action on either page."""

    parser = payment_deadline_parser or _parse_korail_payment_deadline
    observed_at = datetime.now(UTC)
    snapshot: KorailConfirmationSnapshot | None = None
    try:
        snapshot = await session._snapshot()
    except Exception:  # noqa: BLE001 -- provider backend errors stay opaque.
        if target.purpose is ReservationConfirmationPurpose.INITIAL:
            return _inconclusive_evidence(observed_at, credential_version)

    if snapshot is None:
        detail = _inconclusive_evidence(observed_at, credential_version)
    else:
        if _confirmation_snapshot_is_blocked(snapshot):
            return _blocked_evidence(observed_at, KORAIL_CONFIRMATION_SOURCE)

        path = urlsplit(snapshot.url).path.rstrip("/")
        if path == "/ticket/login" and not await _session_is_authenticated(session):
            return _auth_required_evidence(
                observed_at,
                credential_version,
                KORAIL_CONFIRMATION_SOURCE,
            )

        detail = _confirmation_evidence_from_text(
            target=target,
            text=snapshot.body_text,
            observed_at=observed_at,
            credential_version=credential_version,
            source=KORAIL_CONFIRMATION_SOURCE,
            required_path_matched=path == "/ticket/reservation/detail",
            payment_deadline_parser=parser,
        )
    issued_target_absence_confirmed = False
    if target.purpose is ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP:
        (
            issued_evidence,
            issued_target_absence_confirmed,
        ) = await _payment_follow_up_issued_ticket_probe(
            session=session,
            target=target,
            credential_version=credential_version,
        )
        if issued_evidence is not None:
            return issued_evidence
        # Never reaffirm a payment hold from the captured detail DOM. Only the
        # freshly navigated unpaid-reservation list may establish pending/absent.
        detail = _inconclusive_evidence(observed_at, credential_version)
    elif _is_complete_detail_evidence(detail):
        return detail

    if not isinstance(session, KorailReservationListSession):
        return detail
    try:
        list_snapshot = await session.read_reservation_list()
    except Exception:  # noqa: BLE001 -- provider backend errors stay opaque.
        return detail
    list_observed_at = datetime.now(UTC)
    if _reservation_list_snapshot_is_blocked(list_snapshot):
        return _blocked_evidence(list_observed_at, KORAIL_RESERVATION_LIST_SOURCE)

    list_path = urlsplit(list_snapshot.url).path.rstrip("/")
    if list_path == "/ticket/login" and not await _session_is_authenticated(session):
        return _auth_required_evidence(
            list_observed_at,
            credential_version,
            KORAIL_RESERVATION_LIST_SOURCE,
        )

    list_read_completed = (
        list_path == "/ticket/reservation/list" and list_snapshot.official_read_completed
    )

    identity_matches = tuple(
        evidence
        for row in list_snapshot.reservation_rows
        if (
            evidence := _confirmation_evidence_from_text(
                target=target,
                text=row,
                observed_at=list_observed_at,
                credential_version=credential_version,
                source=KORAIL_RESERVATION_LIST_SOURCE,
                required_path_matched=list_path == "/ticket/reservation/list",
                official_list_read_completed=list_read_completed,
                payment_deadline_parser=parser,
            )
        ).exact_identity_matched
        and (not evidence.seat_class_match_required or evidence.seat_class_matched)
        and evidence.passenger_count_matched
    )
    matches = tuple(
        evidence for evidence in identity_matches if evidence.payment_pending_markers_present
    )
    if len(matches) == 1:
        return matches[0]
    if identity_matches:
        # An exact row without the expected pending-payment controls may be a
        # changed provider state. The unpaid list alone cannot distinguish paid,
        # cancelled, or another provider-side transition, so keep it inconclusive.
        list_evidence = KorailSameSessionDetailEvidence(
            observed_at=list_observed_at,
            credential_version=credential_version,
            exact_identity_matched=False,
            payment_pending_markers_present=False,
            seat_class_match_required=False,
            official_list_read_completed=list_read_completed,
            official_list_target_absent=False,
            source=KORAIL_RESERVATION_LIST_SOURCE,
        )
    else:
        list_evidence = KorailSameSessionDetailEvidence(
            observed_at=list_observed_at,
            credential_version=credential_version,
            exact_identity_matched=False,
            payment_pending_markers_present=False,
            seat_class_match_required=False,
            official_list_read_completed=list_read_completed,
            official_list_target_absent=(list_read_completed and len(matches) == 0),
            source=KORAIL_RESERVATION_LIST_SOURCE,
        )

    if (
        target.purpose is ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
        and list_evidence.official_list_target_absent
        and (not target.reserved_seats or not issued_target_absence_confirmed)
    ):
        # A negative unpaid-list read is safe only after a complete issued-ticket
        # read also ruled out this exact persisted seat. Otherwise the ticket may
        # be paid but hidden behind an incomplete or ambiguous issued-card read.
        return _inconclusive_evidence(list_observed_at, credential_version)
    return list_evidence


async def _payment_follow_up_issued_ticket_probe(
    *,
    session: KorailConfirmationSession,
    target: ReservationConfirmationTarget,
    credential_version: int | None,
) -> tuple[KorailSameSessionDetailEvidence | None, bool]:
    """Return paid evidence or a trustworthy exact-seat absence signal."""

    if (
        target.purpose is not ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
        or target.passenger_count != 1
        or target.arrival_at is None
        or len(target.reserved_seats) != 1
        or not isinstance(session, KorailIssuedTicketListSession)
    ):
        return None, False
    try:
        snapshot = await session.read_issued_ticket_list()
    except Exception:  # noqa: BLE001 -- provider backend errors stay opaque.
        return None, False
    observed_at = datetime.now(UTC)
    if _issued_ticket_snapshot_is_blocked(snapshot):
        return _blocked_evidence(observed_at, KORAIL_ISSUED_TICKET_LIST_SOURCE), False

    path = urlsplit(snapshot.url).path.rstrip("/")
    if path == "/ticket/login" and not await _session_is_authenticated(session):
        return (
            _auth_required_evidence(
                observed_at,
                credential_version,
                KORAIL_ISSUED_TICKET_LIST_SOURCE,
            ),
            False,
        )
    if (
        path != "/ticket/myticket/list"
        or not snapshot.page_ready
        or snapshot.malformed_card_count != 0
    ):
        return None, False
    if snapshot.empty_state_visible:
        complete_empty = snapshot.rendered_card_count == 0 and not snapshot.tickets
        return None, complete_empty
    matches = tuple(ticket for ticket in snapshot.tickets if _issued_ticket_matches(target, ticket))
    if len(matches) != 1:
        return None, False
    return (
        KorailSameSessionDetailEvidence(
            observed_at=observed_at,
            credential_version=credential_version,
            exact_identity_matched=True,
            payment_pending_markers_present=False,
            seat_class_matched=True,
            passenger_count_matched=True,
            issued_ticket_exact_match=True,
            source=KORAIL_ISSUED_TICKET_LIST_SOURCE,
        ),
        False,
    )


def _issued_ticket_snapshot_is_blocked(snapshot: KorailIssuedTicketListSnapshot) -> bool:
    if snapshot.protection_detected:
        return True
    return any(
        is_rate_limit_response(status, resource_type)
        or protection_trigger_from_http_response(status, resource_type) is not None
        for status, resource_type in snapshot.network_responses
    )


def _reservation_list_snapshot_is_blocked(snapshot: KorailReservationListSnapshot) -> bool:
    if snapshot.protection_detected:
        return True
    return any(
        is_rate_limit_response(status, resource_type)
        or protection_trigger_from_http_response(status, resource_type) is not None
        for status, resource_type in snapshot.network_responses
    )


def _issued_ticket_matches(
    target: ReservationConfirmationTarget,
    ticket: KorailIssuedTicketSummary,
) -> bool:
    if ticket.returned or ticket.operation_stopped or ticket.transferred:
        return False
    if target.arrival_at is None or len(target.reserved_seats) > 1:
        return False
    local_departure = target.departure_at.astimezone(ZoneInfo("Asia/Seoul"))
    local_arrival = target.arrival_at.astimezone(ZoneInfo("Asia/Seoul"))
    expected_seat = target.reserved_seats[0] if target.reserved_seats else None
    expected_class = "standard" if target.seat_class.value == "standard" else "first"
    return (
        ticket.service_date == local_departure.date()
        and _normalized_train_number(ticket.train_number)
        == _normalized_train_number(target.train_number)
        and _normalize_station(ticket.origin) == _normalize_station(target.origin)
        and _normalize_station(ticket.destination) == _normalize_station(target.destination)
        and ticket.departure_time.strftime("%H:%M") == local_departure.strftime("%H:%M")
        and ticket.arrival_time.strftime("%H:%M") == local_arrival.strftime("%H:%M")
        and ticket.seat_class == expected_class
        and ticket.passenger_count == target.passenger_count == 1
        and (
            expected_seat is None
            or (
                ticket.car_number.strip().upper() == expected_seat.car_number
                and ticket.seat_number.strip().upper() == expected_seat.seat_number
            )
        )
    )


def _normalized_train_number(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    return digits.lstrip("0") or "0"


async def _session_is_authenticated(session: KorailConfirmationSession) -> bool:
    """A transient login route is only unauthenticated when both signals agree."""

    officially_authenticated = await session._probe_official_authenticated_session()
    header_authenticated = await session._has_authenticated_header()
    return officially_authenticated or header_authenticated


def _inconclusive_evidence(
    observed_at: datetime,
    credential_version: int | None,
) -> KorailSameSessionDetailEvidence:
    return KorailSameSessionDetailEvidence(
        observed_at=observed_at,
        credential_version=credential_version,
        exact_identity_matched=False,
        payment_pending_markers_present=False,
    )


def _blocked_evidence(
    observed_at: datetime,
    source: str,
) -> KorailSameSessionDetailEvidence:
    return KorailSameSessionDetailEvidence(
        observed_at=observed_at,
        credential_version=None,
        exact_identity_matched=False,
        payment_pending_markers_present=False,
        provider_blocked=True,
        source=source,
    )


def _auth_required_evidence(
    observed_at: datetime,
    credential_version: int | None,
    source: str,
) -> KorailSameSessionDetailEvidence:
    return KorailSameSessionDetailEvidence(
        observed_at=observed_at,
        credential_version=credential_version,
        exact_identity_matched=False,
        payment_pending_markers_present=False,
        auth_required=True,
        source=source,
    )


def _is_complete_detail_evidence(evidence: KorailSameSessionDetailEvidence) -> bool:
    return (
        evidence.exact_identity_matched
        and evidence.seat_class_matched
        and evidence.passenger_count_matched
        and evidence.payment_pending_markers_present
    )


def _confirmation_snapshot_is_blocked(snapshot: KorailConfirmationSnapshot) -> bool:
    if protection_trigger_from_text(snapshot.body_text) is not None or any(
        protection_trigger_from_text(text) is not None for text in snapshot.protection_texts
    ):
        return True
    return any(
        is_rate_limit_response(status, resource_type)
        or protection_trigger_from_http_response(status, resource_type) is not None
        for status, resource_type in snapshot.network_responses
    )


def _confirmation_evidence_from_text(
    *,
    target: ReservationConfirmationTarget,
    text: str,
    observed_at: datetime,
    credential_version: int | None,
    source: str,
    required_path_matched: bool,
    official_list_read_completed: bool | None = None,
    payment_deadline_parser: PaymentDeadlineParser | None = None,
) -> KorailSameSessionDetailEvidence:
    """Reduce one official reservation surface to secret-free exact evidence."""

    body = " ".join(text.split())
    local_departure = target.departure_at.astimezone(ZoneInfo("Asia/Seoul"))
    local_arrival = (
        target.arrival_at.astimezone(ZoneInfo("Asia/Seoul"))
        if target.arrival_at is not None
        else None
    )
    seat_label = "일반실" if target.seat_class.value == "standard" else "특실"
    seat_class_matched = _has_exact_text_marker(body, seat_label)
    seat_class_match_required = source != KORAIL_RESERVATION_LIST_SOURCE
    passenger_markers = (
        (f"{target.passenger_count}매",)
        if source == KORAIL_RESERVATION_LIST_SOURCE
        else (
            f"총 {target.passenger_count}명",
            f"성인 {target.passenger_count}명",
            f"어른 {target.passenger_count}명",
        )
    )
    passenger_count_matched = any(
        _has_exact_text_marker(body, marker) for marker in passenger_markers
    )
    service_date_and_departure_matched = any(
        re.search(
            rf"{re.escape(marker)}.{{0,120}}"
            rf"(?<!\d){re.escape(local_departure.strftime('%H:%M'))}(?!\d)",
            body,
        )
        is not None
        for marker in _reservation_date_markers(local_departure.date())
    )
    exact_identity_matched = (
        required_path_matched
        and local_arrival is not None
        and _has_exact_train_number_marker(body, target.train_number)
        and _has_exact_text_marker(body, local_departure.strftime("%H:%M"))
        and _has_exact_text_marker(body, local_arrival.strftime("%H:%M"))
        and service_date_and_departure_matched
        and _has_exact_route_markers(body, target.origin, target.destination)
        and (not seat_class_match_required or seat_class_matched)
        and passenger_count_matched
    )
    payment_pending_markers = (
        ("예약취소", "예약변경", "결제/발권")
        if source == KORAIL_RESERVATION_LIST_SOURCE
        else ("예약취소", "장바구니", "결제하기")
    )
    return KorailSameSessionDetailEvidence(
        observed_at=observed_at,
        credential_version=credential_version,
        exact_identity_matched=exact_identity_matched,
        seat_class_matched=seat_class_matched,
        passenger_count_matched=passenger_count_matched,
        seat_class_match_required=seat_class_match_required,
        official_list_read_completed=(
            source == KORAIL_RESERVATION_LIST_SOURCE
            and (
                required_path_matched
                if official_list_read_completed is None
                else official_list_read_completed
            )
        ),
        payment_pending_markers_present=all(marker in body for marker in payment_pending_markers),
        payment_deadline=(payment_deadline_parser or _parse_korail_payment_deadline)(body),
        source=source,
    )


def _has_exact_train_number_marker(body: str, train_number: str) -> bool:
    normalized = train_number.lstrip("0") or "0"
    return re.search(rf"(?<!\d)0*{re.escape(normalized)}(?!\d)", body) is not None


def _has_exact_text_marker(body: str, marker: str) -> bool:
    token = rf"(?<![0-9A-Za-z가-힣]){re.escape(marker)}(?![0-9A-Za-z가-힣])"
    return re.search(token, body) is not None


def _has_exact_route_markers(body: str, origin: str, destination: str) -> bool:
    origin_match = re.search(
        rf"(?<![0-9A-Za-z가-힣]){re.escape(_normalize_station(origin))}(?:역)?"
        r"(?![0-9A-Za-z가-힣])",
        body,
    )
    if origin_match is None:
        return False
    return (
        re.search(
            rf"(?<![0-9A-Za-z가-힣]){re.escape(_normalize_station(destination))}(?:역)?"
            r"(?![0-9A-Za-z가-힣])",
            body[origin_match.end() :],
        )
        is not None
    )


def _reservation_date_markers(value: date) -> tuple[str, ...]:
    return (
        value.isoformat(),
        value.strftime("%Y.%m.%d"),
        value.strftime("%Y. %m. %d"),
        f"{value.year}년{value.month:02d}월{value.day:02d}일",
        f"{value.year}년 {value.month}월 {value.day}일",
        f"{value.month}월 {value.day}일",
    )


def _normalize_station(value: str) -> str:
    return "".join(value.split()).removesuffix("역")


def _parse_korail_payment_deadline(body: str) -> datetime | None:
    """Parse only an explicit provider date and time; never invent a deadline."""

    patterns = (
        re.compile(
            r"결제\s*(?:기한|마감)\s*[:：]?\s*"
            r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})일?"
            r"(?:\s*[.]\s*|\s+)"
            r"(\d{1,2}):(\d{2})"
        ),
        re.compile(
            r"결제\s*(?:기한|마감)\s*[:：]?\s*"
            r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s+"
            r"(\d{1,2}):(\d{2})"
        ),
    )
    for pattern in patterns:
        match = pattern.search(body)
        if match is None:
            continue
        try:
            year, month, day, hour, minute = (int(part) for part in match.groups())
            return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Seoul"))
        except ValueError:
            return None
    return None
