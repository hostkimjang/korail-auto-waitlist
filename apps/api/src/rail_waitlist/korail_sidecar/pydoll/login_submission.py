"""Observe one bounded login submission without retaining provider request material."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from ..browser_contracts import BrowserSourceUnavailable

type LoginSubmissionState = Literal[
    "missing", "posted", "in_flight", "completed", "failed", "ambiguous"
]
type LoginSubmissionFailure = Literal[
    "missing", "timeout", "http_error", "network_error", "invalid_response", "ambiguous"
]


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
    """Correlate the normal UI's unique official POST inside a submit window.

    The driver owns callback attachment and must arm immediately before its
    ordinary submit click. This observer never sends or modifies a request.
    Protection owners inspect 429/403 before interpreting a failed snapshot.
    """

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
        self._request_id: str | None = None
        self._snapshot = LoginSubmissionSnapshot("missing")

    def arm(self) -> None:
        if self._active:
            raise RuntimeError("login submission window is already armed")
        self._request_id = None
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
        self._request_id = None

    def snapshot(self) -> LoginSubmissionSnapshot:
        self._expire_if_due()
        return self._snapshot

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
            self._snapshot = LoginSubmissionSnapshot(
                "ambiguous", self._snapshot.status, "ambiguous"
            )
        elif self._request_id is None:
            self._request_id = request_id
            if self._snapshot.state != "ambiguous":
                self._snapshot = LoginSubmissionSnapshot("posted")
        elif request_id != self._request_id:
            self._snapshot = LoginSubmissionSnapshot(
                "ambiguous", self._snapshot.status, "ambiguous"
            )

    def on_response_received(self, event: object) -> None:
        params = self._matching_event(event)
        if params is None or self._snapshot.state != "posted":
            return
        response = params.get("response")
        if (
            not isinstance(response, Mapping)
            or params.get("type") not in ("XHR", "Fetch")
            or not _official_origin(response.get("url"))
        ):
            self._snapshot = LoginSubmissionSnapshot("failed", failure="invalid_response")
            return
        status = _http_status(response.get("status"))
        if status is None:
            self._snapshot = LoginSubmissionSnapshot("failed", failure="invalid_response")
        elif not 200 <= status < 400:
            self._snapshot = LoginSubmissionSnapshot("failed", status, "http_error")
        else:
            self._snapshot = LoginSubmissionSnapshot("in_flight", status)

    def on_loading_finished(self, event: object) -> None:
        if self._matching_event(event) is None:
            return
        if self._snapshot.state == "in_flight":
            self._snapshot = LoginSubmissionSnapshot("completed", self._snapshot.status)
        elif self._snapshot.state == "posted":
            self._snapshot = LoginSubmissionSnapshot("failed", failure="invalid_response")

    def on_loading_failed(self, event: object) -> None:
        if self._matching_event(event) is None:
            return
        if self._snapshot.state in {"posted", "in_flight"}:
            # Browser errorText, blockedReason and request details are deliberately ignored.
            self._snapshot = LoginSubmissionSnapshot(
                "failed", self._snapshot.status, "network_error"
            )

    def _matching_event(self, event: object) -> Mapping[str, object] | None:
        if not self._accepting_events() or self._request_id is None:
            return None
        params = _event_params(event)
        if params is None or _request_id(params) != self._request_id:
            return None
        return params

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
        self._request_id = None
