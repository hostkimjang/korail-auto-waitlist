from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import rail_waitlist.services as services_module
from rail_waitlist.domain import Provider, ReservationOutcome, SeatObservationStatus, WatchStatus
from rail_waitlist.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.attempt_claim_application import (
    ReservationAttemptClaimDependencies,
)
from rail_waitlist.reservations.attempt_claim_application import (
    begin_reservation_attempt as begin_reservation_attempt_application,
)
from rail_waitlist.reservations.attempt_policy import manual_unknown_rearm_episode_key
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
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


@pytest.mark.parametrize(
    "retry_edge_status",
    [SeatObservationStatus.SOLD_OUT, SeatObservationStatus.STANDING_ONLY],
)
async def test_claim_revalidates_watch_scoped_hold_for_candidate_without_attempt(
    db_engine,
    retry_edge_status: SeatObservationStatus,
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
            status=retry_edge_status,
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


async def test_claim_accepts_standing_only_as_not_available_retry_edge(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
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
            idempotency_key="not-available-before-standing-only",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            outcome=ReservationOutcome.NOT_AVAILABLE,
        )
        retry_edge = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.STANDING_ONLY,
            source="authorized-provider",
            observed_at=now - timedelta(seconds=30),
            fresh_until=now + timedelta(seconds=30),
        )
        rediscovered = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=now - timedelta(seconds=20),
            fresh_until=now + timedelta(seconds=40),
        )
        session.add_all([previous, retry_edge, rediscovered])
        await session.flush()
        episode_key = f"availability-after:{retry_edge.id}"

        retry, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "not-available-after-standing-only",
            episode_key=episode_key,
            retry_authorized=True,
        )

        assert created is True
        assert retry.attempt_sequence == 2
        assert retry.episode_key == episode_key
        assert watch.status is WatchStatus.RESERVING
        assert candidate.state == "reservation_attempted"


@pytest.mark.parametrize(
    (
        "case",
        "outcome",
        "requested_version",
        "authentication_offset_seconds",
        "post_request",
        "expected_created",
    ),
    [
        ("exact", ReservationOutcome.AUTH_REQUIRED, 7, 60, False, True),
        ("arbitrary-episode", ReservationOutcome.AUTH_REQUIRED, 7, 60, False, False),
        ("wrong-version", ReservationOutcome.AUTH_REQUIRED, 8, 60, False, False),
        ("stale-authentication", ReservationOutcome.AUTH_REQUIRED, 7, -1, False, False),
        ("post-request-block", ReservationOutcome.PROVIDER_BLOCKED, 7, 60, True, False),
    ],
)
async def test_claim_requires_exact_current_pre_dispatch_auth_episode(
    db_engine,
    case: str,
    outcome: ReservationOutcome,
    requested_version: int,
    authentication_offset_seconds: int,
    post_request: bool,
    expected_created: bool,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    finished_at = now - timedelta(minutes=2)
    authenticated_at = finished_at + timedelta(seconds=authentication_offset_seconds)
    async with factory() as session:
        watch = make_watch()
        candidate = make_candidate()
        watch.candidates.append(candidate)
        previous = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key=f"auth-retry-source:{case}",
            started_at=finished_at - timedelta(minutes=1),
            finished_at=finished_at,
            outcome=outcome,
            progress_stages=(
                [
                    {
                        "stage": "reservation_requested",
                        "occurred_at": (finished_at - timedelta(seconds=1)).isoformat(),
                    }
                ]
                if post_request
                else []
            ),
        )
        observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="srtrain-2.6.7-accountless",
            observed_at=now,
            fresh_until=now + timedelta(minutes=1),
        )
        account = RailProviderAccount(
            provider=Provider.SRT,
            credentials_ciphertext="test-ciphertext",
            credential_version=7,
            last_auth_status="authenticated",
            last_authenticated_at=authenticated_at,
        )
        session.add_all([watch, previous, observation, account])
        await session.flush()
        generation = int(authenticated_at.timestamp() * 1_000_000)
        episode_key = "auth:7:123" if case == "arbitrary-episode" else f"auth:7:{generation}"

        attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            f"auth-retry-claim:{case}",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=requested_version,
        )

        assert created is expected_created
        if expected_created:
            assert attempt.attempt_sequence == 2
            assert attempt.episode_key == episode_key
        else:
            assert attempt is previous
            assert candidate.state == "seat_found"


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


async def test_claim_revalidates_reconciled_unknown_retry_and_consumes_it_once(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(UTC)
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
            idempotency_key="confirmed-absent-unknown:first",
            outcome=ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=confirmed_at,
            last_reconciled_at=confirmed_at,
            reconciliation_attempt_count=1,
            reconciliation_resolution=(ReservationReconciliationResolution.CONFIRMED_ABSENT),
        )
        non_official = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=confirmed_at + timedelta(seconds=1),
            fresh_until=confirmed_at + timedelta(minutes=1),
        )
        session.add_all([previous, non_official])
        await session.flush()
        episode_key = f"confirmed-absent-retry:{previous.id}"

        blocked, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "confirmed-absent-unknown:blocked",
            episode_key=episode_key,
            retry_authorized=True,
        )
        assert blocked is previous
        assert created is False

        session.add(
            SeatObservation(
                candidate=candidate,
                status=SeatObservationStatus.LIMITED,
                source="srtrain-2.6.7-accountless",
                observed_at=confirmed_at + timedelta(seconds=2),
                fresh_until=confirmed_at + timedelta(minutes=1),
            )
        )
        await session.flush()

        retry, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "confirmed-absent-unknown:retry",
            episode_key=episode_key,
            retry_authorized=True,
        )
        replayed, replay_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "confirmed-absent-unknown:replay",
            episode_key=episode_key,
            retry_authorized=True,
        )

        assert created is True
        assert retry.attempt_sequence == 2
        assert retry.episode_key == episode_key
        assert replayed is retry
        assert replay_created is False


async def test_claim_uses_unique_older_unresolved_source_and_fences_other_candidate_once(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(UTC) - timedelta(minutes=1)
    async with factory() as session:
        watch = make_watch()
        source = make_candidate()
        other = make_candidate()
        other.train_number = "SRT-303"
        other.departure_at += timedelta(minutes=10)
        other.scheduled_departure_at = other.departure_at
        other.priority = 2
        watch.candidates.extend([source, other])
        unknown = ReservationAttempt(
            candidate=source,
            attempt_sequence=1,
            episode_key="availability:source",
            idempotency_key="watch-global-unknown-source",
            started_at=confirmed_at - timedelta(minutes=1),
            finished_at=confirmed_at - timedelta(seconds=30),
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=7,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=confirmed_at,
            last_reconciled_at=confirmed_at,
            reconciliation_attempt_count=3,
            reconciliation_resolution=None,
        )
        later_safe_attempt = ReservationAttempt(
            candidate=other,
            attempt_sequence=1,
            episode_key="availability:later-safe",
            idempotency_key="watch-global-later-safe",
            started_at=confirmed_at + timedelta(seconds=1),
            finished_at=confirmed_at + timedelta(seconds=2),
            outcome=ReservationOutcome.NOT_AVAILABLE,
        )
        source_available = SeatObservation(
            candidate=source,
            status=SeatObservationStatus.LIMITED,
            source="srtrain-2.6.7-accountless",
            observed_at=confirmed_at + timedelta(seconds=10),
            fresh_until=confirmed_at + timedelta(minutes=1),
        )
        other_available = SeatObservation(
            candidate=other,
            status=SeatObservationStatus.AVAILABLE,
            source="srtrain-2.6.7-accountless",
            observed_at=confirmed_at + timedelta(seconds=10),
            fresh_until=confirmed_at + timedelta(minutes=1),
        )
        session.add_all(
            [
                watch,
                unknown,
                later_safe_attempt,
                source_available,
                other_available,
                RailProviderAccount(
                    provider=Provider.SRT,
                    credentials_ciphertext="test-ciphertext",
                    credential_version=7,
                    last_auth_status="authenticated",
                    last_authenticated_at=confirmed_at + timedelta(seconds=3),
                ),
            ]
        )
        await session.flush()
        source.manual_rearm_source_attempt_id = unknown.id
        source.manual_rearm_authorized_at = confirmed_at + timedelta(seconds=5)

        blocked, blocked_created = await begin_reservation_attempt(
            session,
            watch,
            other,
            "watch-global-unknown-other",
            episode_key=f"availability:{other_available.id}",
            retry_authorized=True,
        )
        episode_key = manual_unknown_rearm_episode_key(
            unknown.id,
            source.id,
            source_available.id,
        )
        recovered, recovered_created = await begin_reservation_attempt(
            session,
            watch,
            source,
            "watch-global-unknown-source-recovery",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=7,
        )
        replayed, replay_created = await begin_reservation_attempt(
            session,
            watch,
            source,
            "watch-global-unknown-source-replay",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=7,
        )

        assert blocked is unknown
        assert blocked_created is False
        assert recovered_created is True
        assert recovered.candidate_id == source.id
        assert recovered.episode_key == episode_key
        assert replayed is recovered
        assert replay_created is False


@pytest.mark.parametrize(
    ("outcome", "confirmation", "resolution", "expected_created"),
    [
        (
            ReservationOutcome.UNKNOWN,
            ReservationConfirmationOutcome.NOT_FOUND,
            ReservationReconciliationResolution.CONFIRMED_ABSENT,
            True,
        ),
        (
            ReservationOutcome.RESERVED,
            ReservationConfirmationOutcome.CONFIRMED_PAID,
            None,
            False,
        ),
    ],
)
async def test_claim_allows_other_candidate_after_absence_but_never_after_exact_paid(
    db_engine,
    outcome: ReservationOutcome,
    confirmation: ReservationConfirmationOutcome,
    resolution: ReservationReconciliationResolution | None,
    expected_created: bool,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch()
        source = make_candidate()
        other = make_candidate()
        other.train_number = "SRT-303"
        other.departure_at += timedelta(minutes=10)
        other.scheduled_departure_at = other.departure_at
        other.priority = 2
        watch.candidates.extend([source, other])
        previous = ReservationAttempt(
            candidate=source,
            attempt_sequence=1,
            episode_key="availability:source",
            idempotency_key=f"resolved-vs-paid-source-{outcome.value}",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            outcome=outcome,
            confirmation_outcome=confirmation,
            confirmation_source="official-reservation-list",
            confirmation_observed_at=now - timedelta(minutes=1),
            last_reconciled_at=now - timedelta(minutes=1),
            reconciliation_attempt_count=1,
            reconciliation_resolution=resolution,
        )
        available = SeatObservation(
            candidate=other,
            status=SeatObservationStatus.AVAILABLE,
            source="srtrain-2.6.7-accountless",
            observed_at=now,
            fresh_until=now + timedelta(minutes=1),
        )
        session.add_all([watch, previous, available])
        await session.flush()

        claimed, created = await begin_reservation_attempt(
            session,
            watch,
            other,
            f"resolved-vs-paid-claim-{outcome.value}",
            episode_key=f"availability:{available.id}",
            retry_authorized=True,
        )

        assert created is expected_created
        if expected_created:
            assert claimed.candidate_id == other.id
            assert claimed.outcome is ReservationOutcome.PENDING
        else:
            assert claimed.id == previous.id
            assert watch.status is WatchStatus.SEAT_FOUND


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

        async def scalars(self, _statement: object) -> object:
            class EmptyRows:
                def all(self) -> list[object]:
                    return []

            return EmptyRows()

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
