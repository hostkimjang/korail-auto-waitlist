from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict, dataclass

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


PUBLIC_BUNDLE = "https://cdn.korail.com/bundle/bundle.38e6dfeb5a3af0094a69.js"


def frame(column: object = 30620, *, name: str = "handleLogin") -> dict[str, object]:
    return {"functionName": name, "url": PUBLIC_BUNDLE, "lineNumber": 2, "columnNumber": column}


def with_initiator(event: dict[str, object], initiator: object) -> dict[str, object]:
    params = event["params"]
    assert isinstance(params, dict)
    params["initiator"] = initiator
    return event


@pytest.mark.parametrize(
    ("path", "family"),
    [
        ("/ebizweb/common/loginProcess", "public_login"),
        ("/ebizweb/integrate/srCheck.do", "integration_check"),
        ("/web_s/fixture-private-path", "business_dynamic"),
        ("/web_s", "official_other"),
        ("/ebizweb/common/loginProcess/extra", "official_other"),
        ("/ebizweb/common/LoginProcess", "official_other"),
        ("/fixture-private-path", "official_other"),
    ],
)
def test_request_diagnostics_classify_only_path_family_and_keep_no_material(path, family) -> None:
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
    assert row.initiator_handle_login is False
    assert owner.snapshot().state == "posted"


@pytest.mark.parametrize(
    "changes",
    [
        {"url": PUBLIC_BUNDLE + "#fixture-fragment"},
        {"url": PUBLIC_BUNDLE.replace("38e6", "ffff")},
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
        PUBLIC_BUNDLE + "/extra?fixture-query",
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
    assert row.initiator_complete is (count <= 8)
    assert row.public_callsite == ("login_submit" if count <= 8 else "unresolved")


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
