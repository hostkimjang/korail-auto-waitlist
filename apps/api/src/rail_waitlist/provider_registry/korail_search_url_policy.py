from __future__ import annotations

import re
from datetime import date
from datetime import time as clock_time
from urllib.parse import parse_qsl, urlencode, urlsplit

from .korail_search_contracts import KorailStationIdentity

OFFICIAL_KORAIL_RESULT_URL = "https://www.korail.com/ticket/search/list"
_GENERAL_SEARCH_KEYS = frozenset(
    {
        "srtCheckYn",
        "ebizCrossCheck",
        "adjStnScdlOfrFlg",
        "adjStnScdlOfrFlg2",
        "rtYn",
        "txtMenuId",
        "radJobId",
        "searchType",
        "txtGoStart",
        "txtGoEnd",
        "txtGoStartCode",
        "txtGoEndCode",
        "txtGoAbrdDt",
        "txtGoHour",
        "txtPsgFlg_1",
        "txtPsgFlg_2",
        "txtPsgFlg_3",
        "txtPsgFlg_4",
        "txtPsgFlg_5",
        "txtPsgFlg_8",
        "selGoSeat1",
        "txtSeatAttCd_4",
        "txtTrnGpCd",
        "tkTripChgQryFlg",
        "txtWkndUseFlg",
    }
)
_FIXED_GENERAL_VALUES = {
    "srtCheckYn": "N",
    "ebizCrossCheck": "N",
    "adjStnScdlOfrFlg": "N",
    "adjStnScdlOfrFlg2": "N",
    "rtYn": "N",
    "txtMenuId": "11",
    "radJobId": "1",
    "searchType": "GENERAL",
    "txtPsgFlg_1": "1",
    "txtPsgFlg_2": "0",
    "txtPsgFlg_3": "0",
    "txtPsgFlg_4": "0",
    "txtPsgFlg_5": "0",
    "txtPsgFlg_8": "0",
    "selGoSeat1": "015",
    "txtSeatAttCd_4": "015",
    "txtTrnGpCd": "100",
    "tkTripChgQryFlg": "Y",
    "txtWkndUseFlg": "Y",
}


def build_korail_general_search_url(
    *,
    origin: KorailStationIdentity,
    destination: KorailStationIdentity,
    travel_date: date,
    departure_time: clock_time,
) -> str:
    if origin.code == destination.code or origin.name == destination.name:
        raise ValueError("origin and destination must differ")
    if re.fullmatch(r"[0-9]{4}", origin.code) is None:
        raise ValueError("origin code must be exactly four digits")
    if re.fullmatch(r"[0-9]{4}", destination.code) is None:
        raise ValueError("destination code must be exactly four digits")
    params = (
        ("srtCheckYn", "N"),
        ("ebizCrossCheck", "N"),
        ("adjStnScdlOfrFlg", "N"),
        ("adjStnScdlOfrFlg2", "N"),
        ("rtYn", "N"),
        ("txtMenuId", "11"),
        ("radJobId", "1"),
        ("searchType", "GENERAL"),
        ("txtGoStart", origin.name),
        ("txtGoEnd", destination.name),
        ("txtGoStartCode", origin.code),
        ("txtGoEndCode", destination.code),
        ("txtGoAbrdDt", travel_date.strftime("%Y%m%d")),
        ("txtGoHour", departure_time.strftime("%H0000")),
        ("txtPsgFlg_1", "1"),
        ("txtPsgFlg_2", "0"),
        ("txtPsgFlg_3", "0"),
        ("txtPsgFlg_4", "0"),
        ("txtPsgFlg_5", "0"),
        ("txtPsgFlg_8", "0"),
        ("selGoSeat1", "015"),
        ("txtSeatAttCd_4", "015"),
        ("txtTrnGpCd", "100"),
        ("tkTripChgQryFlg", "Y"),
        ("txtWkndUseFlg", "Y"),
    )
    url = f"{OFFICIAL_KORAIL_RESULT_URL}?{urlencode(params)}"
    return validate_korail_general_search_url(url)


def validate_korail_general_search_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("official search URL length is invalid")
    parsed = urlsplit(value)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == "www.korail.com"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/ticket/search/list"
        and parsed.query
        and not parsed.fragment
    ):
        raise ValueError("official search URL origin or path is invalid")
    if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.query):
        raise ValueError("official search URL encoding is invalid")
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ValueError("official search URL query is invalid") from error
    if len(pairs) != 25:
        raise ValueError("official search URL must contain exactly 25 keys")
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)) or set(keys) != _GENERAL_SEARCH_KEYS:
        raise ValueError("official search URL keys are invalid")
    params = dict(pairs)
    if any(params[key] != expected for key, expected in _FIXED_GENERAL_VALUES.items()):
        raise ValueError("official search URL fixed values are invalid")
    if (
        not params["txtGoStart"].strip()
        or not params["txtGoEnd"].strip()
        or params["txtGoStart"] == params["txtGoEnd"]
        or len(params["txtGoStart"]) > 80
        or len(params["txtGoEnd"]) > 80
    ):
        raise ValueError("official search URL station names are invalid")
    codes = (params["txtGoStartCode"], params["txtGoEndCode"])
    if any(re.fullmatch(r"[0-9]{4}", code) is None for code in codes) or codes[0] == codes[1]:
        raise ValueError("official search URL station codes are invalid")
    try:
        raw_date = params["txtGoAbrdDt"]
        if re.fullmatch(r"[0-9]{8}", raw_date) is None:
            raise ValueError("date format")
        date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}")
    except ValueError as error:
        raise ValueError("official search URL date is invalid") from error
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3])0000", params["txtGoHour"]):
        raise ValueError("official search URL hour is invalid")
    return value
