from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import rail_waitlist.services as services_module
from rail_waitlist.domain import Provider, ReservationOutcome, SeatObservationStatus, WatchStatus
from rail_waitlist.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.attempt_claim_application import (
    ReservationAttemptClaimDependencies,
)
from rail_waitlist.reservations.attempt_claim_application import (
    begin_reservation_attempt as begin_reservation_attempt_application,
)
from rail_waitlist.services import begin_reservation_attempt


def make_watch() -> Watch:
    return Watch(
        provider=Provider.SRT,
        origin="수서",
        origin_node_id="N-SUSEO",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2026, 8, 5),
        time_from=time(12),
        time_to=time(18),
        train_numbers=["SRT-301"],
        notification_channel_ids=[],
        mode="official",
        status=WatchStatus.SEAT_FOUND,
        dedupe_key="attempt-claim-owner-test",
    )


def make_candidate() -> WatchCandidate:
    departure_at = datetime(2026, 8, 5, 3, tzinfo=UTC)
    return WatchCandidate(
        train_number="SRT-301",
        departure_at=departure_at,
        scheduled_departure_at=departure_at,
        seat_class="standard",
        priority=1,
        state="seat_found",
    )


async def test_claim_application_preserves_attempt_state_transition_and_event_contract(
    db_engine,
) -> None:
    transitions: list[tuple[WatchStatus, str | None]] = []
    events: list[dict[str, object]] = []

    async def apply_transition(
        _session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        _idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        observation: SeatObservation | None = None,
    ) -> Watch:
        assert observation is None
        transitions.append((target, reason))
        watch.status = target
        return watch

    async def add_event(
        _session: AsyncSession,
        **values: object,
    ) -> object:
        events.append(values)
        return object()

    dependencies = ReservationAttemptClaimDependencies(
        apply_watch_transition=apply_transition,
        add_outbox_event=add_event,
        is_payment_hold_ended=lambda _attempt: False,
        is_confirmed_absent_retry_source=lambda _attempt: False,
        actionable_seat_statuses=frozenset(
            {
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.LIMITED,
                SeatObservationStatus.STANDING_PLUS_SEAT,
                SeatObservationStatus.WAITLIST_AVAILABLE,
            }
        ),
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = make_watch()
        candidate = make_candidate()
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        seat_detected_at = datetime.now(UTC) - timedelta(seconds=5)
        session.add_all(
            [
                SeatObservation(
                    candidate=candidate,
                    status=SeatObservationStatus.AVAILABLE,
                    source="authorized-provider",
                    observed_at=seat_detected_at,
                    fresh_until=seat_detected_at + timedelta(minutes=1),
                ),
                SeatObservation(
                    candidate=candidate,
                    status=SeatObservationStatus.AVAILABLE,
                    source="authorized-provider",
                    observed_at=seat_detected_at + timedelta(days=1),
                    fresh_until=seat_detected_at + timedelta(days=1, minutes=1),
                ),
            ]
        )
        await session.flush()

        attempt, created = await begin_reservation_attempt_application(
            session,
            watch,
            candidate,
            "reserve:owner-test",
            dependencies=dependencies,
        )
        replayed, replay_created = await begin_reservation_attempt_application(
            session,
            watch,
            candidate,
            "reserve:replay-key-does-not-win",
            episode_key=attempt.episode_key,
            dependencies=dependencies,
        )

        assert created is True
        assert replay_created is False
        assert replayed is attempt
        assert attempt.attempt_sequence == 1
        assert attempt.episode_key == "manual:reserve:owner-test"
        assert attempt.outcome is ReservationOutcome.PENDING
        assert candidate.state == "reservation_attempted"
        assert watch.reservation_attempted is True
        assert transitions == [(WatchStatus.RESERVING, "reservation_attempt_claimed")]
        assert events == [
            {
                "aggregate_type": "watch",
                "aggregate_id": watch.id,
                "event_type": "watch.reservation_attempted",
                "payload": {
                    "watch_id": watch.id,
                    "candidate_id": candidate.id,
                    "attempt_sequence": 1,
                    "seat_detected_at": seat_detected_at.isoformat(),
                    "attempt_started_at": attempt.started_at.isoformat(),
                    "episode_key": "manual:reserve:owner-test",
                    "outcome": ReservationOutcome.PENDING.value,
                },
                "dedupe_key": f"reservation-attempt:{attempt.id}",
            }
        ]
        assert session.in_transaction() is True


async def test_claim_revalidates_watch_scoped_hold_for_candidate_without_attempt(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ended_at = datetime.now(UTC) - timedelta(minutes=2)
    async with factory() as session:
        watch = make_watch()
        primary = make_candidate()
        lower = make_candidate()
        lower.train_number = "SRT-303"
        lower.departure_at += timedelta(minutes=10)
        lower.scheduled_departure_at = lower.departure_at
        lower.priority = 2
        stale_lower = make_candidate()
        stale_lower.train_number = "SRT-305"
        stale_lower.departure_at += timedelta(minutes=20)
        stale_lower.scheduled_departure_at = stale_lower.departure_at
        stale_lower.priority = 3
        watch.candidates.extend([primary, lower, stale_lower])
        session.add(watch)
        await session.flush()
        hold = ReservationAttempt(
            candidate_id=primary.id,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="watch-hold-primary",
            started_at=ended_at - timedelta(minutes=2),
            finished_at=ended_at - timedelta(minutes=1),
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="official-reservation-list",
            confirmation_observed_at=ended_at,
            post_deadline_reconciled_at=ended_at,
        )
        unavailable = SeatObservation(
            candidate=lower,
            status=SeatObservationStatus.SOLD_OUT,
            source="authorized-provider",
            observed_at=ended_at + timedelta(seconds=10),
            fresh_until=ended_at + timedelta(minutes=1),
        )
        session.add_all([hold, unavailable])
        await session.flush()
        episode_key = f"availability-after-hold:{hold.id}:{unavailable.id}"

        blocked, created = await begin_reservation_attempt(
            session,
            watch,
            lower,
            "watch-hold-lower-before-actionable",
            episode_key=episode_key,
            retry_authorized=True,
        )
        assert blocked is hold
        assert created is False

        actionable = SeatObservation(
            candidate=lower,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=ended_at + timedelta(seconds=20),
            fresh_until=ended_at + timedelta(minutes=1),
        )
        session.add(actionable)
        await session.flush()
        retry, created = await begin_reservation_attempt(
            session,
            watch,
            lower,
            "watch-hold-lower-after-actionable",
            episode_key=episode_key,
            retry_authorized=True,
        )
        assert created is True
        assert retry.candidate_id == lower.id
        assert retry.attempt_sequence == 1

        stale_replay, created = await begin_reservation_attempt(
            session,
            watch,
            stale_lower,
            "watch-hold-stale-lower",
            episode_key=episode_key,
            retry_authorized=True,
        )
        assert stale_replay is hold
        assert created is False


@pytest.mark.parametrize(
    ("outcome", "episode_prefix"),
    [
        (ReservationOutcome.NOT_AVAILABLE, "not-available-retry:"),
        (ReservationOutcome.NOT_AVAILABLE, "availability-after:"),
        (ReservationOutcome.UNKNOWN, "confirmed-absent-retry:"),
    ],
)
async def test_claim_rejects_same_availability_retry_even_if_caller_marks_it_authorized(
    db_engine,
    outcome: ReservationOutcome,
    episode_prefix: str,
) -> None:
    async def fail_transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("a fenced retry must not transition the watch")

    async def fail_outbox(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a fenced retry must not emit an outbox event")

    dependencies = ReservationAttemptClaimDependencies(
        apply_watch_transition=fail_transition,
        add_outbox_event=fail_outbox,
        is_payment_hold_ended=lambda _attempt: False,
        is_confirmed_absent_retry_source=lambda _attempt: True,
        actionable_seat_statuses=frozenset({SeatObservationStatus.AVAILABLE}),
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = make_watch()
        candidate = make_candidate()
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        previous = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key=f"previous-{outcome.value}",
            outcome=outcome,
            confirmation_outcome=(
                ReservationConfirmationOutcome.NOT_FOUND
                if outcome is ReservationOutcome.UNKNOWN
                else None
            ),
            confirmation_source=(
                "official-list" if outcome is ReservationOutcome.UNKNOWN else None
            ),
            confirmation_observed_at=(
                datetime(2026, 8, 5, tzinfo=UTC) if outcome is ReservationOutcome.UNKNOWN else None
            ),
        )
        session.add(previous)
        await session.flush()

        replayed, created = await begin_reservation_attempt_application(
            session,
            watch,
            candidate,
            f"retry-{outcome.value}",
            episode_key=f"{episode_prefix}{previous.id}",
            retry_authorized=True,
            dependencies=dependencies,
        )

        assert replayed is previous
        assert created is False
        assert watch.status is WatchStatus.SEAT_FOUND
        assert candidate.state == "seat_found"


async def test_services_wrapper_assembles_dependencies_from_current_module_globals(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    actionable_statuses = frozenset({SeatObservationStatus.ERROR})
    expected = ReservationAttempt(
        candidate_id="candidate-1",
        attempt_sequence=1,
        episode_key="manual:claim",
        idempotency_key="claim",
        outcome=ReservationOutcome.PENDING,
    )

    async def transition(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("wrapper must only assemble this dependency")

    async def outbox(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("wrapper must only assemble this dependency")

    def payment_hold(_attempt: ReservationAttempt) -> bool:
        return False

    def confirmed_absent(_attempt: ReservationAttempt) -> bool:
        return False

    async def application(*args: object, **kwargs: object) -> tuple[ReservationAttempt, bool]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected, True

    monkeypatch.setattr(services_module, "apply_watch_transition", transition)
    monkeypatch.setattr(services_module, "add_outbox_event", outbox)
    monkeypatch.setattr(services_module, "is_payment_hold_ended", payment_hold)
    monkeypatch.setattr(
        services_module,
        "is_confirmed_absent_retry_source",
        confirmed_absent,
    )
    monkeypatch.setattr(services_module, "ACTIONABLE_SEAT_STATUSES", actionable_statuses)
    monkeypatch.setattr(services_module, "begin_reservation_attempt_application", application)

    watch = make_watch()
    candidate = make_candidate()
    result = await begin_reservation_attempt(
        cast(AsyncSession, object()),
        watch,
        candidate,
        "claim",
        episode_key="availability:claim",
        retry_authorized=True,
        credential_version=3,
    )

    assert result == (expected, True)
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    dependencies = kwargs["dependencies"]
    assert isinstance(dependencies, ReservationAttemptClaimDependencies)
    assert dependencies.apply_watch_transition is transition
    assert dependencies.add_outbox_event is outbox
    assert dependencies.is_payment_hold_ended is payment_hold
    assert dependencies.is_confirmed_absent_retry_source is confirmed_absent
    assert dependencies.actionable_seat_statuses is actionable_statuses
    assert kwargs["episode_key"] == "availability:claim"
    assert kwargs["retry_authorized"] is True
    assert kwargs["credential_version"] == 3


async def test_claim_application_replays_the_race_winner_after_savepoint_conflict() -> None:
    race_winner = ReservationAttempt(
        id="winner-attempt",
        candidate_id="candidate-1",
        attempt_sequence=1,
        episode_key="availability:race",
        idempotency_key="winner-key",
        outcome=ReservationOutcome.PENDING,
    )

    class NestedTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            _error_type: object,
            _error: object,
            _traceback: object,
        ) -> None:
            return None

    class RacingSession:
        def __init__(self) -> None:
            self.scalar_results = iter([None, None, race_winner])
            self.added: list[object] = []
            self.flush_calls = 0

        async def scalar(self, _statement: object) -> object:
            return next(self.scalar_results)

        def begin_nested(self) -> NestedTransaction:
            return NestedTransaction()

        def add(self, value: object) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            self.flush_calls += 1
            raise IntegrityError("INSERT", {}, RuntimeError("episode race"))

    async def fail_transition(
        _session: AsyncSession,
        _watch: Watch,
        _target: WatchStatus,
        _idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        observation: SeatObservation | None = None,
    ) -> Watch:
        del reason, observation
        raise AssertionError("a race replay must not emit side effects")

    async def fail_outbox(
        _session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object:
        del aggregate_type, aggregate_id, event_type, payload, dedupe_key
        raise AssertionError("a race replay must not emit side effects")

    racing_session = RacingSession()
    watch = make_watch()
    candidate = make_candidate()
    candidate.id = "candidate-1"
    replayed, created = await begin_reservation_attempt_application(
        cast(AsyncSession, racing_session),
        watch,
        candidate,
        "loser-key",
        episode_key="availability:race",
        dependencies=ReservationAttemptClaimDependencies(
            apply_watch_transition=fail_transition,
            add_outbox_event=fail_outbox,
            is_payment_hold_ended=lambda _attempt: False,
            is_confirmed_absent_retry_source=lambda _attempt: False,
            actionable_seat_statuses=frozenset(),
        ),
    )

    assert replayed is race_winner
    assert created is False
    assert racing_session.flush_calls == 1
    assert len(racing_session.added) == 1


def test_services_keeps_the_reservation_attempt_claim_compatibility_identity() -> None:
    assert begin_reservation_attempt is services_module.begin_reservation_attempt
