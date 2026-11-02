from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

import rail_waitlist.korail_sidecar.pydoll.reservation_list_response as response_module
from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserLocalReadRejected,
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollReservationListSnapshot
from rail_waitlist.korail_sidecar.pydoll.reservation_list_response import (
    ReservationListResponseObserver,
    ReservationListResponseSnapshot,
    observe_reservation_list_response,
    read_reservation_list,
)

LIST = "https://www.korail.com/ticket/reservation/list"
VIEW = "https://www.korail.com/classes/com.korail.mobile.reservation.ReservationView"
EMPTY = {"strResult": "SUCC", "h_msg_cd": "", "jrny_infos": {"jrny_info": []}}
P100_EMPTY = {**EMPTY, "h_msg_cd": "P100", "h_msg_txt": "검색된 데이터가 없습니다."}
FRAME_TREE = {"result": {"frameTree": {"frame": {"id": "main", "loaderId": "previous"}}}}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class Tab:
    def __init__(self, *, enabled: bool = False) -> None:
        self.network_events_enabled = enabled
        self.callbacks: dict[int, Callable[[dict[str, Any]], None]] = {}
        self.events: list[str] = []
        self.commands: list[object] = []
        self.frame_tree: object = FRAME_TREE
        self.body: object = {"result": {"body": json.dumps(EMPTY), "base64Encoded": False}}
        self.fail_on: int | None = None
        self.enable_error = False
        self.body_error = False
        self.body_hook: Callable[[], None] | None = None
        self.go_to = AsyncMock()

    async def enable_network_events(self) -> None:
        self.events.append("enable")
        self.network_events_enabled = True
        if self.enable_error:
            raise RuntimeError("fixture-private-error")

    async def disable_network_events(self) -> None:
        self.events.append("disable")
        self.network_events_enabled = False

    async def on(self, event: object, callback: Callable[[dict[str, Any]], None]) -> int:
        callback_id = len(self.callbacks) + 1
        if callback_id == self.fail_on:
            raise RuntimeError("fixture-private-error")
        self.callbacks[callback_id] = callback
        return callback_id

    async def remove_callback(self, callback_id: int) -> None:
        self.events.append(f"remove:{callback_id}")
        del self.callbacks[callback_id]

    async def _execute_command(self, command: Any) -> object:
        self.commands.append(command)
        if command["method"] == "Page.getFrameTree":
            return self.frame_tree
        assert command["method"] == "Network.getResponseBody"
        if self.body_hook is not None:
            self.body_hook()
        if self.body_error:
            raise RuntimeError("fixture-private-error")
        return self.body

    def emit_cycle(self, *, finish: bool = True) -> None:
        self.callbacks[1](request(document=True))
        self.callbacks[1](request())
        self.callbacks[2](response())
        if finish:
            self.callbacks[3](finished())


def request(*, document: bool = False, **changes: object) -> dict[str, Any]:
    params: dict[str, Any] = {
        "requestId": "navigation" if document else "list-read",
        "frameId": "main",
        "loaderId": "current",
        "wallTime": 100.0,
        "timestamp": 10.0 if document else 11.0,
        "type": "Document" if document else "XHR",
        "documentURL": LIST,
        "request": {"method": "GET", "url": LIST if document else VIEW},
    }
    params.update(changes)
    return {"params": params}


def response(**changes: object) -> dict[str, Any]:
    params: dict[str, Any] = {
        "requestId": "list-read",
        "frameId": "main",
        "loaderId": "current",
        "type": "XHR",
        "timestamp": 12.0,
        "response": {"status": 200, "url": VIEW, "mimeType": "application/json"},
    }
    params.update(changes)
    return {"params": params}


def finished(**changes: object) -> dict[str, Any]:
    params: dict[str, Any] = {
        "requestId": "list-read",
        "timestamp": 13.0,
        "encodedDataLength": 150,
    }
    params.update(changes)
    return {"params": params}


def armed(clock: Clock | None = None) -> ReservationListResponseObserver:
    owner = ReservationListResponseObserver(10, monotonic=clock or Clock(), wall_time=lambda: 100)
    owner.arm(FRAME_TREE)
    owner.on_request(request(document=True))
    return owner


def complete(owner: ReservationListResponseObserver) -> None:
    owner.on_request(request())
    owner.on_response(response())
    owner.on_finished(finished())


@pytest.fixture(autouse=True)
def stub_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        response_module, "_events", lambda: ("request", "response", "finish", "fail")
    )


@pytest.mark.parametrize("base64_encoded", [False, True])
@pytest.mark.parametrize("code", [None, ""])
@pytest.mark.parametrize("message", [None, ""])
async def test_completed_natural_empty_response_is_closed_metadata_only(
    base64_encoded: bool,
    code: str | None,
    message: str | None,
) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    payload = {**EMPTY, "h_msg_cd": code, "h_msg_txt": message}
    raw = json.dumps(payload)
    tab.body = {
        "result": {
            "body": base64.b64encode(raw.encode()).decode() if base64_encoded else raw,
            "base64Encoded": base64_encoded,
        }
    }
    await owner.read_completed_body(tab)
    assert owner.snapshot().state == "empty"
    assert owner.snapshot().failure is None
    assert "jrny_infos" not in repr(vars(owner))
    assert tab.commands == [
        {"method": "Network.getResponseBody", "params": {"requestId": "list-read"}}
    ]


@pytest.mark.parametrize("base64_encoded", [False, True])
async def test_observed_p100_exact_success_message_and_empty_rows_are_accepted(
    base64_encoded: bool,
) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    raw = json.dumps(P100_EMPTY)
    tab.body = {
        "result": {
            "body": base64.b64encode(raw.encode()).decode() if base64_encoded else raw,
            "base64Encoded": base64_encoded,
        }
    }

    await owner.read_completed_body(tab)

    assert owner.snapshot().state == "empty"
    assert owner.snapshot().failure is None
    assert "P100" not in repr(vars(owner))
    assert "검색된 데이터" not in repr(vars(owner))
    assert "jrny_infos" not in repr(vars(owner))


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ({"strResult": "FAIL"}, "invalid_response"),
        ({"strResult": "succ"}, "invalid_response"),
        ({"h_msg_cd": "P058"}, "auth_required"),
        ({"h_msg_cd": "P101"}, "invalid_response"),
        ({"h_msg_cd": "P100 "}, "invalid_response"),
        ({"h_msg_cd": "p100"}, "invalid_response"),
        ({"h_msg_cd": ""}, "invalid_response"),
        ({"h_msg_cd": None}, "invalid_response"),
        ({"h_msg_txt": None}, "invalid_response"),
        ({"h_msg_txt": ""}, "invalid_response"),
        ({"h_msg_txt": "검색된 데이터가 없습니다"}, "invalid_response"),
        ({"h_msg_txt": " 검색된 데이터가 없습니다."}, "invalid_response"),
        ({"h_msg_txt": "다른 공개 안내"}, "invalid_response"),
        ({"h_msg_txt": []}, "invalid_response"),
        ({"jrny_infos": {"jrny_info": [{}]}}, "invalid_response"),
        ({"jrny_infos": {"jrny_info": None}}, "invalid_response"),
        ({"jrny_infos": {"jrny_info": {}}}, "invalid_response"),
    ],
)
async def test_p100_contract_mutations_cannot_confirm_absence(
    mutation: dict[str, object], failure: str
) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body = {"result": {"body": json.dumps({**P100_EMPTY, **mutation}), "base64Encoded": False}}

    await owner.read_completed_body(tab)

    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure == failure


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"strResult": "FAIL", "h_msg_cd": "P058"}, "auth_required"),
        ({**EMPTY, "h_msg_cd": "WRT300004"}, "invalid_response"),
        ({**EMPTY, "h_msg_cd": []}, "invalid_response"),
        ({**EMPTY, "strResult": "FAIL"}, "invalid_response"),
        ({"strResult": "SUCC"}, "invalid_response"),
        ({**EMPTY, "jrny_infos": None}, "invalid_response"),
        ({**EMPTY, "jrny_infos": {"jrny_info": None}}, "invalid_response"),
        ({**EMPTY, "jrny_infos": {"jrny_info": {}}}, "invalid_response"),
        ([], "invalid_response"),
    ],
)
async def test_real_auth_loss_and_malformed_response_cannot_confirm_empty(
    payload: object,
    expected: str,
) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body = {"result": {"body": json.dumps(payload), "base64Encoded": False}}
    await owner.read_completed_body(tab)
    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure == expected


async def test_nonempty_response_keeps_no_private_rows() -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body = {
        "result": {
            "body": json.dumps(
                {
                    **EMPTY,
                    "jrny_infos": {"jrny_info": [{"fixture_marker": "unretained"}]},
                }
            ),
            "base64Encoded": False,
        }
    }
    await owner.read_completed_body(tab)
    assert owner.snapshot().state == "nonempty"
    assert "unretained" not in repr(vars(owner))


def _log_fields(record: logging.LogRecord) -> dict[str, str]:
    return dict(part.split("=", 1) for part in record.getMessage().split() if "=" in part)


NONEMPTY_WITH_NOTICE: dict[str, object] = {
    "strResult": "SUCC",
    "h_msg_cd": "IRG000000",
    "h_msg_txt": "fixture-private-notice",
    "jrny_infos": {"jrny_info": [{"fixture_marker": "fixture-private-row"}]},
}


@pytest.mark.parametrize(
    ("code", "message", "logged"),
    [
        ("IRG000000", None, {"msg_code": "IRG000000", "msg_text": "absent"}),
        ("IRZ000001", "fixture-private-notice", {"msg_code": "IRZ000001", "msg_text": "present"}),
        ("", "fixture-private-notice", {"msg_code": "empty", "msg_text": "present"}),
        (None, "", None),
    ],
)
async def test_nonempty_response_accepts_informational_notice_without_retention(
    code: str | None,
    message: str | None,
    logged: dict[str, str] | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    payload = {**NONEMPTY_WITH_NOTICE, "h_msg_cd": code, "h_msg_txt": message}
    tab.body = {"result": {"body": json.dumps(payload), "base64Encoded": False}}

    with caplog.at_level("INFO", logger=response_module.__name__):
        await owner.read_completed_body(tab)

    assert owner.snapshot().state == "nonempty"
    assert owner.snapshot().failure is None
    retained = repr(vars(owner))
    assert "fixture-private" not in retained
    assert "jrny_infos" not in retained
    assert not code or code not in retained
    assert "fixture-private" not in caplog.text
    records = [record for record in caplog.records if record.name == response_module.__name__]
    if logged is None:
        assert not records
        return
    assert len(records) == 1
    fields = _log_fields(records[0])
    assert fields["event"] == "reservation_list_response_accepted"
    assert fields["state"] == "nonempty"
    assert fields["rows"] == "list:1"
    for key, value in logged.items():
        assert fields[key] == value


@pytest.mark.parametrize(
    ("mutation", "reason", "projection"),
    [
        ({"h_msg_cd": "P100"}, "message_contract", {"msg_code": "P100"}),
        ({"h_msg_cd": "WRT300004"}, "message_contract", {"msg_code": "WRT300004"}),
        ({"h_msg_cd": "IRG00000"}, "message_contract", {"msg_code": "IRG00000"}),
        ({"h_msg_cd": "irg000000"}, "message_contract", {"msg_code": "unrecognized"}),
        ({"h_msg_cd": "fixture-private-code"}, "message_contract", {"msg_code": "unrecognized"}),
        ({"h_msg_cd": ["IRG000000"]}, "message_contract", {"msg_code": "unrecognized"}),
        (
            {"h_msg_txt": ["fixture-private-notice"]},
            "message_contract",
            {"msg_text": "unrecognized"},
        ),
        (
            {"jrny_infos": {"jrny_info": ["fixture-private-row"]}},
            "rows_malformed",
            {"rows": "list:1"},
        ),
        (
            {"jrny_infos": {"jrny_info": {"row": "fixture-private-row"}}},
            "rows_not_list",
            {"rows": "object"},
        ),
        (
            {"strResult": "fixture-private-result"},
            "result_not_success",
            {"result": "unrecognized"},
        ),
        ({"strResult": ["SUCC"]}, "result_not_success", {"result": "unrecognized"}),
    ],
)
async def test_nonempty_contract_rejection_logs_closed_projection_only(
    mutation: dict[str, object],
    reason: str,
    projection: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body = {
        "result": {"body": json.dumps({**NONEMPTY_WITH_NOTICE, **mutation}), "base64Encoded": False}
    }

    with caplog.at_level("INFO", logger=response_module.__name__):
        await owner.read_completed_body(tab)

    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure == "invalid_response"
    records = [record for record in caplog.records if record.name == response_module.__name__]
    assert len(records) == 1
    fields = _log_fields(records[0])
    assert fields["event"] == "reservation_list_response_rejected"
    assert fields["reason"] == reason
    for key, value in projection.items():
        assert fields[key] == value
    assert "fixture-private" not in caplog.text
    assert "jrny_infos" not in caplog.text
    assert "fixture-private" not in repr(vars(owner))


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_http_failure_does_not_read_body(status: int) -> None:
    owner = armed()
    owner.on_request(request())
    owner.on_response(
        response(response={"status": status, "url": VIEW, "mimeType": "application/json"})
    )
    owner.on_finished(finished())
    tab = Tab()
    await owner.read_completed_body(tab)
    assert (
        owner.snapshot().failure
        == {403: "protection", 429: "rate_limited", 500: "http_error"}[status]
    )
    assert tab.commands == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"frameId": "subframe"},
        {"loaderId": "previous"},
        {"documentURL": LIST + "?old=1"},
        {"type": "Image"},
        {"wallTime": 99.0},
        {"timestamp": 9.0},
        {"request": {"method": "POST", "url": VIEW}},
        {"request": {"method": "GET", "url": "http://www.korail.com" + VIEW.split(".com")[1]}},
        {"request": {"method": "GET", "url": VIEW.replace("www.korail.com", "other.invalid")}},
        {
            "request": {
                "method": "GET",
                "url": VIEW.replace("www.korail.com", "member@www.korail.com"),
            }
        },
        {"request": {"method": "GET", "url": VIEW + "#fragment"}},
    ],
)
async def test_unrelated_or_stale_requests_are_ignored(mutation: dict[str, object]) -> None:
    owner = armed()
    owner.on_request(request(**mutation))
    owner.on_response(response())
    owner.on_finished(finished())
    tab = Tab()
    await owner.read_completed_body(tab)
    assert owner.snapshot().state == "missing"
    assert tab.commands == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"loaderId": "previous"},
        {"frameId": "subframe"},
        {"timestamp": 10.0},
        {"response": {"status": 200, "url": VIEW, "mimeType": "text/plain"}},
        {
            "response": {
                "status": 200,
                "url": VIEW,
                "mimeType": "application/json",
                "fromDiskCache": True,
            }
        },
        {
            "response": {
                "status": 200,
                "url": VIEW,
                "mimeType": "application/json",
                "fromServiceWorker": True,
            }
        },
    ],
)
async def test_invalid_or_cached_response_is_closed(mutation: dict[str, object]) -> None:
    owner = armed()
    owner.on_request(request())
    owner.on_response(response(**mutation))
    owner.on_finished(finished())
    tab = Tab()
    await owner.read_completed_body(tab)
    assert owner.snapshot().failure == "invalid_response"
    assert tab.commands == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"encodedDataLength": None}, "body_too_large"),
        ({"encodedDataLength": -1}, "body_too_large"),
        ({"encodedDataLength": 65537}, "body_too_large"),
        ({"timestamp": 10.5}, "invalid_response"),
    ],
)
async def test_invalid_finish_never_reads_body(mutation: dict[str, object], expected: str) -> None:
    owner = armed()
    owner.on_request(request())
    owner.on_response(response())
    owner.on_finished(finished(**mutation))
    tab = Tab()
    await owner.read_completed_body(tab)
    assert owner.snapshot().failure == expected
    assert tab.commands == []


async def test_zero_encoded_length_still_requires_and_validates_actual_body() -> None:
    owner = armed()
    owner.on_request(request())
    owner.on_response(response())
    owner.on_finished(finished(encodedDataLength=0))
    await owner.read_completed_body(Tab())
    assert owner.snapshot().state == "empty"


async def test_finished_without_response_or_inflight_does_not_read_body() -> None:
    owner = armed()
    owner.on_request(request())
    tab = Tab()
    await owner.read_completed_body(tab)
    assert owner.snapshot().state == "in_flight"
    owner.on_finished(finished())
    await owner.read_completed_body(tab)
    assert owner.snapshot().failure == "invalid_response"
    assert tab.commands == []


@pytest.mark.parametrize(
    "body",
    [
        {"result": {"body": "<html>error</html>", "base64Encoded": False}},
        {"result": {"body": "{", "base64Encoded": False}},
        {
            "result": {
                "body": '{"strResult":"FAIL","strResult":"SUCC","jrny_infos":{"jrny_info":[]}}',
                "base64Encoded": False,
            }
        },
        {"result": {"body": "!bad", "base64Encoded": True}},
        {"result": {"body": "x" * 65537, "base64Encoded": False}},
        {"result": {"body": "가" * 30000, "base64Encoded": False}},
        {"result": {"body": "{}"}},
    ],
)
async def test_body_bounds_and_json_shape_fail_closed(body: object) -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body = body
    await owner.read_completed_body(tab)
    assert owner.snapshot().state == "failed"
    assert owner.snapshot().failure in {"body_unavailable", "body_too_large", "invalid_response"}


async def test_read_error_is_closed_and_does_not_keep_exception_text() -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body_error = True
    await owner.read_completed_body(tab)
    assert owner.snapshot().failure == "body_unavailable"
    assert "fixture-private-error" not in repr(vars(owner))


async def test_distinct_request_or_navigation_invalidates_even_completed_empty() -> None:
    owner = armed()
    complete(owner)
    await owner.read_completed_body(Tab())
    owner.on_request(request())
    assert owner.snapshot().state == "empty"
    owner.on_request(request(requestId="second-read"))
    assert owner.snapshot().state == "ambiguous"
    other = armed()
    complete(other)
    await other.read_completed_body(Tab())
    other.on_request(request(document=True, requestId="second-navigation", loaderId="next"))
    assert other.snapshot().failure == "invalid_navigation"


async def test_response_duplicates_and_network_failure_cannot_confirm_empty() -> None:
    owner = armed()
    owner.on_request(request())
    owner.on_response(response())
    owner.on_response(response())
    owner.on_finished(finished())
    assert owner.snapshot().failure == "invalid_response"
    failed = armed()
    failed.on_request(request())
    failed.on_failed({"params": {"requestId": "other", "timestamp": 12}})
    assert failed.snapshot().state == "in_flight"
    failed.on_failed(
        {
            "params": {
                "requestId": "list-read",
                "timestamp": 12,
                "errorText": "fixture-private-error",
            }
        }
    )
    assert failed.snapshot().failure == "network_error"
    assert "fixture-private-error" not in repr(vars(failed))


async def test_read_window_expires_inflight_but_completed_result_survives_until_close() -> None:
    clock = Clock()
    owner = armed(clock)
    complete(owner)
    await owner.read_completed_body(Tab())
    clock.now = 20
    assert owner.snapshot().state == "empty"
    owner.close()
    assert owner.snapshot().failure == "closed"
    expired = armed(clock)
    expired.on_request(request())
    clock.now = 31
    assert expired.snapshot().failure == "timeout"


@pytest.mark.parametrize(
    "frame_tree",
    [
        None,
        {},
        {"frameTree": {}},
        {
            "result": {
                "frameTree": {"frame": {"id": "main", "loaderId": "previous", "parentId": "outer"}}
            }
        },
    ],
)
def test_frame_tree_must_be_the_actual_nested_cdp_main_frame(frame_tree: object) -> None:
    owner = ReservationListResponseObserver(10)
    owner.arm(frame_tree)
    assert owner.snapshot().failure == "observer_unavailable"


@pytest.mark.parametrize("enabled", [False, True])
async def test_context_registers_all_events_before_read_and_preserves_network_owner(
    enabled: bool,
) -> None:
    tab = Tab(enabled=enabled)
    async with observe_reservation_list_response(tab, 10, wall_time=lambda: 100) as owner:
        assert len(tab.callbacks) == 4
        assert tab.commands == [{"method": "Page.getFrameTree"}]
        tab.emit_cycle()
        await owner.read_completed_body(tab)
        assert owner.snapshot().state == "empty"
    assert owner.snapshot().failure == "closed"
    assert tab.callbacks == {}
    assert tab.network_events_enabled is enabled
    assert ("disable" in tab.events) is not enabled


@pytest.mark.parametrize("fail_on", [1, 2, 3, 4])
async def test_partial_registration_cannot_create_proof_and_cleans_every_callback(
    fail_on: int,
) -> None:
    tab = Tab()
    tab.fail_on = fail_on
    async with observe_reservation_list_response(tab, 10) as owner:
        assert owner.snapshot().failure == "observer_unavailable"
    assert tab.callbacks == {}
    assert tab.network_events_enabled is False


async def test_partial_network_enable_and_body_cancellation_clean_original_tab() -> None:
    tab = Tab()
    tab.enable_error = True
    async with observe_reservation_list_response(tab, 10) as owner:
        assert owner.snapshot().failure == "observer_unavailable"
    assert tab.network_events_enabled is False
    tab = Tab(enabled=True)
    entered = asyncio.Event()

    async def operation() -> None:
        async with observe_reservation_list_response(tab, 10):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.callbacks == {}
    assert tab.network_events_enabled is True


async def test_attachment_cancellation_and_repeated_cleanup_cancellation_are_owned() -> None:
    attaching = asyncio.Event()

    class AttachingTab(Tab):
        async def on(self, event: object, callback: Callable[[dict[str, Any]], None]) -> int:
            if len(self.callbacks) == 2:
                attaching.set()
                await asyncio.Event().wait()
            return await super().on(event, callback)

    tab = AttachingTab()

    async def attachment() -> None:
        async with observe_reservation_list_response(tab, 10):
            pytest.fail("Attachment cancellation must not yield")

    task = asyncio.create_task(attachment())
    await attaching.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.callbacks == {} and not tab.network_events_enabled

    cleaning = asyncio.Event()
    release = asyncio.Event()

    class CleanupTab(Tab):
        async def remove_callback(self, callback_id: int) -> None:
            cleaning.set()
            await release.wait()
            await super().remove_callback(callback_id)

    tab = CleanupTab()

    async def cleanup() -> None:
        async with observe_reservation_list_response(tab, 10):
            pass

    task = asyncio.create_task(cleanup())
    await cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.callbacks == {} and not tab.network_events_enabled


@pytest.mark.parametrize("payload", [EMPTY, P100_EMPTY])
async def test_natural_response_empty_requires_two_stable_eligible_dom_reads(
    payload: dict[str, object],
) -> None:
    clock = Clock()
    tab = Tab()
    tab.body = {"result": {"body": json.dumps(payload), "base64Encoded": False}}
    loading = PydollReservationListSnapshot(LIST, page_marker_visible=True, loading_visible=True)
    empty = PydollReservationListSnapshot(LIST, page_marker_visible=True)
    snapshots = AsyncMock(side_effect=[loading, empty, empty])

    async def navigate(url: str, *, timeout: int) -> None:
        assert url == LIST and timeout == 30
        tab.emit_cycle()
        clock.now += 12  # Preserve the normal page navigation budget before the read window.

    result = await read_reservation_list(
        tab=tab,
        current_tab=lambda: tab,
        navigate=navigate,
        snapshot=snapshots,
        timeout_seconds=1,
        navigation_timeout_seconds=30,
        monotonic=clock,
        wall_time=lambda: 100,
        sleep=clock.sleep,
    )
    assert result.empty_response_provenance == "official_response"
    assert result.official_read_completed
    assert snapshots.await_count == 3
    assert [command["method"] for command in tab.commands] == [
        "Page.getFrameTree",
        "Network.getResponseBody",
    ]
    assert tab.callbacks == {}


@pytest.mark.parametrize(
    "mode", ["inflight", "auth_loss", "duplicate", "loading", "missing_marker", "tab_change"]
)
async def test_initial_empty_dom_does_not_manufacture_absence(mode: str) -> None:
    clock = Clock()
    tab = Tab()
    selected = [tab]
    empty = PydollReservationListSnapshot(
        LIST,
        page_marker_visible=mode != "missing_marker",
        loading_visible=mode == "loading",
    )
    if mode == "auth_loss":
        tab.body = {
            "result": {
                "body": json.dumps({"strResult": "FAIL", "h_msg_cd": "P058"}),
                "base64Encoded": False,
            }
        }
    if mode == "tab_change":
        tab.body_hook = lambda: selected.__setitem__(0, Tab())

    async def navigate(url: str, *, timeout: int) -> None:
        tab.emit_cycle(finish=mode != "inflight")
        if mode == "duplicate":
            tab.callbacks[1](request(requestId="second-read"))

    async def read() -> PydollReservationListSnapshot:
        return await read_reservation_list(
            tab=tab,
            current_tab=lambda: selected[0],
            navigate=navigate,
            snapshot=AsyncMock(return_value=empty),
            timeout_seconds=0.5,
            monotonic=clock,
            wall_time=lambda: 100,
            sleep=clock.sleep,
        )

    if mode in {"auth_loss", "duplicate"}:
        with pytest.raises(BrowserSourceUnavailable) as error:
            await read()
        assert error.value.stage == "confirmation_reservation_list_response"
    else:
        result = await read()
        assert result.empty_response_provenance == "not_observed"
        assert not result.official_read_completed
    assert tab.callbacks == {}


@pytest.mark.parametrize(
    "mode",
    [
        "auth_loss",
        "http500",
        "malformed",
        "duplicate",
        "nonempty",
        "inflight",
        "http403",
        "http429",
        "missing_id",
        "redirect",
    ],
)
async def test_observed_failure_or_pending_read_suppresses_legacy_explicit_empty(mode: str) -> None:
    clock = Clock()
    tab = Tab()
    empty = PydollReservationListSnapshot(
        LIST, page_marker_visible=True, explicit_empty_visible=True
    )
    if mode == "auth_loss":
        tab.body = {
            "result": {
                "body": json.dumps({"strResult": "FAIL", "h_msg_cd": "P058"}),
                "base64Encoded": False,
            }
        }
    elif mode == "malformed":
        tab.body = {"result": {"body": "<html>error</html>", "base64Encoded": False}}
    elif mode == "nonempty":
        tab.body = {
            "result": {
                "body": json.dumps({**EMPTY, "jrny_infos": {"jrny_info": [{}]}}),
                "base64Encoded": False,
            }
        }

    async def navigate(url: str, *, timeout: int) -> None:
        tab.callbacks[1](request(document=True))
        if mode == "missing_id":
            tab.callbacks[1](request(requestId=None))
            return
        if mode == "redirect":
            tab.callbacks[1](request(redirectResponse={"status": 302}))
            return
        tab.callbacks[1](request())
        if mode == "inflight":
            return
        status = {"http500": 500, "http403": 403, "http429": 429}.get(mode, 200)
        tab.callbacks[2](
            response(response={"status": status, "url": VIEW, "mimeType": "application/json"})
        )
        tab.callbacks[3](finished())
        if mode == "duplicate":
            tab.callbacks[1](request(requestId="second-read"))

    async def read() -> PydollReservationListSnapshot:
        return await read_reservation_list(
            tab=tab,
            current_tab=lambda: tab,
            navigate=navigate,
            snapshot=AsyncMock(return_value=empty),
            timeout_seconds=0.5,
            monotonic=clock,
            wall_time=lambda: 100,
            sleep=clock.sleep,
        )

    if mode == "inflight":
        assert not (await read()).official_read_completed
    elif mode == "http403":
        with pytest.raises(BrowserProtectionDetected) as error:
            await read()
        assert error.value.trigger == "http_403_subresource"
    elif mode == "http429":
        with pytest.raises(BrowserRateLimited):
            await read()
    else:
        with pytest.raises(BrowserSourceUnavailable) as error:
            await read()
        assert error.value.stage == "confirmation_reservation_list_response"
        assert str(error.value) == "source_unavailable"
        assert error.value.__cause__ is None
    assert tab.callbacks == {}


@pytest.mark.parametrize("unavailable", [False, True])
async def test_unobserved_request_preserves_existing_explicit_empty_dom_fallback(
    unavailable: bool,
) -> None:
    clock = Clock()
    tab = Tab()
    tab.fail_on = 1 if unavailable else None
    empty = PydollReservationListSnapshot(
        LIST, page_marker_visible=True, explicit_empty_visible=True
    )
    navigate = AsyncMock()
    result = await read_reservation_list(
        tab=tab,
        current_tab=lambda: tab,
        navigate=navigate,
        snapshot=AsyncMock(return_value=empty),
        timeout_seconds=0.5,
        monotonic=clock,
        wall_time=lambda: 100,
        sleep=clock.sleep,
    )
    assert result.empty_response_provenance == "not_observed"
    assert result.official_read_completed
    navigate.assert_awaited_once()
    assert tab.callbacks == {}


async def test_session_wires_full_navigation_budget_and_new_completeness_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 30000, True)
    tab = Tab()
    session._tab = tab
    result = PydollReservationListSnapshot(LIST)
    reader = AsyncMock(return_value=result)
    monkeypatch.setattr(session, "_read_reservation_list_response", reader)
    assert await session.read_reservation_list() == result
    assert reader.await_args.kwargs["timeout_seconds"] == 10
    assert reader.await_args.kwargs["navigation_timeout_seconds"] == 30
    assert reader.await_args.kwargs["tab"] is tab


@pytest.mark.parametrize("loading", [False, True])
@pytest.mark.parametrize("marker", [False, True])
def test_response_provenance_is_not_a_page_or_stability_shortcut(
    loading: bool, marker: bool
) -> None:
    snapshot = PydollReservationListSnapshot(
        LIST, page_marker_visible=marker, loading_visible=loading
    )
    completed = snapshot.with_official_empty_response()
    assert completed.render_complete is (marker and not loading)
    assert not completed.official_read_completed
    assert completed.with_stable_observation().official_read_completed is (marker and not loading)


def test_response_empty_provenance_rejects_contradictory_cards_and_unknown_sources() -> None:
    with pytest.raises(ValueError, match="cannot contain rendered cards"):
        PydollReservationListSnapshot(
            LIST,
            reservation_rows=("fixture-row",),
            rendered_card_count=1,
            empty_response_provenance="official_response",
        )
    with pytest.raises(ValueError, match="provenance is invalid"):
        PydollReservationListSnapshot(LIST, empty_response_provenance="guessed")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mode", "boundary", "state", "failure", "completed"),
    [
        ("invalid_body", "observer_rejected", "failed", "invalid_response", True),
        ("body_unavailable", "observer_rejected", "failed", "body_unavailable", True),
        ("http_error", "observer_rejected", "failed", "http_error", False),
        ("network_error", "observer_rejected", "failed", "network_error", False),
        ("auth_required", "observer_rejected", "failed", "auth_required", True),
        ("duplicate", "observer_rejected", "ambiguous", "ambiguous", True),
        ("dom_conflict", "dom_response_conflict", "nonempty", "none", True),
        ("dom_rows", "observer_rejected", "failed", "invalid_response", True),
        ("protection", "observer_rejected", "failed", "protection", False),
        ("rate_limited", "observer_rejected", "failed", "rate_limited", False),
        ("pending", "read_incomplete", "in_flight", "none", False),
        ("loading", "read_incomplete", "empty", "none", True),
        ("missing_marker", "read_incomplete", "empty", "none", True),
        ("tab_changed", "tab_changed", "failed", "tab_changed", True),
        ("auth_redirect", "auth_redirect", "body_pending", "none", True),
        ("dom_protection", "dom_protection", "body_pending", "none", True),
    ],
)
async def test_normal_failure_path_logs_only_closed_projection_once(
    mode: str,
    boundary: str,
    state: str,
    failure: str,
    completed: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    tab = Tab()
    selected = [tab]
    private = (
        "fixture-private-account-346",
        "fixture-private-cookie-348",
        "fixture-private-body-346",
        "fixture-private-request-348",
        "fixture-private-url-value-346",
        "fixture-private-error",
    )
    account, cookie, body_marker, request_id, url_marker, _error = private
    private_url = VIEW + "?account=" + account + "&value=" + url_marker
    payload: dict[str, object] = {
        **EMPTY,
        "fixture_private": {"account": account, "cookie": cookie, "body": body_marker},
    }
    if mode in {"invalid_body", "dom_rows"}:
        payload["strResult"] = account
    elif mode == "auth_required":
        payload.update(strResult="FAIL", h_msg_cd="P058")
    elif mode == "dom_conflict":
        payload["jrny_infos"] = {"jrny_info": [{"private_row": body_marker}]}
    tab.body = {"result": {"body": json.dumps(payload), "base64Encoded": False}}
    if mode == "body_unavailable":
        tab.body_error = True
    elif mode == "tab_changed":
        tab.body_hook = lambda: selected.__setitem__(0, Tab())

    rows_present = mode == "dom_rows"
    dom = PydollReservationListSnapshot(
        "https://www.korail.com/ticket/login" if mode == "auth_redirect" else LIST,
        reservation_rows=(account + cookie + body_marker,) if rows_present else (),
        rendered_card_count=1 if rows_present else 0,
        page_marker_visible=mode != "missing_marker",
        explicit_empty_visible=mode
        not in {"loading", "missing_marker", "dom_rows", "auth_redirect", "dom_protection"},
        loading_visible=mode == "loading",
        protection_detected=mode == "dom_protection",
    )

    async def navigate(url: str, *, timeout: int) -> None:
        tab.callbacks[1](request(document=True))
        tab.callbacks[1](
            request(
                requestId=request_id,
                request={
                    "method": "GET",
                    "url": private_url,
                    "headers": {"Cookie": cookie, "Authorization": account},
                },
            )
        )
        status = {"http_error": 500, "protection": 403, "rate_limited": 429}.get(mode, 200)
        tab.callbacks[2](
            response(
                requestId=request_id,
                response={
                    "status": status,
                    "url": private_url,
                    "mimeType": "application/json",
                    "headers": {"Set-Cookie": cookie},
                },
            )
        )
        if mode == "network_error":
            tab.callbacks[4](
                {
                    "params": {
                        "requestId": request_id,
                        "timestamp": 13.0,
                        "errorText": account + cookie + body_marker,
                    }
                }
            )
        elif mode != "pending":
            tab.callbacks[3](finished(requestId=request_id))
        if mode == "duplicate":
            tab.callbacks[1](request(requestId="fixture-private-second-request"))

    async def read() -> PydollReservationListSnapshot:
        return await read_reservation_list(
            tab=tab,
            current_tab=lambda: selected[0],
            navigate=navigate,
            snapshot=AsyncMock(return_value=dom),
            timeout_seconds=0.5,
            monotonic=clock,
            wall_time=lambda: 100,
            sleep=clock.sleep,
        )

    with caplog.at_level("WARNING", logger=response_module.__name__):
        if boundary in {"observer_rejected", "dom_response_conflict"}:
            error_type = {
                "protection": BrowserProtectionDetected,
                "rate_limited": BrowserRateLimited,
            }.get(mode, BrowserSourceUnavailable)
            with pytest.raises(error_type):
                await read()
        else:
            result = await read()
            assert not result.official_read_completed

    records = [record for record in caplog.records if record.name == response_module.__name__]
    assert len(records) == 1
    record = records[0]
    assert record.exc_info is None
    fields = dict(part.split("=", 1) for part in record.getMessage().split() if "=" in part)
    assert set(fields) == {
        "event",
        "boundary",
        "state",
        "failure",
        "request_observed",
        "response_received",
        "response_completed",
        "dom_observed",
        "dom_render_complete",
        "dom_empty",
        "dom_rows_present",
        "dom_loading",
        "dom_malformed",
    }
    assert fields["event"] == "reservation_list_read_diagnostic"
    assert fields["boundary"] == boundary
    assert fields["state"] == state
    assert fields["failure"] == failure
    assert fields["request_observed"] == "True"
    assert fields["response_completed"] == str(completed)
    assert fields["dom_observed"] == "True"
    assert fields["dom_render_complete"] == str(dom.render_complete)
    assert fields["dom_empty"] == str(dom.explicit_empty_visible)
    assert fields["dom_rows_present"] == str(rows_present)
    assert fields["dom_loading"] == str(dom.loading_visible)
    assert fields["dom_malformed"] == "False"
    boolean_fields = set(fields) - {"event", "boundary", "state", "failure"}
    assert all(fields[key] in {"True", "False"} for key in boolean_fields)
    for forbidden in (
        *private,
        "fixture-private-second-request",
        LIST,
        VIEW,
        private_url,
        "https://",
        "jrny_infos",
        "strResult",
        "Cookie",
        "Authorization",
        "P058",
    ):
        assert forbidden not in caplog.text
    assert tab.callbacks == {}


@pytest.mark.parametrize("unavailable", [False, True])
async def test_completed_read_and_existing_dom_fallback_emit_no_failure_diagnostic(
    unavailable: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    tab = Tab()
    tab.fail_on = 1 if unavailable else None
    dom = PydollReservationListSnapshot(LIST, page_marker_visible=True, explicit_empty_visible=True)

    async def navigate(url: str, *, timeout: int) -> None:
        if not unavailable:
            tab.emit_cycle()

    with caplog.at_level("WARNING", logger=response_module.__name__):
        result = await read_reservation_list(
            tab=tab,
            current_tab=lambda: tab,
            navigate=navigate,
            snapshot=AsyncMock(return_value=dom),
            timeout_seconds=0.5,
            monotonic=clock,
            wall_time=lambda: 100,
            sleep=clock.sleep,
        )

    assert result.official_read_completed
    assert not caplog.records
    assert tab.callbacks == {}


@pytest.mark.parametrize("finish_timestamp", [11.0, 11.5, 11.585699, 12.0])
@pytest.mark.parametrize("payload", [EMPTY, P100_EMPTY])
async def test_completed_response_accepts_timestamp_inversion_after_valid_headers(
    finish_timestamp: float,
    payload: dict[str, object],
) -> None:
    owner = armed()
    owner.on_request(request())  # request timestamp = 11.0
    owner.on_response(response())  # responseReceived processed first, timestamp = 12.0
    owner.on_finished(finished(timestamp=finish_timestamp))
    tab = Tab()
    tab.body = {"result": {"body": json.dumps(payload), "base64Encoded": False}}

    assert owner.snapshot().state == "body_pending"
    await owner.read_completed_body(tab)

    assert owner.snapshot().state == "empty"
    assert owner.snapshot().failure is None
    assert tab.commands == [
        {"method": "Network.getResponseBody", "params": {"requestId": "list-read"}}
    ]


@pytest.mark.parametrize("finish_timestamp", [11.585699, 13.0])
async def test_finish_before_response_processing_cannot_be_repaired_by_late_headers(
    finish_timestamp: float,
) -> None:
    owner = armed()
    owner.on_request(request())
    owner.on_finished(finished(timestamp=finish_timestamp))
    owner.on_response(response())
    tab = Tab()

    await owner.read_completed_body(tab)

    assert owner.snapshot().failure == "invalid_response"
    assert tab.commands == []


@pytest.mark.parametrize("base64_encoded", [False, True])
async def test_reader_timestamp_inversion_p100_still_requires_stable_complete_dom(
    base64_encoded: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    tab = Tab()
    raw = json.dumps(P100_EMPTY)
    tab.body = {
        "result": {
            "body": base64.b64encode(raw.encode()).decode() if base64_encoded else raw,
            "base64Encoded": base64_encoded,
        }
    }
    loading = PydollReservationListSnapshot(LIST, page_marker_visible=True, loading_visible=True)
    empty = PydollReservationListSnapshot(LIST, page_marker_visible=True)
    snapshots = AsyncMock(side_effect=[loading, empty, empty])

    async def navigate(url: str, *, timeout: int) -> None:
        tab.callbacks[1](request(document=True))
        tab.callbacks[1](request())
        tab.callbacks[2](response())
        # Natural CDP sample: callback order is response -> finish, but finish's
        # event timestamp precedes responseReceived by 414.301 milliseconds.
        tab.callbacks[3](finished(timestamp=11.585699))

    with caplog.at_level("WARNING", logger=response_module.__name__):
        result = await read_reservation_list(
            tab=tab,
            current_tab=lambda: tab,
            navigate=navigate,
            snapshot=snapshots,
            timeout_seconds=1,
            monotonic=clock,
            wall_time=lambda: 100,
            sleep=clock.sleep,
        )

    assert result.empty_response_provenance == "official_response"
    assert result.official_read_completed
    assert snapshots.await_count == 3
    assert [command["method"] for command in tab.commands] == [
        "Page.getFrameTree",
        "Network.getResponseBody",
    ]
    assert not caplog.records
    assert tab.callbacks == {}


LOCAL_BODY_REJECTIONS: dict[str, object] = {
    # strResult=SUCC proves an authenticated response; only our row/message contract fails.
    "message_contract": {**EMPTY, "h_msg_cd": "WRT300004", "jrny_infos": {"jrny_info": [{}]}},
    "rows_malformed": {**EMPTY, "jrny_infos": {"jrny_info": [1]}},
    "rows_not_list": {**EMPTY, "jrny_infos": {"jrny_info": None}},
}
PROVIDER_BODY_FAILURES: dict[str, object] = {
    "result_fail": {"strResult": "FAIL", "h_msg_cd": "WRG000000"},
    "auth_required": {"strResult": "FAIL", "h_msg_cd": "P058"},
    "payload_not_object": [],
}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("message_contract", "body_contract_rejected"),
        ("rows_malformed", "body_contract_rejected"),
        ("rows_not_list", "body_contract_rejected"),
        ("body_shape", "body_unavailable"),
        ("cdp_error", "body_unavailable"),
        ("too_large", None),
        ("dom_conflict", "dom_response_conflict"),
        ("result_fail", None),
        ("auth_required", None),
        ("payload_not_object", None),
        ("not_json", None),
        ("http_error", None),
        ("network_error", None),
        ("timeout", None),
        ("duplicate", None),
        ("redirect", None),
        ("unexpected_mime", None),
    ],
)
async def test_only_local_read_rejections_are_typed_session_preserving(
    mode: str, expected: str | None
) -> None:
    clock = Clock()
    tab = Tab()
    payload = LOCAL_BODY_REJECTIONS.get(mode, PROVIDER_BODY_FAILURES.get(mode))
    if payload is not None:
        tab.body = {"result": {"body": json.dumps(payload), "base64Encoded": False}}
    elif mode == "not_json":
        tab.body = {"result": {"body": "<html>login</html>", "base64Encoded": False}}
    elif mode == "body_shape":
        tab.body = {"result": {}}
    elif mode == "cdp_error":
        tab.body_error = True
    elif mode == "dom_conflict":
        rows = {**EMPTY, "jrny_infos": {"jrny_info": [{}]}}
        tab.body = {"result": {"body": json.dumps(rows), "base64Encoded": False}}
    empty = PydollReservationListSnapshot(
        LIST, page_marker_visible=True, explicit_empty_visible=True
    )

    async def navigate(url: str, *, timeout: int) -> None:
        tab.callbacks[1](request(document=True))
        if mode == "redirect":
            tab.callbacks[1](request(redirectResponse={"status": 302}))
            return
        tab.callbacks[1](request())
        if mode == "timeout":
            clock.now += 2
            return
        status = 500 if mode == "http_error" else 200
        mime = "text/plain" if mode == "unexpected_mime" else "application/json"
        tab.callbacks[2](response(response={"status": status, "url": VIEW, "mimeType": mime}))
        if mode == "network_error":
            tab.callbacks[4]({"params": {"requestId": "list-read", "timestamp": 13.0}})
            return
        size = 65_537 if mode == "too_large" else 150
        tab.callbacks[3](finished(encodedDataLength=size))
        if mode == "duplicate":
            tab.callbacks[1](request(requestId="second-read"))

    with pytest.raises(BrowserSourceUnavailable) as error:
        await read_reservation_list(
            tab=tab,
            current_tab=lambda: tab,
            navigate=navigate,
            snapshot=AsyncMock(return_value=empty),
            timeout_seconds=0.5,
            monotonic=clock,
            wall_time=lambda: 100,
            sleep=clock.sleep,
        )

    assert error.value.stage == "confirmation_reservation_list_response"
    assert str(error.value) == "source_unavailable"
    if expected is None:
        assert type(error.value) is BrowserSourceUnavailable
    else:
        assert isinstance(error.value, BrowserLocalReadRejected)
        assert error.value.rejection == expected
    assert tab.callbacks == {}


async def test_later_failure_replaces_local_rejection_atomically() -> None:
    owner = armed()
    complete(owner)
    tab = Tab()
    tab.body_error = True
    await owner.read_completed_body(tab)
    assert owner.snapshot() == ReservationListResponseSnapshot(
        "failed", "body_unavailable", "body_unavailable"
    )

    owner.fail("tab_changed")
    assert owner.snapshot() == ReservationListResponseSnapshot("failed", "tab_changed")
    with pytest.raises(BrowserSourceUnavailable) as error:
        owner.assert_read_allowed()
    assert type(error.value) is BrowserSourceUnavailable

    owner.close()
    assert owner.snapshot() == ReservationListResponseSnapshot("failed", "closed")
