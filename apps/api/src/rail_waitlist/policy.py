from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone

from .domain import Provider, SeatObservationMode, WatchStatus
from .schemas import ErrorPolicyResult

RATE_LIMIT_COOLDOWN = timedelta(minutes=30)
BLOCK_COOLDOWN = timedelta(minutes=5)
PROTECTION_SIGNALS = frozenset(
    {
        "-8002",
        "-8003",
        "403",
        "abnormal_access",
        "access_denied",
        "automation_detected",
        "bot_challenge",
        "captcha",
        "code_-8002",
        "code_-8003",
        "korail_-8002",
        "korail_-8003",
        "macro_err1",
        "netfunnel",
        "queue_challenge",
    }
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


def classify_provider_failure(code: int | str, now: datetime | None = None) -> ErrorPolicyResult:
    now = now or datetime.now(timezone.utc)
    normalized = str(code).strip().casefold().replace(" ", "_")
    if normalized == "429":
        return ErrorPolicyResult(
            status=WatchStatus.COOLDOWN,
            cooldown_seconds=int(RATE_LIMIT_COOLDOWN.total_seconds()),
            requires_manual_resume=False,
            official_handoff_required=False,
            reason="provider_rate_limited",
        )
    if normalized in PROTECTION_SIGNALS:
        return ErrorPolicyResult(
            status=WatchStatus.AUTH_REQUIRED,
            cooldown_seconds=int(BLOCK_COOLDOWN.total_seconds()),
            requires_manual_resume=True,
            official_handoff_required=True,
            reason="provider_block_or_challenge",
        )
    if normalized in {"401", "auth", "login_failed"}:
        return ErrorPolicyResult(
            status=WatchStatus.AUTH_REQUIRED,
            cooldown_seconds=None,
            requires_manual_resume=True,
            official_handoff_required=False,
            reason="provider_authentication_required",
        )
    return ErrorPolicyResult(
        status=WatchStatus.FAILED,
        cooldown_seconds=None,
        requires_manual_resume=True,
        official_handoff_required=False,
        reason="provider_request_failed",
    )


def cooldown_until(result: ErrorPolicyResult, now: datetime | None = None) -> datetime | None:
    if result.cooldown_seconds is None:
        return None
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=result.cooldown_seconds)
