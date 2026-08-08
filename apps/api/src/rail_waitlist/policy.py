from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta

from .domain import Provider, SeatObservationMode
from .watch_management.provider_failure_policy import (
    BLOCK_COOLDOWN as BLOCK_COOLDOWN,
)
from .watch_management.provider_failure_policy import (
    PROTECTION_SIGNALS as PROTECTION_SIGNALS,
)
from .watch_management.provider_failure_policy import (
    RATE_LIMIT_COOLDOWN as RATE_LIMIT_COOLDOWN,
)
from .watch_management.provider_failure_policy import (
    ErrorPolicyResult as ErrorPolicyResult,
)
from .watch_management.provider_failure_policy import (
    classify_provider_failure as classify_provider_failure,
)
from .watch_management.provider_failure_policy import (
    cooldown_until as cooldown_until,
)


def build_watch_dedupe_key(
    provider: Provider,
    origin: str,
    destination: str,
    travel_date: date,
    time_from: time,
    time_to: time,
    seat_class: str,
    passenger_count: int,
    train_numbers: list[str],
    origin_node_id: str | None = None,
    destination_node_id: str | None = None,
) -> str:
    canonical = {
        "provider": provider.value,
        "origin": origin.strip().casefold(),
        "origin_node_id": origin_node_id.strip() if origin_node_id is not None else None,
        "destination": destination.strip().casefold(),
        "destination_node_id": (
            destination_node_id.strip() if destination_node_id is not None else None
        ),
        "travel_date": travel_date.isoformat(),
        "time_from": time_from.isoformat(),
        "time_to": time_to.isoformat(),
        "seat_class": seat_class.casefold(),
        "passenger_count": passenger_count,
        "train_numbers": sorted(set(train_numbers)),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


OBSERVATION_INTERVAL_MIN_SECONDS = 1
OBSERVATION_INTERVAL_MAX_SECONDS = 600


def next_interval(
    now: datetime,
    departure_at: datetime,
    unchanged_runs: int = 0,
    *,
    observation_mode: SeatObservationMode = SeatObservationMode.BALANCED,
    focused_interval_seconds: int = 25,
    balanced_interval_seconds: int = OBSERVATION_INTERVAL_MAX_SECONDS,
    observation_interval_seconds: int = 5,
) -> timedelta:
    # Legacy mode/per-watch arguments remain callable during a rolling deployment but no
    # longer influence cadence. All active watches use the administrator's single value.
    del now, departure_at, unchanged_runs, observation_mode, focused_interval_seconds
    del balanced_interval_seconds
    target = min(
        OBSERVATION_INTERVAL_MAX_SECONDS,
        max(OBSERVATION_INTERVAL_MIN_SECONDS, observation_interval_seconds),
    )
    return timedelta(seconds=target)
