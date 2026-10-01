from __future__ import annotations

from dataclasses import dataclass

import pytest

from rail_waitlist.korail_sidecar.pydoll.login_submission import (
    PydollLoginResponseUnavailable,
    PydollLoginSubmission,
)


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
) -> dict[str, object]:
    return {
        "params": {
            "requestId": request_id,
            "type": resource_type,
            "request": {"url": url, "method": method},
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


def terminal(request_id: str = "login-1") -> dict[str, object]:
    return {"params": {"requestId": request_id}}


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
