"""Drive one exact KORAIL reservation DOM attempt without payment actions."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from ...provider_adapters.korail_reservation_controls import booking_seat_control_key
from ..browser_contracts import BrowserSourceUnavailable
from ..browser_protection import (
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from . import reservation_dialog_policy as _dialog_policy
from .auth_contracts import KorailCredentialInput
from .page_contracts import (
    KORAIL_ROUTE_HEADING,
    PydollPageSnapshot,
    normalize_korail_station,
    normalize_korail_train_number,
)
from .reservation_contracts import (
    KorailReservationOutcome,
    KorailReservationProgress,
    KorailReservationProgressCallback,
    KorailReservationRequest,
    KorailReservationResult,
    KorailReservedSeat,
)

__all__ = (
    "KORAIL_ROUTE_HEADING",
    "Any",
    "Awaitable",
    "BrowserSourceUnavailable",
    "Callable",
    "CurrentSchedule",
    "KorailCredentialInput",
    "KorailReservationOutcome",
    "KorailReservationRequest",
    "KorailReservationResult",
    "Mapping",
    "Protocol",
    "PydollPageSnapshot",
    "PydollReservationDomDriver",
    "ReadControlState",
    "ReservationAttemptState",
    "ReservationControlState",
    "ReservationDomCompatibilityPort",
    "ReservationExecuteScript",
    "VisibleElements",
    "annotations",
    "asyncio",
    "booking_seat_control_key",
    "dataclass",
    "date",
    "datetime",
    "is_rate_limit_response",
    "logging",
    "normalize_korail_station",
    "normalize_korail_train_number",
    "protection_trigger_from_http_response",
    "protection_trigger_from_text",
    "re",
    "urlsplit",
)


@dataclass
class ReservationAttemptState:
    """One-shot latches whose loss could repeat an official booking action."""

    login_attempted: bool = False
    pre_login_route_check_attempted: bool = False
    pre_login_route_authenticated: bool = False
    post_submit_check_attempted: bool = False
    post_submit_authenticated: bool = False
    preserved_selection_checked: bool = False
    preserved_selection_matches: bool = False
    reservation_clicked: bool = False
    dialog_actions_attempted: tuple[
        tuple[_dialog_policy.ReservationDialogPhase, _dialog_policy.ReservationDialogKind], ...
    ] = ()
    dialog_settle_deadlines: (
        dict[
            tuple[_dialog_policy.ReservationDialogPhase, _dialog_policy.ReservationDialogKind],
            float,
        ]
        | None
    ) = None
    post_dialog_action_followup_deadline: float | None = None


class ReservationControlState(Protocol):
    enabled: bool
    aria_disabled: str
    disabled_attribute: bool
    read_error: bool


class ReservationDomCompatibilityPort(Protocol):
    async def _row_matches_reservation(
        self,
        row: Any,
        request: KorailReservationRequest,
    ) -> bool: ...

    async def _actionable_seat_controls(
        self,
        row: Any,
        seat_class_label: str,
    ) -> list[Any]: ...

    async def _seat_price_box_metadata(self, element: Any) -> tuple[str, tuple[str, ...]]: ...

    async def _probe_reservation_terminal(
        self,
        request: KorailReservationRequest,
        attempt: ReservationAttemptState | None = None,
    ) -> KorailReservationResult | None: ...

    async def _authenticate_in_place(
        self,
        credential: KorailCredentialInput,
        attempt: ReservationAttemptState | None = None,
    ) -> bool: ...

    async def _has_exact_preserved_booking_state(
        self,
        request: KorailReservationRequest,
    ) -> bool: ...

    async def _probe_official_authenticated_session(self) -> bool: ...

    async def _has_authenticated_header(self) -> bool: ...

    async def _snapshot(self) -> PydollPageSnapshot: ...


class ReservationExecuteScript(Protocol):
    def __call__(
        self,
        script: str,
        *,
        return_by_value: bool,
        await_promise: bool,
        timeout: int,
    ) -> Awaitable[object]: ...


class VisibleElements(Protocol):
    async def __call__(self, selector: str, *, scope: object = None) -> list[Any]: ...


type CurrentSchedule = Callable[[], Awaitable[tuple[date, int]]]
type ReadControlState = Callable[[Any], Awaitable[ReservationControlState]]


def _control_state_allows_dialog_action(state: ReservationControlState | None) -> bool:
    if state is None or state.read_error or not state.enabled or state.disabled_attribute:
        return False
    return state.aria_disabled.casefold() in {"", "false"}


def _normalized_train_number(value: str) -> str:
    try:
        return normalize_korail_train_number(value)
    except ValueError as error:
        raise BrowserSourceUnavailable("read_result") from error


def _has_exact_train_number_marker(body: str, train_number: str) -> bool:
    normalized = _normalized_train_number(train_number)
    return re.search(rf"(?<!\d)0*{re.escape(normalized)}(?!\d)", body) is not None


def _reservation_date_markers(value: date) -> tuple[str, ...]:
    return (
        value.isoformat(),
        value.strftime("%Y.%m.%d"),
        value.strftime("%Y. %m. %d"),
        f"{value.year}년{value.month:02d}월{value.day:02d}일",
        f"{value.year}년 {value.month}월 {value.day}일",
        f"{value.month}월 {value.day}일",
    )


_RESERVED_SEAT_PATTERN = re.compile(
    r"(?<!\d)([1-9]\d?)호차\s+([1-9]\d{0,2}[A-D])(?:석|좌석)?"
    r"(?![0-9A-Z가-힣])",
    re.IGNORECASE,
)


def _single_reserved_seat(body: str) -> tuple[KorailReservedSeat, ...]:
    matches = {
        (match.group(1), match.group(2).upper()) for match in _RESERVED_SEAT_PATTERN.finditer(body)
    }
    if len(matches) != 1:
        return ()
    car_number, seat_number = next(iter(matches))
    return (KorailReservedSeat(car_number=car_number, seat_number=seat_number),)


def _reserved_seats_from_history_state(
    value: object,
    request: KorailReservationRequest,
) -> tuple[KorailReservedSeat, ...]:
    """Read the exact seat fields consumed by KORAIL's reservation-detail bundle."""
    if not isinstance(value, Mapping):
        return ()
    required_train_fields = (
        "trainNumber",
        "departureDate",
        "departureTime",
        "arrivalTime",
        "origin",
        "destination",
    )
    if type(value.get("journeyCount")) is not int or value.get("journeyCount") != 1:
        return ()
    if any(not isinstance(value.get(field), str) for field in required_train_fields):
        return ()
    try:
        if _normalized_train_number(value["trainNumber"]) != (
            _normalized_train_number(request.train_number)
        ):
            return ()
        departure_date = value["departureDate"].strip()
        departure_time = value["departureTime"].strip()
        arrival_time = value["arrivalTime"].strip()
        if departure_date != request.travel_date.strftime("%Y%m%d"):
            return ()
        if re.fullmatch(r"[0-9]{4}(?:[0-9]{2})?", departure_time) is None:
            return ()
        if re.fullmatch(r"[0-9]{4}(?:[0-9]{2})?", arrival_time) is None:
            return ()
        if departure_time[:4] != request.departure_time.strftime("%H%M"):
            return ()
        if arrival_time[:4] != request.arrival_time.strftime("%H%M"):
            return ()
        if normalize_korail_station(value["origin"]) != (normalize_korail_station(request.origin)):
            return ()
        if normalize_korail_station(value["destination"]) != (
            normalize_korail_station(request.destination)
        ):
            return ()
    except (BrowserSourceUnavailable, TypeError, ValueError):
        return ()

    raw_seats = value.get("seats")
    # KORAIL automatic reservation is currently allowed for exactly one passenger.
    if not isinstance(raw_seats, list) or len(raw_seats) != 1:
        return ()
    raw_seat = raw_seats[0]
    if not isinstance(raw_seat, Mapping):
        return ()
    required_seat_fields = ("seatClass", "carNumber", "seatNumber")
    if any(not isinstance(raw_seat.get(field), str) for field in required_seat_fields):
        return ()
    if raw_seat["seatClass"].strip() != request.seat_class.label:
        return ()
    raw_car_number = raw_seat["carNumber"].strip()
    raw_seat_number = raw_seat["seatNumber"].strip().upper()
    if re.fullmatch(r"0*[1-9][0-9]?", raw_car_number) is None:
        return ()
    seat_match = re.fullmatch(r"0*([1-9][0-9]{0,2})([A-D])", raw_seat_number)
    if seat_match is None:
        return ()
    car_number = str(int(raw_car_number))
    seat_number = f"{int(seat_match.group(1))}{seat_match.group(2)}"
    return (KorailReservedSeat(car_number=car_number, seat_number=seat_number),)


def _sanitized_class_tokens(value: object) -> tuple[str, ...]:
    return tuple(
        token for token in str(value).split()[:8] if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", token)
    )


class PydollReservationDomDriver:
    """Own reservation-specific DOM selection and terminal observation only."""

    def __init__(
        self,
        *,
        port: ReservationDomCompatibilityPort,
        timeout_ms: int,
        timeout_seconds: float,
        execute_script: ReservationExecuteScript,
        visible_elements: VisibleElements,
        current_schedule: CurrentSchedule,
        read_control_state: ReadControlState,
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        utc_now: Callable[[], datetime],
        event_logger: logging.Logger,
        auto_handle_dialogs: bool = False,
    ) -> None:
        self._port = port
        self._timeout_ms = timeout_ms
        self._timeout_seconds = timeout_seconds
        self._execute_script = execute_script
        self._visible_elements = visible_elements
        self._current_schedule = current_schedule
        self._read_control_state = read_control_state
        self._monotonic = monotonic
        self._sleep = sleep
        self._utc_now = utc_now
        self._event_logger = event_logger
        self._auto_handle_dialogs = auto_handle_dialogs

    async def reserve_once(
        self,
        request: KorailReservationRequest,
        *,
        on_progress: KorailReservationProgressCallback | None = None,
    ) -> KorailReservationResult:
        target_rechecked_at: datetime | None = None
        seat_selected_at: datetime | None = None
        reservation_requested_at: datetime | None = None

        def result(
            outcome: KorailReservationOutcome,
            reason: str,
            *,
            seat_clicked: bool = False,
            reservation_clicked: bool = False,
            reserved_seats: tuple[KorailReservedSeat, ...] = (),
        ) -> KorailReservationResult:
            return KorailReservationResult(
                outcome=outcome,
                reason=reason,
                seat_clicked=seat_clicked,
                reservation_clicked=reservation_clicked,
                target_rechecked_at=target_rechecked_at,
                seat_selected_at=seat_selected_at,
                reservation_requested_at=reservation_requested_at,
                reserved_seats=reserved_seats,
            )

        rows = await self._visible_elements("li.tckList")
        matches = [row for row in rows if await self._port._row_matches_reservation(row, request)]
        if len(matches) != 1:
            target_rechecked_at = self._utc_now()
            if on_progress is not None:
                on_progress(KorailReservationProgress("target_rechecked", target_rechecked_at))
            return result(KorailReservationOutcome.UNAVAILABLE, "target_not_unique")

        row = matches[0]
        seat_controls = await self._port._actionable_seat_controls(
            row,
            request.seat_class.label,
        )
        target_rechecked_at = self._utc_now()
        if on_progress is not None:
            on_progress(KorailReservationProgress("target_rechecked", target_rechecked_at))
        if len(seat_controls) > 1:
            return result(KorailReservationOutcome.UNAVAILABLE, "seat_control_not_unique")
        if not seat_controls:
            return result(KorailReservationOutcome.UNAVAILABLE, "seat_not_available")

        seat = seat_controls[0]
        await seat.click()
        seat_selected_at = self._utc_now()
        if on_progress is not None:
            on_progress(KorailReservationProgress("seat_selected", seat_selected_at))
        attempt = ReservationAttemptState()
        deadline = self._monotonic() + self._timeout_seconds
        while self._monotonic() < deadline:
            terminal = await self._port._probe_reservation_terminal(request, attempt)
            if terminal is not None:
                if terminal.outcome is KorailReservationOutcome.AUTH_REQUIRED:
                    if attempt.login_attempted:
                        return result(
                            KorailReservationOutcome.AUTH_REQUIRED,
                            "authentication_required",
                            seat_clicked=True,
                            reservation_clicked=attempt.reservation_clicked,
                        )
                    attempt.login_attempted = True
                    if not await self._port._authenticate_in_place(request.credential, attempt):
                        return result(
                            KorailReservationOutcome.AUTH_REQUIRED,
                            "authentication_required",
                            seat_clicked=True,
                            reservation_clicked=attempt.reservation_clicked,
                        )
                    deadline = self._monotonic() + self._timeout_seconds
                    continue
                return result(
                    terminal.outcome,
                    terminal.reason,
                    seat_clicked=True,
                    reservation_clicked=attempt.reservation_clicked,
                    reserved_seats=terminal.reserved_seats,
                )
            relevant_dialog_deadlines = tuple((attempt.dialog_settle_deadlines or {}).values()) + (
                (attempt.post_dialog_action_followup_deadline,)
                if attempt.post_dialog_action_followup_deadline is not None
                else ()
            )
            if relevant_dialog_deadlines:
                deadline = max(
                    deadline,
                    *(dialog_deadline + 0.1 for dialog_deadline in relevant_dialog_deadlines),
                )
            if any(
                self._monotonic() < settle_deadline
                for settle_deadline in (attempt.dialog_settle_deadlines or {}).values()
            ):
                await self._sleep(0.1)
                continue
            if not attempt.reservation_clicked:
                candidates = []
                for control in await self._visible_elements("button.reservbtn"):
                    if " ".join(str(await control.text).split()) == "예매":
                        candidates.append(control)
                if len(candidates) > 1:
                    return result(
                        KorailReservationOutcome.UNAVAILABLE,
                        "reservation_control_ambiguous",
                        seat_clicked=True,
                    )
                if len(candidates) == 1:
                    if attempt.login_attempted:
                        if not attempt.preserved_selection_checked:
                            attempt.preserved_selection_checked = True
                            attempt.preserved_selection_matches = (
                                await self._port._has_exact_preserved_booking_state(request)
                            )
                        if not attempt.preserved_selection_matches:
                            return result(
                                KorailReservationOutcome.FAILED,
                                "reservation_selection_not_preserved",
                                seat_clicked=True,
                            )
                    state = await self._read_control_state(candidates[0])
                    if (
                        state.read_error
                        or not state.enabled
                        or state.disabled_attribute
                        or state.aria_disabled.casefold() == "true"
                    ):
                        return result(
                            KorailReservationOutcome.UNAVAILABLE,
                            "reservation_control_disabled",
                            seat_clicked=True,
                        )
                    try:
                        await candidates[0].click()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 -- click outcome is intentionally uncertain.
                        return result(
                            KorailReservationOutcome.FAILED,
                            "reservation_result_unknown:reservation_click_error",
                            seat_clicked=True,
                        )
                    attempt.reservation_clicked = True
                    reservation_requested_at = self._utc_now()
                    if on_progress is not None:
                        on_progress(
                            KorailReservationProgress(
                                "reservation_requested",
                                reservation_requested_at,
                            )
                        )
                    deadline = self._monotonic() + self._timeout_seconds
                    continue
            await self._sleep(0.1)
        if not attempt.reservation_clicked:
            return result(
                KorailReservationOutcome.FAILED,
                "reservation_control_timeout",
                seat_clicked=True,
            )
        return result(
            KorailReservationOutcome.FAILED,
            "reservation_result_unknown",
            seat_clicked=True,
            reservation_clicked=True,
        )

    async def has_exact_preserved_booking_state(
        self,
        request: KorailReservationRequest,
    ) -> bool:
        script = """
            (() => {
              const state = history.state?.state;
              if (!state || typeof state !== 'object') return false;
              if (typeof state.redirectUrl !== 'string' ||
                  !state.redirectUrl.startsWith('/ticket/')) return false;
              if (!Array.isArray(state.reservedTrainList) ||
                  state.reservedTrainList.length !== 1) return false;
              return Boolean(state.reserveParams &&
                typeof state.reserveParams === 'object' &&
                !Array.isArray(state.reserveParams));
            })()
        """
        response = await self._execute_script(
            script,
            return_by_value=True,
            await_promise=False,
            timeout=self._timeout_ms,
        )
        if not isinstance(response, Mapping):
            return False
        command_result = response.get("result")
        if not isinstance(command_result, Mapping):
            return False
        script_result = command_result.get("result")
        if not isinstance(script_result, Mapping) or script_result.get("value") is not True:
            return False
        try:
            selected_date, _ = await self._current_schedule()
            rows = [
                row
                for row in await self._visible_elements("li.tckList")
                if await self._port._row_matches_reservation(row, request)
            ]
            if selected_date != request.travel_date or len(rows) != 1:
                return False
            seat_controls = await self._port._actionable_seat_controls(
                rows[0],
                request.seat_class.label,
            )
            return len(seat_controls) == 1
        except Exception:  # noqa: BLE001 -- changed official state fails closed.
            return False

    async def reserved_seats_from_preserved_state(
        self,
        request: KorailReservationRequest,
    ) -> tuple[KorailReservedSeat, ...]:
        script = """
            (() => {
              const isPlainRecord = (value) => {
                if (value === null || typeof value !== 'object' || Array.isArray(value)) {
                  return false;
                }
                const prototype = Object.getPrototypeOf(value);
                return prototype === Object.prototype || prototype === null;
              };
              const state = history.state?.state;
              if (!isPlainRecord(state)) return null;
              const reservation = state.reservation;
              if (!isPlainRecord(reservation) || !isPlainRecord(reservation.jrny_infos)) {
                return null;
              }
              const journeyInfo = reservation.jrny_infos.jrny_info;
              if (!Array.isArray(journeyInfo) || journeyInfo.length !== 1) return null;
              const train = journeyInfo[0];
              if (!isPlainRecord(train) || !isPlainRecord(train.seat_infos)) return null;
              const seatInfo = train.seat_infos.seat_info;
              if (!Array.isArray(seatInfo) || seatInfo.length !== 1) return null;
              const seat = seatInfo[0];
              if (!isPlainRecord(seat)) return null;
              const trainLeaves = [
                'h_trn_no', 'h_dpt_dt', 'h_dpt_tm', 'h_arv_tm',
                'h_dpt_rs_stn_nm', 'h_arv_rs_stn_nm'
              ];
              const seatLeaves = ['h_psrm_cl_nm', 'h_srcar_no', 'h_seat_no'];
              if (!trainLeaves.every((name) => typeof train[name] === 'string') ||
                  !seatLeaves.every((name) => typeof seat[name] === 'string')) return null;
              return {
                journeyCount: journeyInfo.length,
                trainNumber: train.h_trn_no,
                departureDate: train.h_dpt_dt,
                departureTime: train.h_dpt_tm,
                arrivalTime: train.h_arv_tm,
                origin: train.h_dpt_rs_stn_nm,
                destination: train.h_arv_rs_stn_nm,
                seats: [{
                  seatClass: seat.h_psrm_cl_nm,
                  carNumber: seat.h_srcar_no,
                  seatNumber: seat.h_seat_no,
                }],
              };
            })()
        """
        try:
            response = await self._execute_script(
                script,
                return_by_value=True,
                await_promise=False,
                timeout=self._timeout_ms,
            )
            if not isinstance(response, Mapping):
                return ()
            command_result = response.get("result")
            if not isinstance(command_result, Mapping):
                return ()
            script_result = command_result.get("result")
            if not isinstance(script_result, Mapping):
                return ()
            return _reserved_seats_from_history_state(script_result.get("value"), request)
        except Exception:  # noqa: BLE001 -- changed official state fails closed.
            return ()

    async def actionable_seat_controls(
        self,
        row: Any,
        seat_class_label: str,
    ) -> list[Any]:
        actionable_by_label: dict[str, Any] = {}
        for control in await self._visible_elements("a", scope=row):
            raw_text = str(await control.text)
            price_box_text, price_box_classes = await self._port._seat_price_box_metadata(control)
            key = booking_seat_control_key(
                seat_class_label=seat_class_label,
                control_text=raw_text,
                price_box_text=price_box_text,
                price_box_classes=price_box_classes,
            )
            if key is None:
                continue
            state = await self._read_control_state(control)
            if (
                state.read_error
                or not state.enabled
                or state.disabled_attribute
                or state.aria_disabled.casefold() == "true"
            ):
                continue
            actionable_by_label.setdefault(key, control)
        return list(actionable_by_label.values())

    @staticmethod
    async def seat_price_box_metadata(element: Any) -> tuple[str, tuple[str, ...]]:
        try:
            response = await element.execute_script(
                """
                function() {
                  const box = this.closest('.price_box');
                  return {
                    text: box ? (box.innerText || '').slice(0, 200) : '',
                    classes: box ? Array.from(box.classList).slice(0, 8) : [],
                  };
                }
                """,
                return_by_value=True,
            )
            value = response.get("result", {}).get("result", {}).get("value", {})
            if not isinstance(value, dict):
                return "", ()
            classes = value.get("classes", [])
            return (
                str(value.get("text", ""))[:200],
                _sanitized_class_tokens(
                    " ".join(str(item) for item in classes) if isinstance(classes, list) else ""
                ),
            )
        except Exception:  # noqa: BLE001 -- missing owner metadata uses anchor text.
            return "", ()

    async def row_matches_reservation(
        self,
        row: Any,
        request: KorailReservationRequest,
    ) -> bool:
        kind = await row.query(".tck_inner .tit_box", raise_exc=False)
        number = await row.query(".tck_inner .tit_box .num", raise_exc=False)
        route = await row.query(".tck_inner .data_box.right", raise_exc=False)
        if kind is None or route is None:
            return False
        kind_text = " ".join(str(await kind.text).split())
        number_text = " ".join(str(await number.text).split()) if number is not None else kind_text
        try:
            normalized_number = _normalized_train_number(number_text)
        except BrowserSourceUnavailable:
            return False
        if normalized_number != _normalized_train_number(request.train_number):
            return False
        type_text = re.sub(rf"(?<!\d)0*{re.escape(normalized_number)}(?!\d)", "", kind_text)
        if request.train_type is not None:
            normalized_type = re.sub(r"\s+", "", type_text).casefold()
            if normalized_type != re.sub(r"\s+", "", request.train_type).casefold():
                return False
        route_text = " ".join(str(await route.text).split())
        route_match = KORAIL_ROUTE_HEADING.fullmatch(route_text)
        if route_match is None:
            return False
        origin, destination, departure, arrival = route_match.groups()
        return (
            normalize_korail_station(origin) == request.origin
            and normalize_korail_station(destination) == request.destination
            and departure == request.departure_time.strftime("%H:%M")
            and arrival == request.arrival_time.strftime("%H:%M")
        )

    @staticmethod
    async def _safe_element_text(element: Any) -> str:
        try:
            return " ".join(str(await element.text).split())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- unreadable dialog evidence fails closed.
            return ""

    async def _reservation_dialog_decision(
        self,
        dialog: Any,
        *,
        phase: _dialog_policy.ReservationDialogPhase,
    ) -> tuple[_dialog_policy.ReservationDialogDecision, list[Any]]:
        try:
            title_elements = await self._visible_elements(".tit_wrap h1.tit", scope=dialog)
            message_elements = await self._visible_elements(".confirm_message", scope=dialog)
            controls = await self._visible_elements("button,a,[role='button']", scope=dialog)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- detached dialog evidence fails closed.
            return (
                _dialog_policy.ReservationDialogDecision(
                    _dialog_policy.ReservationDialogKind.UNKNOWN,
                    _dialog_policy.ReservationDialogControlShape.OTHER,
                ),
                [],
            )
        title = await self._safe_element_text(title_elements[0]) if len(title_elements) == 1 else ""
        message = (
            await self._safe_element_text(message_elements[0]) if len(message_elements) == 1 else ""
        )
        evidence = _dialog_policy.ReservationDialogEvidence(
            title=title,
            message=message,
            full_text=await self._safe_element_text(dialog),
            control_labels=tuple([await self._safe_element_text(control) for control in controls]),
            has_official_alert_structure=(len(title_elements) == 1 and len(message_elements) == 1),
        )
        return (
            _dialog_policy.classify_reservation_dialog(
                evidence,
                phase=phase,
                auto_handle_dialogs=self._auto_handle_dialogs,
            ),
            controls,
        )

    def _log_reservation_dialog(
        self,
        *,
        phase: _dialog_policy.ReservationDialogPhase,
        decision: _dialog_policy.ReservationDialogDecision,
        dialog_count: str,
        action: str,
    ) -> None:
        self._event_logger.info(
            "KORAIL reservation dialog phase=%s kind=%s control_shape=%s dialog_count=%s action=%s",
            phase.value,
            decision.kind.value,
            decision.control_shape.value,
            dialog_count,
            action,
        )

    async def probe_reservation_terminal(
        self,
        request: KorailReservationRequest,
        attempt: ReservationAttemptState | None = None,
    ) -> KorailReservationResult | None:
        attempt = attempt or ReservationAttemptState()
        snapshot = await self._port._snapshot()
        for status, resource_type in snapshot.network_responses:
            if is_rate_limit_response(status, resource_type):
                return KorailReservationResult(
                    KorailReservationOutcome.PROVIDER_BLOCKED,
                    "rate_limited",
                )
            if protection_trigger_from_http_response(status, resource_type) is not None:
                return KorailReservationResult(
                    KorailReservationOutcome.PROVIDER_BLOCKED,
                    "provider_access_restricted",
                )
        if protection_trigger_from_text(snapshot.body_text) is not None or any(
            protection_trigger_from_text(text) is not None for text in snapshot.protection_texts
        ):
            return KorailReservationResult(
                KorailReservationOutcome.PROVIDER_BLOCKED,
                "provider_access_restricted",
            )

        path = urlsplit(snapshot.url).path
        authenticated_login_route = False
        if path.rstrip("/") == "/ticket/login":
            authenticated = attempt.post_submit_authenticated
            if not authenticated:
                if not attempt.pre_login_route_check_attempted:
                    attempt.pre_login_route_check_attempted = True
                    attempt.pre_login_route_authenticated = (
                        await self._port._probe_official_authenticated_session()
                    )
                authenticated = attempt.pre_login_route_authenticated
            if not authenticated:
                authenticated = await self._port._has_authenticated_header()
            if not authenticated:
                return KorailReservationResult(
                    KorailReservationOutcome.AUTH_REQUIRED,
                    "authentication_required",
                )
            authenticated_login_route = True
            self._event_logger.info(
                "KORAIL reservation marker stage=terminal_probe login_route_authenticated=true"
            )

        body = " ".join(snapshot.body_text.split())
        target_markers = (
            _has_exact_train_number_marker(body, request.train_number)
            and request.departure_time.strftime("%H:%M") in body
            and request.arrival_time.strftime("%H:%M") in body
            and request.seat_class.label in body
            and any(marker in body for marker in _reservation_date_markers(request.travel_date))
        )
        pending_markers = all(marker in body for marker in ("예약취소", "장바구니", "결제하기"))
        if path.rstrip("/") == "/ticket/reservation/detail" and target_markers and pending_markers:
            reserved_seats = await self.reserved_seats_from_preserved_state(request)
            return KorailReservationResult(
                KorailReservationOutcome.PAYMENT_REQUIRED,
                "reservation_pending_payment",
                reserved_seats=reserved_seats,
            )

        try:
            dialogs = await self._visible_elements(
                "[role='dialog'], dialog[open], [aria-modal='true']"
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a changing official dialog fails closed.
            self._event_logger.info(
                "KORAIL reservation dialog phase=%s kind=unknown control_shape=other "
                "dialog_count=unknown action=none",
                (
                    _dialog_policy.ReservationDialogPhase.POST_REQUEST.value
                    if attempt.reservation_clicked
                    else _dialog_policy.ReservationDialogPhase.PRE_REQUEST.value
                ),
            )
            return KorailReservationResult(
                KorailReservationOutcome.ACTION_REQUIRED,
                "official_dialog_probe_unavailable",
            )
        phase = (
            _dialog_policy.ReservationDialogPhase.POST_REQUEST
            if attempt.reservation_clicked
            else _dialog_policy.ReservationDialogPhase.PRE_REQUEST
        )
        if len(dialogs) > 1:
            ambiguous = _dialog_policy.ReservationDialogDecision(
                _dialog_policy.ReservationDialogKind.UNKNOWN,
                control_shape=(await self._reservation_dialog_decision(dialogs[0], phase=phase))[
                    0
                ].control_shape,
            )
            self._log_reservation_dialog(
                phase=phase,
                decision=ambiguous,
                dialog_count="multiple",
                action="none",
            )
            return KorailReservationResult(
                KorailReservationOutcome.ACTION_REQUIRED,
                "official_dialog_ambiguous",
            )
        if dialogs:
            decision, controls = await self._reservation_dialog_decision(dialogs[0], phase=phase)
            if (
                decision.kind is _dialog_policy.ReservationDialogKind.AUTHENTICATED_LOGIN_SHELL
                and authenticated_login_route
            ):
                self._log_reservation_dialog(
                    phase=phase,
                    decision=decision,
                    dialog_count="one",
                    action="ignored_authenticated_shell",
                )
            elif (
                decision.kind is _dialog_policy.ReservationDialogKind.DELAY_CONSENT
                and decision.action is _dialog_policy.ReservationDialogAction.NONE
            ):
                self._log_reservation_dialog(
                    phase=phase,
                    decision=decision,
                    dialog_count="one",
                    action="none",
                )
                return KorailReservationResult(
                    KorailReservationOutcome.CONSENT_REQUIRED,
                    "delay_consent_required",
                )
            elif decision.kind is _dialog_policy.ReservationDialogKind.EXISTING_RESERVATION_CHOICE:
                self._log_reservation_dialog(
                    phase=phase,
                    decision=decision,
                    dialog_count="one",
                    action="none",
                )
                return KorailReservationResult(
                    KorailReservationOutcome.ACTION_REQUIRED,
                    "existing_reservation_action_required",
                )
            elif decision.action is not _dialog_policy.ReservationDialogAction.NONE:
                is_consent_action = decision.action in {
                    _dialog_policy.ReservationDialogAction.ACCEPT_DELAY_CONSENT,
                    _dialog_policy.ReservationDialogAction.ACCEPT_RESERVATION_INFORMATION,
                }
                matching_controls = [
                    control
                    for control in controls
                    if await self._safe_element_text(control) == decision.target_label
                ]
                if len(matching_controls) != 1:
                    self._log_reservation_dialog(
                        phase=phase,
                        decision=decision,
                        dialog_count="one",
                        action="target_unavailable",
                    )
                    return KorailReservationResult(
                        KorailReservationOutcome.ACTION_REQUIRED,
                        "official_dialog_action_target_unavailable",
                    )
                try:
                    target_state = await self._read_control_state(matching_controls[0])
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 -- unreadable official controls fail closed.
                    target_state = None
                if not _control_state_allows_dialog_action(target_state):
                    self._log_reservation_dialog(
                        phase=phase,
                        decision=decision,
                        dialog_count="one",
                        action="target_unavailable",
                    )
                    return KorailReservationResult(
                        KorailReservationOutcome.ACTION_REQUIRED,
                        "official_dialog_action_target_unavailable",
                    )
                action_key = (phase, decision.kind)
                if action_key in attempt.dialog_actions_attempted:
                    if self._monotonic() < (attempt.dialog_settle_deadlines or {}).get(
                        action_key,
                        0.0,
                    ):
                        return None
                    self._log_reservation_dialog(
                        phase=phase,
                        decision=decision,
                        dialog_count="one",
                        action="already_attempted",
                    )
                    return KorailReservationResult(
                        KorailReservationOutcome.ACTION_REQUIRED,
                        (
                            "delay_consent_persisted"
                            if decision.kind is _dialog_policy.ReservationDialogKind.DELAY_CONSENT
                            else "reservation_information_consent_persisted"
                            if is_consent_action
                            else "official_notice_persisted"
                        ),
                    )
                attempt.dialog_actions_attempted += (action_key,)
                if attempt.dialog_settle_deadlines is None:
                    attempt.dialog_settle_deadlines = {}
                attempt.dialog_settle_deadlines[action_key] = self._monotonic() + min(
                    self._timeout_seconds,
                    0.5,
                )
                self._log_reservation_dialog(
                    phase=phase,
                    decision=decision,
                    dialog_count="one",
                    action=(
                        "consent_accept_attempted" if is_consent_action else "dismiss_attempted"
                    ),
                )
                try:
                    await matching_controls[0].click()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 -- official dialog action result is uncertain.
                    self._log_reservation_dialog(
                        phase=phase,
                        decision=decision,
                        dialog_count="one",
                        action=("consent_accept_failed" if is_consent_action else "dismiss_failed"),
                    )
                    return KorailReservationResult(
                        KorailReservationOutcome.ACTION_REQUIRED,
                        (
                            "delay_consent_accept_result_unknown"
                            if decision.kind is _dialog_policy.ReservationDialogKind.DELAY_CONSENT
                            else "reservation_information_consent_accept_result_unknown"
                            if is_consent_action
                            else "official_notice_dismiss_result_unknown"
                        ),
                    )
                self._log_reservation_dialog(
                    phase=phase,
                    decision=decision,
                    dialog_count="one",
                    action=(
                        "consent_accept_succeeded" if is_consent_action else "dismiss_succeeded"
                    ),
                )
                if phase is _dialog_policy.ReservationDialogPhase.POST_REQUEST:
                    attempt.post_dialog_action_followup_deadline = (
                        self._monotonic() + self._timeout_seconds
                    )
                return None
            else:
                self._log_reservation_dialog(
                    phase=phase,
                    decision=decision,
                    dialog_count="one",
                    action="none",
                )
                return KorailReservationResult(
                    KorailReservationOutcome.ACTION_REQUIRED,
                    "official_action_required",
                )

        if (
            attempt.post_dialog_action_followup_deadline is not None
            and self._monotonic() >= attempt.post_dialog_action_followup_deadline
        ):
            return KorailReservationResult(
                KorailReservationOutcome.ACTION_REQUIRED,
                "official_post_dialog_action_unresolved",
            )
        return None
