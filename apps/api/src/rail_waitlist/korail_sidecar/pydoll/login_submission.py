"""Observe one bounded login submission without retaining provider request material."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from ..browser_contracts import BrowserSourceUnavailable

type LoginSubmissionState = Literal[
    "missing", "posted", "in_flight", "completed", "failed", "ambiguous"
]
type LoginSubmissionFailure = Literal[
    "missing", "timeout", "http_error", "network_error", "invalid_response", "ambiguous"
]
type LoginRequestPathFamily = Literal[
    "public_key", "public_login", "integration_check", "business_dynamic", "official_other"
]
type LoginRequestRole = Literal[
    "login_bootstrap", "login_submission", "integration_check", "conflicting", "unresolved"
]
type LoginRequestPayloadRole = Literal["login_submission", "unresolved"]
type LoginRequestPayloadShape = Literal[
    "absent",
    "invalid",
    "unrelated",
    "partial_login",
    "login_with_additions",
    "exact_login",
]
type LoginAuthenticationVerificationMode = Literal["blocked", "pending", "standard", "independent"]
type LoginRequestTerminal = Literal["completed", "failed", "incomplete"]
type LoginPublicCallsite = Literal[
    "login_bootstrap", "login_submit", "integration_check", "unresolved", "mixed"
]
type LoginInitiatorKind = Literal[
    "script", "parser", "preload", "SignedExchange", "preflight", "other", "unknown"
]
type LoginInitiatorSourceScope = Literal[
    "verified_main_bundle", "korail_origin", "official_cdn", "third_party", "empty_or_invalid"
]
type LoginInitiatorCompletionReason = Literal[
    "complete",
    "missing_stack",
    "invalid_stack",
    "invalid_frame",
    "frame_limit",
    "parent_id",
    "depth_limit",
]
type LoginResponseMedia = Literal["json", "html", "text", "other", "unknown"]
type LoginResponseAuthOutcome = Literal[
    "not_observed",
    "accepted",
    "rejected",
    "challenge",
    "protection",
    "not_login_result",
    "unavailable",
]
type LoginExecuteCommand = Callable[[dict[str, object]], Awaitable[object]]

# Public bundle SHA-256: 8a9519dd552db96a7f30bf9f1ceb0b8eae7da0b5c17702b07e1e1650bebdc72b.
# These zero-based UTF-16 spans identify calls in that exact bundle only. They are
# diagnostic evidence, never authentication policy or a meaning inferred from /web_s/.
_PUBLIC_CALLSITE_BUNDLE_PATH = "/bundle/bundle.fcfd32491c1f6e0b2541.js"
_PUBLIC_KEY_PATH = "/ebizweb/tour/mypage/pwd_action/pblk"
_BODY_LIMIT = 65_536
_JSON_DEPTH_LIMIT = 64
_LOGIN_PAYLOAD_BASE_FIELDS = frozenset(
    {
        "UserId",
        "selInputFlg",
        "hidMemberFlg",
        "txtDv",
        "encUserId",
        "keyname",
        "encUserPwd",
        "deviceId",
    }
)
_LOGIN_PAYLOAD_FIELD_SETS = frozenset(
    {
        _LOGIN_PAYLOAD_BASE_FIELDS | {"useKeySec"},
        _LOGIN_PAYLOAD_BASE_FIELDS | {"useKeySecFlg"},
    }
)
_LOGIN_PAYLOAD_KNOWN_FIELDS = _LOGIN_PAYLOAD_BASE_FIELDS | {"useKeySec", "useKeySecFlg"}
# KORAIL's own error code (for example ``macro_err1``) is a short closed token. Only the
# top-level ``errCode`` value is retained; messages, identifiers and every other value stay
# private. A present value of any other shape is recorded as a fixed placeholder.
_LOGIN_ERR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,32}")
LOGIN_ERR_CODE_UNRECOGNIZED = "<unrecognized>"


def sanitized_login_err_code(value: object) -> str:
    """Return a code-shaped provider error code or a fixed placeholder, never free text."""

    if isinstance(value, str) and _LOGIN_ERR_CODE_PATTERN.fullmatch(value):
        return value
    return LOGIN_ERR_CODE_UNRECOGNIZED


@dataclass(frozen=True, slots=True)
class LoginPublicFrameDiagnostic:
    """Coordinates in a verified public source; never retain the source URL or query."""

    source_id: Literal["verified_main_bundle"]
    source_query_present: bool
    line_0based: int
    utf16_column_0based: int


@dataclass(frozen=True, slots=True)
class LoginRequestDiagnostic:
    """Closed facts and a conservative role derived from verified path/callsite evidence."""

    sequence: int
    path_family: LoginRequestPathFamily
    request_role: LoginRequestRole
    payload_role: LoginRequestPayloadRole
    request_payload_shape: LoginRequestPayloadShape
    initiator_handle_login: bool
    initiator_complete: bool
    initiator_kind: LoginInitiatorKind = "unknown"
    initiator_frame_count: int = 0
    initiator_source_scopes: tuple[LoginInitiatorSourceScope, ...] = ()
    initiator_completion_reason: LoginInitiatorCompletionReason = "missing_stack"
    public_callsite: LoginPublicCallsite = "unresolved"
    initiator_public_frames: tuple[LoginPublicFrameDiagnostic, ...] = ()
    initiator_public_frames_truncated: bool = False
    status: int | None = None
    terminal: LoginRequestTerminal = "incomplete"
    evidence_complete: bool = True
    response_media: LoginResponseMedia = "unknown"
    response_auth_outcome: LoginResponseAuthOutcome = "not_observed"
    response_schema_fields: tuple[str, ...] = ()
    response_err_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _InitiatorDiagnostic:
    kind: LoginInitiatorKind = "unknown"
    frame_count: int = 0
    source_scopes: tuple[LoginInitiatorSourceScope, ...] = ()
    handle_login: bool = False
    complete: bool = False
    completion_reason: LoginInitiatorCompletionReason = "missing_stack"
    callsite: LoginPublicCallsite = "unresolved"
    public_frames: tuple[LoginPublicFrameDiagnostic, ...] = ()
    public_frames_truncated: bool = False


def _initiator_kind(value: object) -> LoginInitiatorKind:
    match value:
        case "script":
            return "script"
        case "parser":
            return "parser"
        case "preload":
            return "preload"
        case "SignedExchange":
            return "SignedExchange"
        case "preflight":
            return "preflight"
        case "other":
            return "other"
        case _:
            return "unknown"


def _source_scope(url: object) -> LoginInitiatorSourceScope:
    """An origin category describes source provenance, never a script or login role."""
    if not isinstance(url, str) or len(url) > 16384 or any(ord(char) < 33 for char in url):
        return "empty_or_invalid"
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return "empty_or_invalid"
        port = parsed.port
        if parsed.hostname in {"www.korail.com", "cdn.korail.com"}:
            if parsed.scheme != "https" or port not in {None, 443}:
                return "empty_or_invalid"
            if parsed.hostname == "www.korail.com":
                return "korail_origin"
            if parsed.path == _PUBLIC_CALLSITE_BUNDLE_PATH and "#" not in url:
                return "verified_main_bundle"
            return "official_cdn"
    except ValueError:
        return "empty_or_invalid"
    return "third_party"


def _response_media(value: object) -> LoginResponseMedia:
    if not isinstance(value, str) or len(value) > 256:
        return "unknown"
    media = value.split(";", 1)[0].strip().lower()
    major, separator, minor = media.partition("/")
    if (
        not separator
        or not major
        or not minor
        or "/" in minor
        or any(
            not (char.isascii() and (char.isalnum() or char in "!#$&^_.+-"))
            for char in major + minor
        )
    ):
        return "unknown"
    if media in {"application/json", "text/json"} or (
        major == "application" and minor.endswith("+json")
    ):
        return "json"
    if media in {"text/html", "application/xhtml+xml"}:
        return "html"
    if major == "text":
        return "text"
    return "other"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous JSON object")
        result[key] = value
    return result


def _load_bounded_json(value: str) -> object:
    # The JSON decoder's recursion limit varies by Python build and platform. Bound
    # provider-controlled nesting before decoding without counting quoted delimiters.
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
            if depth > _JSON_DEPTH_LIMIT:
                raise ValueError("JSON nesting exceeds diagnostic limit")
        elif character in "}]":
            depth -= 1
    payload: object = json.loads(value, object_pairs_hook=_unique_json_object)
    return payload


def _bounded_payload_keys(value: object) -> frozenset[str] | None:
    """Read only a bounded field-name set and immediately discard all values."""

    if not isinstance(value, str):
        return None
    try:
        if len(value.encode("utf-8")) > _BODY_LIMIT:
            return None
        if value.lstrip().startswith("{"):
            payload = _load_bounded_json(value)
            if not isinstance(payload, Mapping) or len(payload) > 32:
                return None
            keys = tuple(payload)
        else:
            pairs = parse_qsl(
                value,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=32,
            )
            keys = tuple(key for key, _ in pairs)
            if len(keys) != len(set(keys)):
                return None
        if not keys or any(
            not isinstance(key, str)
            or not 0 < len(key) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in key)
            for key in keys
        ):
            return None
        return frozenset(keys)
    except Exception:  # noqa: BLE001 -- provider-controlled diagnostics fail closed.
        return None


def _request_payload_diagnostics(
    value: object,
) -> tuple[LoginRequestPayloadRole, LoginRequestPayloadShape]:
    if value is None:
        return "unresolved", "absent"
    keys = _bounded_payload_keys(value)
    if keys is None:
        return "unresolved", "invalid"
    if keys in _LOGIN_PAYLOAD_FIELD_SETS:
        return "login_submission", "exact_login"
    if _LOGIN_PAYLOAD_BASE_FIELDS < keys:
        return "unresolved", "login_with_additions"
    if keys & _LOGIN_PAYLOAD_KNOWN_FIELDS:
        return "unresolved", "partial_login"
    return "unresolved", "unrelated"


def _encoded_data_length(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or value < 0
        or value > _BODY_LIMIT
        or not math.isfinite(value)
        or value != int(value)
    ):
        return None
    return int(value)


def _response_schema_fields(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Retain a bounded two-level JSON field shape and discard every value."""

    fields: list[str] = []
    for key in sorted(payload):
        if (
            not isinstance(key, str)
            or not 0 < len(key) <= 128
            or any(ord(char) < 33 or ord(char) > 126 or char == "." for char in key)
        ):
            return ()
        fields.append(key)
        nested = payload[key]
        if isinstance(nested, Mapping):
            for nested_key in sorted(nested):
                if (
                    not isinstance(nested_key, str)
                    or not 0 < len(nested_key) <= 128
                    or any(ord(char) < 33 or ord(char) > 126 or char == "." for char in nested_key)
                ):
                    return ()
                fields.append(f"{key}.{nested_key}")
                if len(fields) > 32:
                    return ()
        if len(fields) > 32:
            return ()
    return tuple(fields)


def _response_body_diagnostic(
    response: object,
    *,
    recognized_login_request: bool,
) -> tuple[LoginResponseAuthOutcome, tuple[str, ...], tuple[str, ...]]:
    outcome, schema_fields, payload = _response_body_outcome(
        response, recognized_login_request=recognized_login_request
    )
    err_codes = (
        (sanitized_login_err_code(payload["errCode"]),)
        if payload is not None and "errCode" in payload
        else ()
    )
    return outcome, schema_fields, err_codes


def _response_body_outcome(
    response: object,
    *,
    recognized_login_request: bool,
) -> tuple[LoginResponseAuthOutcome, tuple[str, ...], Mapping[str, object] | None]:
    try:
        envelope = response if isinstance(response, Mapping) else None
        result = envelope.get("result") if envelope is not None else None
        if not isinstance(result, Mapping):
            return "unavailable", (), None
        raw = result.get("body")
        encoded = result.get("base64Encoded")
        if not isinstance(raw, str) or not isinstance(encoded, bool):
            return "unavailable", (), None
        if len(raw) > _BODY_LIMIT * 4 // 3 + 4:
            return "unavailable", (), None
        body = base64.b64decode(raw, validate=True) if encoded else raw.encode("utf-8")
        if len(body) > _BODY_LIMIT:
            return "unavailable", (), None
        payload = _load_bounded_json(body.decode("utf-8"))
        if not isinstance(payload, Mapping):
            return "not_login_result", (), None
        schema_fields = _response_schema_fields(payload)
        if payload.get("errCode") == "macro_err1":
            return "protection", schema_fields, payload
        login_shape = recognized_login_request or any(
            key in payload for key in ("hc14100En", "S_PASSCHG_YN", "s_chkauth_yn", "intgFlg")
        )
        result_code = payload.get("strResult")
        if not login_shape or not isinstance(result_code, str):
            return "not_login_result", schema_fields, payload
        if result_code != "SUCC":
            return "rejected", schema_fields, payload
        message_code = payload.get("h_msg_cd")
        if message_code not in {"IRZ000001", ""}:
            return "rejected", schema_fields, payload
        if payload.get("S_PASSCHG_YN") == "Y":
            return "challenge", schema_fields, payload
        profile = payload.get("hc14100En")
        if not isinstance(profile, Mapping):
            return "rejected", schema_fields, payload
        if profile.get("strNotiTpCd") in {"MC", "MS", "MM"}:
            return "challenge", schema_fields, payload
        customer_number = profile.get("strCustNo")
        if isinstance(customer_number, str) and 0 < len(customer_number) <= 256:
            return "accepted", schema_fields, payload
        return "rejected", schema_fields, payload
    except Exception:  # noqa: BLE001 -- provider-controlled diagnostics fail closed.
        return "unavailable", (), None


def _path_family(url: str) -> LoginRequestPathFamily:
    # The accepted origin was checked by the caller. Discard the parsed path immediately.
    path = urlsplit(url).path
    if path == _PUBLIC_KEY_PATH:
        return "public_key"
    if path == "/ebizweb/common/loginProcess":
        return "public_login"
    if path == "/ebizweb/integrate/srCheck.do":
        return "integration_check"
    if path.startswith("/web_s/"):
        return "business_dynamic"
    return "official_other"


def _request_role(
    path_family: LoginRequestPathFamily,
    public_callsite: LoginPublicCallsite,
) -> LoginRequestRole:
    path_role: LoginRequestRole = (
        "login_bootstrap"
        if path_family == "public_key"
        else "login_submission"
        if path_family == "public_login"
        else "integration_check"
        if path_family == "integration_check"
        else "unresolved"
    )
    callsite_role: LoginRequestRole = (
        "login_bootstrap"
        if public_callsite == "login_bootstrap"
        else "login_submission"
        if public_callsite == "login_submit"
        else "integration_check"
        if public_callsite == "integration_check"
        else "conflicting"
        if public_callsite == "mixed"
        else "unresolved"
    )
    if "conflicting" in {path_role, callsite_role}:
        return "conflicting"
    if path_role != "unresolved" and callsite_role != "unresolved":
        return path_role if path_role == callsite_role else "conflicting"
    return path_role if path_role != "unresolved" else callsite_role


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


def _initiator_diagnostics(value: object) -> _InitiatorDiagnostic:
    """Inspect only bounded CDP stack structure and retain no frame or function text."""

    if not isinstance(value, Mapping):
        return _InitiatorDiagnostic()
    kind = _initiator_kind(value.get("type"))
    stack = value.get("stack")
    if stack is None:
        return _InitiatorDiagnostic(kind=kind)
    found = False
    bootstrap_call = False
    login_call = False
    integration_call = False
    seen: set[int] = set()
    frame_count = 0
    public_frames: list[LoginPublicFrameDiagnostic] = []
    public_frames_truncated = False
    source_scopes: list[LoginInitiatorSourceScope] = []

    def result(
        completion_reason: LoginInitiatorCompletionReason,
        callsite: LoginPublicCallsite = "unresolved",
    ) -> _InitiatorDiagnostic:
        return _InitiatorDiagnostic(
            kind=kind,
            frame_count=frame_count,
            source_scopes=tuple(source_scopes),
            handle_login=found,
            complete=completion_reason == "complete",
            completion_reason=completion_reason,
            callsite=callsite,
            public_frames=tuple(public_frames),
            public_frames_truncated=public_frames_truncated,
        )

    for _ in range(8):
        if not isinstance(stack, Mapping) or id(stack) in seen:
            return result("invalid_stack")
        seen.add(id(stack))
        frames = stack.get("callFrames")
        if not isinstance(frames, list) or len(frames) > 64 - frame_count:
            return result("frame_limit")
        for frame in frames:
            frame_count += 1
            if not isinstance(frame, Mapping):
                if "empty_or_invalid" not in source_scopes:
                    source_scopes.append("empty_or_invalid")
                return result("invalid_frame")
            scope = _source_scope(frame.get("url"))
            if scope not in source_scopes:
                source_scopes.append(scope)
            name = frame.get("functionName")
            if not isinstance(name, str) or len(name) > 256:
                return result("invalid_frame")
            found = found or name == "handleLogin"
            public_frame, complete = _public_frame_diagnostic(frame)
            if not complete:
                return result("invalid_frame")
            if public_frame is not None:
                if len(public_frames) < 8:
                    public_frames.append(public_frame)
                else:
                    public_frames_truncated = True
                if public_frame.line_0based == 2:
                    column = public_frame.utf16_column_0based
                    bootstrap_call = bootstrap_call or 30054 <= column < 30089
                    login_call = login_call or 30620 <= column < 30652
                    integration_call = integration_call or 24951 <= column < 24993
        if stack.get("parentId") is not None:
            return result("parent_id")
        parent = stack.get("parent")
        if parent is None:
            roles = bootstrap_call + login_call + integration_call
            callsite: LoginPublicCallsite = (
                "mixed"
                if roles > 1
                else (
                    "login_bootstrap"
                    if bootstrap_call
                    else "login_submit"
                    if login_call
                    else "integration_check"
                    if integration_call
                    else "unresolved"
                )
            )
            return result("complete", callsite)
        stack = parent
    return result("depth_limit")


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
        self._response_sizes: dict[str, int | None] = {}
        self._body_read_attempted_sequences: set[int] = set()
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
        self._response_sizes.clear()
        self._body_read_attempted_sequences.clear()
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
        self._response_sizes.clear()
        self._body_read_attempted_sequences.clear()
        self._diagnostics.clear()
        self._transport_failed_sequences.clear()

    def snapshot(self) -> LoginSubmissionSnapshot:
        self._expire_if_due()
        return self._snapshot

    def diagnostics(self) -> tuple[LoginRequestDiagnostic, ...]:
        """Return only bounded, serializable facts before the submission owner closes."""
        self._expire_if_due()
        return tuple(self._diagnostics)

    async def classify_completed_response(
        self,
        execute_command: LoginExecuteCommand,
    ) -> LoginResponseAuthOutcome:
        """Classify one bounded completed body without retaining response material.

        This is post-rejection diagnostics only. The result never authorizes a session
        and is intentionally excluded from ``authentication_verification_mode``.
        """

        self._expire_if_due()
        request_ids = self._policy_request_ids()
        if not self._active or len(request_ids) != 1:
            return "unavailable"
        request_id = request_ids[0]
        snapshot = self._requests[request_id]
        index = tuple(self._requests).index(request_id)
        diagnostic = self._diagnostics[index]
        if diagnostic.response_auth_outcome != "not_observed":
            return diagnostic.response_auth_outcome
        if diagnostic.sequence in self._body_read_attempted_sequences:
            return "unavailable"
        self._body_read_attempted_sequences.add(diagnostic.sequence)
        size = self._response_sizes.get(request_id)
        if not (
            snapshot.state == "completed"
            and diagnostic.status == 200
            and diagnostic.terminal == "completed"
            and diagnostic.response_media == "json"
            and diagnostic.evidence_complete
            and size is not None
        ):
            self._diagnostics[index] = replace(diagnostic, response_auth_outcome="unavailable")
            return "unavailable"
        revision = self._group_revision
        try:
            response = await execute_command(
                {"method": "Network.getResponseBody", "params": {"requestId": request_id}}
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- the closed diagnostic must not expose CDP errors.
            outcome: LoginResponseAuthOutcome = "unavailable"
            schema_fields: tuple[str, ...] = ()
            err_codes: tuple[str, ...] = ()
        else:
            outcome, schema_fields, err_codes = _response_body_diagnostic(
                response,
                recognized_login_request=(
                    diagnostic.request_role == "login_submission"
                    or diagnostic.payload_role == "login_submission"
                ),
            )
        if (
            not self._active
            or self._group_revision != revision
            or self._policy_request_ids() != (request_id,)
        ):
            return "unavailable"
        self._diagnostics[index] = replace(
            self._diagnostics[index],
            response_auth_outcome=outcome,
            response_schema_fields=schema_fields,
            response_err_codes=err_codes,
        )
        return outcome

    def observed_err_codes(self) -> tuple[str, ...]:
        """Return sanitized provider error codes read from this group's response bodies."""

        return tuple(code for row in self._diagnostics for code in row.response_err_codes)

    def authentication_verification_mode(self) -> LoginAuthenticationVerificationMode:
        """Decide whether this closed request group may use independent auth evidence.

        A normal all-success group keeps the existing standard policy. A failed group
        can advance only when a confirmed login request completed successfully and
        every failure belongs to the confirmed integration check. Unknown, conflicting,
        incomplete, redirected, or protection evidence remains blocked.
        """

        self._expire_if_due()
        if self._snapshot.safe_to_probe:
            return "standard"
        rows = tuple(self._diagnostics)
        if (
            not rows
            or self._snapshot.state == "ambiguous"
            or not self._diagnostic_evidence_complete
            or any(not row.evidence_complete for row in rows)
            or any(row.request_role == "conflicting" for row in rows)
            or any(row.status in {403, 429} for row in rows)
        ):
            return "blocked"

        failed = tuple(
            row
            for row in rows
            if row.terminal == "failed" or (row.status is not None and not 200 <= row.status < 400)
        )
        if not failed or any(row.request_role != "integration_check" for row in failed):
            return "blocked"

        login_candidates = tuple(
            row
            for row in rows
            if row.request_role == "login_submission"
            and row.terminal != "failed"
            and (row.status is None or 200 <= row.status < 400)
        )
        if not login_candidates:
            return "blocked"
        if any(row.terminal == "incomplete" for row in rows):
            return "pending"
        if any(
            row.request_role == "login_submission"
            and not (
                row.terminal == "completed" and row.status is not None and 200 <= row.status < 400
            )
            for row in rows
        ):
            return "blocked"
        if not any(
            row.request_role == "login_submission"
            and row.terminal == "completed"
            and row.status is not None
            and 200 <= row.status < 400
            for row in rows
        ):
            return "blocked"
        return "independent"

    def _diagnostic_index(self, params: Mapping[str, object]) -> int:
        # Reuse the existing correlation map rather than retaining a second copy of IDs.
        request_id = _request_id(params)
        assert request_id is not None
        return tuple(self._requests).index(request_id)

    def _policy_request_ids(self) -> tuple[str, ...]:
        """Return requests that may affect authentication, excluding confirmed key bootstrap."""

        return tuple(
            request_id
            for request_id, diagnostic in zip(self._requests, self._diagnostics, strict=True)
            if diagnostic.request_role != "login_bootstrap"
        )

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
        if not isinstance(request, Mapping) or not _official_origin(request.get("url")):
            return
        url = request.get("url")
        assert isinstance(url, str)
        path_family = _path_family(url)
        method = request.get("method")
        if method != "POST" and not (method == "GET" and path_family == "public_key"):
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
            initiator = _initiator_diagnostics(params.get("initiator"))
            payload_role, request_payload_shape = (
                _request_payload_diagnostics(request.get("postData"))
                if method == "POST"
                else ("unresolved", "absent")
            )
            self._diagnostics.append(
                LoginRequestDiagnostic(
                    sequence=len(self._diagnostics) + 1,
                    path_family=path_family,
                    request_role=_request_role(path_family, initiator.callsite),
                    payload_role=payload_role,
                    request_payload_shape=request_payload_shape,
                    initiator_handle_login=initiator.handle_login,
                    initiator_complete=initiator.complete,
                    initiator_kind=initiator.kind,
                    initiator_frame_count=initiator.frame_count,
                    initiator_source_scopes=initiator.source_scopes,
                    initiator_completion_reason=initiator.completion_reason,
                    public_callsite=initiator.callsite,
                    initiator_public_frames=initiator.public_frames,
                    initiator_public_frames_truncated=initiator.public_frames_truncated,
                    evidence_complete=self._diagnostic_evidence_complete
                    and params.get("redirectResponse") is None,
                )
            )
            self._requests[request_id] = LoginSubmissionSnapshot("posted")
            self._response_sizes[request_id] = None
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
        if isinstance(response, Mapping):
            self._diagnostics[index] = replace(
                self._diagnostics[index], response_media=_response_media(response.get("mimeType"))
            )
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
        self._response_sizes[request_id] = _encoded_data_length(params.get("encodedDataLength"))
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
        policy_ids = self._policy_request_ids()
        if not policy_ids:
            self._snapshot = LoginSubmissionSnapshot("missing")
            return
        requests = tuple(self._requests.values())
        policy_requests = tuple(self._requests[request_id] for request_id in policy_ids)
        statuses = [request.status for request in policy_requests if request.status is not None]
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
        self._response_sizes.clear()
        self._body_read_attempted_sequences.clear()
