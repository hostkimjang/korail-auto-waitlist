from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    OutboxStatus,
    ProviderCircuitState,
    ReservationOutcome,
    SeatObservationStatus,
    WatchStatus,
)
from .operation_summary.schemas import (
    OperationCurrentCounts,
    OperationEntry,
    OperationProviderCircuit,
    OperationRate,
    OperationServiceState,
    OperationSourceFreshness,
    OperationsSummary,
    OperationStatusCount,
    OperationsWindow,
    OperationWindowCounts,
)
from .outbox_management.models import OutboxEvent
from .provider_circuit.models import ProviderCircuit
from .timetable_management.models import StationCatalogCache
from .watch_management.models import (
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)

WINDOW_HOURS = 24
RECENT_ENTRY_LIMIT = 40
FUTURE_TIMESTAMP_TOLERANCE = timedelta(minutes=5)
NOTIFICATION_EVENT_TYPES = frozenset(
    {"notification.test_requested", "notification.dispatch_requested"}
)
RESERVATION_FAILURE_OUTCOMES = frozenset(
    {
        ReservationOutcome.FAILED,
        ReservationOutcome.PROVIDER_BLOCKED,
        ReservationOutcome.UNKNOWN,
    }
)
SAFE_OBSERVATION_ERROR_CATEGORIES = frozenset(
    {"timeout", "schema_mismatch", "provider_unavailable", "partial_failure", "unknown"}
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


async def _latest_timestamp(
    session: AsyncSession, column: Any, *conditions: Any
) -> datetime | None:
    query = select(column).where(column.is_not(None))
    if conditions:
        query = query.where(*conditions)
    return await session.scalar(query.order_by(column.desc()).limit(1))


def _freshness(
    source: Literal[
        "seat_observations",
        "reservation_attempts",
        "watch_transition_history",
        "notification_delivery",
        "provider_circuits",
        "station_catalog",
    ],
    timestamp_basis: Literal[
        "observed_at",
        "started_at",
        "created_at",
        "processed_at",
        "updated_at",
        "retrieved_at",
    ],
    observed_at: datetime | None,
    now: datetime,
    window_start: datetime,
) -> OperationSourceFreshness:
    normalized = _utc(observed_at)
    if normalized is None:
        return OperationSourceFreshness(
            source=source,
            status="unknown",
            observed_at=None,
            age_seconds=None,
            timestamp_basis=timestamp_basis,
        )
    if normalized > now + FUTURE_TIMESTAMP_TOLERANCE:
        return OperationSourceFreshness(
            source=source,
            status="unknown",
            observed_at=normalized,
            age_seconds=None,
            timestamp_basis=timestamp_basis,
        )
    return OperationSourceFreshness(
        source=source,
        status="fresh" if normalized >= window_start else "stale",
        observed_at=normalized,
        age_seconds=max(0, int((now - normalized).total_seconds())),
        timestamp_basis=timestamp_basis,
    )


def _observation_error_category(value: str | None, *, is_error: bool) -> str | None:
    if not is_error:
        return None
    return value if value in SAFE_OBSERVATION_ERROR_CATEGORIES else "unknown"


def _observation_level(status: SeatObservationStatus) -> Literal["info", "warning", "error"]:
    if status == SeatObservationStatus.ERROR:
        return "error"
    if status in {SeatObservationStatus.UNKNOWN, SeatObservationStatus.STALE}:
        return "warning"
    return "info"


def _reservation_level(outcome: ReservationOutcome) -> Literal["info", "warning", "error"]:
    if outcome in RESERVATION_FAILURE_OUTCOMES:
        return "error"
    if outcome in {ReservationOutcome.PENDING, ReservationOutcome.AUTH_REQUIRED}:
        return "warning"
    return "info"


def _transition_level(status: WatchStatus) -> Literal["info", "warning", "error"]:
    if status == WatchStatus.FAILED:
        return "error"
    if status in {WatchStatus.AUTH_REQUIRED, WatchStatus.COOLDOWN}:
        return "warning"
    return "info"


async def _recent_entries(session: AsyncSession, window_start: datetime) -> list[OperationEntry]:
    entries: list[OperationEntry] = []

    observation_rows = (
        await session.execute(
            select(SeatObservation, Watch.provider)
            .join(WatchCandidate, WatchCandidate.id == SeatObservation.candidate_id)
            .join(Watch, Watch.id == WatchCandidate.watch_id)
            .where(SeatObservation.observed_at >= window_start)
            .order_by(SeatObservation.observed_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    for observation, provider in observation_rows:
        is_error = observation.status == SeatObservationStatus.ERROR
        entries.append(
            OperationEntry(
                occurred_at=_utc(observation.observed_at),
                kind="seat_observation",
                level=_observation_level(observation.status),
                status=observation.status.value,
                error_category=_observation_error_category(
                    observation.error_category, is_error=is_error
                ),
                provider=provider,
            )
        )

    reservation_rows = (
        await session.execute(
            select(ReservationAttempt, Watch.provider)
            .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
            .join(Watch, Watch.id == WatchCandidate.watch_id)
            .where(ReservationAttempt.started_at >= window_start)
            .order_by(ReservationAttempt.started_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    for attempt, provider in reservation_rows:
        entries.append(
            OperationEntry(
                occurred_at=_utc(attempt.finished_at or attempt.started_at),
                kind="reservation_attempt",
                level=_reservation_level(attempt.outcome),
                status=attempt.outcome.value,
                error_category="unknown"
                if attempt.outcome in RESERVATION_FAILURE_OUTCOMES
                else None,
                provider=provider,
            )
        )

    transition_rows = (
        await session.execute(
            select(WatchTransitionHistory, Watch.provider)
            .join(Watch, Watch.id == WatchTransitionHistory.watch_id)
            .where(WatchTransitionHistory.created_at >= window_start)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    for transition, provider in transition_rows:
        entries.append(
            OperationEntry(
                occurred_at=_utc(transition.created_at),
                kind="watch_transition",
                level=_transition_level(transition.to_status),
                status=transition.to_status.value,
                error_category="unknown" if transition.to_status == WatchStatus.FAILED else None,
                provider=provider,
            )
        )

    notification_rows = list(
        (
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type.in_(NOTIFICATION_EVENT_TYPES),
                    or_(
                        and_(
                            OutboxEvent.status == OutboxStatus.PENDING,
                            OutboxEvent.created_at >= window_start,
                        ),
                        and_(
                            OutboxEvent.status.in_([OutboxStatus.SENT, OutboxStatus.FAILED]),
                            OutboxEvent.processed_at >= window_start,
                        ),
                    ),
                )
                .order_by(func.coalesce(OutboxEvent.processed_at, OutboxEvent.created_at).desc())
                .limit(RECENT_ENTRY_LIMIT)
            )
        ).all()
    )
    for event in notification_rows:
        level: Literal["info", "warning", "error"] = "info"
        if event.status == OutboxStatus.FAILED:
            level = "error"
        elif event.status == OutboxStatus.PENDING:
            level = "warning"
        entries.append(
            OperationEntry(
                occurred_at=_utc(event.processed_at or event.created_at),
                kind="notification_delivery",
                level=level,
                status=event.status.value,
                error_category="unknown" if event.status == OutboxStatus.FAILED else None,
            )
        )

    circuit_rows = list(
        (
            await session.scalars(
                select(ProviderCircuit)
                .where(ProviderCircuit.updated_at >= window_start)
                .order_by(ProviderCircuit.updated_at.desc())
                .limit(RECENT_ENTRY_LIMIT)
            )
        ).all()
    )
    for circuit in circuit_rows:
        entries.append(
            OperationEntry(
                occurred_at=_utc(circuit.updated_at),
                kind="provider_circuit",
                level="info" if circuit.state == ProviderCircuitState.CLOSED else "warning",
                status=circuit.state.value,
                provider=circuit.provider,
            )
        )

    entries.sort(key=lambda item: item.occurred_at, reverse=True)
    return entries[:RECENT_ENTRY_LIMIT]


async def build_operations_summary(
    session: AsyncSession, *, now: datetime | None = None
) -> OperationsSummary:
    generated_at = _utc(now) if now is not None else datetime.now(UTC)
    if generated_at is None:  # pragma: no cover - guarded by the branch above
        raise ValueError("generated_at is required")
    window_start = generated_at - timedelta(hours=WINDOW_HOURS)

    observation_stats = (
        await session.execute(
            select(
                func.count(),
                func.sum(
                    case(
                        (SeatObservation.status == SeatObservationStatus.ERROR, 1),
                        else_=0,
                    )
                ),
            ).where(SeatObservation.observed_at >= window_start)
        )
    ).one()
    observations = int(observation_stats[0] or 0)
    observation_errors = int(observation_stats[1] or 0)
    latest_observation = await _latest_timestamp(session, SeatObservation.observed_at)

    reservation_stats = (
        await session.execute(
            select(
                func.count(),
                func.sum(
                    case(
                        (
                            ReservationAttempt.outcome.in_(RESERVATION_FAILURE_OUTCOMES),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(ReservationAttempt.started_at >= window_start)
        )
    ).one()
    reservation_attempts = int(reservation_stats[0] or 0)
    reservation_failures = int(reservation_stats[1] or 0)
    latest_reservation = await _latest_timestamp(session, ReservationAttempt.started_at)

    transition_stats = (
        await session.execute(
            select(
                func.count(),
                func.sum(
                    case(
                        (
                            WatchTransitionHistory.to_status == WatchStatus.FAILED,
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(WatchTransitionHistory.created_at >= window_start)
        )
    ).one()
    transitions = int(transition_stats[0] or 0)
    failed_transitions = int(transition_stats[1] or 0)
    latest_transition = await _latest_timestamp(session, WatchTransitionHistory.created_at)

    notification_condition = OutboxEvent.event_type.in_(NOTIFICATION_EVENT_TYPES)
    notification_events = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.created_at >= window_start,
                    notification_condition,
                )
            )
        )
        or 0
    )
    notification_delivery_stats = (
        await session.execute(
            select(
                func.sum(
                    case(
                        (OutboxEvent.status == OutboxStatus.SENT, 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (OutboxEvent.status == OutboxStatus.FAILED, 1),
                        else_=0,
                    )
                ),
            ).where(
                OutboxEvent.processed_at >= window_start,
                notification_condition,
                OutboxEvent.status.in_([OutboxStatus.SENT, OutboxStatus.FAILED]),
            )
        )
    ).one()
    notification_sent = int(notification_delivery_stats[0] or 0)
    notification_failed = int(notification_delivery_stats[1] or 0)
    notification_pending = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    notification_condition,
                    OutboxEvent.status == OutboxStatus.PENDING,
                )
            )
        )
        or 0
    )
    latest_notification = await _latest_timestamp(
        session,
        OutboxEvent.processed_at,
        notification_condition,
        OutboxEvent.status.in_([OutboxStatus.SENT, OutboxStatus.FAILED]),
    )
    notification_terminal = notification_sent + notification_failed

    status_rows = (
        await session.execute(select(Watch.status, func.count()).group_by(Watch.status))
    ).all()
    watches_by_status = sorted(
        (OperationStatusCount(status=status, count=int(count)) for status, count in status_rows),
        key=lambda item: item.status.value,
    )
    latest_catalog = await session.scalar(select(func.max(StationCatalogCache.retrieved_at)))

    circuits = list(
        (await session.scalars(select(ProviderCircuit).order_by(ProviderCircuit.provider))).all()
    )
    latest_circuit = max((circuit.updated_at for circuit in circuits), default=None)

    return OperationsSummary(
        generated_at=generated_at,
        window=OperationsWindow(from_at=window_start, to_at=generated_at),
        seat_observation_error_rate=OperationRate(
            numerator=observation_errors,
            denominator=observations,
            rate=_rate(observation_errors, observations),
            definition=(
                "24시간 좌석 관측 오류율: status=error인 seat_observations / "
                "같은 기간의 전체 seat_observations. 서버·HTTP 오류율이 아닙니다."
            ),
        ),
        notification_delivery_failure_rate=OperationRate(
            numerator=notification_failed,
            denominator=notification_terminal,
            rate=_rate(notification_failed, notification_terminal),
            definition=(
                "24시간 알림 전달 최종 실패율: processed_at이 기간 안인 failed / "
                "(sent + failed). pending은 분모에서 제외합니다."
            ),
        ),
        window_counts=OperationWindowCounts(
            seat_observations=observations,
            seat_observation_errors=observation_errors,
            reservation_attempts=reservation_attempts,
            reservation_failures=reservation_failures,
            watch_transitions=transitions,
            watch_failure_transitions=failed_transitions,
            notification_events=notification_events,
            notification_sent=notification_sent,
            notification_failed=notification_failed,
        ),
        current_counts=OperationCurrentCounts(
            watches_by_status=watches_by_status,
            notification_outbox_pending=notification_pending,
        ),
        source_freshness=[
            _freshness(
                "seat_observations", "observed_at", latest_observation, generated_at, window_start
            ),
            _freshness(
                "reservation_attempts", "started_at", latest_reservation, generated_at, window_start
            ),
            _freshness(
                "watch_transition_history",
                "created_at",
                latest_transition,
                generated_at,
                window_start,
            ),
            _freshness(
                "notification_delivery",
                "processed_at",
                latest_notification,
                generated_at,
                window_start,
            ),
            _freshness(
                "provider_circuits", "updated_at", latest_circuit, generated_at, window_start
            ),
            _freshness(
                "station_catalog", "retrieved_at", latest_catalog, generated_at, window_start
            ),
        ],
        services=[
            OperationServiceState(
                service="api",
                status="healthy",
                observed_at=generated_at,
                evidence="summary_request_succeeded",
            ),
            OperationServiceState(
                service="database",
                status="healthy",
                observed_at=generated_at,
                evidence="summary_query_succeeded",
            ),
            OperationServiceState(
                service="worker",
                status="unknown",
                observed_at=None,
                evidence="durable_heartbeat_unavailable",
            ),
            OperationServiceState(
                service="scheduler",
                status="unknown",
                observed_at=None,
                evidence="durable_heartbeat_unavailable",
            ),
        ],
        provider_circuits=[
            OperationProviderCircuit(
                provider=circuit.provider,
                state=circuit.state,
                updated_at=_utc(circuit.updated_at),
                manual_resume_required=circuit.manual_resume_required,
            )
            for circuit in circuits
        ],
        recent_entries=await _recent_entries(session, window_start),
        limitations=[
            "http_and_process_errors_are_not_durably_recorded",
            "worker_and_scheduler_health_require_durable_heartbeats",
            "recent_entries_are_sanitized_categories_without_identifiers_or_raw_errors",
        ],
    )
