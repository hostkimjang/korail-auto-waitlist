from __future__ import annotations

import asyncio
import logging
from datetime import date, time
from typing import Any

import httpx
import pytest

from rail_waitlist.korail_browser_automation import BrowserSeatSearchRequest
from rail_waitlist.korail_http_replay import (
    MAX_RESPONSE_BYTES,
    HttpReplayInvalidCapture,
    HttpReplayInvalidResponse,
    HttpReplayLeaseInvalid,
    HttpReplayProtectionDetected,
    HttpReplayProviderUnavailable,
    HttpReplayRateLimited,
    HttpReplaySessionInvalid,
    HttpReplaySourceUnavailable,
    KorailHttpReplayClient,
    build_http_replay_plan,
)

CAPTURED_DATE = date(2026, 8, 3)
BOUNDARY = "----rail-waitlist-boundary"


def test_http_transport_info_logs_are_disabled_for_ephemeral_paths() -> None:
    assert logging.getLogger("httpx").isEnabledFor(logging.INFO) is False
    assert logging.getLogger("httpcore").isEnabledFor(logging.INFO) is False


def _multipart(
    *,
    origin: str = "서울",
    destination: str = "부산",
    travel_date: str = "20260803",
    hour: str = "140000",
    opaque: str = "opaque-secret-value",
    boundary: str = BOUNDARY,
) -> bytes:
    fields = {
        "txtGoStart": origin,
        "txtGoEnd": destination,
        "txtGoAbrdDt": travel_date,
        "txtGoHour": hour,
        "txtPsgFlg_1": "1",
        "opaqueSessionField": opaque,
    }
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        )
    chunks.append(f"--{boundary}--\r\n")
    return "".join(chunks).encode()


def _event(
    *,
    url: str = "https://www.korail.com:443/web_s/opaque-path",
    body: bytes | None = None,
    redirected: bool = False,
    boundary: str = BOUNDARY,
    user_agent: str = "captured-secret-agent",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "request": {
            "url": url,
            "method": "POST",
            "headers": {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": user_agent,
                "Cookie": "must-be-replaced-by-the-jar",
            },
            "postData": (body or _multipart(boundary=boundary)).decode(),
        }
    }
    if redirected:
        params["redirectResponse"] = {"status": 302}
    return {"method": "Network.requestWillBeSent", "params": params}


def _plan(*events: dict[str, Any]):
    return build_http_replay_plan(
        list(events or (_event(),)),
        [
            {
                "name": "SESSION_SECRET",
                "value": "cookie-secret-value",
                "domain": ".korail.com",
                "path": "/",
                "secure": True,
            }
        ],
        "서울",
        "부산",
        CAPTURED_DATE,
    )


def _request(
    *,
    travel_date: date = date(2026, 8, 4),
    departure_from: time = time(14, 30),
    departure_to: time = time(18),
) -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=travel_date,
        departure_from=departure_from,
        departure_to=departure_to,
        passenger_count=1,
    )


def _row(
    *,
    number: str = "0017",
    departure: str = "150000",
    standard: str = "예약 가능",
    first: str = "매진",
    kind: str = "KTX-산천",
    travel_date: str = "20260804",
    arrival_date: str = "20260804",
    arrival: str = "200000",
    expected_delay_minutes: str | None = None,
) -> dict[str, str]:
    row = {
        "h_trn_clsf_nm": kind,
        "h_trn_no": number,
        "h_dpt_rs_stn_nm": "서울",
        "h_arv_rs_stn_nm": "부산",
        "h_dpt_dt": travel_date,
        "h_dpt_tm": departure,
        "h_arv_dt": arrival_date,
        "h_arv_tm": arrival,
        "h_gen_rsv_nm": standard,
        "h_spe_rsv_nm": first,
    }
    if expected_delay_minutes is not None:
        row["h_expn_dpt_dlay_tnum"] = expected_delay_minutes
    return row


def _payload(*rows: dict[str, str], has_next: str = "N") -> dict[str, Any]:
    return {
        "strResult": "SUCC",
        "h_next_pg_flg": has_next,
        "trn_infos": {"trn_info": list(rows)},
    }


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.mark.asyncio
async def test_http_replay_preserves_exact_expected_departure_delay() -> None:
    transport = httpx.MockTransport(
        lambda _request: _json_response(_payload(_row(expected_delay_minutes="13")))
    )

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        result = await client.search(_request())

    assert result.trains[0].expected_delay_minutes == 13


@pytest.mark.asyncio
async def test_http_replay_preserves_primary_timetable_fields_and_overnight_arrival() -> None:
    transport = httpx.MockTransport(
        lambda _request: _json_response(
            _payload(
                _row(
                    number="0181",
                    kind="KTX-청룡",
                    departure="233000",
                    arrival_date="20260805",
                    arrival="011500",
                    standard="일반실 예약 가능 59,800원",
                )
            )
        )
    )
    request = _request(departure_from=time(23), departure_to=time(23, 59))

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        result = await client.search(request)

    train = result.trains[0]
    assert train.train_number == "181"
    assert train.train_type == "KTX-청룡"
    assert train.departure_at.isoformat() == "2026-08-04T23:30:00+09:00"
    assert train.arrival_at.isoformat() == "2026-08-05T01:15:00+09:00"
    assert train.adult_fare == 59_800


@pytest.mark.asyncio
async def test_http_replay_omits_ambiguous_fare_instead_of_guessing() -> None:
    transport = httpx.MockTransport(
        lambda _request: _json_response(
            _payload(_row(standard="예약 가능 성인 59,800원 어린이 29,900원"))
        )
    )

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        result = await client.search(_request())

    assert result.trains[0].adult_fare is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arrival_date", "arrival"),
    [
        ("20260804", "145959"),
        ("20260804", "25:00"),
        ("20260832", "200000"),
    ],
)
async def test_http_replay_rejects_non_future_or_malformed_arrival(
    arrival_date: str,
    arrival: str,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: _json_response(_payload(_row(arrival_date=arrival_date, arrival=arrival)))
    )

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayInvalidResponse):
            await client.search(_request())


def test_plan_accepts_multiple_business_posts_and_repr_hides_raw_material() -> None:
    first = _event()
    second = _event(body=_multipart(hour="150501"))

    plan = _plan(first, second)

    assert plan.captured_request_count == 1
    rendered = repr(plan)
    assert "opaque-path" not in rendered
    assert "opaque-secret-value" not in rendered
    assert "captured-secret-agent" not in rendered
    assert "cookie-secret-value" not in rendered
    assert "SESSION_SECRET" not in rendered


def test_plan_uses_only_the_first_exact_business_post_as_its_template() -> None:
    first = _event()
    second_boundary = "----rail-waitlist-next-boundary"
    second = _event(
        body=_multipart(hour="150000", boundary=second_boundary),
        boundary=second_boundary,
    )

    assert _plan(first, second).captured_request_count == 1

    changed_header = _event(
        body=_multipart(hour="150000", boundary=second_boundary),
        boundary=second_boundary,
        user_agent="different-agent",
    )
    assert _plan(first, changed_header).captured_request_count == 1


def test_materialization_patches_only_date_and_hour_value_spans() -> None:
    plan = _plan()
    original = _multipart()

    materialized = plan.materialize(_request()).content

    expected = original.replace(b"20260803", b"20260804").replace(b"140000", b"140000")
    assert materialized == expected
    assert b"opaque-secret-value" in materialized
    assert "opaque-path" not in repr(plan.materialize(_request()))
    assert "opaque-secret-value" not in repr(plan.materialize(_request()))
    with pytest.raises(HttpReplayInvalidCapture):
        plan.materialize(
            BrowserSeatSearchRequest(
                origin="대전",
                destination="부산",
                travel_date=date(2026, 8, 4),
                departure_from=time(14),
                departure_to=time(18),
                passenger_count=1,
            )
        )


@pytest.mark.parametrize(
    "url,redirected",
    [
        ("http://www.korail.com/web_s/x", False),
        ("https://evil.example/web_s/x", False),
        ("https://user@www.korail.com/web_s/x", False),
        ("https://www.korail.com:444/web_s/x", False),
        ("https://www.korail.com/web_s/x#fragment", False),
        ("https://www.korail.com/web_s/x", True),
    ],
)
def test_plan_rejects_non_exact_business_target(url: str, redirected: bool) -> None:
    with pytest.raises(HttpReplayInvalidCapture):
        _plan(_event(url=url, redirected=redirected))


def test_plan_keeps_same_origin_query_opaque_and_out_of_repr() -> None:
    plan = _plan(_event(url="https://www.korail.com/web_s/x?opaque=secret-value"))

    assert "opaque=secret-value" not in repr(plan)
    assert "opaque=secret-value" not in repr(plan.materialize(_request()))


def test_plan_rejects_missing_or_duplicate_required_multipart_field() -> None:
    missing = _multipart().replace(
        b'Content-Disposition: form-data; name="txtGoHour"',
        b'Content-Disposition: form-data; name="otherHour"',
    )
    duplicate = _multipart().replace(
        f"--{BOUNDARY}--\r\n".encode(),
        (
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="txtGoHour"\r\n'
            f"\r\n150000\r\n--{BOUNDARY}--\r\n"
        ).encode(),
    )

    with pytest.raises(HttpReplayInvalidCapture):
        _plan(_event(body=missing))
    with pytest.raises(HttpReplayInvalidCapture):
        _plan(_event(body=duplicate))


def test_plan_rejects_missing_session_cookie_and_wrong_capture_date() -> None:
    with pytest.raises(HttpReplaySessionInvalid):
        build_http_replay_plan(
            [_event()],
            [],
            "서울",
            "부산",
            CAPTURED_DATE,
        )


def test_plan_filters_unrelated_cookies_and_keeps_official_https_cookie() -> None:
    plan = build_http_replay_plan(
        [_event()],
        [
            {
                "name": "UNRELATED",
                "value": "discarded",
                "domain": ".example.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "SESSION_SECRET",
                "value": "cookie-secret-value",
                "domain": ".korail.com",
                "path": "/",
                "secure": False,
            },
        ],
        "서울",
        "부산",
        CAPTURED_DATE,
    )

    assert plan.captured_request_count == 1
    assert "cookie-secret-value" not in repr(plan)
    with pytest.raises(HttpReplayInvalidCapture):
        build_http_replay_plan(
            [_event()],
            {"session": "secret"},
            "서울",
            "부산",
            date(2026, 8, 4),
        )


async def test_parser_maps_each_seat_class_and_filters_non_ktx_and_exact_window() -> None:
    response = _payload(
        _row(standard="매진 임박", first="입석 + 좌석"),
        _row(number="19", departure="160000", standard="예약대기", first="특실 없음"),
        _row(number="21", departure="170000", standard="매진", first="좌석 많음"),
        _row(number="23", departure="180100"),
        _row(number="25", departure="153000", kind="ITX-새마을"),
    )
    transport = httpx.MockTransport(lambda _request: _json_response(response))

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        result = await client.search(_request())

    assert [(train.standard, train.first) for train in result.trains] == [
        ("limited", "standing_plus_seat"),
        ("waitlist_available", "not_offered"),
        ("sold_out", "available"),
    ]
    assert result.trains[0].train_number == "17"
    assert result.trains[0].train_type == "KTX-산천"
    assert result.trains[0].departure_at.utcoffset().total_seconds() == 9 * 3600
    assert result.trains[0].arrival_at.isoformat() == "2026-08-04T20:00:00+09:00"
    assert result.trains[0].adult_fare is None


async def test_successful_empty_service_day_is_not_treated_as_source_failure() -> None:
    transport = httpx.MockTransport(lambda _request: _json_response(_payload()))

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        result = await client.search(_request())

    assert result.travel_date == date(2026, 8, 4)
    assert result.trains == []


async def test_unknown_seat_label_is_typed_invalid_response() -> None:
    transport = httpx.MockTransport(
        lambda _request: _json_response(_payload(_row(standard="알 수 없는 신규 상태")))
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayInvalidResponse):
            await client.search(_request())


async def test_pagination_uses_last_response_departure_plus_one_minute() -> None:
    request_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(request.content)
        assert request.headers["cookie"] == "SESSION_SECRET=cookie-secret-value"
        if len(request_bodies) == 1:
            return _json_response(_payload(_row(departure="150000"), has_next="Y"))
        return _json_response(_payload(_row(number="19", departure="160000")))

    async with KorailHttpReplayClient(_plan(), transport=httpx.MockTransport(handler)) as client:
        result = await client.search(_request())

    assert len(result.trains) == 2
    assert b"150100" in request_bodies[1]
    assert b"20260804" in request_bodies[0]


async def test_pagination_is_bounded_to_twenty_pages() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        departure = f"{14 + (calls // 60):02d}{calls % 60:02d}00"
        return _json_response(_payload(_row(departure=departure), has_next="Y"))

    async with KorailHttpReplayClient(_plan(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HttpReplaySourceUnavailable):
            await client.search(_request(departure_from=time(14), departure_to=time(23)))
    assert calls == 20


@pytest.mark.parametrize(
    "status,error_type",
    [
        (401, HttpReplaySessionInvalid),
        (403, HttpReplayProtectionDetected),
        (429, HttpReplayRateLimited),
        (400, HttpReplaySourceUnavailable),
        (307, HttpReplaySourceUnavailable),
    ],
)
async def test_http_statuses_are_classified_without_response_content(
    status: int, error_type: type[Exception]
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status, headers={"location": "https://evil.example"})
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(error_type) as raised:
            await client.search(_request())
    if status == 403:
        assert isinstance(raised.value, HttpReplayProtectionDetected)
        assert raised.value.trigger == "http_403_business"


@pytest.mark.parametrize(
    "location,error_type",
    [
        ("/member/login", HttpReplaySessionInvalid),
        ("https://www.korail.com/auth/session", HttpReplaySessionInvalid),
        ("https://evil.example/login", HttpReplaySourceUnavailable),
        ("/ticket/search/general", HttpReplaySourceUnavailable),
        ("/author/profile", HttpReplaySourceUnavailable),
    ],
)
async def test_only_clear_same_origin_session_redirects_trigger_cold_reinit(
    location: str,
    error_type: type[Exception],
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(302, headers={"location": location})
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(error_type):
            await client.search(_request())


async def test_official_maintenance_redirect_is_provider_unavailable() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            302,
            headers={"location": "/rejectservice_job.html"},
        )
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayProviderUnavailable) as raised:
            await client.search(_request())

    assert raised.value.trigger == "maintenance_page"


async def test_official_maintenance_html_is_provider_unavailable() -> None:
    body = "서비스를 일시중지합니다. 승차권 예약 및 발매서비스".encode()
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=body,
        )
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayProviderUnavailable) as raised:
            await client.search(_request())

    assert raised.value.trigger == "service_outage_page"


async def test_official_service_unavailable_html_is_provider_unavailable() -> None:
    body = "서비스를 일시중지합니다. 승차권 예약 및 발매서비스".encode("cp949")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            503,
            headers={"content-type": "text/html; charset=euc-kr"},
            content=body,
        )
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayProviderUnavailable) as raised:
            await client.search(_request())

    assert raised.value.trigger == "service_outage_page"


async def test_unmarked_service_unavailable_response_stays_query_local() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503, content=b"unavailable"))

    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplaySourceUnavailable):
            await client.search(_request())


async def test_login_html_is_classified_as_an_expired_session() -> None:
    login_html = b"<!doctype html><html><form id='login'><input type='password'></form></html>"
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=login_html,
        )
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplaySessionInvalid):
            await client.search(_request())


async def test_json_text_mentioning_login_does_not_trigger_cold_reinit() -> None:
    payload = _payload(_row())
    payload["notice"] = "login optional"
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        result = await client.search(_request())
    assert len(result.trains) == 1


@pytest.mark.parametrize(
    "marker",
    [
        "-1405",
        "-8002",
        "-8003",
        "macro_err1",
        "CAPTCHA",
        "NetFunnel",
        "미허가",
        "이용 제한",
        "비정상 접근",
    ],
)
async def test_body_protection_markers_are_separate_typed_errors(marker: str) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=marker.encode()))
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayProtectionDetected) as raised:
            await client.search(_request())
    assert raised.value.trigger != ""
    assert marker not in repr(raised.value)


async def test_lease_is_checked_before_any_network_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(_payload(_row()))

    async with KorailHttpReplayClient(
        _plan(), lease_is_current=lambda: False, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(HttpReplayLeaseInvalid):
            await client.search(_request())
    assert calls == 0


async def test_non_json_and_schema_failures_are_ordinary_invalid() -> None:
    responses = iter(
        [
            httpx.Response(200, content=b"not-json"),
            _json_response({"strResult": "SUCC"}),
        ]
    )
    transport = httpx.MockTransport(lambda _request: next(responses))
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplayInvalidResponse):
            await client.search(_request())
        with pytest.raises(HttpReplayInvalidResponse):
            await client.search(_request())


async def test_response_size_cap_fails_closed() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
    )
    async with KorailHttpReplayClient(_plan(), transport=transport) as client:
        with pytest.raises(HttpReplaySourceUnavailable):
            await client.search(_request())


class _ClosingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _json_response(_payload(_row()))

    async def aclose(self) -> None:
        self.closed = True


class _BlockingClosingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return _json_response(_payload(_row()))

    async def aclose(self) -> None:
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


async def test_close_is_async_idempotent_and_closes_owned_client() -> None:
    transport = _ClosingTransport()
    client = KorailHttpReplayClient(_plan(), transport=transport)

    await client.close()
    await client.close()

    assert transport.closed is True
    with pytest.raises(HttpReplaySessionInvalid):
        await client.search(_request())


async def test_close_finishes_owned_cleanup_before_propagating_cancellation() -> None:
    transport = _BlockingClosingTransport()
    client = KorailHttpReplayClient(_plan(), transport=transport)

    close_task = asyncio.create_task(client.close())
    await transport.close_started.wait()
    close_task.cancel()
    transport.release_close.set()

    with pytest.raises(asyncio.CancelledError):
        await close_task
    assert transport.closed is True
    await client.close()
