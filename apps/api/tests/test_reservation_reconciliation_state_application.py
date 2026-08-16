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
    ReservationResultReasonCode,
    WatchStatus,
)
from rail_waitlist.models import ReservationAttempt, Watch, WatchCandidate
from rail_waitlist.provider_account_management.schemas import RailProviderAuthStatus
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.attempt_result_application import (
    record_reservation_confirmation,
)
from rail_waitlist.reservations.payment_hold_application import _utc_instant
from rail_waitlist.reservations.reconciliation_policy import (
    PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS,
    RESERVATION_RECONCILIATION_INTERVAL,
    RESERVATION_RECONCILIATION_MAX_ATTEMPTS,
    UNKNOWN_RECONCILIATION_MAX_ATTEMPTS,
    ReservationReconciliationResolution,
    payment_hold_reconciliation_retry_interval,
    unknown_reconciliation_retry_interval,
)
from rail_waitlist.reservations.reconciliation_state_application import (
    ReservationReconciliationNotEligible,
    ReservationReconciliationStateDependencies,
    _add_reconciliation_outbox_event,
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
    def __init__(
        self,
        candidates: list[WatchCandidate] | None = None,
        *,
        latest_attempt_id: str | None = None,
    ) -> None:
        self.candidates = candidates or []
        self.latest_attempt_id = latest_attempt_id

    async def scalars(self, _statement: object) -> ScalarRows:
        return ScalarRows(self.candidates)

    async def scalar(self, _statement: object) -> str | None:
        return self.latest_attempt_id


def make_dependencies(
    transitions: list[tuple[WatchStatus, str | None]],
    events: list[dict[str, object]],
    *,
    account_updates: list[tuple[Provider, str, int]] | None = None,
    account_update_succeeds: bool = True,
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

    async def update_auth_status(
        _session: AsyncSession,
        provider: Provider,
        status: RailProviderAuthStatus,
        *,
        expected_credential_version: int,
    ) -> bool:
        if account_updates is not None:
            account_updates.append((provider, status, expected_credential_version))
        return account_update_succeeds

    return ReservationReconciliationStateDependencies(
        apply_watch_transition=transition,
        add_outbox_event=outbox,
        record_reservation_confirmation=record_reservation_confirmation,
        update_provider_auth_status=update_auth_status,
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
    ("completed_count", "expected"),
    [
        (0, None),
        (1, timedelta(seconds=30)),
        (2, timedelta(seconds=30)),
        (3, timedelta(minutes=2)),
        (4, timedelta(minutes=5)),
        (5, timedelta(minutes=10)),
        (6, None),
    ],
)
def test_payment_hold_reconciliation_retry_policy_is_bounded(
    completed_count: int,
    expected: timedelta | None,
) -> None:
    assert payment_hold_reconciliation_retry_interval(completed_count) == expected
    assert PAYMENT_HOLD_RECONCILIATION_MAX_ATTEMPTS == 6


async def test_reconciled_payload_normalizes_persisted_attempt_times_to_utc() -> None:
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = (NOW + timedelta(hours=1)).replace(tzinfo=None)
    candidate = make_candidate()
    candidate.state = "payment_required"
    attempt = make_attempt(outcome=ReservationOutcome.PAYMENT_REQUIRED)
    attempt.result_reason_code = ReservationResultReasonCode.PAYMENT_HOLD_CREATED
    attempt.started_at = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    attempt.finished_at = (NOW - timedelta(seconds=30)).replace(tzinfo=None)
    attempt.next_reconcile_at = (NOW + timedelta(seconds=30)).replace(tzinfo=None)
    attempt.payment_deadline = watch.payment_deadline
    attempt.progress_stages = [
        {
            "stage": "reservation_requested",
            "occurred_at": (NOW - timedelta(seconds=45)).isoformat(),
        }
    ]
    attempt.reserved_seats = [{"car_number": "4", "seat_number": "8A"}]
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source="srt.owner-test",
        observed_at=NOW,
    )
    dependencies = make_dependencies([], events)

    await _add_reconciliation_outbox_event(
        cast(AsyncSession, StateSession()),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=dependencies,
    )

    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["attempt_started_at"] == (NOW - timedelta(minutes=1)).isoformat()
    assert payload["attempt_finished_at"] == (NOW - timedelta(seconds=30)).isoformat()
    assert payload["next_reconcile_at"] == (NOW + timedelta(seconds=30)).isoformat()
    assert payload["payment_deadline"] == (NOW + timedelta(hours=1)).isoformat()
    assert payload["progress_stages"] == attempt.progress_stages
    assert payload["reserved_seats"] == attempt.reserved_seats


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
    assert attempt.reconciliation_resolution is (
        ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED
        if starting_count == UNKNOWN_RECONCILIATION_MAX_ATTEMPTS - 1
        else None
    )


async def test_unknown_requires_two_official_absence_reads_before_terminal_fence() -> None:
    attempt = make_attempt()
    attempt.confirmation_outcome = ReservationConfirmationOutcome.INCONCLUSIVE
    attempt.confirmation_source = "srt.owner-test"
    attempt.confirmation_observed_at = NOW - timedelta(seconds=30)
    first_not_found = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.NOT_FOUND,
        source="srt.owner-test",
        observed_at=NOW,
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        make_watch(),
        make_candidate(),
        attempt,
        first_not_found,
        reconciled_at=NOW,
        dependencies=make_dependencies([], []),
    )

    assert attempt.reconciliation_attempt_count == 1
    assert attempt.next_reconcile_at == NOW + timedelta(seconds=30)
    assert attempt.reconciliation_resolution is None

    second_at = NOW + timedelta(seconds=30)
    second_not_found = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.NOT_FOUND,
        source="srt.owner-test",
        observed_at=second_at,
    )
    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        make_watch(),
        make_candidate(),
        attempt,
        second_not_found,
        reconciled_at=second_at,
        dependencies=make_dependencies([], []),
    )

    assert attempt.reconciliation_attempt_count == 2
    assert attempt.next_reconcile_at is None
    assert attempt.reconciliation_resolution is ReservationReconciliationResolution.CONFIRMED_ABSENT


async def test_unknown_initial_absence_and_delayed_absence_close_reconciliation() -> None:
    attempt = make_attempt()
    attempt.confirmation_outcome = ReservationConfirmationOutcome.NOT_FOUND
    attempt.confirmation_source = "srt.owner-test"
    attempt.confirmation_observed_at = NOW - timedelta(seconds=30)
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.NOT_FOUND,
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

    assert attempt.reconciliation_attempt_count == 1
    assert attempt.next_reconcile_at is None
    assert attempt.reconciliation_resolution is ReservationReconciliationResolution.CONFIRMED_ABSENT


async def test_unknown_final_single_absence_is_exhausted_not_confirmed_absent() -> None:
    events: list[dict[str, object]] = []
    attempt = make_attempt(reconciliation_count=UNKNOWN_RECONCILIATION_MAX_ATTEMPTS - 1)
    attempt.confirmation_outcome = ReservationConfirmationOutcome.INCONCLUSIVE
    attempt.confirmation_source = "srt.owner-test"
    attempt.confirmation_observed_at = NOW - timedelta(minutes=1)

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        make_watch(),
        make_candidate(),
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="srt.owner-test",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies([], events),
    )

    assert attempt.reconciliation_attempt_count == UNKNOWN_RECONCILIATION_MAX_ATTEMPTS
    assert attempt.next_reconcile_at is None
    assert (
        attempt.reconciliation_resolution
        is ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED
    )
    payload = cast(dict[str, object], events[-1]["payload"])
    assert payload["reconciliation_resolution"] == "exhausted_unresolved"


@pytest.mark.parametrize(
    ("starting_count", "expected_interval"),
    [
        (0, timedelta(seconds=30)),
        (1, timedelta(seconds=30)),
        (2, timedelta(minutes=2)),
        (3, timedelta(minutes=5)),
        (4, timedelta(minutes=10)),
        (5, None),
    ],
)
@pytest.mark.parametrize(
    "outcome",
    [
        ReservationConfirmationOutcome.INCONCLUSIVE,
        ReservationConfirmationOutcome.NOT_FOUND,
    ],
)
async def test_known_future_payment_hold_keeps_six_read_schedule_after_uncertain_result(
    starting_count: int,
    expected_interval: timedelta | None,
    outcome: ReservationConfirmationOutcome,
) -> None:
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = NOW + timedelta(hours=1)
    candidate = make_candidate()
    candidate.state = "payment_required"
    attempt = make_attempt(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_count=starting_count,
    )
    attempt.payment_deadline = watch.payment_deadline
    attempt.confirmation_outcome = ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=outcome,
        source="srt.payment-follow-up-owner-test",
        observed_at=NOW,
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies([], events),
    )

    assert attempt.confirmation_outcome is outcome
    assert attempt.reconciliation_attempt_count == starting_count + 1
    assert attempt.next_reconcile_at == (
        NOW + expected_interval if expected_interval is not None else None
    )
    assert watch.status is WatchStatus.PAYMENT_REQUIRED
    assert watch.payment_deadline == NOW + timedelta(hours=1)
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["payment_deadline"] == watch.payment_deadline.isoformat()
    assert payload["payment_actionable"] is True


async def test_expired_confirmed_hold_for_unknown_attempt_emits_canonical_progress() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = NOW - timedelta(seconds=1)
    attempt = make_attempt(
        outcome=ReservationOutcome.UNKNOWN,
        reconciliation_count=1,
    )
    attempt.progress_stages = [
        {
            "stage": "reservation_requested",
            "occurred_at": (NOW - timedelta(seconds=45)).isoformat(),
        }
    ]
    attempt.reserved_seats = []
    attempt.payment_deadline = watch.payment_deadline
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="srt.payment-follow-up-owner-test",
        observed_at=NOW,
        payment_deadline=watch.payment_deadline,
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        watch,
        make_candidate(),
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert transitions == []
    assert attempt.reconciliation_attempt_count == 2
    assert attempt.next_reconcile_at == NOW + RESERVATION_RECONCILIATION_INTERVAL
    assert events == [
        {
            "aggregate_type": "watch",
            "aggregate_id": "watch-1",
            "event_type": "watch.reservation_reconciled",
            "payload": {
                "watch_id": "watch-1",
                "candidate_id": "candidate-1",
                "attempt_sequence": 2,
                "attempt_started_at": (NOW - timedelta(minutes=1)).isoformat(),
                "attempt_finished_at": (NOW - timedelta(seconds=30)).isoformat(),
                "outcome": "unknown",
                "result_reason_code": "reservation_request_result_unknown",
                "payment_actionable": False,
                "confirmation_outcome": "confirmed_payment_required",
                "confirmation_diagnostic_code": None,
                "confirmation_observed_at": NOW.isoformat(),
                "reconciliation_attempt_count": 2,
                "reconciliation_resolution": None,
                "next_reconcile_at": (NOW + RESERVATION_RECONCILIATION_INTERVAL).isoformat(),
                "payment_deadline": watch.payment_deadline.isoformat(),
                "progress_stages": attempt.progress_stages,
                "reserved_seats": [],
                "retryable": False,
            },
            "dedupe_key": f"reservation-reconciled:attempt-1:{NOW.isoformat()}",
        }
    ]


async def test_fresh_expired_deadline_overrides_existing_future_hold_for_actionability() -> None:
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = NOW + timedelta(hours=1)
    candidate = make_candidate()
    candidate.state = "payment_required"
    attempt = make_attempt(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_count=1,
    )
    attempt.payment_deadline = watch.payment_deadline
    expired_deadline = NOW - timedelta(seconds=1)
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="srt.payment-follow-up-owner-test",
        observed_at=NOW,
        payment_deadline=expired_deadline,
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies([], events),
    )

    assert watch.payment_deadline == NOW + timedelta(hours=1)
    assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert events[0]["event_type"] == "watch.reservation_reconciled"
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["outcome"] == "payment_required"
    assert payload["payment_actionable"] is False
    assert payload["payment_deadline"] == expired_deadline.isoformat()


async def test_positive_confirmation_restores_handoff_and_suppresses_lower_candidate() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.WATCHING)
    candidate = make_candidate()
    lower = make_candidate(candidate_id="candidate-2", priority=2)
    attempt = make_attempt()
    attempt.progress_stages = [
        {
            "stage": "reservation_requested",
            "occurred_at": (NOW - timedelta(seconds=45)).isoformat(),
        }
    ]
    attempt.reserved_seats = [{"car_number": "4", "seat_number": "8A"}]
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
    assert attempt.next_reconcile_at == NOW + timedelta(seconds=30)
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
                "attempt_started_at": (NOW - timedelta(minutes=1)).isoformat(),
                "attempt_finished_at": (NOW - timedelta(seconds=30)).isoformat(),
                "outcome": "payment_required",
                "result_reason_code": "payment_hold_created",
                "payment_actionable": True,
                "confirmation_outcome": "confirmed_payment_required",
                "confirmation_diagnostic_code": None,
                "confirmation_observed_at": NOW.isoformat(),
                "reconciliation_attempt_count": 1,
                "reconciliation_resolution": None,
                "next_reconcile_at": (NOW + timedelta(seconds=30)).isoformat(),
                "payment_deadline": deadline.isoformat(),
                "progress_stages": attempt.progress_stages,
                "reserved_seats": attempt.reserved_seats,
                "retryable": False,
            },
            "dedupe_key": f"reservation-reconciled:attempt-1:{NOW.isoformat()}",
        }
    ]


async def test_positive_confirmation_without_deadline_remains_payment_actionable() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.WATCHING)
    candidate = make_candidate()
    attempt = make_attempt()
    handoff_url = "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do"
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="srt.owner-test",
        observed_at=NOW,
        official_handoff_url=handoff_url,
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

    assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert attempt.payment_deadline is None
    assert candidate.state == "payment_required"
    assert watch.status is WatchStatus.PAYMENT_REQUIRED
    assert watch.payment_deadline is None
    assert watch.official_booking_url == handoff_url
    assert transitions == [
        (
            WatchStatus.PAYMENT_REQUIRED,
            "reservation_reconciliation_confirmed_payment_required",
        )
    ]
    assert events[0]["event_type"] == "watch.reservation_reconciled"
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["outcome"] == "payment_required"
    assert payload["result_reason_code"] == "payment_hold_created"
    assert payload["payment_actionable"] is True
    assert payload["payment_deadline"] is None


async def test_exact_paid_confirmation_completes_watch_and_clears_payment_prompt() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = NOW + timedelta(minutes=8)
    watch.official_booking_url = "https://etk.srail.kr/hpg/hra/02/selectReservationList.do"
    watch.next_check_at = NOW + timedelta(seconds=30)
    candidate = make_candidate()
    candidate.state = "payment_required"
    suppressed = make_candidate(candidate_id="candidate-2", priority=2)
    suppressed.state = "suppressed_by_priority"
    suppressed.suppressed_by_candidate_id = candidate.id
    attempt = make_attempt(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_count=2,
    )
    attempt.payment_deadline = watch.payment_deadline
    attempt.official_handoff_url = watch.official_booking_url
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
        source="srtrain-reservation-list",
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

    assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert attempt.next_reconcile_at is None
    assert watch.status is WatchStatus.COMPLETED
    assert watch.payment_deadline is None
    assert watch.official_booking_url is None
    assert watch.next_check_at is None
    assert candidate.state == "expired"
    assert suppressed.state == "expired"
    assert transitions == [(WatchStatus.COMPLETED, "reservation_reconciliation_confirmed_paid")]
    assert events[0]["event_type"] == "watch.payment_completed"
    assert events[0]["dedupe_key"] == "payment-completed:attempt-1"


async def test_exact_paid_confirmation_resolves_unknown_without_rewriting_attempt_audit() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.WATCHING)
    watch.next_check_at = NOW + timedelta(seconds=30)
    candidate = make_candidate()
    other_candidate = make_candidate(candidate_id="candidate-2", priority=2)
    attempt = make_attempt(
        outcome=ReservationOutcome.UNKNOWN,
        reconciliation_count=2,
    )
    confirmation = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
        source="srtrain-reservation-list",
        observed_at=NOW,
    )

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([other_candidate])),
        watch,
        candidate,
        attempt,
        confirmation,
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert attempt.outcome is ReservationOutcome.UNKNOWN
    assert (
        attempt.result_reason_code is ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
    )
    assert attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert attempt.reconciliation_resolution is None
    assert attempt.next_reconcile_at is None
    assert watch.status is WatchStatus.COMPLETED
    assert watch.next_check_at is None
    assert candidate.state == "expired"
    assert other_candidate.state == "expired"
    assert other_candidate.suppressed_by_candidate_id is None
    assert transitions == [(WatchStatus.COMPLETED, "reservation_reconciliation_confirmed_paid")]
    assert events[0]["event_type"] == "watch.payment_completed"
    payment_payload = cast(dict[str, object], events[0]["payload"])
    assert payment_payload["from"] == "watching"
    assert payment_payload["to"] == "completed"
    assert events[1]["event_type"] == "watch.reservation_reconciled"
    reconciliation_payload = cast(dict[str, object], events[1]["payload"])
    assert reconciliation_payload["outcome"] == "unknown"
    assert reconciliation_payload["confirmation_outcome"] == "confirmed_paid"
    assert reconciliation_payload["reconciliation_resolution"] is None


async def test_exact_paid_confirmation_preserves_paused_state_but_closes_all_retry_state() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.PAUSED)
    watch.payment_deadline = NOW + timedelta(minutes=8)
    watch.official_booking_url = "https://etk.srail.kr/hpg/hra/02/selectReservationList.do"
    watch.next_check_at = NOW + timedelta(seconds=30)
    watch.observation_in_flight_until = NOW + timedelta(minutes=1)
    candidate = make_candidate()
    candidate.manual_rearm_source_attempt_id = "attempt-1"
    candidate.manual_rearm_authorized_at = NOW - timedelta(minutes=1)
    other_candidate = make_candidate(candidate_id="candidate-2", priority=2)
    other_candidate.manual_rearm_source_attempt_id = "attempt-1"
    other_candidate.manual_rearm_authorized_at = NOW - timedelta(minutes=1)
    attempt = make_attempt(outcome=ReservationOutcome.UNKNOWN, reconciliation_count=2)
    attempt.next_reconcile_at = NOW + timedelta(seconds=30)

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([candidate, other_candidate])),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
            source="srtrain-reservation-list",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert watch.status is WatchStatus.PAUSED
    assert transitions == []
    assert watch.payment_deadline is None
    assert watch.official_booking_url is None
    assert watch.next_check_at is None
    assert watch.observation_in_flight_until is None
    assert attempt.next_reconcile_at is None
    assert attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert all(item.state == "expired" for item in (candidate, other_candidate))
    assert all(
        item.manual_rearm_source_attempt_id is None and item.manual_rearm_authorized_at is None
        for item in (candidate, other_candidate)
    )
    assert [event["event_type"] for event in events] == ["watch.reservation_reconciled"]


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
    assert events[1]["event_type"] == "watch.reservation_reconciled"
    reconciled_payload = cast(dict[str, object], events[1]["payload"])
    assert reconciled_payload["outcome"] == "payment_required"
    assert reconciled_payload["payment_actionable"] is False


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


@pytest.mark.parametrize(
    ("confirmation_outcome", "account_status", "transition_reason"),
    [
        (
            ReservationConfirmationOutcome.AUTH_REQUIRED,
            "auth_required",
            "reservation_reconciliation_auth_required",
        ),
        (
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
            "provider_blocked",
            "reservation_reconciliation_provider_blocked",
        ),
    ],
)
async def test_unknown_auth_confirmation_demotes_account_without_consuming_evidence_budget(
    confirmation_outcome: ReservationConfirmationOutcome,
    account_status: RailProviderAuthStatus,
    transition_reason: str,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    account_updates: list[tuple[Provider, str, int]] = []
    watch = make_watch()
    watch.next_check_at = NOW + timedelta(minutes=1)
    watch.observation_in_flight_until = NOW + timedelta(minutes=2)
    candidate = make_candidate()
    candidate.manual_rearm_source_attempt_id = "attempt-1"
    candidate.manual_rearm_authorized_at = NOW - timedelta(minutes=1)
    sibling = make_candidate(candidate_id="candidate-2", priority=2)
    sibling.manual_rearm_source_attempt_id = "attempt-other"
    sibling.manual_rearm_authorized_at = NOW - timedelta(minutes=2)
    attempt = make_attempt(reconciliation_count=5)
    attempt.credential_version = 7

    await apply_reservation_reconciliation_application(
        cast(
            AsyncSession,
            StateSession([candidate, sibling], latest_attempt_id=attempt.id),
        ),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=confirmation_outcome,
            source="worker-reconciliation",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(
            transitions,
            events,
            account_updates=account_updates,
        ),
    )

    assert account_updates == [(Provider.SRT, account_status, 7)]
    assert attempt.outcome is ReservationOutcome.UNKNOWN
    assert (
        attempt.result_reason_code is ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN
    )
    assert attempt.reconciliation_attempt_count == 5
    assert attempt.last_reconciled_at == NOW
    assert attempt.confirmation_outcome is confirmation_outcome
    assert attempt.next_reconcile_at is None
    assert attempt.reconciliation_resolution is None
    assert candidate.state == "observed"
    assert all(item.manual_rearm_source_attempt_id is None for item in (candidate, sibling))
    assert all(item.manual_rearm_authorized_at is None for item in (candidate, sibling))
    assert watch.status is WatchStatus.AUTH_REQUIRED
    assert watch.next_check_at is None
    assert watch.observation_in_flight_until is None
    assert transitions == [(WatchStatus.AUTH_REQUIRED, transition_reason)]
    assert [event["event_type"] for event in events] == ["watch.reservation_reconciled"]
    payload = cast(dict[str, object], events[0]["payload"])
    assert payload["reconciliation_attempt_count"] == 5
    assert payload["reconciliation_resolution"] is None


async def test_unknown_auth_generation_mismatch_leaves_account_and_watch_state_untouched() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    account_updates: list[tuple[Provider, str, int]] = []
    watch = make_watch()
    candidate = make_candidate()
    candidate.manual_rearm_source_attempt_id = "attempt-1"
    candidate.manual_rearm_authorized_at = NOW - timedelta(minutes=1)
    attempt = make_attempt(reconciliation_count=5)
    attempt.credential_version = 7

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([candidate], latest_attempt_id=attempt.id)),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source="worker-reconciliation",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(
            transitions,
            events,
            account_updates=account_updates,
            account_update_succeeds=False,
        ),
    )

    assert account_updates == [(Provider.SRT, "auth_required", 7)]
    assert attempt.result_reason_code is None
    assert attempt.confirmation_outcome is None
    assert attempt.last_reconciled_at is None
    assert attempt.reconciliation_attempt_count == 5
    assert candidate.manual_rearm_source_attempt_id == "attempt-1"
    assert candidate.manual_rearm_authorized_at is not None
    assert watch.status is WatchStatus.WATCHING
    assert transitions == []
    assert events == []


@pytest.mark.parametrize(
    ("watch_status", "latest_attempt_id", "expected_transitions"),
    [
        (WatchStatus.PAUSED, "attempt-1", []),
        (WatchStatus.EXPIRED, "attempt-1", []),
        (WatchStatus.WATCHING, "newer-attempt", []),
        (
            WatchStatus.SCHEDULED,
            "attempt-1",
            [
                (WatchStatus.WATCHING, "worker_claimed_reconciliation"),
                (WatchStatus.AUTH_REQUIRED, "reservation_reconciliation_provider_blocked"),
            ],
        ),
    ],
)
async def test_unknown_auth_core_evidence_is_independent_from_watch_presentation_races(
    watch_status: WatchStatus,
    latest_attempt_id: str,
    expected_transitions: list[tuple[WatchStatus, str | None]],
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    account_updates: list[tuple[Provider, str, int]] = []
    watch = make_watch(status=watch_status)
    candidate = make_candidate()
    candidate.manual_rearm_source_attempt_id = "attempt-1"
    candidate.manual_rearm_authorized_at = NOW - timedelta(minutes=1)
    attempt = make_attempt(reconciliation_count=5)
    attempt.credential_version = 9

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([candidate], latest_attempt_id=latest_attempt_id)),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.PROVIDER_BLOCKED,
            source="worker-reconciliation",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(
            transitions,
            events,
            account_updates=account_updates,
        ),
    )

    assert account_updates == [(Provider.SRT, "provider_blocked", 9)]
    assert attempt.confirmation_outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED
    assert attempt.reconciliation_attempt_count == 5
    assert attempt.next_reconcile_at is None
    assert candidate.manual_rearm_source_attempt_id is None
    assert candidate.manual_rearm_authorized_at is None
    assert transitions == expected_transitions
    expected_status = expected_transitions[-1][0] if expected_transitions else watch_status
    assert watch.status is expected_status
    assert [event["event_type"] for event in events] == ["watch.reservation_reconciled"]


async def test_unknown_auth_does_not_revive_candidate_expired_during_confirmation_io() -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch()
    candidate = make_candidate()
    candidate.state = "expired"
    attempt = make_attempt(reconciliation_count=4)
    attempt.credential_version = 3

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([candidate], latest_attempt_id=attempt.id)),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source="worker-reconciliation",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert candidate.state == "expired"
    assert candidate.suppressed_by_candidate_id is None
    assert watch.status is WatchStatus.AUTH_REQUIRED
    assert transitions == [(WatchStatus.AUTH_REQUIRED, "reservation_reconciliation_auth_required")]


@pytest.mark.parametrize(
    ("confirmation_outcome", "account_status"),
    [
        (ReservationConfirmationOutcome.AUTH_REQUIRED, "auth_required"),
        (ReservationConfirmationOutcome.PROVIDER_BLOCKED, "provider_blocked"),
    ],
)
async def test_payment_hold_auth_confirmation_preserves_user_payment_state(
    confirmation_outcome: ReservationConfirmationOutcome,
    account_status: RailProviderAuthStatus,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    account_updates: list[tuple[Provider, str, int]] = []
    deadline = NOW + timedelta(minutes=8)
    handoff = "https://etk.srail.kr/hpg/hra/02/selectReservationList.do"
    watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
    watch.payment_deadline = deadline
    watch.official_booking_url = handoff
    watch.next_check_at = NOW + timedelta(minutes=1)
    candidate = make_candidate()
    candidate.state = "payment_required"
    candidate.manual_rearm_source_attempt_id = "attempt-1"
    candidate.manual_rearm_authorized_at = NOW - timedelta(minutes=1)
    attempt = make_attempt(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_count=5,
    )
    attempt.credential_version = 12
    attempt.payment_deadline = deadline
    attempt.official_handoff_url = handoff

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession([candidate])),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=confirmation_outcome,
            source="worker-reconciliation",
            observed_at=NOW,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(
            transitions,
            events,
            account_updates=account_updates,
        ),
    )

    assert account_updates == [(Provider.SRT, account_status, 12)]
    assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert attempt.reconciliation_attempt_count == 5
    assert attempt.confirmation_outcome is confirmation_outcome
    assert attempt.next_reconcile_at is None
    assert attempt.reconciliation_resolution is None
    assert attempt.payment_deadline == deadline
    assert attempt.official_handoff_url == handoff
    assert watch.status is WatchStatus.PAYMENT_REQUIRED
    assert watch.payment_deadline == deadline
    assert watch.official_booking_url == handoff
    assert candidate.state == "payment_required"
    assert candidate.manual_rearm_source_attempt_id is None
    assert candidate.manual_rearm_authorized_at is None
    assert transitions == []
    assert [event["event_type"] for event in events] == ["watch.reservation_reconciled"]


@pytest.mark.parametrize(
    (
        "confirmation_outcome",
        "expected_watch_status",
        "expected_attempt_outcome",
        "expected_resolution",
    ),
    [
        (
            ReservationConfirmationOutcome.INCONCLUSIVE,
            WatchStatus.WATCHING,
            ReservationOutcome.UNKNOWN,
            ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED,
        ),
        (
            ReservationConfirmationOutcome.NOT_FOUND,
            WatchStatus.WATCHING,
            ReservationOutcome.UNKNOWN,
            ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED,
        ),
        (
            ReservationConfirmationOutcome.CONFIRMED_PAID,
            WatchStatus.COMPLETED,
            ReservationOutcome.UNKNOWN,
            None,
        ),
        (
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            WatchStatus.PAYMENT_REQUIRED,
            ReservationOutcome.PAYMENT_REQUIRED,
            None,
        ),
    ],
)
async def test_same_generation_reconciliation_from_scheduled_handles_exact_follow_up(
    confirmation_outcome: ReservationConfirmationOutcome,
    expected_watch_status: WatchStatus,
    expected_attempt_outcome: ReservationOutcome,
    expected_resolution: ReservationReconciliationResolution | None,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []
    watch = make_watch(status=WatchStatus.SCHEDULED)
    candidate = make_candidate()
    attempt = make_attempt(reconciliation_count=5)
    attempt.credential_version = 4
    attempt.confirmation_outcome = ReservationConfirmationOutcome.AUTH_REQUIRED
    attempt.confirmation_source = "worker-reconciliation"
    attempt.confirmation_observed_at = NOW - timedelta(minutes=2)
    attempt.last_reconciled_at = NOW - timedelta(minutes=2)
    confirmation_values: dict[str, object] = {}
    if confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED:
        confirmation_values = {
            "payment_deadline": NOW + timedelta(minutes=10),
            "official_handoff_url": ("https://etk.srail.kr/hpg/hra/02/selectReservationList.do"),
        }

    await apply_reservation_reconciliation_application(
        cast(AsyncSession, StateSession()),
        watch,
        candidate,
        attempt,
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=confirmation_outcome,
            source="worker-reconciliation",
            observed_at=NOW,
            **confirmation_values,
        ),
        reconciled_at=NOW,
        dependencies=make_dependencies(transitions, events),
    )

    assert transitions[0] == (WatchStatus.WATCHING, "worker_claimed_reconciliation")
    assert watch.status is expected_watch_status
    assert attempt.outcome is expected_attempt_outcome
    assert attempt.reconciliation_attempt_count == 6
    assert attempt.reconciliation_resolution is expected_resolution
    assert attempt.next_reconcile_at is None
