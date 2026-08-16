from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.worker as worker_module
from rail_waitlist.domain import ReservationOutcome, WatchStatus
from rail_waitlist.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from rail_waitlist.reservations.stale_attempt_recovery_application import (
    StaleReservationAttemptRecoveryDependencies,
    recover_stale_reservation_attempts,
)


class ExecuteResult:
    def __init__(
        self,
        rows: list[tuple[ReservationAttempt, WatchCandidate, Watch]],
    ) -> None:
        self.rows = rows

    def all(self) -> list[tuple[ReservationAttempt, WatchCandidate, Watch]]:
        return self.rows


class RecordingSession:
    def __init__(
        self,
        rows: list[tuple[ReservationAttempt, WatchCandidate, Watch]],
    ) -> None:
        self.rows = rows
        self.statements: list[object] = []
        self.commit_count = 0

    async def execute(self, statement: object) -> ExecuteResult:
        self.statements.append(statement)
        return ExecuteResult(self.rows)

    async def commit(self) -> None:
        self.commit_count += 1


def recovery_row(
    *,
    watch_status: WatchStatus,
    candidate_state: str,
    next_check_at: datetime | None = None,
) -> tuple[ReservationAttempt, WatchCandidate, Watch]:
    watch = Watch(
        id="watch-1",
        status=watch_status,
        next_check_at=next_check_at,
    )
    candidate = WatchCandidate(
        id="candidate-1",
        watch_id=watch.id,
        state=candidate_state,
    )
    attempt = ReservationAttempt(
        id="attempt-1",
        candidate_id=candidate.id,
        outcome=ReservationOutcome.PENDING,
        started_at=datetime(2026, 8, 6, 2, 50, tzinfo=UTC),
    )
    return attempt, candidate, watch


@pytest.mark.parametrize(
    (
        "watch_status",
        "candidate_state",
        "existing_next_check",
        "expected_watch_status",
        "expected_candidate_state",
        "expected_transition_count",
    ),
    [
        (
            WatchStatus.RESERVING,
            "reservation_attempted",
            None,
            WatchStatus.WATCHING,
            "observed",
            1,
        ),
        (
            WatchStatus.RESERVING,
            "reservation_attempted",
            datetime(2026, 8, 6, 3, 5, tzinfo=UTC),
            WatchStatus.WATCHING,
            "observed",
            1,
        ),
        (
            WatchStatus.EXPIRED,
            "reservation_attempted",
            None,
            WatchStatus.EXPIRED,
            "expired",
            0,
        ),
        (
            WatchStatus.WATCHING,
            "reservation_attempted",
            None,
            WatchStatus.WATCHING,
            "observed",
            0,
        ),
        (
            WatchStatus.WATCHING,
            "seat_found",
            None,
            WatchStatus.WATCHING,
            "seat_found",
            0,
        ),
    ],
)
async def test_stale_recovery_preserves_state_matrix_and_durable_unknown_fence(
    watch_status: WatchStatus,
    candidate_state: str,
    existing_next_check: datetime | None,
    expected_watch_status: WatchStatus,
    expected_candidate_state: str,
    expected_transition_count: int,
) -> None:
    now = datetime(2026, 8, 6, 3, 0, tzinfo=UTC)
    attempt, candidate, watch = recovery_row(
        watch_status=watch_status,
        candidate_state=candidate_state,
        next_check_at=existing_next_check,
    )
    session = RecordingSession([(attempt, candidate, watch)])
    transitions: list[tuple[str, WatchStatus, str | None]] = []
    outbox_events: list[dict[str, object]] = []

    async def apply_transition(
        _session: AsyncSession,
        target_watch: Watch,
        target: WatchStatus,
        _idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        observation: SeatObservation | None = None,
    ) -> Watch:
        assert observation is None
        transitions.append((target_watch.id, target, reason))
        target_watch.status = target
        return target_watch

    async def add_outbox(
        _session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object:
        outbox_events.append(
            {
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "payload": payload,
                "dedupe_key": dedupe_key,
            }
        )
        return object()

    recovered = await recover_stale_reservation_attempts(
        cast(AsyncSession, session),
        now,
        dependencies=StaleReservationAttemptRecoveryDependencies(
            apply_watch_transition=apply_transition,
            add_outbox_event=add_outbox,
        ),
    )

    assert recovered == 1
    assert attempt.outcome is ReservationOutcome.UNKNOWN
    assert attempt.result_reason_code.value == "reservation_request_result_unknown"
    assert attempt.finished_at == now
    assert watch.status is expected_watch_status
    assert candidate.state == expected_candidate_state
    assert len(transitions) == expected_transition_count
    if expected_transition_count:
        assert transitions == [
            (
                watch.id,
                WatchStatus.WATCHING,
                "stale_reservation_attempt_requires_manual_check",
            )
        ]
        assert watch.next_check_at == (existing_next_check or now)
    assert outbox_events == [
        {
            "aggregate_type": "watch",
            "aggregate_id": watch.id,
            "event_type": "watch.reservation_result_requires_manual_check",
            "payload": {
                "watch_id": watch.id,
                "candidate_id": candidate.id,
                "attempt_id": attempt.id,
                "attempt_sequence": attempt.attempt_sequence,
                "attempt_started_at": attempt.started_at.isoformat(),
                "attempt_finished_at": now.isoformat(),
                "outcome": "unknown",
                "result_reason_code": "reservation_request_result_unknown",
                "confirmation_outcome": None,
                "confirmation_diagnostic_code": None,
                "confirmation_observed_at": None,
                "reconciliation_attempt_count": 0,
                "next_reconcile_at": None,
                "retryable": False,
                "manual_check_required": True,
                "retry_condition": None,
                "monitoring_resumed": expected_watch_status == WatchStatus.WATCHING,
                "progress_stages": [],
                "reason": "reservation_attempt_result_unknown_after_restart",
            },
            "dedupe_key": f"reservation-attempt-recovery:{attempt.id}",
        }
    ]
    assert session.commit_count == 1


async def test_stale_recovery_does_not_commit_an_empty_sweep() -> None:
    session = RecordingSession([])

    async def unexpected_transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("empty recovery must not transition a watch")

    async def unexpected_outbox(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("empty recovery must not write an outbox event")

    recovered = await recover_stale_reservation_attempts(
        cast(AsyncSession, session),
        datetime(2026, 8, 6, 3, 0, tzinfo=UTC),
        dependencies=StaleReservationAttemptRecoveryDependencies(
            apply_watch_transition=unexpected_transition,
            add_outbox_event=unexpected_outbox,
        ),
    )

    assert recovered == 0
    assert session.commit_count == 0


async def test_worker_wrapper_uses_current_globals_and_preserves_stale_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[AsyncSession, datetime, timedelta, object, object]] = []

    async def canonical_owner(
        session: AsyncSession,
        now: datetime,
        *,
        stale_after: timedelta,
        dependencies: StaleReservationAttemptRecoveryDependencies,
    ) -> int:
        calls.append(
            (
                session,
                now,
                stale_after,
                dependencies.apply_watch_transition,
                dependencies.add_outbox_event,
            )
        )
        return 3

    async def transition(*_args: object, **_kwargs: object) -> Watch:
        return cast(Watch, object())

    async def outbox(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(worker_module, "recover_stale_reservation_attempts", canonical_owner)
    monkeypatch.setattr(worker_module, "apply_watch_transition", transition)
    monkeypatch.setattr(worker_module, "add_outbox_event", outbox)
    monkeypatch.setattr(
        worker_module,
        "RESERVATION_ATTEMPT_STALE_AFTER",
        timedelta(minutes=9),
    )
    session = cast(AsyncSession, object())
    now = datetime(2026, 8, 6, 3, 0, tzinfo=UTC)

    recovered = await worker_module._recover_stale_reservation_attempts(session, now)

    assert recovered == 3
    assert calls == [(session, now, timedelta(minutes=9), transition, outbox)]
    assert (
        worker_module._process_due_watches.__module__
        == worker_module._recover_stale_reservation_attempts.__module__
        == "rail_waitlist.worker"
    )
