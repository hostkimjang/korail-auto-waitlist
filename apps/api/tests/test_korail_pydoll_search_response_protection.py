from __future__ import annotations

import asyncio
import base64
import json
import logging

import pytest

from rail_waitlist.korail_pydoll_browser import PydollPageSnapshot, _PydollSession
from rail_waitlist.korail_sidecar.browser_contracts import BrowserProtectionDetected
from rail_waitlist.korail_sidecar.pydoll.page_safety import (
    assert_pydoll_response_allowed,
    classify_pydoll_page_block,
)
from rail_waitlist.korail_sidecar.pydoll.search_response_protection import (
    SEARCH_RESPONSE_MACRO_PROTECTION,
    PydollSearchResponseProtectionObserver,
)

SEARCH_URL = "https://www.korail.com/ticket/search/general"
BUSINESS_URL = "https://www.korail.com/classes/com.korail.mobile.seatMovie.ScheduleView"
MACRO_JSON = json.dumps({"errCode": "macro_err1", "errMsg": "fixture"})
NORMAL_JSON = json.dumps({"strResult": "SUCC", "trn_infos": {"trn_info": []}})
# Ordinary static configuration mentions NetFunnel and even the macro marker in free
# text. Only an exact top-level errCode may be treated as protection evidence.
NETFUNNEL_CONFIG_JSON = json.dumps(
    {
        "netfunnel": {"service_id": "service_1", "action_id": "act_1"},
        "message": "NetFunnel guard; legacy key macro_err1 kept for compatibility",
        "errCode": None,
        "nested": {"errCode": "macro_err1"},
    }
)


def response_event(
    request_id: str = "req-1",
    *,
    status: object = 200,
    resource_type: str = "XHR",
    url: str = BUSINESS_URL,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "params": {
            "requestId": request_id,
            "type": resource_type,
            "response": {"status": status, "url": url, "headers": headers or {}},
        }
    }


def body_reply(body: str, *, encoded: bool = False) -> dict[str, object]:
    raw = base64.b64encode(body.encode("utf-8")).decode("ascii") if encoded else body
    return {"result": {"body": raw, "base64Encoded": encoded}}


class FakeCdp:
    def __init__(self, replies: dict[str, list[object]]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    async def __call__(self, command: dict[str, object]) -> object:
        assert command["method"] == "Network.getResponseBody"
        params = command["params"]
        assert isinstance(params, dict)
        request_id = params["requestId"]
        assert isinstance(request_id, str)
        self.calls.append(request_id)
        queue = self.replies.get(request_id, [])
        reply = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else None)
        if isinstance(reply, Exception):
            raise reply
        return reply


def armed_observer(*events: dict[str, object]) -> PydollSearchResponseProtectionObserver:
    observer = PydollSearchResponseProtectionObserver()
    observer.arm()
    for event in events:
        observer.observe(event)
    return observer


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [False, True])
async def test_exact_macro_errcode_becomes_typed_protection_evidence(encoded: bool) -> None:
    observer = armed_observer(response_event())
    cdp = FakeCdp({"req-1": [body_reply(MACRO_JSON, encoded=encoded)]})

    evidence = await observer.drain(cdp)

    assert evidence == (200, SEARCH_RESPONSE_MACRO_PROTECTION)
    assert observer.pending_count == 0
    # The verdict is retained without more body reads.
    assert await observer.drain(cdp) == evidence
    assert cdp.calls == ["req-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        NORMAL_JSON,
        NETFUNNEL_CONFIG_JSON,
        "<html><body>macro_err1 NetFunnel</body></html>",
        json.dumps(["macro_err1"]),
        json.dumps({"errCode": "MACRO_ERR1"}),
    ],
    ids=["normal", "netfunnel_config", "html", "json_array", "case_mismatch"],
)
async def test_non_exact_bodies_are_never_protection(body: str) -> None:
    observer = armed_observer(response_event())

    assert await observer.drain(FakeCdp({"req-1": [body_reply(body)]})) is None
    assert observer.pending_count == 0


@pytest.mark.parametrize(
    "event",
    [
        response_event(url="https://korail.com.example/web_s/fixture"),
        response_event(url="http://www.korail.com/web_s/fixture"),
        response_event(url="https://www.korail.com:8443/web_s/fixture"),
        response_event(url="https://user@www.korail.com/web_s/fixture"),
        response_event(resource_type="Document"),
        response_event(resource_type="Script"),
        response_event(status=302),
        response_event(status=True),
        response_event(headers={"Content-Length": str(1024 * 1024)}),
        {"params": {"type": "XHR", "response": {"status": 200, "url": BUSINESS_URL}}},
    ],
    ids=[
        "wrong_host",
        "http",
        "wrong_port",
        "userinfo",
        "document",
        "script",
        "redirect",
        "bool_status",
        "declared_oversize",
        "missing_request_id",
    ],
)
def test_only_bounded_official_business_responses_become_candidates(
    event: dict[str, object],
) -> None:
    assert armed_observer(event).pending_count == 0


@pytest.mark.asyncio
async def test_oversize_body_is_ignored_without_decoding() -> None:
    observer = armed_observer(response_event())
    oversize = MACRO_JSON + " " * (PydollSearchResponseProtectionObserver.MAX_BODY_BYTES * 2)

    assert await observer.drain(FakeCdp({"req-1": [body_reply(oversize)]})) is None
    assert observer.pending_count == 0


@pytest.mark.asyncio
async def test_unfinished_body_is_retried_on_later_snapshots_then_classified() -> None:
    observer = armed_observer(response_event())
    cdp = FakeCdp(
        {
            "req-1": [
                RuntimeError("No data found for resource with given identifier"),
                {"error": {"code": -32000}},
                body_reply(MACRO_JSON),
            ]
        }
    )

    assert await observer.drain(cdp) is None
    assert await observer.drain(cdp) is None
    assert observer.pending_count == 1
    assert await observer.drain(cdp) == (200, SEARCH_RESPONSE_MACRO_PROTECTION)


@pytest.mark.asyncio
async def test_unreadable_body_is_dropped_after_a_bounded_number_of_reads() -> None:
    observer = armed_observer(response_event())
    cdp = FakeCdp({"req-1": [RuntimeError("unreadable")]})

    for _ in range(PydollSearchResponseProtectionObserver.MAX_READ_ATTEMPTS):
        assert await observer.drain(cdp) is None

    assert observer.pending_count == 0
    assert len(cdp.calls) == PydollSearchResponseProtectionObserver.MAX_READ_ATTEMPTS
    assert await observer.drain(cdp) is None
    assert len(cdp.calls) == PydollSearchResponseProtectionObserver.MAX_READ_ATTEMPTS


def test_candidates_are_capped_per_search_and_ignored_until_armed() -> None:
    observer = PydollSearchResponseProtectionObserver()
    observer.observe(response_event("before-arm"))
    assert observer.pending_count == 0

    observer.arm()
    for index in range(PydollSearchResponseProtectionObserver.MAX_CANDIDATES_PER_SEARCH + 5):
        observer.observe(response_event(f"req-{index}"))

    assert (
        observer.pending_count == PydollSearchResponseProtectionObserver.MAX_CANDIDATES_PER_SEARCH
    )


async def _drain_until_settled(
    observer: PydollSearchResponseProtectionObserver, cdp: FakeCdp
) -> tuple[int, str] | None:
    limit = PydollSearchResponseProtectionObserver.MAX_CANDIDATES_PER_SEARCH * (
        PydollSearchResponseProtectionObserver.MAX_READ_ATTEMPTS + 1
    )
    for _ in range(limit):
        evidence = await observer.drain(cdp)
        if evidence is not None or observer.pending_count == 0:
            return evidence
    raise AssertionError("observer did not settle")


@pytest.mark.asyncio
async def test_business_macro_after_more_than_sixteen_bootstrap_responses_is_detected() -> None:
    bootstrap = [f"bootstrap-{index}" for index in range(20)]
    events = [
        response_event(request_id, resource_type="Fetch" if index % 2 else "XHR")
        for index, request_id in enumerate(bootstrap)
    ]
    observer = armed_observer(*events, response_event("schedule"))
    replies: dict[str, list[object]] = {
        request_id: [body_reply(NETFUNNEL_CONFIG_JSON if index % 3 == 0 else NORMAL_JSON)]
        for index, request_id in enumerate(bootstrap)
    }
    replies["schedule"] = [body_reply(MACRO_JSON)]

    assert await _drain_until_settled(observer, FakeCdp(replies)) == (
        200,
        SEARCH_RESPONSE_MACRO_PROTECTION,
    )


@pytest.mark.asyncio
async def test_unfinished_early_responses_do_not_starve_a_later_completed_verdict() -> None:
    stalled = [f"stalled-{index}" for index in range(8)]
    observer = armed_observer(*(response_event(rid) for rid in stalled), response_event("late"))
    replies: dict[str, list[object]] = {rid: [RuntimeError("not finished")] for rid in stalled}
    replies["late"] = [body_reply(MACRO_JSON)]
    cdp = FakeCdp(replies)

    evidence = None
    for _ in range(4):
        evidence = await observer.drain(cdp)
        if evidence is not None:
            break

    assert evidence == (200, SEARCH_RESPONSE_MACRO_PROTECTION)
    # The verdict arrives long before the stalled reads exhaust their attempt budget.
    assert cdp.calls.count("late") == 1


@pytest.mark.asyncio
async def test_drain_reads_a_bounded_number_of_bodies_per_snapshot() -> None:
    count = PydollSearchResponseProtectionObserver.MAX_READS_PER_DRAIN + 3
    observer = armed_observer(*(response_event(f"req-{index}") for index in range(count)))
    cdp = FakeCdp({f"req-{index}": [body_reply(NORMAL_JSON)] for index in range(count)})

    assert await observer.drain(cdp) is None

    assert len(cdp.calls) == PydollSearchResponseProtectionObserver.MAX_READS_PER_DRAIN
    assert observer.pending_count == 3


@pytest.mark.asyncio
async def test_stalled_body_read_is_bounded_and_requeued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PydollSearchResponseProtectionObserver, "BODY_READ_TIMEOUT_SECONDS", 0.01)
    observer = armed_observer(response_event("stalled"), response_event("late"))
    calls: list[str] = []

    async def cdp(command: dict[str, object]) -> object:
        params = command["params"]
        assert isinstance(params, dict)
        calls.append(str(params["requestId"]))
        if params["requestId"] == "stalled":
            await asyncio.Event().wait()
        return body_reply(MACRO_JSON)

    evidence = await asyncio.wait_for(observer.drain(cdp), timeout=1)

    assert evidence == (200, SEARCH_RESPONSE_MACRO_PROTECTION)
    assert calls == ["stalled", "late"]


@pytest.mark.asyncio
async def test_new_search_during_body_read_discards_the_stale_verdict() -> None:
    observer = armed_observer(response_event())
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_cdp(command: dict[str, object]) -> object:
        started.set()
        await release.wait()
        return body_reply(MACRO_JSON)

    read = asyncio.create_task(observer.drain(slow_cdp))
    await started.wait()
    observer.arm()
    release.set()

    assert await read is None
    assert observer.pending_count == 0
    assert await observer.drain(slow_cdp) is None


class FakeSearchTab:
    network_events_enabled = True

    def __init__(self, replies: dict[str, list[object]]) -> None:
        self.cdp = FakeCdp(replies)

    async def _execute_command(self, command: dict[str, object]) -> object:
        return await self.cdp(command)

    async def execute_script(self, script: str, *, return_by_value: bool) -> object:
        return {
            "result": {
                "result": {
                    "value": {
                        "body": "해당 스케줄 운행 열차 없음",
                        "url": SEARCH_URL,
                        "title": "",
                        "reservationRows": [],
                        "protectionTexts": [],
                        "rows": [],
                    }
                }
            }
        }


def search_session(replies: dict[str, list[object]]) -> tuple[_PydollSession, FakeSearchTab]:
    session = _PydollSession(SEARCH_URL, 5_000, True)
    tab = FakeSearchTab(replies)
    session._tab = tab
    return session, tab


@pytest.mark.asyncio
async def test_empty_schedule_with_macro_json_stops_wait_immediately_as_protection() -> None:
    session, _tab = search_session({"req-1": [body_reply(MACRO_JSON)]})
    session._mark_search_submitted()
    session._on_response_received(response_event())

    snapshot = await asyncio.wait_for(session.wait_for_result(), timeout=1)

    assert snapshot.rows == ()
    assert snapshot.network_responses == ((200, SEARCH_RESPONSE_MACRO_PROTECTION),)
    with pytest.raises(BrowserProtectionDetected) as raised:
        assert_pydoll_response_allowed(
            snapshot, "wait_result", event_logger=logging.getLogger("fixture")
        )
    assert raised.value.trigger == "marker_macro_err1"
    assert raised.value.stage == "wait_result"


@pytest.mark.asyncio
async def test_normal_json_keeps_header_evidence_and_adds_no_protection() -> None:
    session, tab = search_session({"req-1": [body_reply(NETFUNNEL_CONFIG_JSON)]})
    session._mark_search_submitted()
    session._on_response_received(response_event())
    session._on_response_received(
        {"params": {"type": "Fetch", "response": {"status": 429, "url": BUSINESS_URL}}}
    )

    snapshot = await session._snapshot()

    assert snapshot.network_responses == ((429, "fetch"),)
    assert tab.cdp.calls == ["req-1"]


@pytest.mark.asyncio
async def test_reset_search_state_and_unsubmitted_pages_never_read_bodies() -> None:
    session, tab = search_session({"req-1": [body_reply(MACRO_JSON)]})
    session._on_response_received(response_event())
    await session._snapshot()

    session._mark_search_submitted()
    session._on_response_received(response_event())
    session.reset_search_state()
    snapshot = await session._snapshot()

    assert snapshot.network_responses == ()
    assert tab.cdp.calls == []


def test_page_safety_classifies_only_the_code_owned_marker() -> None:
    blocked = classify_pydoll_page_block(
        PydollPageSnapshot(
            body_text="해당 스케줄 운행 열차 없음",
            rows=(),
            network_responses=((200, SEARCH_RESPONSE_MACRO_PROTECTION),),
        )
    )
    assert blocked is not None
    assert blocked.kind == "protection"
    assert blocked.trigger == "marker_macro_err1"
    assert (
        classify_pydoll_page_block(
            PydollPageSnapshot(
                body_text="해당 스케줄 운행 열차 없음",
                rows=(),
                network_responses=((200, "business_xhr"),),
            )
        )
        is None
    )
