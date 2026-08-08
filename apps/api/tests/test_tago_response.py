from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from rail_waitlist import providers
from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider
from rail_waitlist.provider_adapters import tago, tago_response
from rail_waitlist.provider_contracts import ProviderUnavailable
from rail_waitlist.timetable_management.schemas import StationItem

API_ROOT = Path(__file__).resolve().parents[1]


def valid_payload(*, items: object, include_pagination: bool = True) -> dict[str, object]:
    body: dict[str, object] = {"items": items}
    if include_pagination:
        body.update(totalCount=1, pageNo=1, numOfRows=100)
    return {
        "response": {
            "header": {"resultCode": "00"},
            "body": body,
        }
    }


def response_payload(*, body: object, header: object = None) -> dict[str, object]:
    return {
        "response": {
            "header": {"resultCode": "00"} if header is None else header,
            "body": body,
        }
    }


def test_tago_runtime_and_public_facade_share_canonical_parser_objects() -> None:
    assert tago.TagoPage is tago_response.TagoPage
    assert tago.response_page is tago_response.response_page
    assert providers.TagoPage is tago_response.TagoPage
    assert providers.response_page is tago_response.response_page
    assert tago_response.TagoPage.__module__ == "rail_waitlist.provider_adapters.tago_response"
    assert tago_response.response_page.__module__ == (
        "rail_waitlist.provider_adapters.tago_response"
    )


def test_response_page_preserves_single_object_and_empty_item_normalization() -> None:
    row = {"citycode": "11", "cityname": "서울특별시"}

    assert tago_response.response_page(valid_payload(items={"item": row})).items == [row]
    assert tago_response.response_page(valid_payload(items=None)).items == []
    assert tago_response.response_page(valid_payload(items="")).items == []


def test_only_explicit_city_code_path_may_omit_pagination_metadata() -> None:
    payload = valid_payload(
        items={"item": [{"citycode": "11", "cityname": "서울특별시"}]},
        include_pagination=False,
    )

    with pytest.raises(ProviderUnavailable, match="missing pagination metadata"):
        tago_response.response_page(payload)
    page = tago_response.response_page(payload, allow_unpaginated=True)
    assert page.total_count == 1
    assert page.page_no == 1
    assert page.num_rows == 100


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "TAGO returned an invalid response envelope"),
        ({}, "TAGO returned an invalid response envelope"),
        ({"response": []}, "TAGO returned an invalid response object"),
        (response_payload(body={}, header=[]), "TAGO returned an invalid response header"),
        (response_payload(body={}, header={}), "TAGO response header is missing resultCode"),
        (
            response_payload(body={}, header={"resultCode": "99"}),
            "TAGO returned an unsuccessful result",
        ),
        (
            {"response": {"header": {"resultCode": "00"}}},
            "TAGO response is missing body",
        ),
        (response_payload(body=[]), "TAGO returned an invalid response body"),
        (
            response_payload(body={"items": {"item": []}}),
            "TAGO response is missing pagination metadata",
        ),
        (
            response_payload(body={"totalCount": 0, "pageNo": 1, "numOfRows": 100}),
            "TAGO response is missing items",
        ),
        (
            valid_payload(items=[]),
            "TAGO returned invalid items",
        ),
        (
            valid_payload(items={"item": "not-a-list-or-object"}),
            "TAGO returned invalid items",
        ),
        (
            response_payload(
                body={
                    "items": {"item": []},
                    "totalCount": "many",
                    "pageNo": 1,
                    "numOfRows": 100,
                }
            ),
            "TAGO returned invalid totalCount",
        ),
        (
            response_payload(
                body={
                    "items": {"item": []},
                    "totalCount": 0,
                    "pageNo": 0,
                    "numOfRows": 100,
                }
            ),
            "TAGO returned invalid pageNo",
        ),
        (
            response_payload(
                body={
                    "items": {"item": []},
                    "totalCount": 0,
                    "pageNo": 1,
                    "numOfRows": 0,
                }
            ),
            "TAGO returned invalid numOfRows",
        ),
        (
            response_payload(
                body={
                    "items": {"item": []},
                    "totalCount": float("inf"),
                    "pageNo": 1,
                    "numOfRows": 100,
                }
            ),
            "TAGO returned invalid totalCount",
        ),
        (
            response_payload(
                body={
                    "items": {"item": []},
                    "totalCount": 0,
                    "pageNo": True,
                    "numOfRows": 100,
                }
            ),
            "TAGO returned invalid pageNo",
        ),
        (
            response_payload(
                body={
                    "items": {"item": []},
                    "totalCount": 0,
                    "pageNo": 1,
                    "numOfRows": 1.5,
                }
            ),
            "TAGO returned invalid numOfRows",
        ),
    ],
)
def test_response_page_has_stable_fail_closed_error_taxonomy(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(ProviderUnavailable) as caught:
        tago_response.response_page(payload)

    assert str(caught.value) == message


@pytest.mark.parametrize(
    "rows",
    [
        ["not-an-object"],
        [42],
        [{"citycode": "11"}, None],
    ],
)
def test_response_page_rejects_the_whole_page_when_any_row_is_not_an_object(
    rows: list[object],
) -> None:
    with pytest.raises(ProviderUnavailable) as caught:
        tago_response.response_page(valid_payload(items={"item": rows}))

    assert str(caught.value) == "TAGO returned an invalid item"


async def test_request_page_preserves_runtime_parser_patch_seam_and_city_only_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, bool]] = []

    def parser(
        payload: object,
        requested_page: int,
        requested_num_rows: int,
        *,
        allow_unpaginated: bool,
    ) -> tago_response.TagoPage:
        assert payload == {"ignored": True}
        calls.append((requested_page, requested_num_rows, allow_unpaginated))
        return tago_response.TagoPage([], 0, requested_page, requested_num_rows)

    monkeypatch.setattr(tago, "response_page", parser)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ignored": True}, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = tago.TagoClient(Settings(tago_service_key="decoded-key"), http_client)
        await client._request_page("GetCtyCodeList", {}, 1, 100)
        await client._request_page("getTrainAcctoSttnList", {}, 2, 50)

    assert calls == [(1, 100, True), (2, 50, False)]


async def test_unpaginated_http_response_is_accepted_only_for_city_codes() -> None:
    operations: list[str] = []
    payload = valid_payload(
        items={"item": [{"citycode": "11", "cityname": "서울특별시"}]},
        include_pagination=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        operations.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = tago.TagoClient(Settings(tago_service_key="decoded-key"), http_client)
        assert await client.city_codes() == [{"citycode": "11", "cityname": "서울특별시"}]
        with pytest.raises(ProviderUnavailable) as caught:
            await client._request("GetCtyAcctoTrainSttnList", {"cityCode": "11"})

    assert str(caught.value) == "TAGO response is missing pagination metadata"
    assert operations == ["GetCtyCodeList", "GetCtyAcctoTrainSttnList"]


async def test_malformed_city_row_is_rejected_before_cache_or_projection() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        rows: list[object] = [42] if calls == 1 else [{"citycode": "11", "cityname": "서울특별시"}]
        payload = valid_payload(items={"item": rows}, include_pagination=False)
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = tago.TagoClient(Settings(tago_service_key="decoded-key"), http_client)
        with pytest.raises(ProviderUnavailable) as caught:
            await client.city_codes()
        assert client._cached("cities") is None
        assert await client.city_codes() == [{"citycode": "11", "cityname": "서울특별시"}]

    assert str(caught.value) == "TAGO returned an invalid item"
    assert calls == 2


async def test_malformed_timetable_row_is_not_cached_and_normal_retry_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        rows: list[object] = [42]
        if calls == 2:
            rows = [
                {
                    "trainno": "101",
                    "traingradename": "KTX",
                    "depplandtime": "20260801090000",
                    "arrplandtime": "20260801100000",
                    "depplacename": "대전",
                    "arrplacename": "서울",
                }
            ]
        return httpx.Response(
            200,
            json=valid_payload(items={"item": rows}),
            request=request,
        )

    now = datetime.now(UTC)
    departure = datetime(2026, 8, 1, 8, tzinfo=ZoneInfo("Asia/Seoul"))
    cache_key = "timetable:raw:N1:N2:20260801"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = tago.TagoClient(Settings(tago_service_key="decoded-key"), http_client)
        client.hydrate_station_catalog(
            [
                StationItem(node_id="N1", name="대전", city_code="25", city_name="대전"),
                StationItem(node_id="N2", name="서울", city_code="11", city_name="서울"),
            ],
            retrieved_at=now,
            refresh_after=now + timedelta(hours=1),
        )

        with pytest.raises(ProviderUnavailable) as caught:
            await client.timetable(
                Provider.KORAIL,
                "대전",
                "서울",
                departure,
                "https://www.korail.com/ticket/main",
                "N1",
                "N2",
            )
        await asyncio.sleep(0)
        assert client._cached(cache_key) is None
        assert cache_key not in client._inflight

        result = await client.timetable(
            Provider.KORAIL,
            "대전",
            "서울",
            departure,
            "https://www.korail.com/ticket/main",
            "N1",
            "N2",
        )

    assert str(caught.value) == "TAGO returned an invalid item"
    assert [item.train_number for item in result] == ["101"]
    assert calls == 2


@pytest.mark.parametrize("import_order", ["canonical-first", "runtime-first", "facade-first"])
def test_tago_response_import_orders_share_exact_symbols(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    import rail_waitlist.provider_adapters.tago_response as canonical
    import rail_waitlist.provider_adapters.tago as runtime
    import rail_waitlist.providers as facade
elif sys.argv[1] == "runtime-first":
    import rail_waitlist.provider_adapters.tago as runtime
    import rail_waitlist.provider_adapters.tago_response as canonical
    import rail_waitlist.providers as facade
else:
    import rail_waitlist.providers as facade
    import rail_waitlist.provider_adapters.tago_response as canonical
    import rail_waitlist.provider_adapters.tago as runtime

print(json.dumps({
    "identities": [
        runtime.TagoPage is canonical.TagoPage,
        runtime.response_page is canonical.response_page,
        facade.TagoPage is canonical.TagoPage,
        facade.response_page is canonical.response_page,
    ],
    "modules": [canonical.TagoPage.__module__, canonical.response_page.__module__],
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "identities": [True, True, True, True],
        "modules": [
            "rail_waitlist.provider_adapters.tago_response",
            "rail_waitlist.provider_adapters.tago_response",
        ],
    }
