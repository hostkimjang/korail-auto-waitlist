"""Attest only a completed natural reservation-list read in the current navigation."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from .chromium_lifecycle import finish_owned_cleanup
from .page_contracts import PydollReservationListSnapshot

_LIST_PATH = "/ticket/reservation/list"
_VIEW_PATH = "/classes/com.korail.mobile.reservation.ReservationView"
_BODY_LIMIT = 65_536
# KORAIL message codes are short upper-case identifiers such as P100 or IRG000000. Only
# this closed shape may reach a log line; any other value is projected as unrecognized.
_MESSAGE_CODE_SHAPE = re.compile(r"[A-Z]{1,4}[0-9]{3,9}")
# Informational success codes (the official timetable GET answers IRG000000) may
# accompany a list that carries reservations. They never attest absence; only the
# closed empty-array contracts below can do that.
_INFORMATIONAL_SUCCESS_CODE = re.compile(r"I[A-Z]{2}[0-9]{6}")
_logger = logging.getLogger(__name__)
ReservationListResponseFailure = Literal[
    "observer_unavailable",
    "invalid_navigation",
    "invalid_response",
    "http_error",
    "network_error",
    "body_unavailable",
    "body_too_large",
    "auth_required",
    "timeout",
    "tab_changed",
    "closed",
    "ambiguous",
    "rate_limited",
    "protection",
]
ReservationListDiagnosticBoundary = Literal[
    "observer_rejected",
    "dom_response_conflict",
    "tab_changed",
    "auth_redirect",
    "dom_protection",
    "read_incomplete",
]
ReservationListBodyRejection = Literal[
    "body_shape",
    "payload_not_object",
    "result_not_success",
    "rows_not_list",
    "rows_malformed",
    "message_contract",
]


class ReservationListObservationTab(Protocol):
    @property
    def network_events_enabled(self) -> bool: ...

    async def enable_network_events(self) -> object: ...

    async def disable_network_events(self) -> object: ...

    async def on(self, event: object, callback: Callable[[dict[str, Any]], None]) -> int: ...

    async def remove_callback(self, callback_id: int) -> object: ...

    async def _execute_command(self, command: Any) -> object: ...


@dataclass(frozen=True, slots=True)
class ReservationListResponseSnapshot:
    state: Literal[
        "missing", "in_flight", "body_pending", "empty", "nonempty", "failed", "ambiguous"
    ] = "missing"
    failure: ReservationListResponseFailure | None = None


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _params(event: object) -> Mapping[str, object] | None:
    envelope = _mapping(event)
    return _mapping(envelope.get("params")) if envelope is not None else None


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 256 else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _official_url(value: object, path: str, *, allow_query: bool = False) -> bool:
    if not isinstance(value, str) or len(value) > 16_384:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.korail.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path == path
            and not parsed.fragment
            and (allow_query or not parsed.query)
        )
    except ValueError:
        return False


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous reservation list response")
        result[key] = value
    return result


def _result_projection(value: object) -> str:
    if value is None:
        return "absent"
    return value if isinstance(value, str) and value in {"SUCC", "FAIL"} else "unrecognized"


def _code_projection(value: object) -> str:
    if value is None:
        return "absent"
    if value == "":
        return "empty"
    if isinstance(value, str) and _MESSAGE_CODE_SHAPE.fullmatch(value) is not None:
        return value
    return "unrecognized"


def _text_projection(value: object) -> str:
    if value is None:
        return "absent"
    if value == "":
        return "empty"
    return "present" if isinstance(value, str) else "unrecognized"


def _rows_projection(value: object) -> str:
    if value is None:
        return "absent"
    if isinstance(value, list):
        return f"list:{len(value)}"
    return "object" if isinstance(value, Mapping) else "unrecognized"


def _body_projection(payload: Mapping[str, object] | None) -> tuple[str, str, str, str]:
    """Project result, message code, message presence and row shape; never row content."""

    if payload is None:
        return ("absent", "absent", "absent", "absent")
    journeys = _mapping(payload.get("jrny_infos"))
    return (
        _result_projection(payload.get("strResult")),
        _code_projection(payload.get("h_msg_cd")),
        _text_projection(payload.get("h_msg_txt")),
        _rows_projection(journeys.get("jrny_info") if journeys is not None else None),
    )


class ReservationListResponseObserver:
    """Keep transient CDP correlation identifiers, never request or response material."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("reservation list timeout must be finite and positive")
        self._timeout = timeout_seconds
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._deadline: float | None = None
        self._armed_wall_time: float | None = None
        self._frame: str | None = None
        self._initial_loader: str | None = None
        self._loader: str | None = None
        self._navigation_timestamp: float | None = None
        self._request_timestamp: float | None = None
        self._response_timestamp: float | None = None
        self._request: str | None = None
        self._request_observed = False
        self._document_request: str | None = None
        self._reading_body = False
        self._response_received = False
        self._response_completed = False
        self._closed = False
        self._snapshot = ReservationListResponseSnapshot()

    def arm(self, frame_tree_response: object) -> None:
        result = _mapping(frame_tree_response)
        result = _mapping(result.get("result")) if result is not None else None
        tree = _mapping(result.get("frameTree")) if result is not None else None
        frame = _mapping(tree.get("frame")) if tree is not None else None
        if frame is None or frame.get("parentId") is not None:
            self.fail("observer_unavailable")
            return
        self._frame = _identifier(frame.get("id"))
        self._initial_loader = _identifier(frame.get("loaderId"))
        if self._frame is None or self._initial_loader is None:
            self.fail("observer_unavailable")
            return
        self._deadline = self._monotonic() + self._timeout
        self._armed_wall_time = self._wall_time()

    def fail(self, reason: ReservationListResponseFailure) -> None:
        self._snapshot = ReservationListResponseSnapshot("failed", reason)

    def _reject_body(
        self,
        reason: ReservationListBodyRejection,
        payload: Mapping[str, object] | None,
    ) -> None:
        """Record which closed body contract failed, then fail without retaining the body.

        The read-level warning diagnostic stays the single failure record. This detail line
        only names the broken contract and closed projections so a new official response
        shape can be identified from logs without any reservation or account material.
        """

        result, code, text, rows = _body_projection(payload)
        _logger.info(
            "KORAIL reservation list response rejected "
            "event=reservation_list_response_rejected reason=%s result=%s msg_code=%s "
            "msg_text=%s rows=%s",
            reason,
            result,
            code,
            text,
            rows,
        )
        self.fail("invalid_response")

    def snapshot(self) -> ReservationListResponseSnapshot:
        if self._closed:
            return ReservationListResponseSnapshot("failed", "closed")
        if (
            self._deadline is not None
            and self._monotonic() >= self._deadline
            and self._snapshot.state not in {"empty", "nonempty", "failed", "ambiguous"}
        ):
            self.fail("timeout")
        return self._snapshot

    def _accepting(self) -> bool:
        return (
            self._deadline is not None
            and not self._closed
            and self.snapshot().state not in {"failed", "ambiguous"}
        )

    @property
    def request_observed(self) -> bool:
        return self._request_observed

    def log_diagnostic(
        self,
        boundary: ReservationListDiagnosticBoundary,
        dom: PydollReservationListSnapshot | None = None,
    ) -> None:
        """Project only code-owned states and booleans, never provider material.

        response_received means headers passed the existing response checks;
        response_completed means loadingFinished passed the existing completion checks.
        Neither flag attests a successful JSON body or official reservation absence.
        """

        current = self.snapshot()
        _logger.warning(
            "KORAIL reservation list read diagnostic event=reservation_list_read_diagnostic "
            "boundary=%s state=%s failure=%s request_observed=%s "
            "response_received=%s response_completed=%s dom_observed=%s "
            "dom_render_complete=%s dom_empty=%s dom_rows_present=%s "
            "dom_loading=%s dom_malformed=%s",
            boundary,
            current.state,
            current.failure or "none",
            self.request_observed,
            self._response_received,
            self._response_completed,
            dom is not None,
            dom.render_complete if dom is not None else False,
            dom.explicit_empty_visible if dom is not None else False,
            bool(dom.rendered_card_count or dom.reservation_rows) if dom is not None else False,
            dom.loading_visible if dom is not None else False,
            bool(dom.malformed_card_count) if dom is not None else False,
        )

    def assert_read_allowed(self, dom: PydollReservationListSnapshot | None = None) -> None:
        current = self.snapshot()
        if current.failure == "rate_limited":
            self.log_diagnostic("observer_rejected", dom)
            raise BrowserRateLimited()
        if current.failure == "protection":
            self.log_diagnostic("observer_rejected", dom)
            raise BrowserProtectionDetected(
                "http_403_subresource", "confirmation_reservation_list_response"
            )
        if self.request_observed and current.state in {"failed", "ambiguous"}:
            self.log_diagnostic("observer_rejected", dom)
            raise BrowserSourceUnavailable("confirmation_reservation_list_response")

    def on_request(self, event: object) -> None:
        if not self._accepting():
            return
        params = _params(event)
        request = _mapping(params.get("request")) if params is not None else None
        if params is None or request is None or params.get("frameId") != self._frame:
            return
        wall = _number(params.get("wallTime"))
        timestamp = _number(params.get("timestamp"))
        if (
            wall is None
            or self._armed_wall_time is None
            or wall < self._armed_wall_time
            or timestamp is None
        ):
            return
        loader = _identifier(params.get("loaderId"))
        request_id = _identifier(params.get("requestId"))
        if params.get("type") == "Document":
            if request_id == self._document_request:
                if params.get("redirectResponse") is not None:
                    self.fail("invalid_navigation")
                return
            if self._loader is not None:
                self.fail("invalid_navigation")
                return
            if (
                loader is None
                or loader == self._initial_loader
                or request_id is None
                or request.get("method") != "GET"
                or not _official_url(request.get("url"), _LIST_PATH)
                or not _official_url(params.get("documentURL"), _LIST_PATH)
                or params.get("redirectResponse") is not None
            ):
                self.fail("invalid_navigation")
                return
            self._loader = loader
            self._document_request = request_id
            self._navigation_timestamp = timestamp
            return
        if (
            self._loader is None
            or loader != self._loader
            or params.get("type") not in {"XHR", "Fetch"}
            or request.get("method") != "GET"
            or not _official_url(request.get("url"), _VIEW_PATH, allow_query=True)
            or not _official_url(params.get("documentURL"), _LIST_PATH)
            or self._navigation_timestamp is None
            or timestamp < self._navigation_timestamp
        ):
            return
        self._request_observed = True
        if request_id is None or params.get("redirectResponse") is not None:
            self.fail("invalid_response")
        elif self._request is None:
            self._request = request_id
            self._request_timestamp = timestamp
            self._snapshot = ReservationListResponseSnapshot("in_flight")
        elif request_id != self._request:
            self._snapshot = ReservationListResponseSnapshot("ambiguous", "ambiguous")

    def _matching(self, event: object) -> Mapping[str, object] | None:
        if not self._accepting() or self._request is None:
            return None
        params = _params(event)
        if params is None or params.get("requestId") != self._request:
            return None
        timestamp = _number(params.get("timestamp"))
        if (
            timestamp is None
            or self._request_timestamp is None
            or timestamp < self._request_timestamp
        ):
            self.fail("invalid_response")
            return None
        return params

    def on_response(self, event: object) -> None:
        params = self._matching(event)
        if params is None or self._snapshot.state != "in_flight":
            return
        if self._response_received:
            self.fail("invalid_response")
            return
        response = _mapping(params.get("response"))
        if (
            response is None
            or params.get("frameId") != self._frame
            or params.get("loaderId") != self._loader
            or params.get("type") not in {"XHR", "Fetch"}
            or not _official_url(response.get("url"), _VIEW_PATH, allow_query=True)
            or any(
                response.get(flag) is True
                for flag in ("fromDiskCache", "fromServiceWorker", "fromPrefetchCache")
            )
        ):
            self.fail("invalid_response")
            return
        status = _number(response.get("status"))
        if status == 429:
            self.fail("rate_limited")
        elif status == 403:
            self.fail("protection")
        elif status != 200:
            self.fail("http_error")
        else:
            mime = response.get("mimeType")
            if not isinstance(mime, str) or mime.strip().lower() not in {
                "application/json",
                "text/html",
            }:
                self.fail("invalid_response")
            else:
                # Headers alone are not proof. loadingFinished owns the next transition.
                self._snapshot = ReservationListResponseSnapshot("in_flight")
                self._reading_body = False
                self._response_received = True
                self._response_timestamp = _number(params.get("timestamp"))

    def on_finished(self, event: object) -> None:
        params = self._matching(event)
        if params is None or self._snapshot.state != "in_flight":
            return
        size = _number(params.get("encodedDataLength"))
        if size is None or size > _BODY_LIMIT:
            self.fail("body_too_large")
        elif not self._response_received or self._response_timestamp is None:
            self.fail("invalid_response")
        else:
            # A responseReceived callback already processed here can carry a timestamp
            # later than the actual transfer completion. Require validated headers first,
            # not finish >= responseReceived time. _matching still fences completion to
            # this request id and a finite timestamp at or after its own request start.
            self._response_completed = True
            self._snapshot = ReservationListResponseSnapshot("body_pending")

    def on_failed(self, event: object) -> None:
        if self._matching(event) is not None:
            self.fail("network_error")

    async def read_completed_body(self, tab: ReservationListObservationTab) -> None:
        if (
            not self._accepting()
            or self._snapshot.state != "body_pending"
            or self._request is None
            or self._reading_body
        ):
            return
        self._reading_body = True
        try:
            response = await tab._execute_command(
                {"method": "Network.getResponseBody", "params": {"requestId": self._request}}
            )
            if not self._accepting() or self._snapshot.state != "body_pending":
                return
            result = _mapping(response)
            result = _mapping(result.get("result")) if result is not None else None
            raw = result.get("body") if result is not None else None
            encoded = result.get("base64Encoded") if result is not None else None
            if not isinstance(raw, str) or not isinstance(encoded, bool):
                self._reject_body("body_shape", None)
                return
            if len(raw) > _BODY_LIMIT * 4 // 3 + 4:
                self.fail("body_too_large")
                return
            body = base64.b64decode(raw, validate=True) if encoded else raw.encode("utf-8")
            if len(body) > _BODY_LIMIT:
                self.fail("body_too_large")
                return
            payload = _mapping(json.loads(body, object_pairs_hook=_unique_object))
            if payload is None:
                self._reject_body("payload_not_object", None)
                return
            if payload.get("h_msg_cd") == "P058":
                self.fail("auth_required")
                return
            code = payload.get("h_msg_cd")
            if payload.get("strResult") != "SUCC":
                self._reject_body("result_not_success", payload)
                return
            journeys = _mapping(payload.get("jrny_infos"))
            rows = journeys.get("jrny_info") if journeys is not None else None
            if not isinstance(rows, list):
                self._reject_body("rows_not_list", payload)
                return
            message = payload.get("h_msg_txt")
            if rows:
                if not all(isinstance(row, Mapping) for row in rows):
                    self._reject_body("rows_malformed", payload)
                    return
                # Rows are affirmative data, so a plain notice or an informational success
                # code may accompany them. The reservation itself is still matched only
                # from the rendered official cards, and this state never attests absence.
                informational = isinstance(code, str) and (
                    code == "" or _INFORMATIONAL_SUCCESS_CODE.fullmatch(code) is not None
                )
                if not (code is None or informational) or not (
                    message is None or isinstance(message, str)
                ):
                    self._reject_body("message_contract", payload)
                    return
                if code not in (None, "") or message not in (None, ""):
                    _, code_projection, text_projection, rows_projection = _body_projection(payload)
                    _logger.info(
                        "KORAIL reservation list response accepted "
                        "event=reservation_list_response_accepted state=nonempty "
                        "msg_code=%s msg_text=%s rows=%s",
                        code_projection,
                        text_projection,
                        rows_projection,
                    )
                self._snapshot = ReservationListResponseSnapshot("nonempty")
                return
            ordinary_success = (code is None or code == "") and (message is None or message == "")
            explicit_empty = code == "P100" and message == "검색된 데이터가 없습니다."
            if not ordinary_success and not explicit_empty:
                self._reject_body("message_contract", payload)
                return
            self._snapshot = ReservationListResponseSnapshot("empty")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail("body_unavailable")
        finally:
            self._reading_body = False

    def close(self) -> None:
        self._closed = True
        self._frame = self._initial_loader = self._loader = self._request = None
        self._document_request = None
        self._request_observed = False
        self._navigation_timestamp = self._armed_wall_time = self._deadline = None
        self._request_timestamp = self._response_timestamp = None


def _events() -> tuple[object, object, object, object]:
    from pydoll.protocol.network.events import NetworkEvent

    return (
        NetworkEvent.REQUEST_WILL_BE_SENT,
        NetworkEvent.RESPONSE_RECEIVED,
        NetworkEvent.LOADING_FINISHED,
        NetworkEvent.LOADING_FAILED,
    )


@asynccontextmanager
async def observe_reservation_list_response(
    tab: ReservationListObservationTab,
    timeout_seconds: float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
) -> AsyncIterator[ReservationListResponseObserver]:
    owner = ReservationListResponseObserver(
        timeout_seconds, monotonic=monotonic, wall_time=wall_time
    )
    callback_ids: list[int] = []
    network_owned = False
    try:
        try:
            events = _events()
            if not tab.network_events_enabled:
                network_owned = True
                await tab.enable_network_events()
            for event, callback in zip(
                events,
                (owner.on_request, owner.on_response, owner.on_finished, owner.on_failed),
                strict=True,
            ):
                callback_ids.append(await tab.on(event, callback))
            owner.arm(await tab._execute_command({"method": "Page.getFrameTree"}))
        except Exception:
            # Existing explicit DOM evidence remains usable; missing observation
            # can never manufacture the new response-empty provenance.
            owner.fail("observer_unavailable")
        yield owner
    finally:
        owner.close()
        await finish_owned_cleanup(_remove_callbacks(tab, callback_ids, network_owned))


async def _remove_callbacks(
    tab: ReservationListObservationTab, callback_ids: list[int], network_owned: bool
) -> None:
    for callback_id in callback_ids:
        try:
            await tab.remove_callback(callback_id)
        except Exception:
            _logger.warning("KORAIL reservation list observation cleanup failed")
    if network_owned:
        try:
            await tab.disable_network_events()
        except Exception:
            _logger.warning("KORAIL reservation list observation network cleanup failed")


async def read_reservation_list(
    *,
    tab: ReservationListObservationTab,
    current_tab: Callable[[], object],
    navigate: Callable[..., Awaitable[object]],
    snapshot: Callable[[], Awaitable[PydollReservationListSnapshot]],
    timeout_seconds: float,
    navigation_timeout_seconds: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> PydollReservationListSnapshot:
    navigation_timeout = (
        timeout_seconds if navigation_timeout_seconds is None else navigation_timeout_seconds
    )
    if not math.isfinite(navigation_timeout) or navigation_timeout <= 0:
        raise ValueError("reservation list navigation timeout must be finite and positive")
    async with observe_reservation_list_response(
        tab, navigation_timeout + timeout_seconds, monotonic=monotonic, wall_time=wall_time
    ) as owner:
        await navigate(
            "https://www.korail.com/ticket/reservation/list",
            timeout=max(1, int(navigation_timeout)),
        )
        deadline = monotonic() + timeout_seconds
        last = await snapshot()
        stable: PydollReservationListSnapshot | None = None
        while monotonic() < deadline:
            if current_tab() is not tab:
                owner.fail("tab_changed")
                owner.log_diagnostic("tab_changed", last)
                return last
            path = urlsplit(last.url).path.rstrip("/")
            if path == "/ticket/login" or last.protection_detected:
                owner.log_diagnostic(
                    "auth_redirect" if path == "/ticket/login" else "dom_protection", last
                )
                return last
            await owner.read_completed_body(tab)
            if current_tab() is not tab:
                owner.fail("tab_changed")
                owner.log_diagnostic("tab_changed", last)
                return last
            owner.assert_read_allowed(last)
            response_state = owner.snapshot().state
            if (
                owner.request_observed
                and last.explicit_empty_visible
                and response_state == "nonempty"
            ):
                owner.log_diagnostic("dom_response_conflict", last)
                raise BrowserSourceUnavailable("confirmation_reservation_list_response")
            if (
                response_state == "empty"
                and _official_url(last.url, _LIST_PATH)
                and last.page_marker_visible
                and not last.loading_visible
                and last.rendered_card_count == 0
                and last.malformed_card_count == 0
                and not last.reservation_rows
            ):
                last = last.with_official_empty_response()
            explicit_empty_waiting = (
                last.explicit_empty_visible and owner.request_observed and response_state != "empty"
            )
            if (
                _official_url(last.url, _LIST_PATH)
                and last.render_complete
                and not explicit_empty_waiting
            ):
                if stable == last:
                    return last.with_stable_observation()
                stable = last
            else:
                stable = None
            await sleep(0.2)
            last = await snapshot()
        owner.log_diagnostic("read_incomplete", last)
        return last
