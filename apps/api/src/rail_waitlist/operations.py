from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    OutboxStatus,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationResultReasonCode,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
    reservation_result_reason_code_for_outcome,
)
from .operation_summary.schemas import (
    OperationCurrentCounts,
    OperationEntry,
    OperationEntryReasonCode,
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
from .reservations.payment_hold_application import payment_hold_end_reason
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    effective_reservation_confirmation_diagnostic_code,
)
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
KOREA_TIMEZONE = ZoneInfo("Asia/Seoul")
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
RESERVATION_REASON_CODES: dict[ReservationOutcome, OperationEntryReasonCode] = {
    ReservationOutcome.PENDING: "reservation_pending",
    ReservationOutcome.PAYMENT_REQUIRED: "payment_hold_created",
    ReservationOutcome.RESERVED: "payment_hold_created",
    ReservationOutcome.NOT_AVAILABLE: "target_not_available",
    ReservationOutcome.AUTH_REQUIRED: "authentication_required",
    ReservationOutcome.PROVIDER_BLOCKED: "provider_blocked",
    ReservationOutcome.FAILED: "reservation_failed",
    ReservationOutcome.UNKNOWN: "reservation_request_result_unknown",
}
PAYMENT_HOLD_TRANSITION_REASONS = frozenset(
    {
        "confirmed_payment_hold_no_longer_actionable_monitoring_resumed",
        "confirmed_payment_hold_no_longer_actionable_one_off_expired",
    }
)
SAFE_PROJECTED_TRANSITION_REASONS = PAYMENT_HOLD_TRANSITION_REASONS | {
    "reservation_reconciliation_confirmed_paid"
}
SAFE_OBSERVATION_ERROR_CATEGORIES = frozenset(
    {"timeout", "schema_mismatch", "provider_unavailable", "partial_failure", "unknown"}
)
RECENT_ENTRY_OBSERVATION_STATUSES = frozenset(
    {
        SeatObservationStatus.ERROR,
        SeatObservationStatus.UNKNOWN,
        SeatObservationStatus.STALE,
    }
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _reservation_error_category(attempt: ReservationAttempt) -> str | None:
    if attempt.outcome not in RESERVATION_FAILURE_OUTCOMES:
        return None
    reason_code = attempt.result_reason_code or reservation_result_reason_code_for_outcome(
        attempt.outcome
    )
    if reason_code is ReservationResultReasonCode.PROVIDER_UNAVAILABLE:
        return "provider_unavailable"
    if reason_code is ReservationResultReasonCode.PROVIDER_RESPONSE_INVALID:
        return "schema_mismatch"
    return "unknown"


def _kst(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(KOREA_TIMEZONE)


def _safe_train_number(value: str) -> str | None:
    if any(unicodedata.category(character).startswith("C") for character in value):
        return None
    normalized = " ".join(value.split())
    return normalized if 0 < len(normalized) <= 40 else None


def _safe_seat_class(value: str) -> SeatClass | None:
    try:
        return SeatClass(value)
    except ValueError:
        return None


def _payment_hold_reason_code(
    attempt: ReservationAttempt,
    *,
    monitoring_resumed: bool,
) -> OperationEntryReasonCode | None:
    end_reason = payment_hold_end_reason(attempt)
    if end_reason == "confirmed_payment_deadline_elapsed":
        return (
            "payment_deadline_elapsed_monitoring_resumed"
            if monitoring_resumed
            else "payment_deadline_elapsed_one_off_expired"
        )
    if end_reason == "confirmed_payment_hold_no_longer_present":
        return (
            "payment_hold_no_longer_present_monitoring_resumed"
            if monitoring_resumed
            else "payment_hold_no_longer_present_one_off_expired"
        )
    return None


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
            select(SeatObservation, WatchCandidate, Watch.provider)
            .join(WatchCandidate, WatchCandidate.id == SeatObservation.candidate_id)
            .join(Watch, Watch.id == WatchCandidate.watch_id)
            .where(
                SeatObservation.observed_at >= window_start,
                SeatObservation.status.in_(RECENT_ENTRY_OBSERVATION_STATUSES),
            )
            .order_by(SeatObservation.observed_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    for observation, candidate, provider in observation_rows:
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
                train_number=_safe_train_number(candidate.train_number),
                departure_at=_kst(candidate.departure_at),
                seat_class=_safe_seat_class(candidate.seat_class),
            )
        )

    reservation_occurred_at = func.coalesce(
        ReservationAttempt.finished_at,
        ReservationAttempt.started_at,
    )
    reservation_rows = (
        await session.execute(
            select(ReservationAttempt, WatchCandidate, Watch.provider)
            .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
            .join(Watch, Watch.id == WatchCandidate.watch_id)
            .where(reservation_occurred_at >= window_start)
            .order_by(reservation_occurred_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    for attempt, candidate, provider in reservation_rows:
        entries.append(
            OperationEntry(
                occurred_at=_utc(attempt.finished_at or attempt.started_at),
                kind="reservation_attempt",
                level=_reservation_level(attempt.outcome),
                status=attempt.outcome.value,
                error_category=_reservation_error_category(attempt),
                provider=provider,
                train_number=_safe_train_number(candidate.train_number),
                departure_at=_kst(candidate.departure_at),
                seat_class=_safe_seat_class(candidate.seat_class),
                reason_code=cast(
                    OperationEntryReasonCode,
                    (
                        attempt.result_reason_code
                        or reservation_result_reason_code_for_outcome(attempt.outcome)
                    ).value,
                ),
                confirmation_diagnostic_code=(
                    effective_reservation_confirmation_diagnostic_code(
                        attempt.confirmation_outcome,
                        attempt.confirmation_diagnostic_code,
                    )
                ),
            )
        )

    paid_confirmation_rows = (
        await session.execute(
            select(ReservationAttempt, WatchCandidate, Watch)
            .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
            .join(Watch, Watch.id == WatchCandidate.watch_id)
            .where(
                ReservationAttempt.confirmation_outcome
                == ReservationConfirmationOutcome.CONFIRMED_PAID,
                ReservationAttempt.last_reconciled_at >= window_start,
            )
            .order_by(ReservationAttempt.last_reconciled_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    paid_watch_ids = {watch.id for _attempt, _candidate, watch in paid_confirmation_rows}
    paid_transitions_by_watch: dict[str, list[WatchTransitionHistory]] = {}
    used_paid_transition_ids: set[str] = set()
    if paid_watch_ids:
        paid_transition_rows = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory)
                    .where(
                        WatchTransitionHistory.watch_id.in_(paid_watch_ids),
                        WatchTransitionHistory.from_status.in_(
                            [WatchStatus.PAYMENT_REQUIRED, WatchStatus.WATCHING]
                        ),
                        WatchTransitionHistory.to_status == WatchStatus.COMPLETED,
                        WatchTransitionHistory.reason
                        == "reservation_reconciliation_confirmed_paid",
                        WatchTransitionHistory.created_at
                        >= window_start - FUTURE_TIMESTAMP_TOLERANCE,
                    )
                    .order_by(WatchTransitionHistory.created_at.desc())
                )
            ).all()
        )
        for transition in paid_transition_rows:
            paid_transitions_by_watch.setdefault(transition.watch_id, []).append(transition)
    for attempt, candidate, watch in paid_confirmation_rows:
        confirmed_at = attempt.last_reconciled_at
        if confirmed_at is None:
            continue
        confirmed_at_utc = _utc(confirmed_at)
        if confirmed_at_utc is None:
            continue
        matching_transitions = [
            (item, created_at)
            for item in paid_transitions_by_watch.get(watch.id, [])
            if item.id not in used_paid_transition_ids
            if (created_at := _utc(item.created_at)) is not None
        ]
        transition_match = min(
            matching_transitions,
            key=lambda item: abs((item[1] - confirmed_at_utc).total_seconds()),
            default=None,
        )
        if transition_match is None:
            continue
        transition, transition_created_at = transition_match
        if abs((transition_created_at - confirmed_at_utc).total_seconds()) > 300:
            continue
        used_paid_transition_ids.add(transition.id)
        entries.append(
            OperationEntry(
                occurred_at=confirmed_at_utc,
                kind="watch_transition",
                level="info",
                status=WatchStatus.COMPLETED,
                provider=watch.provider,
                train_number=_safe_train_number(candidate.train_number),
                departure_at=_kst(candidate.departure_at),
                seat_class=_safe_seat_class(candidate.seat_class),
                reason_code="payment_completed",
            )
        )

    payment_hold_rows = (
        await session.execute(
            select(ReservationAttempt, WatchCandidate, Watch)
            .join(WatchCandidate, WatchCandidate.id == ReservationAttempt.candidate_id)
            .join(Watch, Watch.id == WatchCandidate.watch_id)
            .where(ReservationAttempt.post_deadline_reconciled_at >= window_start)
            .order_by(ReservationAttempt.post_deadline_reconciled_at.desc())
            .limit(RECENT_ENTRY_LIMIT)
        )
    ).all()
    payment_hold_watch_ids = {watch.id for _attempt, _candidate, watch in payment_hold_rows}
    payment_hold_transitions_by_watch: dict[str, list[WatchTransitionHistory]] = {}
    used_payment_hold_transition_ids: set[str] = set()
    if payment_hold_watch_ids:
        payment_hold_transition_rows = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory)
                    .where(
                        WatchTransitionHistory.watch_id.in_(payment_hold_watch_ids),
                        WatchTransitionHistory.reason.in_(PAYMENT_HOLD_TRANSITION_REASONS),
                        WatchTransitionHistory.created_at
                        >= window_start - FUTURE_TIMESTAMP_TOLERANCE,
                    )
                    .order_by(WatchTransitionHistory.created_at.desc())
                )
            ).all()
        )
        for transition in payment_hold_transition_rows:
            payment_hold_transitions_by_watch.setdefault(transition.watch_id, []).append(transition)
    for attempt, candidate, watch in payment_hold_rows:
        reconciled_at = _utc(attempt.post_deadline_reconciled_at)
        if reconciled_at is None:
            continue
        matching_transitions = [
            (item, created_at)
            for item in payment_hold_transitions_by_watch.get(watch.id, [])
            if item.id not in used_payment_hold_transition_ids
            if (created_at := _utc(item.created_at)) is not None
        ]
        transition_match = min(
            matching_transitions,
            key=lambda item: abs((item[1] - reconciled_at).total_seconds()),
            default=None,
        )
        if transition_match is None:
            continue
        transition, transition_created_at = transition_match
        if abs((transition_created_at - reconciled_at).total_seconds()) > 300:
            continue
        used_payment_hold_transition_ids.add(transition.id)
        monitoring_resumed = (
            transition.reason == "confirmed_payment_hold_no_longer_actionable_monitoring_resumed"
            and transition.to_status is WatchStatus.WATCHING
        )
        one_off_expired = (
            transition.reason == "confirmed_payment_hold_no_longer_actionable_one_off_expired"
            and transition.to_status is WatchStatus.EXPIRED
        )
        if not monitoring_resumed and not one_off_expired:
            continue
        reason_code = _payment_hold_reason_code(
            attempt,
            monitoring_resumed=monitoring_resumed,
        )
        if reason_code is None:
            continue
        target_status = transition.to_status
        entries.append(
            OperationEntry(
                occurred_at=reconciled_at,
                kind="watch_transition",
                level=_transition_level(target_status),
                status=target_status,
                provider=watch.provider,
                train_number=_safe_train_number(candidate.train_number),
                departure_at=_kst(candidate.departure_at),
                seat_class=_safe_seat_class(candidate.seat_class),
                reason_code=reason_code,
            )
        )

    transition_rows = (
        await session.execute(
            select(WatchTransitionHistory, Watch.provider)
            .join(Watch, Watch.id == WatchTransitionHistory.watch_id)
            .where(
                WatchTransitionHistory.created_at >= window_start,
                or_(
                    WatchTransitionHistory.reason.is_(None),
                    WatchTransitionHistory.reason.not_in(SAFE_PROJECTED_TRANSITION_REASONS),
                ),
            )
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
