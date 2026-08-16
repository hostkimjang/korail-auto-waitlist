from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.services as services_module
from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationResultReasonCode,
    WatchStatus,
)
from rail_waitlist.models import ReservationAttempt, Watch, WatchCandidate
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.attempt_result_application import (
    ReservationAttemptAlreadyCompleted,
    ReservationAttemptResultDependencies,
)
from rail_waitlist.reservations.attempt_result_application import (
    complete_reservation_attempt as complete_reservation_attempt_application,
)
from rail_waitlist.reservations.attempt_result_application import (
    record_reservation_confirmation as record_reservation_confirmation_application,
)
from rail_waitlist.reservations.domain import reservation_attempt_result_policy
from rail_waitlist.schemas import ReservationProgressStage, ReservationResult
from rail_waitlist.services import complete_reservation_attempt, record_reservation_confirmation

OBSERVED_AT = datetime(2026, 8, 5, 3, tzinfo=UTC)
COMPLETED_AT = OBSERVED_AT + timedelta(seconds=5)
SEAT_DETECTED_AT = OBSERVED_AT - timedelta(seconds=11)


def make_watch(*, provider: Provider = Provider.SRT) -> Watch:
    return Watch(
        id="watch-1",
        provider=provider,
        origin="수서",
        origin_node_id="N-SUSEO",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2026, 8, 5),
        time_from=time(12),
        time_to=time(18),
        train_numbers=["301"],
        notification_channel_ids=[],
        mode="official",
        status=WatchStatus.RESERVING,
        dedupe_key="attempt-result-owner-test",
    )


def make_candidate(*, candidate_id: str = "candidate-1", priority: int = 1) -> WatchCandidate:
    return WatchCandidate(
        id=candidate_id,
        watch_id="watch-1",
        train_number=f"30{priority}",
        departure_at=datetime(2026, 8, 5, 6, tzinfo=UTC),
        scheduled_departure_at=datetime(2026, 8, 5, 6, tzinfo=UTC),
        seat_class="standard",
        priority=priority,
        state="reservation_attempted" if priority == 1 else "active",
    )


def make_attempt(*, outcome: ReservationOutcome = ReservationOutcome.PENDING) -> ReservationAttempt:
    return ReservationAttempt(
        id="attempt-1",
        candidate_id="candidate-1",
        attempt_sequence=2,
        episode_key="availability:owner-test",
        idempotency_key="reserve:owner-test",
        started_at=OBSERVED_AT - timedelta(seconds=10),
        outcome=outcome,
    )


class ScalarRows:
    def __init__(self, values: list[WatchCandidate]) -> None:
        self.values = values

    def all(self) -> list[WatchCandidate]:
        return self.values


class ResultSession:
    def __init__(self, lower_candidates: list[WatchCandidate] | None = None) -> None:
        self.lower_candidates = lower_candidates or []

    async def scalars(self, _statement: object) -> ScalarRows:
        return ScalarRows(self.lower_candidates)

    async def scalar(self, _statement: object) -> datetime:
        return SEAT_DETECTED_AT


def make_dependencies(
    transitions: list[tuple[WatchStatus, str | None]],
    events: list[dict[str, object]],
) -> ReservationAttemptResultDependencies:
    async def apply_transition(
        _session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        *_args: object,
        reason: str | None = None,
        **_kwargs: object,
    ) -> Watch:
        transitions.append((target, reason))
        watch.status = target
        return watch

    async def add_event(
        _session: AsyncSession,
        **values: object,
    ) -> object:
        events.append(values)
        return object()

    return ReservationAttemptResultDependencies(
        apply_watch_transition=apply_transition,
        add_outbox_event=add_event,
        now=lambda: COMPLETED_AT,
        result_policy=reservation_attempt_result_policy,
        record_reservation_confirmation=record_reservation_confirmation_application,
    )


async def test_success_result_suppresses_lower_candidates_and_emits_stable_events() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    lower = make_candidate(candidate_id="candidate-2", priority=2)
    attempt = make_attempt()
    deadline = COMPLETED_AT + timedelta(minutes=10)
    result = ReservationResult(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        source="srt.owner-test",
        observed_at=OBSERVED_AT,
        credential_version=4,
        payment_deadline=deadline,
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
        progress_stages=(
            ReservationProgressStage(
                stage="authenticated_session_ready",
                occurred_at=OBSERVED_AT - timedelta(seconds=1),
            ),
        ),
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession([lower])),
        watch,
        candidate,
        attempt,
        result,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert attempt.credential_version == 4
    assert attempt.finished_at == COMPLETED_AT
    assert attempt.payment_deadline == deadline
    assert attempt.official_handoff_url == str(result.official_handoff_url)
    assert attempt.progress_stages == [
        {
            "stage": "authenticated_session_ready",
            "occurred_at": (OBSERVED_AT - timedelta(seconds=1)).isoformat(),
        }
    ]
    assert candidate.state == "payment_required"
    assert watch.status is WatchStatus.PAYMENT_REQUIRED
    assert watch.payment_deadline == deadline
    assert watch.official_booking_url == str(result.official_handoff_url)
    assert lower.state == "suppressed_by_priority"
    assert lower.suppressed_by_candidate_id == candidate.id
    assert transitions == [(WatchStatus.PAYMENT_REQUIRED, "reservation_requires_user_payment")]
    assert [event["event_type"] for event in events] == [
        "watch.candidate_suppressed",
        "watch.reservation_result",
    ]
    assert events[0]["dedupe_key"] == "candidate-suppressed:candidate-2:candidate-1"
    result_payload = cast(dict[str, object], events[1]["payload"])
    assert result_payload["seat_detected_at"] == SEAT_DETECTED_AT.isoformat()
    assert result_payload["retryable"] is False
    assert result_payload["manual_check_required"] is False
    assert result_payload["progress_stages"] == [
        {
            "stage": "authenticated_session_ready",
            "occurred_at": (OBSERVED_AT - timedelta(seconds=1)).isoformat(),
        }
    ]
    assert events[1]["dedupe_key"] == "reservation-result:attempt-1"


async def test_initial_exact_paid_event_uses_the_actual_watching_to_completed_edge() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    attempt = make_attempt()
    result = ReservationResult(
        outcome=ReservationOutcome.UNKNOWN,
        result_reason_code=ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
        source="srt.owner-test",
        observed_at=OBSERVED_AT,
        credential_version=4,
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        result,
        confirmation=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
            source="srtrain-reservation-list",
            observed_at=COMPLETED_AT,
        ),
        dependencies=make_dependencies(transitions, events),
    )

    assert watch.status is WatchStatus.COMPLETED
    assert transitions == [
        (WatchStatus.WATCHING, "reservation_result_unknown_confirmed_paid"),
        (WatchStatus.COMPLETED, "reservation_reconciliation_confirmed_paid"),
    ]
    assert [event["event_type"] for event in events] == ["watch.payment_completed"]
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["from"] == "watching"
    assert payload["to"] == "completed"


async def test_exact_paid_cleanup_does_not_emit_a_second_completion_event() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    watch.status = WatchStatus.COMPLETED
    candidate = make_candidate()
    attempt = make_attempt()
    result = ReservationResult(
        outcome=ReservationOutcome.UNKNOWN,
        result_reason_code=ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
        source="srt.owner-test",
        observed_at=OBSERVED_AT,
        credential_version=4,
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        result,
        confirmation=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
            source="srtrain-reservation-list",
            observed_at=COMPLETED_AT,
        ),
        dependencies=make_dependencies(transitions, events),
    )

    assert watch.status is WatchStatus.COMPLETED
    assert transitions == []
    assert events == []


async def test_terminal_result_reuses_progress_already_persisted_during_provider_io() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    attempt = make_attempt()
    attempt.progress_stages = [
        {
            "stage": "authenticated_session_ready",
            "occurred_at": (OBSERVED_AT - timedelta(seconds=1)).isoformat(),
        }
    ]
    result = ReservationResult(
        outcome=ReservationOutcome.NOT_AVAILABLE,
        source="srt.owner-test",
        observed_at=OBSERVED_AT,
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        result,
        dependencies=make_dependencies(transitions, events),
    )

    result_event = next(
        event for event in events if event["event_type"] == "watch.reservation_result"
    )
    result_payload = cast(dict[str, object], result_event["payload"])
    assert result_payload["progress_stages"] == attempt.progress_stages


async def test_terminal_time_covers_progress_persisted_before_wall_clock_rollback() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    attempt = make_attempt()
    latest_progress_at = COMPLETED_AT + timedelta(milliseconds=673)
    attempt.progress_stages = [
        {
            "stage": "authenticated_session_ready",
            "occurred_at": latest_progress_at.isoformat(),
        }
    ]
    result = ReservationResult(
        outcome=ReservationOutcome.FAILED,
        source="korail.owner-test",
        observed_at=OBSERVED_AT,
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        result,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.finished_at == latest_progress_at
    result_event = next(
        event for event in events if event["event_type"] == "watch.reservation_result"
    )
    result_payload = cast(dict[str, object], result_event["payload"])
    assert result_payload["attempt_finished_at"] == latest_progress_at.isoformat()


async def test_expired_success_deadline_becomes_unknown_manual_check_fence() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    attempt = make_attempt()
    result = ReservationResult(
        outcome=ReservationOutcome.RESERVED,
        source="srt.owner-test",
        observed_at=OBSERVED_AT,
        payment_deadline=OBSERVED_AT + timedelta(seconds=2),
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        result,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.outcome is ReservationOutcome.UNKNOWN
    assert attempt.payment_deadline is None
    assert attempt.official_handoff_url is None
    assert candidate.state == "observed"
    assert watch.status is WatchStatus.WATCHING
    assert transitions == [(WatchStatus.WATCHING, "reservation_result_deadline_already_elapsed")]
    assert events == [
        {
            "aggregate_type": "watch",
            "aggregate_id": "watch-1",
            "event_type": "watch.reservation_result_requires_manual_check",
            "payload": {
                "watch_id": "watch-1",
                "candidate_id": "candidate-1",
                "outcome": "unknown",
                "result_reason_code": "reservation_request_result_unknown",
                "confirmation_outcome": None,
                "confirmation_diagnostic_code": None,
                "confirmation_observed_at": None,
                "reconciliation_attempt_count": 0,
                "next_reconcile_at": None,
                "reason": "payment_deadline_already_elapsed",
            },
            "dedupe_key": "reservation-result-expired-deadline:attempt-1",
        }
    ]


@pytest.mark.parametrize(
    ("outcome", "candidate_state", "target", "reason", "manual_check"),
    [
        (
            ReservationOutcome.NOT_AVAILABLE,
            "observed",
            WatchStatus.WATCHING,
            "reservation_not_available",
            False,
        ),
        (
            ReservationOutcome.UNKNOWN,
            "observed",
            WatchStatus.WATCHING,
            "reservation_unknown",
            True,
        ),
        (
            ReservationOutcome.FAILED,
            "observed",
            WatchStatus.WATCHING,
            "reservation_failed_monitoring_resumed",
            False,
        ),
        (
            ReservationOutcome.AUTH_REQUIRED,
            "failed",
            WatchStatus.AUTH_REQUIRED,
            "reservation_auth_required",
            False,
        ),
        (
            ReservationOutcome.PROVIDER_BLOCKED,
            "failed",
            WatchStatus.AUTH_REQUIRED,
            "reservation_provider_blocked",
            True,
        ),
    ],
)
async def test_non_success_outcome_state_transition_and_policy_contract(
    outcome: ReservationOutcome,
    candidate_state: str,
    target: WatchStatus,
    reason: str,
    manual_check: bool,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    attempt = make_attempt()

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        ReservationResult(outcome=outcome, source="srt.owner-test", observed_at=OBSERVED_AT),
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.outcome is outcome
    assert candidate.state == candidate_state
    assert watch.status is target
    assert transitions == [(target, reason)]
    result_event = next(
        event for event in events if event["event_type"] == "watch.reservation_result"
    )
    payload = cast(dict[str, object], result_event["payload"])
    assert payload["manual_check_required"] is manual_check
    assert payload["monitoring_resumed"] is (candidate_state == "observed")
    if outcome is ReservationOutcome.FAILED:
        assert [event["event_type"] for event in events] == [
            "watch.reservation_failed_monitoring_resumed",
            "watch.reservation_result",
        ]


async def test_confirmation_identity_and_timestamp_contracts_are_preserved() -> None:
    attempt = make_attempt()
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.NOT_FOUND,
        source="srt.reservations",
        observed_at=OBSERVED_AT,
    )
    reconciled_at = OBSERVED_AT + timedelta(seconds=3)

    record_reservation_confirmation_application(
        attempt,
        confirmation,
        reconciled_at=reconciled_at,
    )

    assert attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert attempt.confirmation_source == "srt.reservations"
    assert attempt.confirmation_observed_at == OBSERVED_AT
    assert attempt.last_reconciled_at == reconciled_at
    with pytest.raises(ValueError, match="reconciled_at must include a timezone"):
        record_reservation_confirmation_application(
            attempt,
            confirmation,
            reconciled_at=datetime(2026, 8, 5, 3),
        )


@pytest.mark.parametrize(
    "confirmation_outcome",
    [
        ReservationConfirmationOutcome.INCONCLUSIVE,
        ReservationConfirmationOutcome.NOT_FOUND,
    ],
)
async def test_unknown_schedules_one_delayed_official_recheck(
    confirmation_outcome: ReservationConfirmationOutcome,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    attempt = make_attempt()
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=confirmation_outcome,
        source="srtrain-reservation-list",
        observed_at=OBSERVED_AT,
    )

    await complete_reservation_attempt_application(
        cast(AsyncSession, ResultSession()),
        watch,
        candidate,
        attempt,
        ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            source="srt.owner-test",
            observed_at=OBSERVED_AT,
        ),
        confirmation,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.confirmation_outcome is confirmation_outcome
    assert attempt.confirmation_diagnostic_code is (
        ReservationConfirmationDiagnosticCode.UNSPECIFIED
        if confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
        else None
    )
    assert attempt.next_reconcile_at == COMPLETED_AT + timedelta(seconds=30)
    result_event = next(
        event for event in events if event["event_type"] == "watch.reservation_result"
    )
    assert result_event["payload"]["confirmation_diagnostic_code"] == (
        "unspecified"
        if confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
        else None
    )


async def test_result_application_rejects_completion_and_confirmation_mismatch() -> None:
    dependencies = make_dependencies([], [])
    with pytest.raises(ReservationAttemptAlreadyCompleted):
        await complete_reservation_attempt_application(
            cast(AsyncSession, ResultSession()),
            make_watch(),
            make_candidate(),
            make_attempt(outcome=ReservationOutcome.FAILED),
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source="srt.owner-test",
                observed_at=OBSERVED_AT,
            ),
            dependencies=dependencies,
        )

    invalid_success = ReservationResult.model_construct(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        source="srt.owner-test",
        observed_at=OBSERVED_AT,
        credential_version=None,
        payment_deadline=COMPLETED_AT + timedelta(minutes=10),
        official_handoff_url=None,
        progress_stages=(),
    )
    with pytest.raises(
        RuntimeError,
        match="successful reservation result requires an official handoff URL",
    ):
        await complete_reservation_attempt_application(
            cast(AsyncSession, ResultSession()),
            make_watch(),
            make_candidate(),
            make_attempt(),
            invalid_success,
            dependencies=dependencies,
        )

    with pytest.raises(
        ValueError,
        match="official handoff URL must use the provider allowlist",
    ):
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="srt.reservations",
            observed_at=OBSERVED_AT,
            official_handoff_url="https://example.invalid/reservation",
        )

    mismatch = ReservationConfirmationResult(
        provider=Provider.KORAIL,
        outcome=ReservationConfirmationOutcome.NOT_FOUND,
        source="korail.reservations",
        observed_at=OBSERVED_AT,
    )
    with pytest.raises(ValueError, match="reservation confirmation provider does not match watch"):
        await complete_reservation_attempt_application(
            cast(AsyncSession, ResultSession()),
            make_watch(),
            make_candidate(),
            make_attempt(),
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source="srt.owner-test",
                observed_at=OBSERVED_AT,
            ),
            mismatch,
            dependencies=dependencies,
        )


async def test_services_wrapper_assembles_current_globals_and_translates_only_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("wrapper must only assemble this dependency")

    async def outbox(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("wrapper must only assemble this dependency")

    def policy(_outcome: ReservationOutcome):
        return reservation_attempt_result_policy(ReservationOutcome.UNKNOWN)

    def confirmation(*_args: object, **_kwargs: object) -> None:
        return None

    async def application(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(services_module, "apply_watch_transition", transition)
    monkeypatch.setattr(services_module, "add_outbox_event", outbox)
    monkeypatch.setattr(services_module, "reservation_attempt_result_policy", policy)
    monkeypatch.setattr(services_module, "record_reservation_confirmation", confirmation)
    monkeypatch.setattr(services_module, "complete_reservation_attempt_application", application)

    await complete_reservation_attempt(
        cast(AsyncSession, object()),
        make_watch(),
        make_candidate(),
        make_attempt(),
        ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            source="srt.owner-test",
            observed_at=OBSERVED_AT,
        ),
    )

    kwargs = cast(dict[str, object], captured["kwargs"])
    dependencies = kwargs["dependencies"]
    assert isinstance(dependencies, ReservationAttemptResultDependencies)
    assert dependencies.apply_watch_transition is transition
    assert dependencies.add_outbox_event is outbox
    assert dependencies.result_policy is policy
    assert dependencies.record_reservation_confirmation is confirmation

    async def conflict(*_args: object, **_kwargs: object) -> None:
        raise ReservationAttemptAlreadyCompleted

    monkeypatch.setattr(services_module, "complete_reservation_attempt_application", conflict)
    with pytest.raises(HTTPException) as captured_error:
        await complete_reservation_attempt(
            cast(AsyncSession, object()),
            make_watch(),
            make_candidate(),
            make_attempt(),
            ReservationResult(
                outcome=ReservationOutcome.UNKNOWN,
                source="srt.owner-test",
                observed_at=OBSERVED_AT,
            ),
        )
    assert captured_error.value.status_code == 409
    assert captured_error.value.detail == "reservation attempt was already completed"


def test_services_keeps_confirmation_canonical_identity() -> None:
    assert record_reservation_confirmation is record_reservation_confirmation_application
    assert record_reservation_confirmation is services_module.record_reservation_confirmation
