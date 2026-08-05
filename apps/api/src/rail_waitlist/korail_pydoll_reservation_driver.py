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

from .korail_browser_automation import (
    BrowserSourceUnavailable,
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_text,
)
from .korail_pydoll_auth_contracts import KorailCredentialInput
from .korail_pydoll_contracts import (
    KORAIL_ROUTE_HEADING,
    PydollPageSnapshot,
    normalize_korail_station,
    normalize_korail_train_number,
)
from .korail_pydoll_reservation_contracts import (
    KorailReservationOutcome,
    KorailReservationRequest,
    KorailReservationResult,
)
from .korail_reservation_controls import booking_seat_control_key


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

    async def reserve_once(
        self,
        request: KorailReservationRequest,
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
        ) -> KorailReservationResult:
            return KorailReservationResult(
                outcome=outcome,
                reason=reason,
                seat_clicked=seat_clicked,
                reservation_clicked=reservation_clicked,
                target_rechecked_at=target_rechecked_at,
                seat_selected_at=seat_selected_at,
                reservation_requested_at=reservation_requested_at,
            )

        rows = await self._visible_elements("li.tckList")
        matches = [row for row in rows if await self._port._row_matches_reservation(row, request)]
        if len(matches) != 1:
            target_rechecked_at = self._utc_now()
            return result(KorailReservationOutcome.UNAVAILABLE, "target_not_unique")

        row = matches[0]
        seat_controls = await self._port._actionable_seat_controls(
            row,
            request.seat_class.label,
        )
        target_rechecked_at = self._utc_now()
        if len(seat_controls) > 1:
            return result(KorailReservationOutcome.UNAVAILABLE, "seat_control_not_unique")
        if not seat_controls:
            return result(KorailReservationOutcome.UNAVAILABLE, "seat_not_available")

        seat = seat_controls[0]
        await seat.click()
        seat_selected_at = self._utc_now()
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
                )
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
                    attempt.reservation_clicked = True
                    reservation_requested_at = self._utc_now()
                    try:
                        await candidates[0].click()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 -- click outcome is intentionally uncertain.
                        return result(
                            KorailReservationOutcome.FAILED,
                            "reservation_result_unknown:reservation_click_error",
                            seat_clicked=True,
                            reservation_clicked=True,
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

        dialogs = await self._visible_elements("[role='dialog'], dialog[open], [aria-modal='true']")
        delay_dialogs = []
        for dialog in dialogs:
            text = " ".join(str(await dialog.text).split())
            labels = {
                " ".join(str(await control.text).split())
                for control in await self._visible_elements("button,a", scope=dialog)
            }
            if "지연승낙 안내" in text and {"아니오", "네"}.issubset(labels):
                delay_dialogs.append(dialog)
        if len(delay_dialogs) == 1:
            return KorailReservationResult(
                KorailReservationOutcome.CONSENT_REQUIRED,
                "delay_consent_required",
            )
        if len(delay_dialogs) > 1:
            return KorailReservationResult(
                KorailReservationOutcome.FAILED,
                "delay_consent_ambiguous",
            )
        if dialogs and not authenticated_login_route:
            return KorailReservationResult(
                KorailReservationOutcome.ACTION_REQUIRED,
                "official_action_required",
            )
        if dialogs:
            self._event_logger.info(
                "KORAIL reservation marker stage=terminal_probe "
                "authenticated_login_shell_ignored=true"
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
            return KorailReservationResult(
                KorailReservationOutcome.PAYMENT_REQUIRED,
                "reservation_pending_payment",
            )
        return None
