from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import Settings, get_settings
from ..domain import Provider
from ..provider_contracts import ProviderUnavailable, RouteValidationError
from ..timetable_management import tago_timetable_projection as _tago_projection_owner
from ..timetable_management.schemas import SeatAvailability as SeatAvailability
from ..timetable_management.schemas import (
    StationCatalog,
    StationItem,
    TimetableItem,
)
from .tago_response import TagoPage as TagoPage
from .tago_response import response_page as response_page
from .timetable_support import (
    normalize_departure_window,
    normalize_station_name,
    official_unknown_seat_classes,
)

STATION_CITY_HINTS = {
    "서울": "서울",
    "수서": "서울",
    "부산": "부산",
}


class TagoClient:
    def __init__(
        self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.http_client = http_client
        self._cache: dict[str, tuple[float, Any, datetime]] = {}
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def _cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        self._cache.pop(key, None)
        return None

    def _cache_retrieved_at(self, key: str) -> datetime | None:
        entry = self._cache.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[2]
        self._cache.pop(key, None)
        return None

    def _remember(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        retrieved_at: datetime | None = None,
    ) -> Any:
        self._cache[key] = (
            time.monotonic() + (ttl or self.settings.tago_cache_ttl_seconds),
            value,
            retrieved_at or datetime.now(timezone.utc),
        )
        return value

    def hydrate_station_catalog(
        self,
        stations: list[StationItem],
        retrieved_at: datetime,
        refresh_after: datetime,
    ) -> None:
        now = datetime.now(timezone.utc)
        remaining_ttl = max(1, int((refresh_after - now).total_seconds()))
        self._remember(
            "station_catalog:all",
            {"stations": stations, "retrieved_at": retrieved_at},
            ttl=remaining_ttl,
            retrieved_at=retrieved_at,
        )

    async def _request_page(
        self, operation: str, params: dict[str, Any], page_no: int, num_rows: int
    ) -> TagoPage:
        service_key = self.settings.tago_key()
        if not service_key:
            raise ProviderUnavailable("TAGO service key is not configured")
        owns_client = self.http_client is None
        client = self.http_client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        try:
            response = await client.get(
                f"{self.settings.tago_base_url}/{operation}",
                params={
                    "serviceKey": service_key,
                    "_type": "json",
                    **params,
                    "numOfRows": num_rows,
                    "pageNo": page_no,
                },
                follow_redirects=False,
            )
            if response.is_redirect:
                raise ProviderUnavailable("TAGO redirect was blocked")
            response.raise_for_status()
            return response_page(
                response.json(),
                page_no,
                num_rows,
                # TAGO's live city-code operation returns the complete city list
                # without pagination fields. Other operations remain fail-closed.
                allow_unpaginated=operation == "GetCtyCodeList",
            )
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise ProviderUnavailable(f"TAGO request failed: {type(error).__name__}") from None
        finally:
            if owns_client:
                await client.aclose()

    async def _request(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        num_rows = int(params.get("numOfRows", 100))
        request_params = {
            key: value for key, value in params.items() if key not in {"numOfRows", "pageNo"}
        }
        items: list[dict[str, Any]] = []
        page_no = 1
        expected_total: int | None = None
        while True:
            page = await self._request_page(operation, request_params, page_no, num_rows)
            if page.page_no != page_no:
                raise ProviderUnavailable("TAGO returned an unexpected page number")
            if expected_total is None:
                expected_total = page.total_count
            elif page.total_count != expected_total:
                raise ProviderUnavailable("TAGO totalCount changed during pagination")
            items.extend(page.items)
            if len(items) >= expected_total:
                return items[:expected_total]
            if not page.items:
                raise ProviderUnavailable("TAGO pagination ended before totalCount")
            page_no += 1
            if page_no > 1000:
                raise ProviderUnavailable("TAGO pagination limit exceeded")

    async def city_codes(self) -> list[dict[str, Any]]:
        cached = self._cached("cities")
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cached("cities")
            if cached is not None:
                return cached
            cities = await self._request("GetCtyCodeList", {"numOfRows": 100, "pageNo": 1})
            if not cities:
                raise ProviderUnavailable("TAGO city catalog is empty")
            return self._remember("cities", cities, ttl=86400)

    async def city_stations(self, city_code: str) -> list[dict[str, Any]]:
        key = f"stations:{city_code}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cached(key)
            if cached is not None:
                return cached
            return self._remember(
                key,
                await self._request(
                    "GetCtyAcctoTrainSttnList",
                    {"cityCode": city_code, "numOfRows": 1000, "pageNo": 1},
                ),
                ttl=86400,
            )

    async def resolve_station(self, station_name: str) -> tuple[str, str]:
        wanted = normalize_station_name(station_name)
        cities = await self.city_codes()
        hint = STATION_CITY_HINTS.get(wanted)
        candidate_cities = cities
        if hint:
            candidate_cities = [
                city
                for city in cities
                if hint in str(city.get("cityname", city.get("cityName", "")))
            ]
        matches: list[tuple[str, str]] = []
        for city in candidate_cities:
            city_code = str(city.get("citycode", city.get("cityCode", "")))
            if not city_code:
                continue
            for station in await self.city_stations(city_code):
                name = str(station.get("nodename", station.get("nodeName", "")))
                node_id = str(station.get("nodeid", station.get("nodeId", "")))
                if node_id and normalize_station_name(name) == wanted:
                    matches.append((node_id, name))
        if len(matches) != 1:
            raise RouteValidationError("station must resolve to exactly one official TAGO node")
        return matches[0]

    async def station_catalog(self, provider: Provider) -> StationCatalog:
        cache_key = "station_catalog:all"
        snapshot = self._cached(cache_key)
        if snapshot is None:
            cities = await self.city_codes()
            source_times = [self._cache_retrieved_at("cities")]
            stations_by_node: dict[str, StationItem] = {}
            for city in cities:
                city_code = str(city.get("citycode", city.get("cityCode", ""))).strip()
                city_name = str(city.get("cityname", city.get("cityName", ""))).strip()
                if not city_code or not city_name:
                    continue
                city_station_cache_key = f"stations:{city_code}"
                for station in await self.city_stations(city_code):
                    node_id = str(station.get("nodeid", station.get("nodeId", ""))).strip()
                    name = str(station.get("nodename", station.get("nodeName", ""))).strip()
                    if not node_id or not name:
                        continue
                    stations_by_node.setdefault(
                        node_id,
                        StationItem(
                            node_id=node_id,
                            name=name,
                            city_code=city_code,
                            city_name=city_name,
                        ),
                    )
                source_times.append(self._cache_retrieved_at(city_station_cache_key))
            if not stations_by_node:
                raise ProviderUnavailable("TAGO station catalog is empty")
            retrieved_at = min(timestamp for timestamp in source_times if timestamp is not None)
            source_age_seconds = max(
                0,
                int((datetime.now(timezone.utc) - retrieved_at).total_seconds()),
            )
            remaining_ttl = max(1, 86400 - source_age_seconds)
            snapshot = self._remember(
                cache_key,
                {
                    "stations": sorted(
                        stations_by_node.values(),
                        key=lambda station: (station.name, station.node_id),
                    ),
                    "retrieved_at": retrieved_at,
                },
                ttl=remaining_ttl,
                retrieved_at=retrieved_at,
            )
        return StationCatalog(
            provider=provider,
            source="TAGO",
            retrieved_at=snapshot["retrieved_at"],
            catalog_scope="all_tago_train_stations",
            provider_membership="not_verified_by_source",
            note=(
                "TAGO가 제공하는 공용 철도역 카탈로그입니다. provider는 요청 문맥이며 "
                "각 역의 KORAIL/SRT 소속 또는 정차 여부를 뜻하지 않습니다."
            ),
            stations=snapshot["stations"],
        )

    async def fetch_station_catalog(self, provider: Provider) -> StationCatalog:
        """Collect a complete upstream snapshot without consulting or changing L1 caches."""
        retrieved_at = datetime.now(timezone.utc)
        cities = await self._request("GetCtyCodeList", {"numOfRows": 100, "pageNo": 1})
        if not cities:
            raise ProviderUnavailable("TAGO city catalog is empty")
        stations_by_node: dict[str, StationItem] = {}
        for city in cities:
            city_code = str(city.get("citycode", city.get("cityCode", ""))).strip()
            city_name = str(city.get("cityname", city.get("cityName", ""))).strip()
            if not city_code or not city_name:
                continue
            rows = await self._request(
                "GetCtyAcctoTrainSttnList",
                {"cityCode": city_code, "numOfRows": 1000, "pageNo": 1},
            )
            for station in rows:
                node_id = str(station.get("nodeid", station.get("nodeId", ""))).strip()
                name = str(station.get("nodename", station.get("nodeName", ""))).strip()
                if not node_id or not name:
                    continue
                stations_by_node.setdefault(
                    node_id,
                    StationItem(
                        node_id=node_id,
                        name=name,
                        city_code=city_code,
                        city_name=city_name,
                    ),
                )
        if not stations_by_node:
            raise ProviderUnavailable("TAGO station catalog is empty")
        return StationCatalog(
            provider=provider,
            source="TAGO",
            retrieved_at=retrieved_at,
            catalog_scope="all_tago_train_stations",
            provider_membership="not_verified_by_source",
            note=(
                "TAGO가 제공하는 공용 철도역 카탈로그입니다. provider는 요청 문맥이며 "
                "각 역의 KORAIL/SRT 소속 또는 정차 여부를 뜻하지 않습니다."
            ),
            stations=sorted(
                stations_by_node.values(), key=lambda station: (station.name, station.node_id)
            ),
        )

    async def timetable(
        self,
        provider: Provider,
        origin: str,
        destination: str,
        departure_from: datetime,
        official_booking_url: str,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        if normalize_station_name(origin) == normalize_station_name(destination):
            raise RouteValidationError("origin and destination must differ")
        korea = ZoneInfo("Asia/Seoul")
        local_from, local_to = normalize_departure_window(departure_from, departure_to)
        if origin_node_id is None or destination_node_id is None:
            raise RouteValidationError(
                "official timetable requests require both origin and destination node ids"
            )
        if origin_node_id == destination_node_id:
            raise RouteValidationError("origin and destination nodes must differ")
        catalog = await self.station_catalog(provider)
        stations_by_node = {station.node_id: station for station in catalog.stations}

        def validate_node(node_id: str, station_name: str) -> str:
            station = stations_by_node.get(node_id)
            names_match = station is not None and normalize_station_name(
                station.name
            ) == normalize_station_name(station_name)
            if not names_match:
                raise RouteValidationError(
                    "station node id and name must match the official TAGO catalog"
                )
            return station.node_id

        origin_node = validate_node(origin_node_id, origin)
        destination_node = validate_node(destination_node_id, destination)
        cache_key = f"timetable:raw:{origin_node}:{destination_node}:{local_from:%Y%m%d}"
        snapshot = self._cached(cache_key)
        if snapshot is None:
            async with self._lock:
                snapshot = self._cached(cache_key)
                task = self._inflight.get(cache_key) if snapshot is None else None
                if snapshot is None and task is None:

                    async def fetch_snapshot() -> dict[str, Any]:
                        rows = await self._request(
                            "GetStrtpntAlocFndTrainInfo",
                            {
                                "depPlaceId": origin_node,
                                "arrPlaceId": destination_node,
                                "depPlandTime": local_from.strftime("%Y%m%d"),
                                "numOfRows": 100,
                                "pageNo": 1,
                            },
                        )
                        return self._remember(
                            cache_key,
                            {"rows": rows, "retrieved_at": datetime.now(timezone.utc)},
                        )

                    task = asyncio.create_task(fetch_snapshot())
                    self._inflight[cache_key] = task
                    task.add_done_callback(
                        lambda completed: (
                            self._inflight.pop(cache_key, None)
                            if self._inflight.get(cache_key) is completed
                            else None
                        )
                    )
            if snapshot is None:
                assert task is not None
                snapshot = await asyncio.shield(task)
        rows = snapshot["rows"]
        retrieved_at = snapshot["retrieved_at"]
        return _tago_projection_owner.project_tago_timetable_rows(
            rows,
            provider=provider,
            origin=origin,
            destination=destination,
            departure_from=local_from,
            departure_to=local_to,
            retrieved_at=retrieved_at,
            official_booking_url=official_booking_url,
            service_timezone=korea,
            seat_class_projector=official_unknown_seat_classes,
        )


_default_tago_client: TagoClient | None = None


def default_tago_client() -> TagoClient:
    global _default_tago_client
    if _default_tago_client is None:
        _default_tago_client = TagoClient()
    return _default_tago_client
