from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from ..domain import Provider, SeatClass, SeatObservationStatus
from ..korail_sidecar.browser_contracts import (
    SOURCE_NAME,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
)
from ..observations.contracts import SeatObservationRequest, SeatObservationResult

__all__ = (
    "build_observation_search_request",
    "project_observation_result",
)

KOREA = ZoneInfo("Asia/Seoul")


def build_observation_search_request(
    request: SeatObservationRequest,
    *,
    enabled: bool,
    origin: str,
    destination: str,
    select_departure_from: Callable[[datetime, datetime], clock_time | None],
) -> BrowserSeatSearchRequest | None:
    """Build one service-day query only for a supported exact observation."""
    if not enabled:
        return None
    if request.provider != Provider.KORAIL or request.passenger_count != 1:
        return None
    if request.seat_class not in {SeatClass.STANDARD, SeatClass.FIRST}:
        return None

    local_departure = request.departure_at.astimezone(KOREA)
    departure_from = select_departure_from(
        local_departure.replace(minute=0, second=0, microsecond=0),
        local_departure.replace(hour=23, minute=59, second=59, microsecond=0),
    )
    if departure_from is None:
        return None
    return BrowserSeatSearchRequest(
        origin=origin,
        destination=destination,
        travel_date=local_departure.date(),
        # Future service days start at midnight. Today's picker cannot select elapsed
        # KST hours, so the injected selector chooses a bounded current/request hour.
        departure_from=departure_from,
        departure_to=clock_time(23, 59, 59),
        passenger_count=request.passenger_count,
    )


def project_observation_result(
    request: SeatObservationRequest,
    result: BrowserSeatSearchResult,
    *,
    normalize_train_number: Callable[[object], str],
    cache_ttl_seconds: int,
) -> list[SeatObservationResult] | None:
    """Project one exact train/time/class match without broadening provider evidence."""
    local_departure = request.departure_at.astimezone(KOREA)
    identity = (
        normalize_train_number(request.train_number),
        local_departure.strftime("%Y%m%d%H%M%S"),
    )
    snapshot = next(
        (
            item
            for item in result.trains
            if (
                normalize_train_number(item.train_number),
                item.departure_at.astimezone(KOREA).strftime("%Y%m%d%H%M%S"),
            )
            == identity
        ),
        None,
    )
    if snapshot is None:
        return None

    status = SeatObservationStatus(
        snapshot.standard if request.seat_class == SeatClass.STANDARD else snapshot.first
    )
    freshness_seconds = max(0, min(cache_ttl_seconds, 30))
    return [
        SeatObservationResult(
            seat_class=request.seat_class,
            status=status,
            source=SOURCE_NAME,
            observed_at=result.observed_at,
            fresh_until=result.observed_at + timedelta(seconds=freshness_seconds),
            delay_minutes=snapshot.expected_delay_minutes,
        )
    ]
