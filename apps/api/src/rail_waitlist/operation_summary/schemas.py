from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..domain import (
    OutboxStatus,
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    SeatObservationStatus,
    WatchStatus,
)
from ..schema_base import ApiModel

OperationFreshness = Literal["fresh", "stale", "unknown"]
OperationServiceStatus = Literal["healthy", "unknown"]
OperationEntryKind = Literal[
    "seat_observation",
    "reservation_attempt",
    "watch_transition",
    "notification_delivery",
    "provider_circuit",
]
OperationEntryLevel = Literal["info", "warning", "error"]
OperationErrorCategory = Literal[
    "timeout",
    "schema_mismatch",
    "provider_unavailable",
    "partial_failure",
    "unknown",
]


class OperationsWindow(ApiModel):
    from_at: datetime
    to_at: datetime
    hours: Literal[24] = 24


class OperationRate(ApiModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)
    definition: str = Field(min_length=1, max_length=240)


class OperationWindowCounts(ApiModel):
    seat_observations: int = Field(ge=0)
    seat_observation_errors: int = Field(ge=0)
    reservation_attempts: int = Field(ge=0)
    reservation_failures: int = Field(ge=0)
    watch_transitions: int = Field(ge=0)
    watch_failure_transitions: int = Field(ge=0)
    notification_events: int = Field(ge=0)
    notification_sent: int = Field(ge=0)
    notification_failed: int = Field(ge=0)


class OperationStatusCount(ApiModel):
    status: WatchStatus
    count: int = Field(ge=0)


class OperationCurrentCounts(ApiModel):
    watches_by_status: list[OperationStatusCount]
    notification_outbox_pending: int = Field(ge=0)


class OperationSourceFreshness(ApiModel):
    source: Literal[
        "seat_observations",
        "reservation_attempts",
        "watch_transition_history",
        "notification_delivery",
        "provider_circuits",
        "station_catalog",
    ]
    status: OperationFreshness
    observed_at: datetime | None
    age_seconds: int | None = Field(default=None, ge=0)
    timestamp_basis: Literal[
        "observed_at",
        "started_at",
        "created_at",
        "processed_at",
        "updated_at",
        "retrieved_at",
    ]


class OperationServiceState(ApiModel):
    service: Literal["api", "database", "worker", "scheduler"]
    status: OperationServiceStatus
    observed_at: datetime | None
    evidence: Literal[
        "summary_request_succeeded",
        "summary_query_succeeded",
        "durable_heartbeat_unavailable",
    ]


class OperationProviderCircuit(ApiModel):
    provider: Provider
    state: ProviderCircuitState
    updated_at: datetime
    manual_resume_required: bool


class OperationEntry(ApiModel):
    occurred_at: datetime
    kind: OperationEntryKind
    level: OperationEntryLevel
    status: (
        SeatObservationStatus
        | ReservationOutcome
        | WatchStatus
        | OutboxStatus
        | ProviderCircuitState
    )
    error_category: OperationErrorCategory | None = None
    provider: Provider | None = None


class OperationsSummary(ApiModel):
    generated_at: datetime
    window: OperationsWindow
    seat_observation_error_rate: OperationRate
    notification_delivery_failure_rate: OperationRate
    window_counts: OperationWindowCounts
    current_counts: OperationCurrentCounts
    source_freshness: list[OperationSourceFreshness]
    services: list[OperationServiceState]
    provider_circuits: list[OperationProviderCircuit]
    recent_entries: list[OperationEntry]
    limitations: list[
        Literal[
            "http_and_process_errors_are_not_durably_recorded",
            "worker_and_scheduler_health_require_durable_heartbeats",
            "recent_entries_are_sanitized_categories_without_identifiers_or_raw_errors",
        ]
    ]
