from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import Settings, get_settings
from .database import SessionFactory
from .domain import Provider, ReservationOutcome, SeatClass
from .korail_execution import (
    KorailSeatObserver,
    ManagedKorailSeatObserver,
    default_korail_execution_source,
    korail_background_monitoring_enabled,
)
from .provider_accounts import ProviderCredentials, get_enabled_provider_credentials
from .provider_adapters.base import OFFICIAL_BOOKING_URLS, RailProviderAdapter
from .provider_adapters.execution import FailClosedExecutionAdapter
from .provider_adapters.tago import TagoPage, response_page
from .provider_adapters.timetable_support import (
    normalize_departure_window,
    normalize_station_name,
    official_unknown_seat_classes,
)
from .provider_contracts import (
    ExecutionProvider,
    ProviderUnavailable,
    RouteValidationError,
    TimetableProvider,
)
from .reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from .schemas import (
    ProviderCapabilities,
    ReservationRequest,
    ReservationResult,
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    SeatObservationRequest,
    SeatObservationResult,
    StationCatalog,
    StationItem,
    TimetableItem,
)
from .srt_execution import (
    SrtSeatObserver,
    default_srt_execution_source,
    srt_background_monitoring_enabled,
)
from .srt_provider_adapter import SrtProviderAdapterClient
from .srt_reservation import SrtReservationExecutor, default_srt_reservation_executor
from .srt_station_roster import (
    SrtStationRoster,
    SrtStationRosterUnavailable,
    load_srt_station_roster,
)

STATION_CITY_HINTS = {
    "서울": "서울",
    "수서": "서울",
    "부산": "부산",
}

ProviderCredentialLoader = Callable[[Provider], Awaitable[ProviderCredentials | None]]


async def default_provider_credential_loader(provider: Provider) -> ProviderCredentials | None:
    async with SessionFactory() as session:
        return await get_enabled_provider_credentials(session, provider)


def mock_seat_classes(index: int, observed_at: datetime) -> list[SeatClassAvailability]:
    status_pairs = [
        ("available", "sold_out"),
        ("sold_out", "waitlist_available"),
        ("stale", "error"),
    ]
    booking_url = OFFICIAL_BOOKING_URLS[Provider.MOCK]

    def actions(status: str) -> list[SeatAvailabilityAction]:
        if status == "available":
            return [
                SeatAvailabilityAction(kind="official_check", url=booking_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        if status == "sold_out":
            return [SeatAvailabilityAction(kind="add_to_watch")]
        if status == "waitlist_available":
            return [
                SeatAvailabilityAction(kind="official_waitlist", url=booking_url),
                SeatAvailabilityAction(kind="add_to_watch"),
            ]
        if status in {"stale", "error"}:
            return [SeatAvailabilityAction(kind="retry_provider")]
        return [SeatAvailabilityAction(kind="official_check", url=booking_url)]

    fares = (59_800, 83_700)
    return [
        SeatClassAvailability(
            seat_class=seat_class,
            status=status,
            provenance=SeatAvailabilityProvenance(
                kind="mock",
                source="mock",
                observed_at=observed_at,
            ),
            fare=fare,
            actions=actions(status),
        )
        for seat_class, status, fare in zip(
            (SeatClass.STANDARD, SeatClass.FIRST),
            status_pairs[index % len(status_pairs)],
            fares,
            strict=True,
        )
    ]


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
        result: list[TimetableItem] = []
        for row in rows:
            grade = str(row.get("traingradename", ""))
            normalized_grade = grade.upper()
            if provider == Provider.KORAIL and "KTX" not in normalized_grade:
                continue
            if provider == Provider.SRT and "SRT" not in normalized_grade:
                continue
            try:
                departure_at = datetime.strptime(
                    str(row["depplandtime"]).zfill(14), "%Y%m%d%H%M%S"
                ).replace(tzinfo=korea)
                arrival_at = datetime.strptime(
                    str(row["arrplandtime"]).zfill(14), "%Y%m%d%H%M%S"
                ).replace(tzinfo=korea)
            except (KeyError, ValueError):
                continue
            if departure_at < local_from:
                continue
            if local_to is not None and departure_at > local_to:
                continue
            raw_fare = str(row.get("adultcharge", "")).replace(",", "").strip()
            try:
                adult_fare = int(raw_fare) if raw_fare else None
            except ValueError:
                adult_fare = None
            result.append(
                TimetableItem(
                    provider=provider,
                    train_number=str(row.get("trainno", "")),
                    train_type=grade or provider.value,
                    origin=str(row.get("depplacename", origin)),
                    destination=str(row.get("arrplacename", destination)),
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    adult_fare=adult_fare,
                    timetable_source="TAGO",
                    timetable_retrieved_at=retrieved_at,
                    availability=SeatAvailability(status="unavailable"),
                    seat_classes=official_unknown_seat_classes(
                        official_booking_url,
                        reason="source_not_configured",
                    ),
                    official_booking_url=official_booking_url,
                )
            )
        return result


_default_tago_client: TagoClient | None = None


def default_tago_client() -> TagoClient:
    global _default_tago_client
    if _default_tago_client is None:
        _default_tago_client = TagoClient()
    return _default_tago_client


class OfficialTimetableAdapter(RailProviderAdapter):
    def __init__(
        self,
        provider: Provider,
        settings: Settings | None = None,
        tago_client: TagoClient | None = None,
        srt_station_roster: SrtStationRoster | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()
        self.tago_client = tago_client or default_tago_client()
        self._srt_station_roster = srt_station_roster

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=True,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
            note="TAGO 공식 시간표와 철도사 예매 링크만 제공합니다. 예약대기 자동 API는 아닙니다.",
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        if self.provider == Provider.SRT:
            if normalize_station_name(origin) == normalize_station_name(destination):
                raise RouteValidationError("origin and destination must differ")
            if origin_node_id is None or destination_node_id is None:
                raise RouteValidationError(
                    "official timetable requests require both origin and destination node ids"
                )
            if origin_node_id == destination_node_id:
                raise RouteValidationError("origin and destination nodes must differ")
            try:
                roster = self._srt_station_roster or load_srt_station_roster()
            except SrtStationRosterUnavailable as error:
                raise ProviderUnavailable("SRT station roster is unavailable") from error
            if not roster.supports_route(origin, destination):
                return []
        return await self.tago_client.timetable(
            self.provider,
            origin,
            destination,
            departure_from,
            self.official_booking_url(),
            origin_node_id,
            destination_node_id,
            departure_to,
        )

    async def stations(self) -> StationCatalog:
        return await self.tago_client.station_catalog(self.provider)


class MockProviderAdapter(RailProviderAdapter):
    provider = Provider.MOCK

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=True,
            official_booking_link=True,
            official_waitlist_link=True,
            seat_monitoring=True,
            reservation_once=True,
            note="상태 전이 검증용 mock provider이며 외부 요청을 보내지 않습니다.",
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        if (origin_node_id is None) != (destination_node_id is None):
            raise RouteValidationError("origin and destination node ids must be provided together")
        if origin_node_id is not None and destination_node_id is not None:
            stations = {station.node_id: station for station in (await self.stations()).stations}
            if (
                origin_node_id == destination_node_id
                or origin_node_id not in stations
                or destination_node_id not in stations
                or normalize_station_name(stations[origin_node_id].name)
                != normalize_station_name(origin)
                or normalize_station_name(stations[destination_node_id].name)
                != normalize_station_name(destination)
            ):
                raise RouteValidationError("mock station node id and name pair is invalid")
        local_from, local_to = normalize_departure_window(departure_from, departure_to)
        window_to = local_to or local_from + timedelta(minutes=80)
        fixture_count = ((window_to - local_from) // timedelta(minutes=40)) + 1
        observed_at = datetime.now(timezone.utc)
        return [
            TimetableItem(
                provider=self.provider,
                train_number=f"MOCK-{index + 1:03d}",
                train_type="MOCK",
                origin=origin,
                destination=destination,
                departure_at=local_from + timedelta(minutes=index * 40),
                arrival_at=local_from + timedelta(minutes=120 + index * 40),
                adult_fare=10000 + index * 1000,
                timetable_source="mock",
                timetable_retrieved_at=observed_at,
                availability=SeatAvailability(
                    status="available",
                    source="mock",
                    observed_at=observed_at,
                ),
                seat_classes=mock_seat_classes(index, observed_at),
                official_booking_url=self.official_booking_url(),
            )
            for index in range(fixture_count)
        ]

    async def stations(self) -> StationCatalog:
        return StationCatalog(
            provider=self.provider,
            source="mock",
            retrieved_at=datetime.now(timezone.utc),
            catalog_scope="mock",
            provider_membership="mock",
            note="외부 요청 없이 상태 전이 검증에만 사용하는 mock 역 카탈로그입니다.",
            stations=[
                StationItem(node_id="MOCK-SEOUL", name="서울", city_code="11", city_name="서울"),
                StationItem(node_id="MOCK-SUSEO", name="수서", city_code="11", city_name="서울"),
                StationItem(node_id="MOCK-DAEJEON", name="대전", city_code="30", city_name="대전"),
                StationItem(node_id="MOCK-BUSAN", name="부산", city_code="26", city_name="부산"),
            ],
        )

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        observed_at = datetime.now(timezone.utc)
        statuses = {
            SeatClass.STANDARD: "available",
            SeatClass.FIRST: "sold_out",
            SeatClass.INFANT: "not_offered",
            SeatClass.FREE: "not_offered",
            SeatClass.WAITLIST: "waitlist_available",
        }
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=statuses[request.seat_class],
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
            )
        ]

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        observed_at = datetime.now(timezone.utc)
        return ReservationResult(
            outcome="payment_required",
            source="mock",
            observed_at=observed_at,
            payment_deadline=observed_at + timedelta(minutes=20),
            official_handoff_url=self.official_booking_url(),
        )


class ExperimentalRailAdapter(RailProviderAdapter):
    def __init__(self, provider: Provider, settings: Settings | None = None) -> None:
        self.provider = provider
        self.settings = settings or get_settings()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
            experimental=True,
            enabled=self.settings.experimental_rail_enabled,
            note="실험 어댑터는 미구현이며 비공식 endpoint를 호출하지 않습니다.",
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        raise NotImplementedError("experimental provider has no external implementation")

    async def stations(self) -> StationCatalog:
        raise NotImplementedError("experimental provider has no external implementation")


class SrtLiveExecutionAdapter(RailProviderAdapter):
    """Background-only SRT observation adapter with an explicit three-part opt-in."""

    provider = Provider.SRT

    def __init__(
        self,
        settings: Settings | None = None,
        source: SrtSeatObserver | None = None,
        credential_loader: ProviderCredentialLoader = default_provider_credential_loader,
        reservation_executor: SrtReservationExecutor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._source = source
        self._owns_source = source is None
        self._credential_loader = credential_loader
        self._reservation_executor = reservation_executor
        if self._reservation_executor is None and not self.settings.srt_provider_adapter_enabled:
            self._reservation_executor = default_srt_reservation_executor()

    def _source_instance(self) -> SrtSeatObserver:
        if self._source is None:
            self._source = default_srt_execution_source(self.settings)
        return self._source

    def capabilities(self) -> ProviderCapabilities:
        enabled = srt_background_monitoring_enabled(self.settings)
        reservation_enabled = enabled and self.settings.srt_reservation_once_enabled
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=False,
            official_waitlist_link=False,
            seat_monitoring=enabled,
            reservation_once=reservation_enabled,
            experimental=True,
            enabled=enabled,
            note=(
                "SRT 계정 없는 좌석 관측을 background 감시에 사용합니다. "
                "명시적으로 활성화한 대기는 저장된 계정으로 결제 직전 임시 예약을 "
                "한 번만 시도하며 결제는 실행하지 않습니다."
            ),
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        raise ProviderUnavailable("SRT execution provider does not expose timetables")

    async def stations(self) -> StationCatalog:
        raise ProviderUnavailable("SRT execution provider does not expose stations")

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        source = self._source_instance()
        return await source.observe(
            request,
            origin=request.origin,
            destination=request.destination,
        )

    async def observation_deferred_until(self) -> datetime | None:
        if not self.capabilities().seat_monitoring:
            return None
        source = self._source_instance()
        return await source.observation_deferred_until()

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        try:
            credentials = await self._credential_loader(self.provider)
        except RuntimeError:
            credentials = None
        if credentials is None:
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="srtrain-2.6.7-reservation",
                observed_at=datetime.now(timezone.utc),
            )
        if (
            request.expected_credential_version is not None
            and credentials.credential_version != request.expected_credential_version
        ):
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="srtrain-2.6.7-reservation",
                observed_at=datetime.now(timezone.utc),
                credential_version=request.expected_credential_version,
            )
        reservation_executor = self._reservation_executor
        if reservation_executor is None:
            source = self._source_instance()
            if not isinstance(source, SrtProviderAdapterClient):
                raise ProviderUnavailable("SRT provider adapter is unavailable")
            reservation_executor = source
        result = await reservation_executor.reserve_once(request, credentials)
        return result.model_copy(update={"credential_version": credentials.credential_version})

    async def _confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        try:
            credentials = await self._credential_loader(self.provider)
        except RuntimeError:
            credentials = None
        if credentials is None or credentials.credential_version != target.credential_version:
            return ReservationConfirmationResult(
                provider=self.provider,
                outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
                source="srtrain-reservation-list",
                observed_at=datetime.now(timezone.utc),
            )
        confirmer = self._reservation_executor
        if confirmer is None:
            source = self._source_instance()
            if not isinstance(source, SrtProviderAdapterClient):
                raise ProviderUnavailable("SRT provider adapter is unavailable")
            confirmer = source
        return await confirmer.confirm_reservation(target, credentials)

    async def drain_pending_calls(self) -> None:
        if self._source is not None:
            await self._source.drain_pending_calls()

    async def aclose(self) -> None:
        if self._owns_source and self._source is not None:
            close_source = getattr(self._source, "aclose", None)
            if close_source is not None:
                await close_source()
            self._source = None


class KorailBrowserExecutionAdapter(RailProviderAdapter):
    """Background-only official-page observation with an explicit three-part opt-in."""

    provider = Provider.KORAIL

    def __init__(
        self,
        settings: Settings | None = None,
        source: KorailSeatObserver | None = None,
        credential_loader: ProviderCredentialLoader = default_provider_credential_loader,
    ) -> None:
        self.settings = settings or get_settings()
        self._source = source
        self._owns_source = source is None
        self._credential_loader = credential_loader

    def _source_instance(self) -> KorailSeatObserver:
        if self._source is None:
            self._source = default_korail_execution_source(self.settings)
        return self._source

    def capabilities(self) -> ProviderCapabilities:
        enabled = korail_background_monitoring_enabled(self.settings)
        reservation_enabled = enabled and self.settings.korail_reservation_once_enabled
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=False,
            official_waitlist_link=False,
            seat_monitoring=enabled,
            reservation_once=reservation_enabled,
            experimental=True,
            enabled=enabled,
            note=(
                "서버 관리 표준 Chromium의 공식 결과 DOM을 background 감시에만 사용합니다. "
                "보호 응답에서는 shared cooldown으로 중단합니다. 명시적으로 활성화한 "
                "대기는 결제 직전 임시 예약을 한 번만 시도하며 결제는 실행하지 않습니다."
            ),
        )

    async def timetable(
        self,
        origin: str,
        destination: str,
        departure_from: datetime,
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
        departure_to: datetime | None = None,
    ) -> list[TimetableItem]:
        raise ProviderUnavailable("KORAIL execution provider does not expose timetables")

    async def stations(self) -> StationCatalog:
        raise ProviderUnavailable("KORAIL execution provider does not expose stations")

    async def _observe_seats(self, request: SeatObservationRequest) -> list[SeatObservationResult]:
        return await self._source_instance().observe(
            request,
            origin=request.origin,
            destination=request.destination,
        )

    async def observation_deferred_until(self) -> datetime | None:
        if not self.capabilities().seat_monitoring:
            return None
        return await self._source_instance().observation_deferred_until()

    async def _reserve_once(self, request: ReservationRequest) -> ReservationResult:
        try:
            credentials = await self._credential_loader(self.provider)
        except RuntimeError:
            credentials = None
        if credentials is None:
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="korail-pydoll-reservation",
                observed_at=datetime.now(timezone.utc),
            )
        if (
            request.expected_credential_version is not None
            and credentials.credential_version != request.expected_credential_version
        ):
            return ReservationResult(
                outcome=ReservationOutcome.AUTH_REQUIRED,
                source="korail-pydoll-reservation",
                observed_at=datetime.now(timezone.utc),
                credential_version=request.expected_credential_version,
            )
        result = await self._source_instance().reserve_once(request, credentials)
        return result.model_copy(update={"credential_version": credentials.credential_version})

    async def _confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        return await self._source_instance().confirm_reservation(target)

    async def drain_pending_calls(self) -> None:
        if self._source is not None:
            await self._source.drain_pending_calls()

    async def aclose(self) -> None:
        if self._owns_source and isinstance(self._source, ManagedKorailSeatObserver):
            await self._source.aclose()
            self._source = None


def get_timetable_provider(
    provider: Provider, settings: Settings | None = None
) -> TimetableProvider:
    """Resolve adapters used only by request-time timetable and station flows."""

    settings = settings or get_settings()
    if provider == Provider.MOCK:
        return MockProviderAdapter()
    return OfficialTimetableAdapter(provider, settings)


def get_execution_provider(
    provider: Provider, settings: Settings | None = None
) -> ExecutionProvider:
    """Resolve adapters allowed to execute background observation or reservation work."""

    settings = settings or get_settings()
    if provider == Provider.MOCK:
        return MockProviderAdapter()
    if provider == Provider.KORAIL:
        return KorailBrowserExecutionAdapter(settings)
    if provider == Provider.SRT:
        return SrtLiveExecutionAdapter(settings)
    return FailClosedExecutionAdapter(provider)


def get_provider(provider: Provider, settings: Settings | None = None) -> TimetableProvider:
    """Compatibility alias for the historical request-time provider registry."""

    return get_timetable_provider(provider, settings)


def list_capabilities(settings: Settings | None = None) -> list[ProviderCapabilities]:
    settings = settings or get_settings()
    official_capabilities: list[ProviderCapabilities] = []
    for provider in (Provider.KORAIL, Provider.SRT):
        timetable = get_timetable_provider(provider, settings).capabilities()
        execution = get_execution_provider(provider, settings).capabilities()
        official_capabilities.append(
            timetable.model_copy(
                update={
                    "seat_monitoring": execution.seat_monitoring,
                    "reservation_once": execution.reservation_once,
                    "note": f"{timetable.note or ''} {execution.note or ''}".strip(),
                }
            )
        )
    return [
        *official_capabilities,
        get_timetable_provider(Provider.MOCK, settings).capabilities(),
        ExperimentalRailAdapter(Provider.KORAIL, settings).capabilities(),
        ExperimentalRailAdapter(Provider.SRT, settings).capabilities(),
    ]
