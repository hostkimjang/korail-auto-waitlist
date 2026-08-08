from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..config import OFFICIAL_KORAIL_STATION_DATA_URL as OFFICIAL_KORAIL_STATION_DATA_URL
from ..provider_registry.korail_search_contracts import (
    KorailStationIdentity as KorailStationIdentity,
)

MIN_STATION_COUNT = 250
MAX_STATION_COUNT = 400
STATION_REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_STATION_ALIASES = {"김천(구미)": "김천구미", "여수엑스포": "여수expo", "신경주": "경주"}


class KorailStationIdentityUnavailable(RuntimeError):
    """The official station identity catalog cannot safely resolve a requested route."""


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
