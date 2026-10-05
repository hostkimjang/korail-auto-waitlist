"""Observe one bounded login submission without retaining provider request material."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import urlsplit

from ..browser_contracts import BrowserSourceUnavailable

type LoginSubmissionState = Literal[
    "missing", "posted", "in_flight", "completed", "failed", "ambiguous"
]
type LoginSubmissionFailure = Literal[
    "missing", "timeout", "http_error", "network_error", "invalid_response", "ambiguous"
]
type LoginRequestPathFamily = Literal[
    "public_login", "integration_check", "business_dynamic", "official_other"
]
type LoginRequestTerminal = Literal["completed", "failed", "incomplete"]
type LoginPublicCallsite = Literal["login_submit", "integration_check", "unresolved", "mixed"]

# Public bundle SHA-256: b7a06c687d15747f2a13ef11eadacd7a8188d361a4d28261282891d0a7d8f319.
# These zero-based UTF-16 spans identify calls in that exact bundle only. They are
# diagnostic evidence, never authentication policy or a meaning inferred from /web_s/.
_PUBLIC_CALLSITE_BUNDLE_PATH = "/bundle/bundle.38e6dfeb5a3af0094a69.js"


@dataclass(frozen=True, slots=True)
class LoginPublicFrameDiagnostic:
    """Coordinates in a verified public source; never retain the source URL or query."""

    source_id: Literal["verified_main_bundle"]
    source_query_present: bool
    line_0based: int
    utf16_column_0based: int


@dataclass(frozen=True, slots=True)
class LoginRequestDiagnostic:
    """Closed observations; neither a path family nor an initiator proves business role."""

    sequence: int
    path_family: LoginRequestPathFamily
    initiator_handle_login: bool
    initiator_complete: bool
    public_callsite: LoginPublicCallsite = "unresolved"
    initiator_public_frames: tuple[LoginPublicFrameDiagnostic, ...] = ()
    status: int | None = None
    terminal: LoginRequestTerminal = "incomplete"
    evidence_complete: bool = True


def _path_family(url: str) -> LoginRequestPathFamily:
    # The accepted origin was checked by the caller. Discard the parsed path immediately.
    path = urlsplit(url).path
    if path == "/ebizweb/common/loginProcess":
        return "public_login"
    if path == "/ebizweb/integrate/srCheck.do":
        return "integration_check"
    if path.startswith("/web_s/"):
        return "business_dynamic"
    return "official_other"


def _public_frame_diagnostic(
    frame: Mapping[str, object],
) -> tuple[LoginPublicFrameDiagnostic | None, bool]:
    url = frame.get("url")
    if not isinstance(url, str) or len(url) > 16384 or any(ord(char) < 33 for char in url):
        return None, False
    try:
        parsed = urlsplit(url)
        verified = (
            parsed.scheme == "https"
            and parsed.hostname == "cdn.korail.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and "#" not in url
            and parsed.path == _PUBLIC_CALLSITE_BUNDLE_PATH
        )
    except ValueError:
        return None, False
    if not verified:
        return None, True
    line, column = frame.get("lineNumber"), frame.get("columnNumber")
    if not (
        type(line) is int and 0 <= line < 10000 and type(column) is int and 0 <= column <= 6000000
    ):
        return None, False
    return LoginPublicFrameDiagnostic("verified_main_bundle", "?" in url, line, column), True


def _initiator_diagnostics(
    value: object,
) -> tuple[bool, bool, LoginPublicCallsite, tuple[LoginPublicFrameDiagnostic, ...]]:
    """Inspect only bounded CDP stack structure and retain no frame or function text."""

    if not isinstance(value, Mapping):
        return False, False, "unresolved", ()
    stack = value.get("stack")
    if stack is None:
        return False, False, "unresolved", ()
    found = False
    login_call = False
    integration_call = False
    seen: set[int] = set()
    frame_count = 0
    public_frames: list[LoginPublicFrameDiagnostic] = []
    for _ in range(8):
        if not isinstance(stack, Mapping) or id(stack) in seen:
            return found, False, "unresolved", tuple(public_frames)
        seen.add(id(stack))
        frames = stack.get("callFrames")
        if not isinstance(frames, list) or len(frames) > 64 - frame_count:
            return found, False, "unresolved", tuple(public_frames)
        for frame in frames:
            if not isinstance(frame, Mapping):
                return found, False, "unresolved", tuple(public_frames)
            name = frame.get("functionName")
            if not isinstance(name, str) or len(name) > 256:
                return found, False, "unresolved", tuple(public_frames)
            found = found or name == "handleLogin"
            public_frame, complete = _public_frame_diagnostic(frame)
            if not complete:
                return found, False, "unresolved", tuple(public_frames)
            if public_frame is not None:
                if len(public_frames) >= 8:
                    return found, False, "unresolved", tuple(public_frames)
                public_frames.append(public_frame)
                if public_frame.line_0based == 2:
                    column = public_frame.utf16_column_0based
                    login_call = login_call or 30620 <= column < 30652
                    integration_call = integration_call or 24951 <= column < 24993
        frame_count += len(frames)
        if stack.get("parentId") is not None:
            return found, False, "unresolved", tuple(public_frames)
        parent = stack.get("parent")
        if parent is None:
            callsite: LoginPublicCallsite = (
                "mixed"
                if login_call and integration_call
                else "login_submit"
                if login_call
                else "integration_check"
                if integration_call
                else "unresolved"
            )
            return found, True, callsite, tuple(public_frames)
        stack = parent
    return found, False, "unresolved", tuple(public_frames)


class PydollLoginResponseUnavailable(BrowserSourceUnavailable):
    """An observed provider submission failed; do not retry it as a local startup error."""

    failure_kind: Literal["provider_submission_failed"] = "provider_submission_failed"
    retry_after_seconds: int = 300

    def __init__(self, submission: LoginSubmissionSnapshot | None = None) -> None:
        super().__init__("login_response")
        # Retain only the observer's closed diagnostics, never its request material.
        self.submission_state = submission.state if submission is not None else None
        self.submission_status = submission.status if submission is not None else None
        self.submission_failure = submission.failure if submission is not None else None


@dataclass(frozen=True, slots=True)
class LoginSubmissionSnapshot:
    state: LoginSubmissionState
    status: int | None = None
    failure: LoginSubmissionFailure | None = None

    @property
    def safe_to_probe(self) -> bool:
        """A received header alone cannot attest the submission has finished."""
        return (
            self.state == "completed"
            and self.status is not None
            and 200 <= self.status < 400
            and self.failure is None
        )


def _event_params(event: object) -> Mapping[str, object] | None:
    if not isinstance(event, Mapping):
        return None
    params = event.get("params")
    return params if isinstance(params, Mapping) else None


def _official_origin(url: object) -> bool:
    # Parse only on the callback stack: dynamic paths, query strings and request
    # bodies must never enter owner state, snapshots, logs or exception messages.
    if not isinstance(url, str) or not url or len(url) > 16384:
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "www.korail.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except ValueError:
        return False


def _request_id(params: Mapping[str, object]) -> str | None:
    value = params.get("requestId")
    return value if isinstance(value, str) and 0 < len(value) <= 256 else None


def _http_status(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not 100 <= value <= 599
        or not math.isfinite(value)
        or value != int(value)
    ):
        return None
    return int(value)


class PydollLoginSubmission:
    """Correlate bounded official POSTs from one normal UI submit window.

    The driver owns callback attachment and must arm immediately before its
    ordinary submit click. This observer never sends or modifies a request.
    Protection owners inspect 429/403 before interpreting a failed snapshot.
    """

    MAX_REQUESTS = 8

    def __init__(
        self,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("login submission timeout must be finite and positive")
        self._timeout_seconds = timeout_seconds
        self._monotonic = monotonic
        self._deadline: float | None = None
        self._active = False
        self._requests: dict[str, LoginSubmissionSnapshot] = {}
        self._diagnostics: list[LoginRequestDiagnostic] = []
        self._diagnostic_evidence_complete = True
        self._transport_failed_sequences: set[int] = set()
        self._group_revision = 0
        self._snapshot = LoginSubmissionSnapshot("missing")

    @property
    def group_revision(self) -> int:
        """Internal membership token; never include it in snapshots or diagnostics."""
        return self._group_revision

    def arm(self) -> None:
        if self._active:
            raise RuntimeError("login submission window is already armed")
        self._requests.clear()
        self._diagnostics.clear()
        self._transport_failed_sequences.clear()
        self._diagnostic_evidence_complete = True
        self._snapshot = LoginSubmissionSnapshot("missing")
        self._deadline = self._monotonic() + self._timeout_seconds
        self._active = True

    def close(self) -> None:
        self._expire_if_due()
        if self._snapshot.state in {"missing", "posted", "in_flight"}:
            failure: LoginSubmissionFailure = (
                "missing" if self._snapshot.state == "missing" else "timeout"
            )
            self._snapshot = LoginSubmissionSnapshot("failed", self._snapshot.status, failure)
        self._active = False
        self._requests.clear()
        self._diagnostics.clear()
        self._transport_failed_sequences.clear()

    def snapshot(self) -> LoginSubmissionSnapshot:
        self._expire_if_due()
        return self._snapshot

    def diagnostics(self) -> tuple[LoginRequestDiagnostic, ...]:
        """Return only bounded, serializable facts before the submission owner closes."""
        self._expire_if_due()
        return tuple(self._diagnostics)

    def _diagnostic_index(self, params: Mapping[str, object]) -> int:
        # Reuse the existing correlation map rather than retaining a second copy of IDs.
        request_id = _request_id(params)
        assert request_id is not None
        return tuple(self._requests).index(request_id)

    def _mark_diagnostics_incomplete(self) -> None:
        self._diagnostic_evidence_complete = False
        self._diagnostics = [replace(row, evidence_complete=False) for row in self._diagnostics]

    def on_request_will_be_sent(self, event: object) -> None:
        if not self._accepting_events():
            return
        params = _event_params(event)
        if params is None or params.get("type") not in ("XHR", "Fetch"):
            return
        request = params.get("request")
        if (
            not isinstance(request, Mapping)
            or request.get("method") != "POST"
            or not _official_origin(request.get("url"))
        ):
            return
        request_id = _request_id(params)
        if request_id is None:
            self._mark_diagnostics_incomplete()
            self._fail("ambiguous")
        elif request_id not in self._requests:
            if len(self._requests) >= self.MAX_REQUESTS:
                self._mark_diagnostics_incomplete()
                self._fail("ambiguous")
                return
            url = request.get("url")
            assert isinstance(url, str)
            initiator_found, initiator_complete, callsite, public_frames = _initiator_diagnostics(
                params.get("initiator")
            )
            self._diagnostics.append(
                LoginRequestDiagnostic(
                    sequence=len(self._diagnostics) + 1,
                    path_family=_path_family(url),
                    initiator_handle_login=initiator_found,
                    initiator_complete=initiator_complete,
                    public_callsite=callsite,
                    initiator_public_frames=public_frames,
                    evidence_complete=self._diagnostic_evidence_complete
                    and params.get("redirectResponse") is None,
                )
            )
            self._requests[request_id] = LoginSubmissionSnapshot("posted")
            self._group_revision += 1
            self._refresh()
        elif params.get("redirectResponse") is not None:
            index = self._diagnostic_index(params)
            self._diagnostics[index] = replace(self._diagnostics[index], evidence_complete=False)

    def on_response_received(self, event: object) -> None:
        params = self._matching_event(event)
        if params is None:
            return
        index = self._diagnostic_index(params)
        response = params.get("response")
        if (
            not isinstance(response, Mapping)
            or params.get("type") not in ("XHR", "Fetch")
            or not _official_origin(response.get("url"))
        ):
            self._diagnostics[index] = replace(
                self._diagnostics[index], terminal="incomplete", evidence_complete=False
            )
            self._fail("invalid_response")
            return
        status = _http_status(response.get("status"))
        if status is None:
            self._diagnostics[index] = replace(
                self._diagnostics[index], terminal="incomplete", evidence_complete=False
            )
            self._fail("invalid_response")
            return
        self._diagnostics[index] = replace(
            self._diagnostics[index],
            status=status,
            evidence_complete=self._diagnostics[index].evidence_complete
            and not 300 <= status < 400,
        )
        if not 200 <= status < 400:
            self._fail("http_error", status)
        else:
            request_id = _request_id(params)
            assert request_id is not None
            if self._requests[request_id].state == "posted":
                self._requests[request_id] = LoginSubmissionSnapshot("in_flight", status)
                self._refresh()

    def on_loading_finished(self, event: object) -> None:
        params = self._matching_event(event)
        if params is None:
            return
        request_id = _request_id(params)
        assert request_id is not None
        index = self._diagnostic_index(params)
        diagnostic = self._diagnostics[index]
        if (
            diagnostic.status is not None
            and diagnostic.sequence not in self._transport_failed_sequences
        ):
            self._diagnostics[index] = replace(diagnostic, terminal="completed")
        elif diagnostic.status is None:
            self._diagnostics[index] = replace(diagnostic, evidence_complete=False)
        request = self._requests[request_id]
        if request.state == "in_flight":
            self._requests[request_id] = LoginSubmissionSnapshot("completed", request.status)
            self._refresh()
        elif request.state == "posted":
            self._fail("invalid_response")

    def on_loading_failed(self, event: object) -> None:
        params = self._matching_event(event)
        if params is None:
            return
        index = self._diagnostic_index(params)
        self._transport_failed_sequences.add(self._diagnostics[index].sequence)
        self._diagnostics[index] = replace(self._diagnostics[index], terminal="failed")
        # Browser errorText, blockedReason and request details are deliberately ignored.
        self._fail("network_error")

    def _matching_event(self, event: object) -> Mapping[str, object] | None:
        if not self._accepting_events() or not self._requests:
            return None
        params = _event_params(event)
        if params is None:
            return None
        request_id = _request_id(params)
        if request_id is None:
            self._mark_diagnostics_incomplete()
            self._fail("invalid_response")
            return None
        if request_id not in self._requests:
            return None
        return params

    def _fail(self, failure: LoginSubmissionFailure, status: int | None = None) -> None:
        # Failures remain sticky. An explicit protection response still takes priority
        # over an earlier generic failure so the driver's 403/429 owners can stop it.
        if self._snapshot.state in {"failed", "ambiguous"} and (
            status not in {403, 429} or self._snapshot.status in {403, 429}
        ):
            return
        state: LoginSubmissionState = "ambiguous" if failure == "ambiguous" else "failed"
        self._snapshot = LoginSubmissionSnapshot(
            state, status if status is not None else self._snapshot.status, failure
        )

    def _refresh(self) -> None:
        if self._snapshot.state in {"failed", "ambiguous"}:
            return
        requests = tuple(self._requests.values())
        statuses = [request.status for request in requests if request.status is not None]
        status = max(statuses) if statuses else None
        if all(request.state == "completed" for request in requests):
            self._snapshot = LoginSubmissionSnapshot("completed", status)
        elif statuses:
            self._snapshot = LoginSubmissionSnapshot("in_flight", status)
        else:
            self._snapshot = LoginSubmissionSnapshot("posted")

    def _accepting_events(self) -> bool:
        self._expire_if_due()
        return self._active

    def _expire_if_due(self) -> None:
        if not self._active or self._deadline is None or self._monotonic() < self._deadline:
            return
        if self._snapshot.state in {"missing", "posted", "in_flight"}:
            failure: LoginSubmissionFailure = (
                "missing" if self._snapshot.state == "missing" else "timeout"
            )
            self._snapshot = LoginSubmissionSnapshot("failed", self._snapshot.status, failure)
        self._active = False
        self._requests.clear()
