"""Classify exact KORAIL reservation dialogs into bounded operator-approved actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReservationDialogPhase(StrEnum):
    PRE_REQUEST = "pre_request"
    POST_REQUEST = "post_request"


class ReservationDialogKind(StrEnum):
    RESERVATION_INFORMATION = "reservation_information"
    RESERVATION_INFORMATION_CONSENT = "reservation_information_consent"
    POST_REQUEST_NOTICE = "post_request_notice"
    GENERIC_ACKNOWLEDGEMENT = "generic_acknowledgement"
    DELAY_CONSENT = "delay_consent"
    EXISTING_RESERVATION_CHOICE = "existing_reservation_choice"
    AUTHENTICATED_LOGIN_SHELL = "authenticated_login_shell"
    UNKNOWN = "unknown"


class ReservationDialogControlShape(StrEnum):
    SINGLE_ACKNOWLEDGEMENT = "single_acknowledgement"
    BINARY_CHOICE = "binary_choice"
    OTHER = "other"


class ReservationDialogAction(StrEnum):
    NONE = "none"
    ACKNOWLEDGE = "acknowledge"
    ACCEPT_DELAY_CONSENT = "accept_delay_consent"
    ACCEPT_RESERVATION_INFORMATION = "accept_reservation_information"


@dataclass(frozen=True)
class ReservationDialogEvidence:
    """Bounded DOM evidence used only in memory for a closed classification."""

    title: str
    message: str
    full_text: str
    control_labels: tuple[str, ...]
    has_official_alert_structure: bool


@dataclass(frozen=True)
class ReservationDialogDecision:
    kind: ReservationDialogKind
    control_shape: ReservationDialogControlShape
    action: ReservationDialogAction = ReservationDialogAction.NONE
    target_label: str | None = None


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _control_shape(labels: tuple[str, ...]) -> ReservationDialogControlShape:
    if labels == ("확인",):
        return ReservationDialogControlShape.SINGLE_ACKNOWLEDGEMENT
    if len(labels) == 2:
        return ReservationDialogControlShape.BINARY_CHOICE
    return ReservationDialogControlShape.OTHER


def classify_reservation_dialog(
    evidence: ReservationDialogEvidence,
    *,
    phase: ReservationDialogPhase,
    auto_handle_dialogs: bool = False,
) -> ReservationDialogDecision:
    """Return one closed action for an exact official dialog shape."""

    title = _normalized_text(evidence.title)
    message = _normalized_text(evidence.message)
    full_text = _normalized_text(evidence.full_text)
    labels = tuple(_normalized_text(label) for label in evidence.control_labels)
    shape = _control_shape(labels)

    delay_text = f"{title} {message} {full_text}"
    if (
        evidence.has_official_alert_structure
        and title == "지연승낙 안내"
        and len(labels) == 2
        and set(labels) == {"네", "아니오"}
        and labels.count("네") == 1
        and labels.count("아니오") == 1
        and "지연배상" in delay_text
        and "계속 진행하시겠습니까?" in delay_text
    ):
        action = (
            ReservationDialogAction.ACCEPT_DELAY_CONSENT
            if auto_handle_dialogs and phase is ReservationDialogPhase.POST_REQUEST
            else ReservationDialogAction.NONE
        )
        return ReservationDialogDecision(
            ReservationDialogKind.DELAY_CONSENT,
            shape,
            action=action,
            target_label="네" if action is ReservationDialogAction.ACCEPT_DELAY_CONSENT else None,
        )

    exact_binary_information = (
        evidence.has_official_alert_structure
        and title == "이용안내"
        and bool(message)
        and len(labels) == 2
        and set(labels) == {"네", "아니오"}
        and labels.count("네") == 1
        and labels.count("아니오") == 1
    )
    if exact_binary_information:
        action = (
            ReservationDialogAction.ACCEPT_RESERVATION_INFORMATION
            if auto_handle_dialogs and phase is ReservationDialogPhase.POST_REQUEST
            else ReservationDialogAction.NONE
        )
        return ReservationDialogDecision(
            ReservationDialogKind.RESERVATION_INFORMATION_CONSENT,
            shape,
            action=action,
            target_label="네"
            if action is ReservationDialogAction.ACCEPT_RESERVATION_INFORMATION
            else None,
        )
    if len(labels) == 2 and set(labels) == {"예약내역확인", "다른여정예약"}:
        return ReservationDialogDecision(
            ReservationDialogKind.EXISTING_RESERVATION_CHOICE,
            shape,
        )

    if title == "로그인" or (not title and full_text == "로그인" and not labels):
        return ReservationDialogDecision(
            ReservationDialogKind.AUTHENTICATED_LOGIN_SHELL,
            shape,
        )

    exact_single_ack = (
        evidence.has_official_alert_structure
        and shape is ReservationDialogControlShape.SINGLE_ACKNOWLEDGEMENT
        and bool(message)
    )
    if exact_single_ack and title == "이용안내":
        return ReservationDialogDecision(
            ReservationDialogKind.RESERVATION_INFORMATION,
            shape,
            action=ReservationDialogAction.ACKNOWLEDGE,
            target_label="확인",
        )
    if exact_single_ack and phase is ReservationDialogPhase.POST_REQUEST and title == "안내메세지":
        return ReservationDialogDecision(
            ReservationDialogKind.POST_REQUEST_NOTICE,
            shape,
            action=ReservationDialogAction.ACKNOWLEDGE,
            target_label="확인",
        )
    if exact_single_ack and auto_handle_dialogs and bool(title):
        return ReservationDialogDecision(
            ReservationDialogKind.GENERIC_ACKNOWLEDGEMENT,
            shape,
            action=ReservationDialogAction.ACKNOWLEDGE,
            target_label="확인",
        )
    return ReservationDialogDecision(ReservationDialogKind.UNKNOWN, shape)
