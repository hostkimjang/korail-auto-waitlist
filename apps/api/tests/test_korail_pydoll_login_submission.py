from __future__ import annotations

import base64
import json
from dataclasses import FrozenInstanceError, asdict, dataclass
from urllib.parse import urlencode

import pytest

from rail_waitlist.korail_sidecar.pydoll.login_submission import (
    LOGIN_ERR_CODE_UNRECOGNIZED,
    PydollLoginResponseUnavailable,
    PydollLoginSubmission,
    sanitized_login_err_code,
)


def test_login_err_code_keeps_only_closed_code_tokens() -> None:
    assert sanitized_login_err_code("macro_err1") == "macro_err1"
    for value in ("로그인 차단", "free text code", "x" * 33, "", None, 1):
        assert sanitized_login_err_code(value) == LOGIN_ERR_CODE_UNRECOGNIZED


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


def request(
    request_id: str = "login-1",
    *,
    url: str = "https://www.korail.com/dynamic-login-endpoint",
    method: str = "POST",
    resource_type: str = "XHR",
    post_data: object = None,
) -> dict[str, object]:
    request_data: dict[str, object] = {"url": url, "method": method}
    if post_data is not None:
        request_data["postData"] = post_data
    return {
        "params": {
            "requestId": request_id,
            "type": resource_type,
            "request": request_data,
        }
    }


def response(
    status: object = 200,
    *,
    request_id: str = "login-1",
    url: str = "https://www.korail.com/dynamic-login-endpoint",
    resource_type: str = "XHR",
) -> dict[str, object]:
    return {
        "params": {
            "requestId": request_id,
            "type": resource_type,
            "response": {"url": url, "status": status},
        }
    }


def terminal(
    request_id: str = "login-1",
    *,
    encoded_data_length: object = 512,
) -> dict[str, object]:
    return {"params": {"requestId": request_id, "encodedDataLength": encoded_data_length}}


LOGIN_PAYLOAD = {
    "UserId": "fixture-private-account",
    "selInputFlg": "2",
    "hidMemberFlg": "1",
    "txtDv": "2",
    "encUserId": "fixture-private-encrypted-account",
    "keyname": "fixture-private-key",
    "encUserPwd": "fixture-private-encrypted-password",
    "useKeySec": "N",
    "deviceId": "fixture-private-device",
}


@pytest.mark.parametrize("resource_type", ["XHR", "Fetch"])
def test_probe_is_blocked_until_unique_submission_body_finishes(resource_type: str) -> None:
    clock = Clock()
    owner = PydollLoginSubmission(10, monotonic=clock)
    owner.arm()
    assert owner.snapshot().state == "missing"
    assert owner.snapshot().safe_to_probe is False
    owner.on_request_will_be_sent(request(resource_type=resource_type))
    clock.now += 2
    assert owner.snapshot().state == "posted"
    assert owner.snapshot().safe_to_probe is False
    owner.on_response_received(response(resource_type=resource_type))
    assert owner.snapshot().state == "in_flight"
    assert owner.snapshot().safe_to_probe is False
    owner.on_loading_finished(terminal())
    assert owner.snapshot().state == "completed"
    assert owner.snapshot().safe_to_probe is True
    owner.close()
    assert owner.snapshot().safe_to_probe is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 599])
def test_http_failure_cannot_become_success_and_preserves_protection_status(status: int) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response(status))
    owner.on_loading_finished(terminal())
    snapshot = owner.snapshot()
    assert (snapshot.state, snapshot.status, snapshot.failure) == ("failed", status, "http_error")
    assert snapshot.safe_to_probe is False


def test_failure_exception_keeps_only_closed_submission_diagnostics() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    event = {
        "params": {
            "requestId": "login-1",
            "type": "XHR",
            "request": {
                "method": "POST",
                "url": "https://www.korail.com/fixture-sensitive-path?fixture-query",
                "postData": "fixture-password",
            },
        }
    }
    owner.on_request_will_be_sent(event)
    owner.on_response_received(response(500))
    error = PydollLoginResponseUnavailable(owner.snapshot())
    assert (error.submission_state, error.submission_failure, error.submission_status) == (
        "failed",
        "http_error",
        500,
    )
    assert error.stage == "login_response"
    assert error.retry_after_seconds == 300
    diagnostic = repr(vars(error)) + str(error)
    for private_value in ("fixture-sensitive-path", "fixture-query", "fixture-password", "login-1"):
        assert private_value not in diagnostic


def test_failure_exception_without_observation_preserves_legacy_constructor() -> None:
    error = PydollLoginResponseUnavailable()
    assert error.submission_state is None
    assert error.submission_status is None
    assert error.submission_failure is None
    assert error.stage == "login_response"
    assert error.failure_kind == "provider_submission_failed"
    assert error.retry_after_seconds == 300


@pytest.mark.parametrize("with_headers", [False, True])
def test_network_failure_is_closed_and_does_not_retain_browser_error(with_headers: bool) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    if with_headers:
        owner.on_response_received(response())
    owner.on_loading_failed({"params": {"requestId": "login-1", "errorText": "fixture-error"}})
    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure == "network_error"
    assert "fixture-error" not in repr(vars(owner))
    owner.on_loading_finished(terminal())
    assert owner.snapshot().safe_to_probe is False


@pytest.mark.parametrize("stage", ["missing", "posted", "in_flight"])
def test_deadline_and_late_completion_never_authorize_probe(stage: str) -> None:
    clock = Clock()
    owner = PydollLoginSubmission(1, monotonic=clock)
    owner.arm()
    if stage != "missing":
        owner.on_request_will_be_sent(request())
    if stage == "in_flight":
        owner.on_response_received(response())
    clock.now += 1
    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure == ("missing" if stage == "missing" else "timeout")
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    assert owner.snapshot().safe_to_probe is False


@pytest.mark.parametrize(
    "url",
    [
        "http://www.korail.com/login",
        "https://korail.com/login",
        "https://www.korail.com.evil.example/login",
        "https://www.korail.com:444/login",
        "https://fixture@www.korail.com/login",
        "https://www.korail.com/login#fragment",
        "https://www.korail.com:invalid/login",
    ],
)
def test_non_official_origin_cannot_supply_submission(url: str) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(url=url))
    owner.on_response_received(response(url=url))
    owner.on_loading_finished(terminal())
    assert owner.snapshot().state == "missing"


@pytest.mark.parametrize(
    ("method", "resource_type"),
    [("GET", "XHR"), ("POST", "Script"), ("POST", "Stylesheet"), ("POST", "Document")],
)
def test_background_assets_and_non_post_requests_are_ignored(
    method: str, resource_type: str
) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(method=method, resource_type=resource_type))
    assert owner.snapshot().state == "missing"


def test_pre_window_and_mismatched_callbacks_do_not_attest_login() -> None:
    owner = PydollLoginSubmission(10)
    owner.on_request_will_be_sent(request("old"))
    owner.arm()
    owner.on_response_received(response(request_id="old"))
    owner.on_loading_finished(terminal("old"))
    owner.on_loading_failed(terminal("old"))
    assert owner.snapshot().state == "missing"
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response(500, request_id="other"))
    owner.on_loading_finished(terminal("other"))
    owner.on_loading_failed(terminal("other"))
    assert owner.snapshot().state == "posted"


@pytest.mark.parametrize("completed_first", [False, True])
def test_incomplete_second_post_waits_then_times_out_even_after_first_completion(
    completed_first: bool,
) -> None:
    # 두 200/완료 XHR 양성 표본에 따라 두 번째 요청 자체를 오류로 보지 않는다.
    # 미완료 요청은 여전히 인증 확인을 차단하고 제출 창 만료 시 실패한다.
    clock = Clock()
    owner = PydollLoginSubmission(10, monotonic=clock)
    owner.arm()
    owner.on_request_will_be_sent(request())
    if completed_first:
        owner.on_response_received(response())
        owner.on_loading_finished(terminal())
    owner.on_request_will_be_sent(request("login-2"))
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    assert owner.snapshot().safe_to_probe is False
    clock.now += 10
    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure == "timeout"


@pytest.mark.parametrize("late_second", [False, True])
def test_two_official_posts_require_every_header_and_completion(late_second: bool) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    if late_second:
        owner.on_loading_finished(terminal())
        assert owner.snapshot().safe_to_probe is True
    owner.on_request_will_be_sent(request("login-2", resource_type="Fetch"))
    assert owner.snapshot().safe_to_probe is False
    owner.on_loading_finished(terminal())
    assert owner.snapshot().safe_to_probe is False
    owner.on_response_received(response(request_id="login-2", resource_type="Fetch"))
    assert owner.snapshot().safe_to_probe is False
    owner.on_loading_finished(terminal("login-2"))
    assert owner.snapshot().safe_to_probe is True


@pytest.mark.parametrize("status", [200, 204, 302, 500, 403, 429])
def test_second_post_response_and_completion_control_aggregate_safety(status: int) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    owner.on_request_will_be_sent(request("login-2"))
    owner.on_response_received(response(status, request_id="login-2"))
    assert owner.snapshot().safe_to_probe is False
    owner.on_loading_finished(terminal("login-2"))
    assert owner.snapshot().safe_to_probe is (status < 400)
    assert owner.snapshot().status == status
    if status >= 400:
        assert owner.snapshot().failure == "http_error"


def test_only_a_confirmed_integration_failure_can_open_independent_auth_verification() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    login_url = "https://www.korail.com/ebizweb/common/loginProcess"
    integration_url = "https://www.korail.com/ebizweb/integrate/srCheck.do"
    owner.on_request_will_be_sent(request(url=login_url))
    owner.on_response_received(response(url=login_url))
    owner.on_loading_finished(terminal())
    owner.on_request_will_be_sent(request("integration", url=integration_url))
    owner.on_response_received(response(500, request_id="integration", url=integration_url))

    assert owner.authentication_verification_mode() == "pending"
    owner.on_loading_finished(terminal("integration"))
    assert owner.authentication_verification_mode() == "independent"
    assert [row.request_role for row in owner.diagnostics()] == [
        "login_submission",
        "integration_check",
    ]


@pytest.mark.parametrize(
    ("failure_url", "expected_role"),
    [
        ("https://www.korail.com/ebizweb/common/loginProcess", "login_submission"),
        ("https://www.korail.com/dynamic-login-endpoint", "unresolved"),
    ],
)
def test_login_or_unresolved_failure_never_opens_independent_verification(
    failure_url: str,
    expected_role: str,
) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(url=failure_url))
    owner.on_response_received(response(500, url=failure_url))
    owner.on_loading_finished(terminal())

    assert owner.authentication_verification_mode() == "blocked"
    assert owner.diagnostics()[0].request_role == expected_role


def test_integration_failure_without_a_completed_login_request_stays_blocked() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    integration_url = "https://www.korail.com/ebizweb/integrate/srCheck.do"
    owner.on_request_will_be_sent(request(url=integration_url))
    owner.on_response_received(response(500, url=integration_url))
    owner.on_loading_finished(terminal())

    assert owner.authentication_verification_mode() == "blocked"


@pytest.mark.parametrize("failure", ["network", "invalid", "missing_id", "bound"])
def test_multi_post_failure_is_sticky_and_late_success_cannot_erase_it(failure: str) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    owner.on_request_will_be_sent(request("login-2"))
    if failure == "network":
        owner.on_loading_failed(terminal("login-2"))
    elif failure == "invalid":
        owner.on_response_received(response(None, request_id="login-2"))
    elif failure == "missing_id":
        owner.on_request_will_be_sent(request(""))
    else:
        for index in range(owner.MAX_REQUESTS):
            owner.on_request_will_be_sent(request(f"extra-{index}"))
    failed = owner.snapshot()
    owner.on_response_received(response(request_id="login-2"))
    owner.on_loading_finished(terminal("login-2"))
    assert owner.snapshot() == failed
    assert failed.safe_to_probe is False
    assert len(owner._requests) <= owner.MAX_REQUESTS


@pytest.mark.parametrize("status", [403, 429])
def test_later_protection_status_survives_an_earlier_generic_failure(status: int) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_request_will_be_sent(request("login-2"))
    owner.on_response_received(response(500))
    owner.on_response_received(response(status, request_id="login-2"))
    assert owner.snapshot().status == status
    assert owner.snapshot().failure == "http_error"
    assert owner.snapshot().safe_to_probe is False


def test_multiple_unfinished_requests_expire_and_clear_private_ids_on_close() -> None:
    clock = Clock()
    owner = PydollLoginSubmission(1, monotonic=clock)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_request_will_be_sent(request("login-2"))
    owner.on_response_received(response())
    owner.on_response_received(response(request_id="login-2"))
    clock.now += 1
    assert owner.snapshot().failure == "timeout"
    assert not owner._requests
    owner.close()
    owner.on_loading_finished(terminal())
    owner.on_loading_finished(terminal("login-2"))
    assert owner.snapshot().safe_to_probe is False


def test_private_group_revision_changes_only_for_accepted_distinct_members() -> None:
    owner = PydollLoginSubmission(10)
    initial = owner.group_revision
    owner.arm()
    owner.on_request_will_be_sent(request(method="GET"))
    assert owner.group_revision == initial
    owner.on_request_will_be_sent(request())
    first = owner.group_revision
    assert first > initial
    owner.on_request_will_be_sent(request())
    assert owner.group_revision == first
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    completed = owner.snapshot()
    owner.on_request_will_be_sent(request("login-2"))
    owner.on_response_received(response(request_id="login-2"))
    owner.on_loading_finished(terminal("login-2"))
    assert owner.snapshot() == completed
    assert owner.group_revision > first
    assert "revision" not in repr(owner.snapshot())
    last = owner.group_revision
    owner.close()
    owner.on_request_will_be_sent(request("after-close"))
    assert owner.group_revision == last
    owner.arm()
    owner.on_request_will_be_sent(request())
    assert owner.group_revision > last


def test_duplicate_request_callback_does_not_create_a_second_submission() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    assert owner.snapshot().safe_to_probe is True


@pytest.mark.parametrize("status", [True, "200", None, float("nan"), 200.5, 600, 10**400])
def test_invalid_status_cannot_authorize_probe(status: object) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response(status))
    owner.on_loading_finished(terminal())
    assert owner.snapshot().failure == "invalid_response"
    assert owner.snapshot().safe_to_probe is False


def test_finished_without_headers_and_external_redirect_are_fail_closed() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_loading_finished(terminal())
    assert owner.snapshot().failure == "invalid_response"
    owner.close()
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response(url="https://other.example/login"))
    assert owner.snapshot().failure == "invalid_response"


def test_owner_does_not_retain_url_body_or_headers() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    event = {
        "params": {
            "requestId": "login-1",
            "type": "XHR",
            "request": {
                "url": "https://www.korail.com/fixture-private-path?fixture=value",
                "method": "POST",
                "postData": "fixture-body",
                "headers": {"fixture-header": "fixture-value"},
            },
        }
    }
    owner.on_request_will_be_sent(event)
    owner.on_response_received(response(url="https://www.korail.com/fixture-private-path"))
    state = repr(vars(owner)) + repr(owner.snapshot())
    for value in ("fixture-private-path", "fixture-body", "fixture-header", "fixture-value"):
        assert value not in state


@pytest.mark.parametrize(
    "event", [None, {}, {"params": []}, {"params": {"type": [], "request": {}}}]
)
def test_malformed_unrelated_events_are_ignored(event: object) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(event)
    owner.on_response_received(event)
    owner.on_loading_finished(event)
    owner.on_loading_failed(event)
    assert owner.snapshot().state == "missing"


def test_missing_candidate_request_id_stays_ambiguous() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(""))
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    assert owner.snapshot().state == "ambiguous"
    assert owner.snapshot().safe_to_probe is False


def test_explicit_official_tls_port_is_accepted_and_close_ignores_late_events() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(url="https://www.korail.com:443/login"))
    owner.on_response_received(response())
    owner.on_loading_finished(terminal())
    owner.close()
    owner.on_request_will_be_sent(request("login-2"))
    owner.on_loading_failed(terminal())
    assert owner.snapshot().safe_to_probe is True


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        PydollLoginSubmission(value)


PUBLIC_BUNDLE_PATH = "https://cdn.korail.com/bundle/bundle.fcfd32491c1f6e0b2541.js"
PUBLIC_BUNDLE = PUBLIC_BUNDLE_PATH + "?v=1.0.221"


def frame(column: object = 30620, *, name: str = "handleLogin") -> dict[str, object]:
    return {"functionName": name, "url": PUBLIC_BUNDLE, "lineNumber": 2, "columnNumber": column}


def with_initiator(event: dict[str, object], initiator: object) -> dict[str, object]:
    params = event["params"]
    assert isinstance(params, dict)
    params["initiator"] = initiator
    return event


@pytest.mark.parametrize(
    ("path", "family", "request_role"),
    [
        (
            "/ebizweb/tour/mypage/pwd_action/pblk",
            "public_key",
            "conflicting",
        ),
        ("/ebizweb/common/loginProcess", "public_login", "login_submission"),
        ("/ebizweb/integrate/srCheck.do", "integration_check", "conflicting"),
        ("/web_s/fixture-private-path", "business_dynamic", "login_submission"),
        ("/web_s", "official_other", "login_submission"),
        ("/ebizweb/common/loginProcess/extra", "official_other", "login_submission"),
        ("/ebizweb/common/LoginProcess", "official_other", "login_submission"),
        ("/fixture-private-path", "official_other", "login_submission"),
    ],
)
def test_request_diagnostics_combine_closed_path_and_callsite_without_material(
    path: str,
    family: str,
    request_role: str,
) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    event = with_initiator(
        request(url=f"https://www.korail.com{path}?fixture-query"),
        {"stack": {"callFrames": [frame()]}},
    )
    owner.on_request_will_be_sent(event)
    owner.on_response_received(response(500))
    owner.on_loading_finished(terminal())
    rows = owner.diagnostics()
    assert isinstance(rows, tuple) and len(rows) == 1
    row = rows[0]
    assert (row.sequence, row.path_family, row.status, row.terminal) == (
        1,
        family,
        500,
        "completed",
    )
    assert row.initiator_handle_login is True
    assert row.initiator_complete is True
    assert row.public_callsite == "login_submit"
    assert row.request_role == request_role
    assert owner.snapshot().failure == "http_error"
    encoded = json.dumps([asdict(item) for item in rows])
    for private_value in (path, "fixture-query", "login-1", "handleLogin", PUBLIC_BUNDLE):
        assert private_value not in encoded
    with pytest.raises(FrozenInstanceError):
        row.sequence = 2
    owner.close()
    assert owner.diagnostics() == ()
    assert not owner._requests


@pytest.mark.parametrize(
    ("column", "callsite"),
    [
        (30053, "unresolved"),
        (30054, "login_bootstrap"),
        (30088, "login_bootstrap"),
        (30089, "unresolved"),
        (30619, "unresolved"),
        (30620, "login_submit"),
        (30651, "login_submit"),
        (30652, "unresolved"),
        (24950, "unresolved"),
        (24951, "integration_check"),
        (24992, "integration_check"),
        (24993, "unresolved"),
        (True, "unresolved"),
        ("30620", "unresolved"),
        (30620.0, "unresolved"),
    ],
)
def test_exact_public_callsite_spans_use_integer_utf16_columns(column, callsite) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(
        with_initiator(request(), {"stack": {"callFrames": [frame(column, name="callApi")]}})
    )
    row = owner.diagnostics()[0]
    assert row.public_callsite == callsite
    expected_role = {
        "login_bootstrap": "login_bootstrap",
        "login_submit": "login_submission",
        "integration_check": "integration_check",
        "unresolved": "unresolved",
    }[callsite]
    assert row.request_role == expected_role
    assert row.initiator_handle_login is False
    assert owner.snapshot().state == ("missing" if callsite == "login_bootstrap" else "posted")


@pytest.mark.parametrize(
    "changes",
    [
        {"url": PUBLIC_BUNDLE + "#fixture-fragment"},
        {"url": PUBLIC_BUNDLE.replace("fcfd", "ffff")},
        {"lineNumber": 3},
        {"lineNumber": "2"},
        {"lineNumber": 2.0},
        {"columnNumber": None},
        {"url": None},
    ],
)
def test_bundle_identity_or_coordinates_cannot_be_inferred_from_function_name(changes) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    changed = {**frame(), **changes}
    owner.on_request_will_be_sent(with_initiator(request(), {"stack": {"callFrames": [changed]}}))
    assert owner.diagnostics()[0].initiator_handle_login is True
    assert owner.diagnostics()[0].public_callsite == "unresolved"


def test_parent_stack_can_supply_exact_callsite_and_both_roles_remain_mixed() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    initiator = {
        "stack": {
            "callFrames": [frame(24951, name="callApi")],
            "parent": {"callFrames": [frame()]},
        }
    }
    owner.on_request_will_be_sent(with_initiator(request(), initiator))
    assert owner.diagnostics()[0].public_callsite == "mixed"
    assert owner.diagnostics()[0].initiator_handle_login is True


@pytest.mark.parametrize("wrapped", [False, True])
def test_public_key_bootstrap_cannot_authorize_probe_before_login_submission(
    wrapped: bool,
) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    bootstrap_url = (
        "https://www.korail.com/fixture-protection-transport"
        if wrapped
        else "https://www.korail.com/ebizweb/tour/mypage/pwd_action/pblk"
    )
    bootstrap_method = "POST" if wrapped else "GET"
    owner.on_request_will_be_sent(
        with_initiator(
            request(
                "bootstrap",
                url=bootstrap_url,
                method=bootstrap_method,
                resource_type="Fetch",
            ),
            {"type": "script", "stack": {"callFrames": [frame(30054, name="callApi")]}},
        )
    )
    owner.on_response_received(
        response(request_id="bootstrap", url=bootstrap_url, resource_type="Fetch")
    )
    owner.on_loading_finished(terminal("bootstrap"))
    assert owner.snapshot().state == "missing"
    assert owner.snapshot().safe_to_probe is False
    assert owner.diagnostics()[0].request_role == "login_bootstrap"

    login_url = (
        "https://www.korail.com/fixture-protection-transport"
        if wrapped
        else "https://www.korail.com/ebizweb/common/loginProcess"
    )
    owner.on_request_will_be_sent(
        with_initiator(
            request("login", url=login_url, resource_type="Fetch"),
            {"type": "script", "stack": {"callFrames": [frame(30620, name="callApi")]}},
        )
    )
    owner.on_response_received(response(request_id="login", url=login_url, resource_type="Fetch"))
    owner.on_loading_finished(terminal("login"))
    assert owner.snapshot().safe_to_probe is True
    assert [row.request_role for row in owner.diagnostics()] == [
        "login_bootstrap",
        "login_submission",
    ]


@pytest.mark.parametrize(
    "kind", ["absent", "missing_stack", "invalid", "parent_id", "wide", "deep", "cycle", "name"]
)
def test_malformed_or_truncated_initiators_remain_bounded_and_unresolved(kind) -> None:
    stack: dict[str, object] = {"callFrames": [frame()]}
    initiator: object = {"stack": stack}
    if kind == "absent":
        initiator = None
    elif kind == "missing_stack":
        initiator = {"type": "script"}
    elif kind == "invalid":
        stack["callFrames"] = "fixture-private-stack"
    elif kind == "parent_id":
        stack["parentId"] = {"id": "fixture-private-id"}
    elif kind == "wide":
        stack["callFrames"] = [frame()] * 65
    elif kind == "deep":
        for _ in range(9):
            stack = {"callFrames": [], "parent": stack}
        initiator = {"stack": stack}
    elif kind == "cycle":
        stack["parent"] = stack
    else:
        stack["callFrames"] = [frame(name="fixture-private-name" * 100)]
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(with_initiator(request(), initiator))
    row = owner.diagnostics()[0]
    assert row.initiator_complete is False
    assert row.public_callsite == "unresolved"
    assert owner.snapshot().state == "posted"
    assert "fixture-private" not in repr(vars(owner)) + repr(row)


def test_total_frame_budget_applies_across_parent_nodes() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    stack = {"callFrames": [frame()] * 40, "parent": {"callFrames": [frame(24951)] * 25}}
    owner.on_request_will_be_sent(with_initiator(request(), {"stack": stack}))
    assert owner.diagnostics()[0].initiator_complete is False
    assert owner.diagnostics()[0].public_callsite == "unresolved"


def test_diagnostics_distinguish_transport_failure_from_http_error_completion() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_request_will_be_sent(request("second"))
    owner.on_request_will_be_sent(request("third"))
    owner.on_response_received(response(500))
    owner.on_loading_finished(terminal())
    owner.on_response_received(response(200, request_id="second"))
    owner.on_loading_failed(terminal("second"))
    owner.on_loading_finished(terminal("second"))
    rows = owner.diagnostics()
    assert [(row.sequence, row.status, row.terminal) for row in rows] == [
        (1, 500, "completed"),
        (2, 200, "failed"),
        (3, None, "incomplete"),
    ]
    assert owner.snapshot().failure == "http_error"


@pytest.mark.parametrize(
    ("transport_event", "expected_terminal"),
    [("none", "incomplete"), ("finished", "completed"), ("failed", "failed")],
)
def test_http_error_headers_and_transport_terminal_remain_distinct(
    transport_event: str, expected_terminal: str
) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response(500))
    assert owner.diagnostics()[0].terminal == "incomplete"
    if transport_event == "finished":
        owner.on_loading_finished(terminal())
    elif transport_event == "failed":
        owner.on_loading_failed(terminal())
    row = owner.diagnostics()[0]
    assert (row.status, row.terminal) == (500, expected_terminal)
    assert (owner.snapshot().failure, owner.snapshot().status) == ("http_error", 500)
    assert owner.snapshot().safe_to_probe is False


@pytest.mark.parametrize("status", [None, "200", True])
def test_invalid_response_does_not_supply_a_transport_terminal(status: object) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response(status))
    row = owner.diagnostics()[0]
    assert row.terminal == "incomplete"
    assert row.evidence_complete is False
    assert row.status is None
    assert owner.snapshot().failure == "invalid_response"


@pytest.mark.parametrize("kind", ["redirect", "invalid", "missing_id", "overflow"])
def test_unknown_observation_evidence_is_explicit_without_changing_verdict(kind) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    if kind == "redirect":
        redirect = request()
        params = redirect["params"]
        assert isinstance(params, dict)
        params["redirectResponse"] = {"url": "https://fixture-private.example", "status": 302}
        owner.on_request_will_be_sent(redirect)
        owner.on_response_received(response(302))
        owner.on_loading_finished(terminal())
        assert owner.snapshot().safe_to_probe is True
    elif kind == "invalid":
        owner.on_response_received(response(None))
        assert owner.snapshot().failure == "invalid_response"
    elif kind == "missing_id":
        owner.on_request_will_be_sent(request(""))
        assert owner.snapshot().state == "ambiguous"
    else:
        for index in range(9):
            owner.on_request_will_be_sent(request(str(index)))
        assert owner.snapshot().state == "ambiguous"
        assert len(owner.diagnostics()) == owner.MAX_REQUESTS
    assert all(row.evidence_complete is False for row in owner.diagnostics())


def test_safe_diagnostics_remain_available_at_deadline_before_close() -> None:
    clock = Clock()
    owner = PydollLoginSubmission(1, monotonic=clock)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.on_response_received(response())
    clock.now += 1
    assert owner.snapshot().failure == "timeout"
    assert not owner._requests
    row = owner.diagnostics()[0]
    assert (row.status, row.terminal) == (200, "incomplete")
    assert "login-1" not in repr(vars(owner))
    owner.close()
    assert owner.diagnostics() == ()


def test_arming_again_does_not_reuse_closed_diagnostics() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    owner.close()
    owner.arm()
    owner.on_request_will_be_sent(request("new"))
    assert owner.diagnostics()[0].sequence == 1
    assert len(owner.diagnostics()) == 1


@pytest.mark.parametrize(
    "url",
    [PUBLIC_BUNDLE, PUBLIC_BUNDLE + "?fixture-private-query", PUBLIC_BUNDLE + "?"],
)
def test_verified_source_matches_with_query_but_retains_only_query_presence(url: str) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    public_frame = {**frame(), "url": url}
    owner.on_request_will_be_sent(
        with_initiator(request(), {"stack": {"callFrames": [public_frame]}})
    )
    row = owner.diagnostics()[0]
    assert row.public_callsite == "login_submit"
    assert row.initiator_complete is True
    assert len(row.initiator_public_frames) == 1
    fact = row.initiator_public_frames[0]
    assert asdict(fact) == {
        "source_id": "verified_main_bundle",
        "source_query_present": "?" in url,
        "line_0based": 2,
        "utf16_column_0based": 30620,
    }
    encoded = json.dumps(asdict(row))
    assert "fixture-private-query" not in encoded
    assert PUBLIC_BUNDLE not in encoded
    assert "handleLogin" not in encoded
    assert "callFrames" not in encoded
    assert "url" not in encoded
    assert "fixture-private-query" not in repr(vars(owner))
    with pytest.raises(FrozenInstanceError):
        fact.line_0based = 3


@pytest.mark.parametrize(
    "url",
    [
        PUBLIC_BUNDLE.replace("https://", "http://"),
        PUBLIC_BUNDLE.replace("cdn.korail.com", "cdn.korail.com.evil.example"),
        PUBLIC_BUNDLE.replace("cdn.korail.com", "fixture@cdn.korail.com"),
        PUBLIC_BUNDLE.replace("cdn.korail.com", "cdn.korail.com:444"),
        PUBLIC_BUNDLE.replace("cdn.korail.com", "cdn.korail.com:invalid"),
        PUBLIC_BUNDLE.replace("cdn.korail.com", "cdn.korail.com."),
        PUBLIC_BUNDLE.replace("/bundle/bundle.", "/bundle/other."),
        PUBLIC_BUNDLE.replace("/bundle/", "/%62undle/"),
        PUBLIC_BUNDLE + "#",
        PUBLIC_BUNDLE + "?fixture-query#fragment",
        "https://evil.example?source=" + PUBLIC_BUNDLE,
        PUBLIC_BUNDLE_PATH + "/extra?fixture-query",
        PUBLIC_BUNDLE.replace("cdn.korail", "cdn.\nkorail"),
        " " + PUBLIC_BUNDLE,
        PUBLIC_BUNDLE + "?" + "x" * 16384,
    ],
)
def test_query_does_not_expand_verified_source_identity(url: str) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    public_frame = {**frame(), "url": url}
    owner.on_request_will_be_sent(
        with_initiator(request(), {"stack": {"callFrames": [public_frame]}})
    )
    row = owner.diagnostics()[0]
    assert row.public_callsite == "unresolved"
    assert row.initiator_public_frames == ()
    assert url not in json.dumps(asdict(row))


def test_default_tls_port_and_canonical_hostname_are_accepted() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    public_frame = {
        **frame(),
        "url": PUBLIC_BUNDLE.replace("cdn.korail.com", "CDN.KORAIL.COM:443") + "?v=fixture",
    }
    owner.on_request_will_be_sent(
        with_initiator(request(), {"stack": {"callFrames": [public_frame]}})
    )
    assert owner.diagnostics()[0].public_callsite == "login_submit"
    assert owner.diagnostics()[0].initiator_public_frames[0].source_query_present is True


@pytest.mark.parametrize(
    ("line", "column"),
    [
        (-1, 30620),
        (10000, 30620),
        (True, 30620),
        (2.0, 30620),
        (2, -1),
        (2, 6000001),
        (2, True),
        (2, 30620.0),
        (2, float("inf")),
        (2, float("nan")),
        (2, 10**400),
    ],
)
def test_invalid_public_coordinates_remain_unresolved_and_incomplete(line, column) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    public_frame = {**frame(), "lineNumber": line, "columnNumber": column}
    owner.on_request_will_be_sent(
        with_initiator(request(), {"stack": {"callFrames": [public_frame]}})
    )
    row = owner.diagnostics()[0]
    assert row.public_callsite == "unresolved"
    assert row.initiator_complete is False
    assert row.initiator_public_frames == ()
    assert owner.snapshot().state == "posted"


@pytest.mark.parametrize(("line", "column"), [(0, 0), (9999, 6000000), (2, 30619)])
def test_generic_verified_coordinates_are_retained_without_role_inference(line, column) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    public_frame = {**frame(), "lineNumber": line, "columnNumber": column}
    owner.on_request_will_be_sent(
        with_initiator(request(), {"stack": {"callFrames": [public_frame]}})
    )
    row = owner.diagnostics()[0]
    assert row.public_callsite == "unresolved"
    assert row.initiator_complete is True
    assert row.initiator_public_frames[0].line_0based == line
    assert row.initiator_public_frames[0].utf16_column_0based == column


@pytest.mark.parametrize("count", [8, 9])
def test_public_frame_budget_across_stack_parents_is_explicit(count: int) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    frames = [frame(name="callApi") for _ in range(count)]
    stack = {"callFrames": frames[:4], "parent": {"callFrames": frames[4:]}}
    owner.on_request_will_be_sent(with_initiator(request(), {"stack": stack}))
    row = owner.diagnostics()[0]
    assert len(row.initiator_public_frames) == min(count, 8)
    assert row.initiator_public_frames_truncated is (count > 8)
    assert row.initiator_complete is True
    assert row.initiator_completion_reason == "complete"
    assert row.public_callsite == "login_submit"


def test_retention_limit_does_not_hide_late_parent_callsite() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    wrapper = {
        **frame(name="requestWrapper"),
        "lineNumber": 403,
        "columnNumber": 3377,
    }
    stack = {
        "callFrames": [wrapper] * 9,
        "parent": {"callFrames": [frame(30054, name="callApi")]},
    }
    owner.on_request_will_be_sent(with_initiator(request(), {"stack": stack}))
    row = owner.diagnostics()[0]
    assert row.initiator_frame_count == 10
    assert len(row.initiator_public_frames) == 8
    assert row.initiator_public_frames_truncated is True
    assert row.initiator_complete is True
    assert row.initiator_completion_reason == "complete"
    assert row.public_callsite == "login_bootstrap"
    assert row.request_role == "login_bootstrap"
    assert owner.snapshot().state == "missing"


def test_unresolved_parent_id_preserves_only_verified_coordinate_prefix() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    stack = {"callFrames": [frame()], "parentId": {"id": "fixture-private-stack-id"}}
    owner.on_request_will_be_sent(with_initiator(request(), {"stack": stack}))
    row = owner.diagnostics()[0]
    assert row.initiator_complete is False
    assert row.public_callsite == "unresolved"
    assert len(row.initiator_public_frames) == 1
    assert "fixture-private-stack-id" not in json.dumps(asdict(row))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("script", "script"),
        ("parser", "parser"),
        ("preload", "preload"),
        ("SignedExchange", "SignedExchange"),
        ("preflight", "preflight"),
        ("other", "other"),
        ("signedexchange", "unknown"),
        ("fixture-private-kind", "unknown"),
        (None, "unknown"),
        ([], "unknown"),
        ("x" * 1000, "unknown"),
    ],
)
def test_initiator_kind_is_a_closed_cdp_enum(kind, expected) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(
        with_initiator(request(), {"type": kind, "stack": {"callFrames": []}})
    )
    row = owner.diagnostics()[0]
    assert row.initiator_kind == expected
    assert row.initiator_frame_count == 0
    assert row.initiator_source_scopes == ()
    assert row.initiator_complete is True
    assert "fixture-private-kind" not in json.dumps(asdict(row))


@pytest.mark.parametrize(
    ("url", "scope"),
    [
        (PUBLIC_BUNDLE + "?fixture-private-query", "verified_main_bundle"),
        ("https://cdn.korail.com/unknown-main.js", "official_cdn"),
        ("https://CDN.KORAIL.COM:443/unknown-main.js", "official_cdn"),
        ("https://www.korail.com/source.js", "korail_origin"),
        ("https://www.korail.com:443/source.js", "korail_origin"),
        ("https://www.korail.com:444/source.js", "empty_or_invalid"),
        ("https://cdn.korail.com:444/source.js", "empty_or_invalid"),
        ("http://www.korail.com/source.js", "empty_or_invalid"),
        ("https://fixture@cdn.korail.com/source.js", "empty_or_invalid"),
        ("https://cdn.korail.com:invalid/source.js", "empty_or_invalid"),
        ("https://cdn.korail.com.evil.example/source.js", "third_party"),
        ("https://fixture-third-party.example/private-path?fixture-private-query", "third_party"),
        ("http://fixture-third-party.example/source.js", "third_party"),
        ("data:fixture-private-source", "empty_or_invalid"),
        ("", "empty_or_invalid"),
        (None, "empty_or_invalid"),
    ],
)
def test_initiator_scope_never_preserves_url_or_infers_login_role(url, scope) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    observed = {**frame(100, name="callApi"), "url": url}
    owner.on_request_will_be_sent(
        with_initiator(request(), {"type": "script", "stack": {"callFrames": [observed]}})
    )
    row = owner.diagnostics()[0]
    assert row.initiator_kind == "script"
    assert row.initiator_frame_count == 1
    assert row.initiator_source_scopes == (scope,)
    assert row.public_callsite == "unresolved"
    assert row.initiator_handle_login is False
    encoded = json.dumps(asdict(row))
    for private_value in (
        "fixture-private-query",
        "private-path",
        "fixture-third-party.example",
        "source.js",
    ):
        assert private_value not in encoded + repr(vars(owner))


def test_empty_stack_and_unregistered_cdn_script_are_distinct() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(
        with_initiator(request(), {"type": "script", "stack": {"callFrames": []}})
    )
    other = {**frame(name="callApi"), "url": "https://cdn.korail.com/unknown-main.js"}
    owner.on_request_will_be_sent(
        with_initiator(request("second"), {"type": "script", "stack": {"callFrames": [other]}})
    )
    empty, unregistered = owner.diagnostics()
    assert (empty.initiator_frame_count, empty.initiator_source_scopes) == (0, ())
    assert (unregistered.initiator_frame_count, unregistered.initiator_source_scopes) == (
        1,
        ("official_cdn",),
    )
    assert empty.initiator_public_frames == unregistered.initiator_public_frames == ()
    assert empty.public_callsite == unregistered.public_callsite == "unresolved"


@pytest.mark.parametrize("count", [64, 65])
def test_frame_count_is_actual_bounded_work_and_scopes_are_unique(count: int) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    unknown = {**frame(name="callApi"), "url": "https://cdn.korail.com/unknown-main.js"}
    owner.on_request_will_be_sent(
        with_initiator(request(), {"type": "script", "stack": {"callFrames": [unknown] * count}})
    )
    row = owner.diagnostics()[0]
    # Oversized nodes are rejected before visiting any of their frames.
    assert row.initiator_frame_count == (64 if count == 64 else 0)
    assert row.initiator_complete is (count == 64)
    assert row.initiator_completion_reason == ("complete" if count == 64 else "frame_limit")
    assert row.initiator_public_frames_truncated is False
    assert row.initiator_source_scopes == (("official_cdn",) if count == 64 else ())


def test_malformed_frame_and_parent_id_keep_counted_prefix_only() -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(
        with_initiator(request(), {"type": "script", "stack": {"callFrames": [frame(), None]}})
    )
    row = owner.diagnostics()[0]
    assert row.initiator_frame_count == 2
    assert row.initiator_source_scopes == ("verified_main_bundle", "empty_or_invalid")
    assert row.initiator_complete is False
    owner.on_request_will_be_sent(
        with_initiator(
            request("second"),
            {
                "type": "script",
                "stack": {"callFrames": [frame()], "parentId": {"id": "fixture-private-id"}},
            },
        )
    )
    second = owner.diagnostics()[1]
    assert second.initiator_frame_count == 1
    assert second.initiator_source_scopes == ("verified_main_bundle",)
    assert second.initiator_complete is False


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("application/json", "json"),
        ("Application/JSON; charset=UTF-8", "json"),
        ("application/problem+json", "json"),
        ("text/json", "json"),
        (" text/html ; charset=fixture-private-media", "html"),
        ("application/xhtml+xml", "html"),
        ("text/plain", "text"),
        ("text/css", "text"),
        ("application/octet-stream", "other"),
        ("image/png", "other"),
        (None, "unknown"),
        ([], "unknown"),
        ("", "unknown"),
        ("invalid", "unknown"),
        ("text/", "unknown"),
        ("/html", "unknown"),
        ("text/html/extra", "unknown"),
        ("text/ht\nml", "unknown"),
        ("x" * 257, "unknown"),
    ],
)
def test_response_media_is_closed_bounded_and_independent_of_http_failure(mime, expected) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request())
    event = response(500)
    params = event["params"]
    assert isinstance(params, dict)
    data = params["response"]
    assert isinstance(data, dict)
    data["mimeType"] = mime
    data["headers"] = {"Content-Type": "fixture-private-header"}
    owner.on_response_received(event)
    owner.on_loading_finished(terminal())
    row = owner.diagnostics()[0]
    assert row.response_media == expected
    assert (row.status, row.terminal) == (500, "completed")
    assert owner.snapshot().failure == "http_error"
    assert owner.snapshot().safe_to_probe is False
    encoded = json.dumps(asdict(row)) + repr(vars(owner))
    assert "fixture-private-media" not in encoded
    assert "fixture-private-header" not in encoded


@pytest.mark.parametrize(
    "post_data",
    [
        json.dumps(LOGIN_PAYLOAD),
        urlencode(LOGIN_PAYLOAD),
        json.dumps(
            {
                **LOGIN_PAYLOAD,
                "UserId": "fixture-private-quoted-" + '[{"\\' * 100,
            }
        ),
    ],
    ids=["json", "form", "json-quoted-delimiters"],
)
def test_exact_public_login_payload_signature_is_closed_and_diagnostic_only(post_data: str) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(post_data=post_data))
    owner.on_response_received(response(500))
    owner.on_loading_finished(terminal())

    row = owner.diagnostics()[0]
    assert row.payload_role == "login_submission"
    assert row.request_payload_shape == "exact_login"
    assert row.request_role == "unresolved"
    assert owner.authentication_verification_mode() == "blocked"
    retained = json.dumps(asdict(row)) + repr(vars(owner))
    for private_value in (
        value for value in LOGIN_PAYLOAD.values() if value.startswith("fixture-private-")
    ):
        assert private_value not in retained
    assert "fixture-private-quoted" not in retained


@pytest.mark.parametrize(
    ("opener", "closer"),
    [("[", "]"), ('{"nested":', "}")],
    ids=["array", "object"],
)
@pytest.mark.parametrize(
    ("depth", "expected_shape"),
    [(64, "unrelated"), (65, "invalid")],
)
def test_json_request_nesting_has_a_platform_independent_limit(
    opener: str,
    closer: str,
    depth: int,
    expected_shape: str,
) -> None:
    post_data = (
        '{"x":' + opener * (depth - 1) + '"fixture-private-nested"' + closer * (depth - 1) + "}"
    )
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(post_data=post_data))

    row = owner.diagnostics()[0]
    assert row.payload_role == "unresolved"
    assert row.request_payload_shape == expected_shape
    retained = json.dumps(asdict(row)) + repr(vars(owner))
    assert "fixture-private-nested" not in retained


@pytest.mark.parametrize(
    ("post_data", "expected_shape"),
    [
        (None, "absent"),
        (1, "invalid"),
        ("not-form-data", "invalid"),
        (
            json.dumps({key: value for key, value in LOGIN_PAYLOAD.items() if key != "keyname"}),
            "partial_login",
        ),
        (
            json.dumps({**LOGIN_PAYLOAD, "extra": "fixture-private-extra"}),
            "login_with_additions",
        ),
        (urlencode(LOGIN_PAYLOAD) + "&UserId=fixture-private-duplicate", "invalid"),
        ('{"UserId":"first","UserId":"fixture-private-duplicate"}', "invalid"),
        ('{"x":' + "[" * 5000 + "0" + "]" * 5000 + "}", "invalid"),
        ("x=" + "fixture-private-oversize" * 4000, "invalid"),
    ],
    ids=(
        "missing",
        "non-string",
        "malformed-form",
        "missing-field",
        "extra-field",
        "duplicate-form-field",
        "duplicate-json-field",
        "excessive-json-depth",
        "oversize",
    ),
)
def test_non_exact_payload_never_claims_login_role_or_retains_values(
    post_data: object,
    expected_shape: str,
) -> None:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(request(post_data=post_data))

    row = owner.diagnostics()[0]
    assert row.payload_role == "unresolved"
    assert row.request_payload_shape == expected_shape
    retained = json.dumps(asdict(row)) + repr(vars(owner))
    for private_value in ("fixture-private-extra", "fixture-private-duplicate", "oversize"):
        assert private_value not in retained


def _completed_json_login_owner(
    *,
    recognized: bool = True,
    encoded_data_length: object = 512,
) -> PydollLoginSubmission:
    owner = PydollLoginSubmission(10)
    owner.arm()
    owner.on_request_will_be_sent(
        request(post_data=json.dumps(LOGIN_PAYLOAD) if recognized else "{}")
    )
    event = response()
    params = event["params"]
    assert isinstance(params, dict)
    response_data = params["response"]
    assert isinstance(response_data, dict)
    response_data["mimeType"] = "application/json; charset=utf-8"
    owner.on_response_received(event)
    owner.on_loading_finished(terminal(encoded_data_length=encoded_data_length))
    return owner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "recognized", "expected"),
    [
        pytest.param(
            '{"strResult":"SUCC","h_msg_cd":"IRZ000001",'
            '"hc14100En":{"strCustNo":"fixture-private-customer"}}',
            True,
            "accepted",
            id="accepted",
        ),
        pytest.param(
            json.dumps(
                {
                    "strResult": "SUCC",
                    "h_msg_cd": "IRZ000001",
                    "hc14100En": {"strCustNo": "fixture-private-customer"},
                    "h_msg_txt": "fixture-private-quoted-" + '[{"\\' * 100,
                }
            ),
            True,
            "accepted",
            id="quoted-delimiters",
        ),
        pytest.param(
            '{"strResult":"FAIL","h_msg_cd":"fixture-private-code",'
            '"h_msg_txt":"fixture-private-message"}',
            True,
            "rejected",
            id="failed-result",
        ),
        pytest.param(
            '{"strResult":"SUCC","h_msg_cd":"fixture-private-code",'
            '"hc14100En":{"strCustNo":"fixture-private-customer"}}',
            True,
            "rejected",
            id="rejected-code",
        ),
        pytest.param(
            '{"strResult":"SUCC","h_msg_cd":"","S_PASSCHG_YN":"Y",'
            '"hc14100En":{"strCustNo":"fixture-private-customer"}}',
            True,
            "challenge",
            id="password-change",
        ),
        pytest.param(
            '{"strResult":"SUCC","h_msg_cd":"IRZ000001",'
            '"hc14100En":{"strNotiTpCd":"MC",'
            '"strCustNo":"fixture-private-customer"}}',
            True,
            "challenge",
            id="notification-challenge",
        ),
        pytest.param(
            '{"dynaPathResultCode":"fixture-private-result",'
            '"errCode":"macro_err1","errEngMsg":"fixture-private-english",'
            '"errMsg":"fixture-private-message","globalFlag":"fixture-private-flag",'
            '"requestId":"fixture-private-request"}',
            False,
            "protection",
            id="macro-protection",
        ),
        pytest.param(
            '{"strResult":"SUCC","h_msg_cd":""}',
            False,
            "not_login_result",
            id="generic-success",
        ),
        pytest.param(
            '{"strResult":"SUCC","h_msg_cd":"",'
            '"hc14100En":{"strCustNo":"first","strCustNo":"fixture-private-duplicate"}}',
            True,
            "unavailable",
            id="duplicate-key",
        ),
        pytest.param(
            "fixture-private-invalid-json",
            True,
            "unavailable",
            id="invalid-json",
        ),
        pytest.param(
            "[" * 5000 + "0" + "]" * 5000,
            True,
            "unavailable",
            id="excessive-json-depth",
        ),
    ],
)
async def test_completed_response_body_is_reduced_to_one_closed_outcome(
    body: str,
    recognized: bool,
    expected: str,
) -> None:
    owner = _completed_json_login_owner(recognized=recognized)
    commands: list[dict[str, object]] = []

    async def execute(command: dict[str, object]) -> object:
        commands.append(command)
        return {"result": {"body": body, "base64Encoded": False}}

    assert await owner.classify_completed_response(execute) == expected
    assert await owner.classify_completed_response(execute) == expected
    assert len(commands) == 1
    row = owner.diagnostics()[0]
    assert row.response_auth_outcome == expected
    # Only KORAIL's closed error code survives; every other response value stays private.
    assert row.response_err_codes == (("macro_err1",) if expected == "protection" else ())
    assert owner.authentication_verification_mode() == "standard"
    retained = json.dumps(asdict(row)) + repr(vars(owner))
    for private_value in (
        "fixture-private-customer",
        "fixture-private-code",
        "fixture-private-message",
        "fixture-private-duplicate",
        "fixture-private-invalid-json",
        "fixture-private-result",
        "fixture-private-english",
        "fixture-private-request",
        "fixture-private-quoted",
    ):
        assert private_value not in retained


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("opener", "closer"),
    [("[", "]"), ('{"nested":', "}")],
    ids=["array", "object"],
)
@pytest.mark.parametrize(
    ("depth", "expected"),
    [(64, "accepted"), (65, "unavailable")],
)
async def test_json_response_nesting_has_a_platform_independent_limit(
    opener: str,
    closer: str,
    depth: int,
    expected: str,
) -> None:
    body = (
        '{"strResult":"SUCC","h_msg_cd":"IRZ000001",'
        '"hc14100En":{"strCustNo":"fixture-private-customer"},"extra":'
        + opener * (depth - 1)
        + '"fixture-private-nested"'
        + closer * (depth - 1)
        + "}"
    )
    owner = _completed_json_login_owner()

    async def execute(_command: dict[str, object]) -> object:
        return {"result": {"body": body, "base64Encoded": False}}

    assert await owner.classify_completed_response(execute) == expected
    row = owner.diagnostics()[0]
    assert row.response_auth_outcome == expected
    retained = json.dumps(asdict(row)) + repr(vars(owner))
    assert "fixture-private-customer" not in retained
    assert "fixture-private-nested" not in retained


@pytest.mark.asyncio
async def test_unknown_json_retains_only_bounded_field_shape_and_discards_values() -> None:
    owner = _completed_json_login_owner(recognized=False)
    body = json.dumps(
        {
            "alpha": "fixture-private-top-level",
            "nested": {
                "beta": "fixture-private-nested",
                "gamma": 17,
            },
        }
    )

    async def execute(_: dict[str, object]) -> object:
        return {"result": {"body": body, "base64Encoded": False}}

    assert await owner.classify_completed_response(execute) == "not_login_result"
    row = owner.diagnostics()[0]
    assert row.response_schema_fields == ("alpha", "nested", "nested.beta", "nested.gamma")
    retained = json.dumps(asdict(row)) + repr(vars(owner))
    assert "fixture-private-top-level" not in retained
    assert "fixture-private-nested" not in retained


@pytest.mark.asyncio
async def test_base64_response_body_is_classified_without_retaining_material() -> None:
    owner = _completed_json_login_owner()
    body = (
        b'{"strResult":"SUCC","h_msg_cd":"","hc14100En":{"strCustNo":"fixture-private-customer"}}'
    )

    async def execute(_: dict[str, object]) -> object:
        return {
            "result": {
                "body": base64.b64encode(body).decode("ascii"),
                "base64Encoded": True,
            }
        }

    assert await owner.classify_completed_response(execute) == "accepted"
    assert "fixture-private-customer" not in repr(vars(owner))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded_data_length",
    [None, True, -1, 65_537, float("nan"), 10**400],
)
async def test_missing_invalid_or_oversize_finished_length_blocks_body_read(
    encoded_data_length: object,
) -> None:
    owner = _completed_json_login_owner(encoded_data_length=encoded_data_length)
    called = False

    async def execute(_: dict[str, object]) -> object:
        nonlocal called
        called = True
        raise AssertionError("ineligible response body must not be read")

    assert await owner.classify_completed_response(execute) == "unavailable"
    assert called is False
    assert owner.diagnostics()[0].response_auth_outcome == "unavailable"
