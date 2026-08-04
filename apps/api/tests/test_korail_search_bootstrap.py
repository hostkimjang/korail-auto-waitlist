from __future__ import annotations

from datetime import date, time
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from rail_waitlist.korail_search_bootstrap import (
    KorailStationIdentity,
    KorailStationIdentityResolver,
    KorailStationIdentityUnavailable,
    build_korail_general_search_url,
    parse_korail_station_identities,
    validate_korail_general_search_url,
)


def station_payload() -> dict[str, object]:
    stations = [
        {"stn_cd": "0001", "stn_nm": "서울"},
        {"stn_cd": "0551", "stn_nm": "수서"},
        {"stn_cd": "0010", "stn_nm": "대전"},
        {"stn_cd": "0020", "stn_nm": "부산"},
    ]
    stations.extend(
        {"stn_cd": f"{index:04d}", "stn_nm": f"테스트{index}"} for index in range(1000, 1246)
    )
    return {"stns": {"stn": stations}}


def test_station_identity_parser_preserves_exact_official_code_name_pairs() -> None:
    catalog = parse_korail_station_identities(station_payload())

    assert catalog.resolve("서울역") == KorailStationIdentity(code="0001", name="서울")
    assert catalog.by_code["0010"].name == "대전"


def test_station_identity_parser_rejects_non_four_digit_codes() -> None:
    payload = station_payload()
    payload["stns"]["stn"][0]["stn_cd"] = "1"  # type: ignore[index]

    with pytest.raises(KorailStationIdentityUnavailable):
        parse_korail_station_identities(payload)


def test_station_identity_parser_rejects_non_ascii_decimal_codes() -> None:
    payload = station_payload()
    payload["stns"]["stn"][0]["stn_cd"] = "٠٠٠١"  # type: ignore[index]

    with pytest.raises(KorailStationIdentityUnavailable):
        parse_korail_station_identities(payload)


def test_general_search_builder_emits_exact_safe_25_key_contract() -> None:
    url = build_korail_general_search_url(
        origin=KorailStationIdentity("0010", "대전"),
        destination=KorailStationIdentity("0001", "서울"),
        travel_date=date(2026, 8, 3),
        departure_time=time(5, 55),
    )
    parsed = urlsplit(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.korail.com"
    assert parsed.path == "/ticket/search/list"
    assert len(params) == 25
    assert params["txtGoStartCode"] == "0010"
    assert params["txtGoEndCode"] == "0001"
    assert params["txtGoAbrdDt"] == "20260803"
    assert params["txtGoHour"] == "050000"
    assert params["txtTrnGpCd"] == "100"
    assert params["srtCheckYn"] == "N"
    assert {"mutMrkVrfCd", "srtJob", "selectedTrainList", "limitStartDate"}.isdisjoint(params)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda url: url + "&reqTime=",
        lambda url: url + "&txtMenuId=11",
        lambda url: url.replace("www.korail.com", "evil.example"),
        lambda url: url.replace("/ticket/search/list", "/ticket/search/general"),
        lambda url: url.replace("https://", "https://user@"),
        lambda url: url.replace("www.korail.com", "www.korail.com:443"),
        lambda url: url + "#fragment",
        lambda url: url.replace("txtGoAbrdDt=20260803", "txtGoAbrdDt=202608031"),
        lambda url: url + ("x" * 2048),
    ],
)
def test_general_search_validator_rejects_extra_duplicate_or_unsafe_urls(mutate) -> None:
    valid = build_korail_general_search_url(
        origin=KorailStationIdentity("0010", "대전"),
        destination=KorailStationIdentity("0001", "서울"),
        travel_date=date(2026, 8, 3),
        departure_time=time(5),
    )

    with pytest.raises(ValueError):
        validate_korail_general_search_url(mutate(valid))


@pytest.mark.asyncio
async def test_station_resolver_uses_one_fetch_until_ttl_expires() -> None:
    requests = 0
    now = 10.0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json=station_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = KorailStationIdentityResolver(
            http_client=client, ttl_seconds=30, monotonic=lambda: now
        )
        first = await resolver.resolve_pair("대전", "서울")
        await resolver.resolve_pair("대전", "서울")
        now = 41.0
        await resolver.resolve_pair("대전", "서울")

    assert first == (
        KorailStationIdentity("0010", "대전"),
        KorailStationIdentity("0001", "서울"),
    )
    assert requests == 2
