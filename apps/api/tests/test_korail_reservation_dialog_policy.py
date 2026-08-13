from __future__ import annotations

import pytest

from rail_waitlist.korail_sidecar.pydoll.reservation_dialog_policy import (
    ReservationDialogAction,
    ReservationDialogEvidence,
    ReservationDialogKind,
    ReservationDialogPhase,
    classify_reservation_dialog,
)


def _evidence(
    *,
    title: str,
    message: str = "공식 안내입니다.",
    labels: tuple[str, ...] = ("확인",),
    structured: bool = True,
) -> ReservationDialogEvidence:
    return ReservationDialogEvidence(
        title=title,
        message=message,
        full_text=" ".join((title, message, *labels)),
        control_labels=labels,
        has_official_alert_structure=structured,
    )


@pytest.mark.parametrize(
    ("phase", "title", "expected_kind"),
    (
        (
            ReservationDialogPhase.PRE_REQUEST,
            "이용안내",
            ReservationDialogKind.RESERVATION_INFORMATION,
        ),
        (
            ReservationDialogPhase.POST_REQUEST,
            "이용안내",
            ReservationDialogKind.RESERVATION_INFORMATION,
        ),
        (
            ReservationDialogPhase.POST_REQUEST,
            "안내메세지",
            ReservationDialogKind.POST_REQUEST_NOTICE,
        ),
    ),
)
def test_exact_official_single_ack_notice_selects_acknowledgement_action(
    phase: ReservationDialogPhase,
    title: str,
    expected_kind: ReservationDialogKind,
) -> None:
    decision = classify_reservation_dialog(_evidence(title=title), phase=phase)

    assert decision.kind is expected_kind
    assert decision.action is ReservationDialogAction.ACKNOWLEDGE
    assert decision.target_label == "확인"


@pytest.mark.parametrize(
    "evidence",
    (
        _evidence(title="이용안내", structured=False),
        _evidence(title="이용안내", message=""),
        _evidence(title="이용안내", labels=("확인", "닫기")),
        _evidence(title="안내 메시지"),
        _evidence(title="안내메세지"),
    ),
)
def test_single_ack_notice_drift_or_unsupported_phase_fails_closed(
    evidence: ReservationDialogEvidence,
) -> None:
    decision = classify_reservation_dialog(
        evidence,
        phase=ReservationDialogPhase.PRE_REQUEST,
    )

    assert decision.kind is ReservationDialogKind.UNKNOWN
    assert decision.action is ReservationDialogAction.NONE


@pytest.mark.parametrize("labels", (("아니오", "네"), ("네", "아니오")))
def test_delay_consent_accepts_only_exact_post_request_opt_in(
    labels: tuple[str, ...],
) -> None:
    evidence = _evidence(
        title="지연승낙 안내",
        message=(
            "선택하신 열차는 지연 열차입니다. 승차권 구입 시 열차지연에 따른 "
            "지연배상을 하지 않습니다. 계속 진행하시겠습니까?"
        ),
        labels=labels,
    )

    decision = classify_reservation_dialog(
        evidence,
        phase=ReservationDialogPhase.POST_REQUEST,
        auto_handle_dialogs=True,
    )

    assert decision.kind is ReservationDialogKind.DELAY_CONSENT
    assert decision.action is ReservationDialogAction.ACCEPT_DELAY_CONSENT
    assert decision.target_label == "네"


@pytest.mark.parametrize(
    ("phase", "enabled"),
    (
        (ReservationDialogPhase.PRE_REQUEST, True),
        (ReservationDialogPhase.POST_REQUEST, False),
    ),
)
def test_delay_consent_remains_manual_without_post_request_opt_in(
    phase: ReservationDialogPhase,
    enabled: bool,
) -> None:
    decision = classify_reservation_dialog(
        _evidence(
            title="지연승낙 안내",
            message="지연배상을 하지 않습니다. 계속 진행하시겠습니까?",
            labels=("아니오", "네"),
        ),
        phase=phase,
        auto_handle_dialogs=enabled,
    )

    assert decision.kind is ReservationDialogKind.DELAY_CONSENT
    assert decision.action is ReservationDialogAction.NONE
    assert decision.target_label is None


def test_post_request_binary_information_selects_only_exact_yes_when_enabled() -> None:
    evidence = _evidence(
        title="이용안내",
        message="선택한 열차의 운행 안내입니다. 계속 진행하시겠습니까?",
        labels=("아니오", "네"),
    )
    manual = classify_reservation_dialog(
        evidence,
        phase=ReservationDialogPhase.POST_REQUEST,
    )
    automatic = classify_reservation_dialog(
        evidence,
        phase=ReservationDialogPhase.POST_REQUEST,
        auto_handle_dialogs=True,
    )

    assert manual.kind is ReservationDialogKind.RESERVATION_INFORMATION_CONSENT
    assert manual.action is ReservationDialogAction.NONE
    assert automatic.kind is ReservationDialogKind.RESERVATION_INFORMATION_CONSENT
    assert automatic.action is ReservationDialogAction.ACCEPT_RESERVATION_INFORMATION
    assert automatic.target_label == "네"


@pytest.mark.parametrize("phase", tuple(ReservationDialogPhase))
def test_unknown_official_single_ack_is_bounded_by_auto_action_opt_in(
    phase: ReservationDialogPhase,
) -> None:
    evidence = _evidence(title="운영 안내")

    manual = classify_reservation_dialog(
        evidence,
        phase=phase,
    )
    automatic = classify_reservation_dialog(
        evidence,
        phase=phase,
        auto_handle_dialogs=True,
    )

    assert manual.kind is ReservationDialogKind.UNKNOWN
    assert manual.action is ReservationDialogAction.NONE
    assert automatic.kind is ReservationDialogKind.GENERIC_ACKNOWLEDGEMENT
    assert automatic.action is ReservationDialogAction.ACKNOWLEDGE
    assert automatic.target_label == "확인"


def test_delay_consent_with_an_extra_control_fails_closed() -> None:
    evidence = _evidence(
        title="지연승낙 안내",
        message="지연배상을 하지 않습니다. 계속 진행하시겠습니까?",
        labels=("아니오", "네", "닫기"),
    )

    decision = classify_reservation_dialog(
        evidence,
        phase=ReservationDialogPhase.PRE_REQUEST,
    )

    assert decision.kind is ReservationDialogKind.UNKNOWN


def test_existing_reservation_choice_is_typed_but_never_selected() -> None:
    decision = classify_reservation_dialog(
        _evidence(
            title="안내메세지",
            labels=("다른여정예약", "예약내역확인"),
        ),
        phase=ReservationDialogPhase.POST_REQUEST,
    )

    assert decision.kind is ReservationDialogKind.EXISTING_RESERVATION_CHOICE
    assert decision.action is ReservationDialogAction.NONE


def test_authenticated_login_shell_is_separate_from_actionable_notices() -> None:
    decision = classify_reservation_dialog(
        _evidence(title="로그인", message="", labels=(), structured=False),
        phase=ReservationDialogPhase.POST_REQUEST,
    )

    assert decision.kind is ReservationDialogKind.AUTHENTICATED_LOGIN_SHELL
    assert decision.action is ReservationDialogAction.NONE
