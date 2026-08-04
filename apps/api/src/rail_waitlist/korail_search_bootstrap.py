from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from datetime import time as clock_time
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from .config import OFFICIAL_KORAIL_STATION_DATA_URL

OFFICIAL_KORAIL_RESULT_URL = "https://www.korail.com/ticket/search/list"
MIN_STATION_COUNT = 250
MAX_STATION_COUNT = 400
STATION_REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_STATION_ALIASES = {"김천(구미)": "김천구미", "여수엑스포": "여수expo", "신경주": "경주"}
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


class KorailStationIdentityUnavailable(RuntimeError):
    """The official station identity catalog cannot safely resolve a requested route."""


@dataclass(frozen=True)
class KorailStationIdentity:
    code: str
    name: str


def _normalize_station_name(value: str) -> str:
    normalized = "".join(unicodedata.normalize("NFKC", value).split()).casefold()
    normalized = normalized.removesuffix("역")
    return _STATION_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class KorailStationIdentityCatalog:
    by_name: dict[str, KorailStationIdentity]
    by_code: dict[str, KorailStationIdentity]

    def resolve(self, name: str) -> KorailStationIdentity:
        normalized = _normalize_station_name(name)
        identity = self.by_name.get(normalized)
        if identity is None:
            raise KorailStationIdentityUnavailable("official station identity is unavailable")
        return identity


def parse_korail_station_identities(payload: object) -> KorailStationIdentityCatalog:
    if not isinstance(payload, dict):
        raise KorailStationIdentityUnavailable("official station identity schema is invalid")
    container = payload.get("stns")
    raw_items = container.get("stn") if isinstance(container, dict) else None
    if (
        not isinstance(raw_items, list)
        or not MIN_STATION_COUNT <= len(raw_items) <= MAX_STATION_COUNT
    ):
        raise KorailStationIdentityUnavailable("official station identity count is invalid")

    by_name: dict[str, KorailStationIdentity] = {}
    by_code: dict[str, KorailStationIdentity] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise KorailStationIdentityUnavailable("official station identity item is invalid")
        code = raw.get("stn_cd")
        name = raw.get("stn_nm")
        if (
            not isinstance(code, str)
            or re.fullmatch(r"[0-9]{4}", code) is None
            or not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 80
        ):
            raise KorailStationIdentityUnavailable("official station identity item is invalid")
        canonical_name = unicodedata.normalize("NFKC", name.strip())
        normalized_name = _normalize_station_name(canonical_name)
        identity = KorailStationIdentity(code=code, name=canonical_name)
        if code in by_code or normalized_name in by_name:
            raise KorailStationIdentityUnavailable("official station identity is duplicated")
        by_code[code] = identity
        by_name[normalized_name] = identity

    required = {"서울", "수서", "대전", "부산"}
    if not required.issubset(by_name):
        raise KorailStationIdentityUnavailable("official station identity sentinels are missing")
    return KorailStationIdentityCatalog(by_name=by_name, by_code=by_code)


class KorailStationIdentityResolver:
    def __init__(
        self,
        *,
        url: str = OFFICIAL_KORAIL_STATION_DATA_URL,
        ttl_seconds: float = 86_400,
        http_client: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.url = url
        self._ttl_seconds = ttl_seconds
        self._http_client = http_client
        self._monotonic = monotonic
        self._catalog: KorailStationIdentityCatalog | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def resolve_pair(
        self, origin: str, destination: str
    ) -> tuple[KorailStationIdentity, KorailStationIdentity]:
        catalog = await self._get_catalog()
        origin_identity = catalog.resolve(origin)
        destination_identity = catalog.resolve(destination)
        if origin_identity.code == destination_identity.code:
            raise KorailStationIdentityUnavailable("origin and destination must differ")
        return origin_identity, destination_identity

    async def _get_catalog(self) -> KorailStationIdentityCatalog:
        now = self._monotonic()
        if self._catalog is not None and now < self._expires_at:
            return self._catalog
        async with self._lock:
            now = self._monotonic()
            if self._catalog is not None and now < self._expires_at:
                return self._catalog
            owns_client = self._http_client is None
            client = self._http_client or httpx.AsyncClient(
                timeout=STATION_REQUEST_TIMEOUT, follow_redirects=False
            )
            try:
                try:
                    response = await client.get(
                        self.url, timeout=STATION_REQUEST_TIMEOUT, follow_redirects=False
                    )
                except (httpx.TimeoutException, httpx.TransportError) as error:
                    raise KorailStationIdentityUnavailable(
                        "official station identity source is unavailable"
                    ) from error
                if response.status_code != httpx.codes.OK:
                    raise KorailStationIdentityUnavailable(
                        "official station identity source returned an invalid status"
                    )
                try:
                    catalog = parse_korail_station_identities(response.json())
                except ValueError as error:
                    raise KorailStationIdentityUnavailable(
                        "official station identity source is not valid JSON"
                    ) from error
            finally:
                if owns_client:
                    await client.aclose()
            self._catalog = catalog
            self._expires_at = self._monotonic() + self._ttl_seconds
            return catalog


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
