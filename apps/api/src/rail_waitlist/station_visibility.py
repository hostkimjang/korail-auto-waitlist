from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import OFFICIAL_KORAIL_STATION_DATA_URL
from .provider_contracts import ProviderUnavailable
from .schemas import StationItem

KORAIL_STATION_DATA_URL = OFFICIAL_KORAIL_STATION_DATA_URL
MIN_KORAIL_ROSTER_COUNT = 250
MAX_KORAIL_ROSTER_COUNT = 400
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# These are name equivalences published or renamed by KORAIL, not fuzzy matching rules.
STATION_NAME_ALIASES: Mapping[str, str] = {
    "김천(구미)": "김천구미",
    "여수엑스포": "여수expo",
    "신경주": "경주",
}

# KORAIL's station-information asset also contains a small number of Seoul commuter stops.
# They are deliberately not exposed as intercity journey discovery entries.
NON_INTERCITY_STATION_NAMES = frozenset({"광운대", "노량진", "신도림", "서빙고", "왕십리", "옥수"})
REQUIRED_STATION_NAMES = frozenset({"서울", "수서", "대전", "부산"})


@dataclass(frozen=True)
class StationVisibilityRoster:
    """Validated KORAIL discoverability names, without provider-ownership semantics."""

    names: frozenset[str]
    retrieved_at: datetime
    etag: str | None
    last_modified: str | None


class StationVisibilityUnavailable(ProviderUnavailable):
    """The official discoverability roster cannot be safely used."""


def normalize_visibility_station_name(value: str) -> str:
    normalized = "".join(unicodedata.normalize("NFKC", value).split()).casefold()
    normalized = normalized.removesuffix("역")
    return STATION_NAME_ALIASES.get(normalized, normalized)


def filter_station_items(
    stations: Sequence[StationItem], roster: StationVisibilityRoster
) -> list[StationItem]:
    """Return original TAGO objects whose names are discoverable in the KORAIL roster."""

    if not stations:
        raise StationVisibilityUnavailable("TAGO station catalog is empty")

    visible: list[StationItem] = []
    for station in stations:
        normalized_name = normalize_visibility_station_name(station.name)
        if normalized_name in roster.names and normalized_name not in NON_INTERCITY_STATION_NAMES:
            visible.append(station)
    if not visible:
        raise StationVisibilityUnavailable("station visibility intersection is empty")
    return visible


class KorailStationVisibility:
    """Loads KORAIL's public station roster and filters TAGO StationItem values with it.

    The roster is only a discoverability allowlist. Matching a station does not establish
    KORAIL ownership, service availability, or a stop on a particular train/date.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        url: str = KORAIL_STATION_DATA_URL,
    ) -> None:
        self._http_client = http_client
        self.url = url

    async def load_roster(self) -> StationVisibilityRoster:
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
        )
        try:
            try:
                response = await client.get(
                    self.url,
                    follow_redirects=False,
                    timeout=REQUEST_TIMEOUT,
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                raise StationVisibilityUnavailable(
                    "KORAIL station visibility roster is unavailable"
                ) from error

            if response.status_code != httpx.codes.OK:
                raise StationVisibilityUnavailable(
                    "KORAIL station visibility roster returned an invalid status"
                )
            try:
                payload: object = response.json()
            except ValueError as error:
                raise StationVisibilityUnavailable(
                    "KORAIL station visibility roster is not valid JSON"
                ) from error

            return _parse_roster(
                payload,
                retrieved_at=datetime.now(UTC),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
        finally:
            if owns_client:
                await client.aclose()

    async def filter_stations(self, stations: Sequence[StationItem]) -> list[StationItem]:
        return filter_station_items(stations, await self.load_roster())


def _parse_roster(
    payload: object,
    *,
    retrieved_at: datetime,
    etag: str | None,
    last_modified: str | None,
) -> StationVisibilityRoster:
    if not isinstance(payload, dict):
        raise StationVisibilityUnavailable("KORAIL station visibility schema is invalid")
    container = payload.get("stns")
    if not isinstance(container, dict):
        raise StationVisibilityUnavailable("KORAIL station visibility schema is invalid")
    raw_items = container.get("stn")
    if not isinstance(raw_items, list) or not (
        MIN_KORAIL_ROSTER_COUNT <= len(raw_items) <= MAX_KORAIL_ROSTER_COUNT
    ):
        raise StationVisibilityUnavailable("KORAIL station visibility count is invalid")

    station_codes: set[str] = set()
    names: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            raise StationVisibilityUnavailable("KORAIL station visibility item is invalid")
        code = item.get("stn_cd")
        name = item.get("stn_nm")
        if (
            not isinstance(code, str)
            or not code.strip()
            or len(code.strip()) > 20
            or not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 80
        ):
            raise StationVisibilityUnavailable("KORAIL station visibility item is invalid")
        normalized_name = normalize_visibility_station_name(name)
        if not normalized_name or code.strip() in station_codes or normalized_name in names:
            raise StationVisibilityUnavailable("KORAIL station visibility item is duplicated")
        station_codes.add(code.strip())
        names.add(normalized_name)

    if not REQUIRED_STATION_NAMES.issubset(names):
        raise StationVisibilityUnavailable("KORAIL station visibility sentinels are missing")

    names.difference_update(NON_INTERCITY_STATION_NAMES)
    return StationVisibilityRoster(
        names=frozenset(names),
        retrieved_at=retrieved_at,
        etag=etag,
        last_modified=last_modified,
    )
