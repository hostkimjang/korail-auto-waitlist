from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain import Provider, SeatClass
from ..observations.contracts import SeatObservationRequest, SeatObservationResult
from ..provider_contracts import RouteValidationError
from ..provider_registry.contracts import ProviderCapabilities
from ..reservations.contracts import ReservationRequest, ReservationResult
from ..timetable_management.schemas import (
    SeatAvailability,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    StationCatalog,
    StationItem,
    TimetableItem,
)
from .base import OFFICIAL_BOOKING_URLS, RailProviderAdapter
from .timetable_support import normalize_departure_window, normalize_station_name


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
