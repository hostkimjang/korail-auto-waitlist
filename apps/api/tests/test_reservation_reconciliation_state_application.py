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
    ReservationPolicy,
    WatchStatus,
)
from rail_waitlist.models import ReservationAttempt, Watch, WatchCandidate
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.attempt_result_application import (
    record_reservation_confirmation,
)
from rail_waitlist.reservations.payment_hold_application import _utc_instant
from rail_waitlist.reservations.reconciliation_policy import (
    RESERVATION_RECONCILIATION_INTERVAL,
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
    unknown_reconciliation_retry_interval,
)
from rail_waitlist.reservations.reconciliation_state_application import (
    ReservationReconciliationNotEligible,
    ReservationReconciliationStateDependencies,
)
from rail_waitlist.reservations.reconciliation_state_application import (
    apply_reservation_reconciliation as apply_reservation_reconciliation_application,
)
from rail_waitlist.services import apply_reservation_reconciliation

NOW = datetime(2026, 8, 5, 6, tzinfo=UTC)


def make_watch(
    *,
    status: WatchStatus = WatchStatus.WATCHING,
    policy: ReservationPolicy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
) -> Watch:
    return Watch(
        id="watch-1",
        provider=Provider.SRT,
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
        reservation_policy=policy,
        status=status,
        dedupe_key="reconciliation-state-owner-test",
    )


def make_candidate(*, candidate_id: str = "candidate-1", priority: int = 1) -> WatchCandidate:
    return WatchCandidate(
        id=candidate_id,
        watch_id="watch-1",
        train_number=f"30{priority}",
        departure_at=NOW + timedelta(hours=3),
        scheduled_departure_at=NOW + timedelta(hours=3),
        seat_class="standard",
        priority=priority,
        state="observed" if priority == 1 else "active",
    )


def make_attempt(
    *,
    outcome: ReservationOutcome = ReservationOutcome.UNKNOWN,
    reconciliation_count: int = 0,
) -> ReservationAttempt:
    return ReservationAttempt(
        id="attempt-1",
        candidate_id="candidate-1",
        attempt_sequence=2,
        episode_key="availability:reconcile-owner",
        idempotency_key="reserve:reconcile-owner",
        started_at=NOW - timedelta(minutes=1),
        finished_at=NOW - timedelta(seconds=30),
        outcome=outcome,
        reconciliation_attempt_count=reconciliation_count,
    )


class ScalarRows:
    def __init__(self, values: list[WatchCandidate]) -> None:
        self.values = values

    def all(self) -> list[WatchCandidate]:
        return self.values


class StateSession:
    def __init__(self, candidates: list[WatchCandidate] | None = None) -> None:
        self.candidates = candidates or []

    async def scalars(self, _statement: object) -> ScalarRows:
        return ScalarRows(self.candidates)


def make_dependencies(
    transitions: list[tuple[WatchStatus, str | None]],
    events: list[dict[str, object]],
) -> ReservationReconciliationStateDependencies:
    async def transition(
        _session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        _idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        observation: object | None = None,
    ) -> Watch:
        assert observation is None
        transitions.append((target, reason))
        watch.status = target
        return watch

    async def outbox(_session: AsyncSession, **values: object) -> object:
        events.append(values)
        return object()

    return ReservationReconciliationStateDependencies(
        apply_watch_transition=transition,
        add_outbox_event=outbox,
        record_reservation_confirmation=record_reservation_confirmation,
        utc_instant=_utc_instant,
    )


@pytest.mark.parametrize(
    ("completed_count", "expected"),
    [
        (-1, None),
        (0, None),
        (1, timedelta(seconds=30)),
        (2, timedelta(seconds=30)),
        (3, timedelta(minutes=5)),
        (4, timedelta(minutes=15)),
        (5, timedelta(minutes=60)),
        (6, None),
        (7, None),
    ],
)
def test_unknown_reconciliation_retry_policy_matrix(
    completed_count: int,
    expected: timedelta | None,
) -> None:
    assert unknown_reconciliation_retry_interval(completed_count) == expected
    assert RESERVATION_RECONCILIATION_MAX_ATTEMPTS == 3
    assert UNKNOWN_RECONCILIATION_MAX_ATTEMPTS == 6
    assert RESERVATION_RECONCILIATION_INTERVAL == timedelta(seconds=30)


@pytest.mark.parametrize(
    ("starting_count", "expected_interval"),
    [
        (0, timedelta(seconds=30)),
        (1, timedelta(seconds=30)),
        (2, timedelta(minutes=5)),
        (3, timedelta(minutes=15)),
        (4, timedelta(minutes=60)),
        (5, None),
    ],
)
async def test_unknown_inconclusive_state_preserves_extended_bounded_schedule(
    starting_count: int,
    expected_interval: timedelta | None,
) -> None:
    attempt = make_attempt(reconciliation_count=starting_count)
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source="srt.owner-test",
        observed_at=NOW,
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        make_watch(),
        make_candidate(),
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies([], []),
    )

    assert attempt.reconciliation_attempt_count == starting_count + 1
    assert attempt.last_reconciled_at == NOW
    assert attempt.next_reconcile_at == (
        NOW + expected_interval if expected_interval is not None else None
    )


async def test_positive_confirmation_restores_handoff_and_suppresses_lower_candidate() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.WATCHING)
    candidate = make_candidate()
    lower = make_candidate(candidate_id="candidate-2", priority=2)
    attempt = make_attempt()
    deadline = NOW + timedelta(minutes=10)
    handoff_url = "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do"
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="srt.owner-test",
        observed_at=NOW,
        payment_deadline=deadline,
        official_handoff_url=handoff_url,
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([lower])),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert attempt.payment_deadline == deadline
    assert attempt.official_handoff_url == handoff_url
    assert candidate.state == "payment_required"
    assert lower.state == "suppressed_by_priority"
    assert lower.suppressed_by_candidate_id == candidate.id
    assert watch.status is WatchStatus.PAYMENT_REQUIRED
    assert watch.payment_deadline == deadline
    assert watch.official_booking_url == handoff_url
    assert transitions == [
        (
            WatchStatus.PAYMENT_REQUIRED,
            "reservation_reconciliation_confirmed_payment_required",
        )
    ]
    assert events == [
        {
            "aggregate_type": "watch",
            "aggregate_id": "watch-1",
            "event_type": "watch.reservation_reconciled",
            "payload": {
                "watch_id": "watch-1",
                "candidate_id": "candidate-1",
                "attempt_sequence": 2,
                "confirmation_outcome": "confirmed_payment_required",
                "payment_deadline": deadline.isoformat(),
                "retryable": False,
            },
            "dedupe_key": f"reservation-reconciled:attempt-1:{NOW.isoformat()}",
        }
    ]


@pytest.mark.parametrize(
    ("policy", "expected_status", "expected_state", "event_type", "retryable"),
    [
        (
            ReservationPolicy.NOTIFY_ONLY,
            WatchStatus.EXPIRED,
            "expired",
            "watch.payment_hold_ended_one_off_expired",
            False,
        ),
        (
            ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            WatchStatus.WATCHING,
            "observed",
            "watch.payment_hold_ended_monitoring_resumed",
            True,
        ),
    ],
)
async def test_exact_not_found_ends_hold_for_one_off_or_resumed_monitoring(
    policy: ReservationPolicy,
    expected_status: WatchStatus,
    expected_state: str,
    event_type: str,
    retryable: bool,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED, policy=policy)
    watch.payment_deadline = NOW - timedelta(seconds=1)
    watch.official_booking_url = "https://etk.srail.kr/"
    candidate = make_candidate()
    candidate.state = "payment_required"
    suppressed = make_candidate(candidate_id="candidate-2", priority=2)
    suppressed.state = "suppressed_by_priority"
    suppressed.suppressed_by_candidate_id = candidate.id
    attempt = make_attempt(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_count=RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    )
    attempt.payment_deadline = watch.payment_deadline
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.NOT_FOUND,
        source="srt.owner-test",
        observed_at=NOW,
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([suppressed])),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert watch.status is expected_status
    assert candidate.state == expected_state
    assert suppressed.state == expected_state
    assert candidate.suppressed_by_candidate_id is None
    assert suppressed.suppressed_by_candidate_id is None
    assert watch.payment_deadline is None
    assert watch.official_booking_url is None
    assert watch.next_check_at == NOW
    assert attempt.post_deadline_reconciled_at == NOW
    assert transitions[0][0] is expected_status
    assert events[0]["event_type"] == event_type
    assert events[0]["dedupe_key"] == "payment-hold-ended:attempt-1"
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["automatic_reservation_retry"] is retryable
    assert payload["retry_condition"] == ("new_availability_episode" if retryable else None)


async def test_legacy_expired_confirmation_cleanup_keeps_single_extra_read_contract() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = NOW - timedelta(minutes=2)
    candidate = make_candidate()
    candidate.state = "payment_required"
    attempt = make_attempt(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_count=RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    )
    attempt.payment_deadline = NOW - timedelta(minutes=3)
    attempt.post_deadline_reconciled_at = NOW - timedelta(minutes=1)
    attempt.confirmation_outcome = ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    attempt.confirmation_source = "legacy.srt"
    attempt.confirmation_observed_at = NOW - timedelta(minutes=1)
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="srt.owner-test",
        observed_at=NOW,
        payment_deadline=NOW - timedelta(seconds=1),
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.reconciliation_attempt_count == 4
    assert attempt.post_deadline_reconciled_at == NOW
    assert attempt.payment_deadline == confirmation.payment_deadline
    assert watch.status is WatchStatus.WATCHING
    assert events[0]["event_type"] == "watch.payment_hold_ended_monitoring_resumed"
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["reason"] == "confirmed_payment_deadline_elapsed"
    assert payload["payment_deadline"] == confirmation.payment_deadline.isoformat()


async def test_state_rejects_ineligible_attempt_and_provider_mismatch() -> None:
    dependencies = make_dependencies([], [])
    with pytest.raises(ReservationReconciliationNotEligible):
        await apply_reservation_reconciliation_application(
            cast(AsyncSession, StateSession()),
            make_watch(),
            make_candidate(),
            make_attempt(outcome=ReservationOutcome.FAILED),
            ReservationConfirmationResult(
                provider=Provider.SRT,
                outcome=ReservationConfirmationOutcome.NOT_FOUND,
                source="srt.owner-test",
                observed_at=NOW,
            ),
            reconciled_at=NOW,
            dependencies=dependencies,
        )

    with pytest.raises(ValueError, match="reservation confirmation provider does not match watch"):
        await apply_reservation_reconciliation_application(
            cast(AsyncSession, StateSession()),
            make_watch(),
            make_candidate(),
            make_attempt(),
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.NOT_FOUND,
                source="korail.owner-test",
                observed_at=NOW,
            ),
            reconciled_at=NOW,
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

    def confirmation(*_args: object, **_kwargs: object) -> None:
        return None

    def utc_instant(value: datetime) -> datetime:
        return value

    async def application(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(services_module, "apply_watch_transition", transition)
    monkeypatch.setattr(services_module, "add_outbox_event", outbox)
    monkeypatch.setattr(services_module, "record_reservation_confirmation", confirmation)
    monkeypatch.setattr(services_module, "_utc_instant", utc_instant)
    monkeypatch.setattr(
        services_module,
        "apply_reservation_reconciliation_application",
        application,
    )

    await apply_reservation_reconciliation(
        cast(AsyncSession, object()),
        make_watch(),
        make_candidate(),
        make_attempt(),
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="srt.owner-test",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
    )

    kwargs = cast(dict[str, object], captured["kwargs"])
    dependencies = kwargs["dependencies"]
    assert isinstance(dependencies, ReservationReconciliationStateDependencies)
    assert dependencies.apply_watch_transition is transition
    assert dependencies.add_outbox_event is outbox
    assert dependencies.record_reservation_confirmation is confirmation
    assert dependencies.utc_instant is utc_instant

    async def conflict(*_args: object, **_kwargs: object) -> None:
        raise ReservationReconciliationNotEligible

    monkeypatch.setattr(
        services_module,
        "apply_reservation_reconciliation_application",
        conflict,
    )
    with pytest.raises(HTTPException) as captured_error:
        await apply_reservation_reconciliation(
            cast(AsyncSession, object()),
            make_watch(),
            make_candidate(),
            make_attempt(),
            ReservationConfirmationResult(
                provider=Provider.SRT,
                outcome=ReservationConfirmationOutcome.NOT_FOUND,
                source="srt.owner-test",
                observed_at=NOW,
            ),
            reconciled_at=NOW,
        )
    assert captured_error.value.status_code == 409
    assert captured_error.value.detail == "reservation attempt is not eligible for reconciliation"


def test_services_keeps_policy_compatibility_identities() -> None:
    assert (
        services_module.unknown_reconciliation_retry_interval
        is unknown_reconciliation_retry_interval
    )
    assert (
        services_module.RESERVATION_RECONCILIATION_MAX_ATTEMPTS
        == RESERVATION_RECONCILIATION_MAX_ATTEMPTS
    )
    assert (
        services_module.UNKNOWN_RECONCILIATION_MAX_ATTEMPTS == UNKNOWN_RECONCILIATION_MAX_ATTEMPTS
    )
