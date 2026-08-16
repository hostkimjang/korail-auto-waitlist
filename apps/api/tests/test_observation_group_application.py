from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from rail_waitlist.domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from rail_waitlist.observations.group_application import (
    ObservationGroupDependencies,
    ObservationTarget,
    _locked_deferred_watches_query,
    apply_current_circuit_to_watch,
    defer_watch_group_observation,
    prepare_watch,
    process_watch_group_observation,
    retryable_reservation_episode_key,
)
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.provider_circuit.models import ProviderCircuit
from rail_waitlist.reservation_confirmation import ReservationConfirmationOutcome
from rail_waitlist.reservations.attempt_policy import (
    is_unresolved_unknown_manual_rearm_source,
    manual_unknown_rearm_episode_key,
)
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
)
from rail_waitlist.schemas import ProviderCapabilities, SeatObservationResult
from rail_waitlist.services import (
    add_outbox_event,
    apply_watch_transition,
    finish_observation_cycle,
    get_or_create_provider_circuit,
    is_confirmed_absent_retry_source,
    is_payment_hold_ended,
    latest_observation_fingerprint,
    record_seat_observation,
)


class RecordingObservationAdapter:
    def __init__(
        self,
        status: SeatObservationStatus,
        *,
        provider: Provider = Provider.MOCK,
        reservation_once: bool = False,
        return_matching_seat_class: bool = True,
    ) -> None:
        self.provider = provider
        self.status = status
        self.reservation_once = reservation_once
        self.return_matching_seat_class = return_matching_seat_class
        self.observe_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=self.reservation_once,
        )

    async def observation_deferred_until(self) -> datetime | None:
        return None

    async def observe_seats(self, request) -> list[SeatObservationResult]:
        self.observe_calls += 1
        observed_at = datetime.now(UTC)
        seat_class = request.seat_class if self.return_matching_seat_class else SeatClass.FIRST
        return [
            SeatObservationResult(
                seat_class=seat_class,
                status=self.status,
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
            )
        ]


def test_deferred_group_uses_deterministic_postgresql_lock_order() -> None:
    query = _locked_deferred_watches_query(
        ["watch-b", "watch-a"],
        datetime(2026, 8, 5, tzinfo=UTC),
    )
    sql = str(query.compile(dialect=postgresql.dialect()))

    assert "ORDER BY watches.id" in sql
    assert sql.endswith("FOR UPDATE")


def test_prepared_deferred_group_locks_only_rows_with_an_observation_claim() -> None:
    query = _locked_deferred_watches_query(["watch-b", "watch-a"], None)
    sql = str(query.compile(dialect=postgresql.dialect()))

    assert "watches.observation_in_flight_until IS NOT NULL" in sql
    assert "AND watches.next_check_at" not in sql
    assert "ORDER BY watches.id" in sql


def dependencies(session_factory, reserved: list[ObservationTarget], *, locked=True):
    async def lease_is_current(_grant: object, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        return True

    async def lease_is_current_in_session(_session, _grant, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        return locked

    async def reserve_winner(target: ObservationTarget) -> None:
        reserved.append(target)

    return ObservationGroupDependencies(
        session_factory=session_factory,
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        latest_observation_fingerprint=latest_observation_fingerprint,
        record_seat_observation=record_seat_observation,
        finish_observation_cycle=finish_observation_cycle,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
        is_payment_hold_ended=is_payment_hold_ended,
        reserve_winner=reserve_winner,
        lease_is_current=lease_is_current,
        lease_is_current_in_session=lease_is_current_in_session,
        provider_call_errors=(RuntimeError, ValueError),
    )


async def persist_due_watch(
    session_factory,
    *,
    dedupe_key: str,
    now: datetime | None = None,
    provider: Provider = Provider.MOCK,
    reservation_policy: ReservationPolicy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
) -> str:
    now = now or datetime.now(UTC)
    async with session_factory() as session:
        watch = Watch(
            provider=provider,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="부산",
            destination_node_id="MOCK-BUSAN",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=reservation_policy,
            status=WatchStatus.SCHEDULED,
            dedupe_key=dedupe_key,
            next_check_at=now - timedelta(seconds=1),
        )
        watch.candidates.append(
            WatchCandidate(
                train_number="MOCK-001",
                departure_at=now + timedelta(days=1),
                arrival_at=now + timedelta(days=1, hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="active",
            )
        )
        session.add(watch)
        await session.commit()
        return watch.id


async def test_group_deduplicates_provider_request_and_persists_each_watch(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    watch_ids = [
        await persist_due_watch(
            session_factory,
            dedupe_key=f"group-dedupe-{index}",
            now=now,
        )
        for index in range(2)
    ]
    adapter = RecordingObservationAdapter(SeatObservationStatus.SOLD_OUT)
    reserved: list[ObservationTarget] = []

    await process_watch_group_observation(
        watch_ids,
        datetime.now(UTC),
        provider=Provider.MOCK,
        adapter=adapter,
        lease_grant=None,
        dependencies=dependencies(session_factory, reserved),
    )

    assert adapter.observe_calls == 1
    assert reserved == []
    async with session_factory() as session:
        watches = list((await session.scalars(select(Watch))).all())
        observation_count = await session.scalar(select(func.count()).select_from(SeatObservation))
        assert {watch.status for watch in watches} == {WatchStatus.WATCHING}
        assert all(watch.observation_in_flight_until is None for watch in watches)
        assert observation_count == 2


@pytest.mark.parametrize(
    "outcome",
    [ReservationOutcome.AUTH_REQUIRED, ReservationOutcome.PROVIDER_BLOCKED],
)
async def test_group_never_opens_auth_episode_after_reservation_was_requested(
    app,
    outcome: ReservationOutcome,
) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    async with session_factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="N-SUSEO",
            destination="부산",
            destination_node_id="N-BUSAN",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"post-request-auth-group-{outcome.value}",
        )
        candidate = WatchCandidate(
            train_number="SRT-301",
            departure_at=now + timedelta(days=1),
            scheduled_departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key=f"post-request-auth-{outcome.value}",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            outcome=outcome,
            progress_stages=[
                {
                    "stage": "reservation_requested",
                    "occurred_at": (now - timedelta(minutes=1, seconds=30)).isoformat(),
                }
            ],
        )
        current = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="srtrain-2.6.7-accountless",
            observed_at=now,
            fresh_until=now + timedelta(minutes=1),
        )
        session.add_all(
            [
                watch,
                attempt,
                current,
                RailProviderAccount(
                    provider=Provider.SRT,
                    credentials_ciphertext="test-ciphertext",
                    credential_version=3,
                    last_auth_status="authenticated",
                    last_authenticated_at=now,
                ),
            ]
        )
        await session.flush()

        episode = await retryable_reservation_episode_key(
            session,
            candidate,
            current,
            Provider.SRT,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )

        assert episode is None


async def test_group_uses_unique_older_unresolved_source_and_fences_other_candidate(
    app,
) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    async with session_factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="N-SUSEO",
            destination="부산",
            destination_node_id="N-BUSAN",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="watch-global-unknown-group-fence",
        )
        source = WatchCandidate(
            train_number="SRT-301",
            departure_at=now + timedelta(days=1),
            scheduled_departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        other = WatchCandidate(
            train_number="SRT-303",
            departure_at=now + timedelta(days=1, minutes=10),
            scheduled_departure_at=now + timedelta(days=1, minutes=10),
            seat_class=SeatClass.STANDARD,
            priority=2,
            state="seat_found",
        )
        watch.candidates.extend([source, other])
        confirmed_at = now - timedelta(minutes=1)
        unknown = ReservationAttempt(
            candidate=source,
            attempt_sequence=1,
            episode_key="availability:source",
            idempotency_key="watch-global-unknown-source",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1, seconds=30),
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=3,
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
            started_at=now - timedelta(seconds=30),
            finished_at=now - timedelta(seconds=20),
            outcome=ReservationOutcome.NOT_AVAILABLE,
        )
        source_available = SeatObservation(
            candidate=source,
            status=SeatObservationStatus.AVAILABLE,
            source="srtrain-2.6.7-accountless",
            observed_at=now,
            fresh_until=now + timedelta(minutes=1),
        )
        other_available = SeatObservation(
            candidate=other,
            status=SeatObservationStatus.AVAILABLE,
            source="srtrain-2.6.7-accountless",
            observed_at=now,
            fresh_until=now + timedelta(minutes=1),
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
                    credential_version=3,
                    last_auth_status="authenticated",
                    last_authenticated_at=now - timedelta(seconds=10),
                ),
            ]
        )
        await session.flush()
        source.manual_rearm_source_attempt_id = unknown.id
        source.manual_rearm_authorized_at = now - timedelta(seconds=5)

        other_episode = await retryable_reservation_episode_key(
            session,
            other,
            other_available,
            Provider.SRT,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )
        source_episode = await retryable_reservation_episode_key(
            session,
            source,
            source_available,
            Provider.SRT,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )

        assert other_episode is None
        assert source_episode == manual_unknown_rearm_episode_key(
            unknown.id,
            source.id,
            source_available.id,
        )


@pytest.mark.parametrize(
    ("outcome", "confirmation", "resolution", "expected_allowed"),
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
async def test_group_treats_confirmed_absence_as_resolved_but_exact_paid_as_absolute(
    app,
    outcome: ReservationOutcome,
    confirmation: ReservationConfirmationOutcome,
    resolution: ReservationReconciliationResolution | None,
    expected_allowed: bool,
) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    async with session_factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            destination="부산",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"resolved-vs-paid-group-{outcome.value}",
        )
        source = WatchCandidate(
            train_number="SRT-301",
            departure_at=now + timedelta(days=1),
            scheduled_departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        other = WatchCandidate(
            train_number="SRT-303",
            departure_at=now + timedelta(days=1, minutes=10),
            scheduled_departure_at=now + timedelta(days=1, minutes=10),
            seat_class=SeatClass.STANDARD,
            priority=2,
            state="seat_found",
        )
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

        episode = await retryable_reservation_episode_key(
            session,
            other,
            available,
            Provider.SRT,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )

        assert episode == (f"availability:{available.id}" if expected_allowed else None)


async def test_prepare_uses_an_expiring_claim_without_rewriting_the_due_schedule(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="separate-observation-claim",
        now=now,
    )
    adapter = RecordingObservationAdapter(SeatObservationStatus.SOLD_OUT)
    group_dependencies = dependencies(session_factory, [])

    targets = await prepare_watch(
        watch_id,
        now,
        adapter=adapter,
        lease_grant=None,
        dependencies=group_dependencies,
    )
    duplicate_targets = await prepare_watch(
        watch_id,
        now + timedelta(seconds=1),
        adapter=adapter,
        lease_grant=None,
        dependencies=group_dependencies,
    )

    assert len(targets) == 1
    assert duplicate_targets == []
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.next_check_at is not None
        assert watch.next_check_at.replace(tzinfo=UTC) == now - timedelta(seconds=1)
        assert watch.observation_in_flight_until is not None
        assert watch.observation_in_flight_until.replace(tzinfo=UTC) == now + timedelta(minutes=1)


async def test_prepare_preserves_the_deferred_operational_retry_fallback(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    scheduled_departure = now - timedelta(minutes=1)
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="deferred-operational-retry",
        now=now,
    )
    async with session_factory() as session:
        candidate = await session.scalar(
            select(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        assert candidate is not None
        candidate.departure_at = scheduled_departure
        candidate.scheduled_departure_at = scheduled_departure
        await session.commit()

    targets = await prepare_watch(
        watch_id,
        now,
        adapter=RecordingObservationAdapter(SeatObservationStatus.SOLD_OUT),
        lease_grant=None,
        dependencies=dependencies(session_factory, []),
    )

    assert targets
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.next_check_at.replace(tzinfo=UTC) == scheduled_departure + timedelta(
            minutes=15
        )
        assert watch.observation_in_flight_until is not None
        assert watch.observation_in_flight_until.replace(tzinfo=UTC) == now + timedelta(minutes=1)


async def test_prepared_cooldown_replaces_schedule_and_clears_claim(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    deferred_until = now + timedelta(minutes=5)
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="prepared-cooldown-clears-claim",
        now=now,
    )
    group_dependencies = dependencies(session_factory, [])
    targets = await prepare_watch(
        watch_id,
        now,
        adapter=RecordingObservationAdapter(SeatObservationStatus.ERROR),
        lease_grant=None,
        dependencies=group_dependencies,
    )
    assert targets

    await defer_watch_group_observation(
        [watch_id],
        deferred_until,
        now,
        lease_grant=None,
        prepared=True,
        dependencies=group_dependencies,
    )

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.next_check_at.replace(tzinfo=UTC) == deferred_until
        assert watch.cooldown_until.replace(tzinfo=UTC) == deferred_until
        assert watch.observation_in_flight_until is None


async def test_current_circuit_transition_clears_prepared_claim(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    cooldown_until = now + timedelta(minutes=5)
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="circuit-clears-observation-claim",
        now=now,
    )
    group_dependencies = dependencies(session_factory, [])
    targets = await prepare_watch(
        watch_id,
        now,
        adapter=RecordingObservationAdapter(SeatObservationStatus.SOLD_OUT),
        lease_grant=None,
        dependencies=group_dependencies,
    )
    assert targets
    async with session_factory() as session:
        circuit = await session.scalar(
            select(ProviderCircuit).where(ProviderCircuit.provider == Provider.MOCK)
        )
        assert circuit is not None
        circuit.state = ProviderCircuitState.OPEN
        circuit.opened_at = now
        circuit.cooldown_until = cooldown_until
        circuit.manual_resume_required = False
        await session.commit()

    await apply_current_circuit_to_watch(
        watch_id,
        lease_grant=None,
        dependencies=group_dependencies,
    )

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.status is WatchStatus.COOLDOWN
        assert watch.next_check_at.replace(tzinfo=UTC) == cooldown_until
        assert watch.observation_in_flight_until is None


@pytest.mark.parametrize("provider", [Provider.KORAIL, Provider.SRT])
async def test_blocked_provider_account_stops_notify_only_observation_without_repeat_io_or_writes(
    app,
    provider: Provider,
) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=provider,
                credentials_ciphertext="test-ciphertext",
                last_auth_status="provider_blocked",
            )
        )
        await session.commit()

    watch_ids = [
        await persist_due_watch(
            session_factory,
            dedupe_key="blocked-account-group",
            now=now,
            provider=provider,
            reservation_policy=ReservationPolicy.NOTIFY_ONLY,
        )
        for _ in range(2)
    ]
    adapter = RecordingObservationAdapter(SeatObservationStatus.AVAILABLE, provider=provider)
    group_dependencies = dependencies(session_factory, [])

    await process_watch_group_observation(
        watch_ids,
        now,
        provider=provider,
        adapter=adapter,
        lease_grant=None,
        dependencies=group_dependencies,
    )

    assert adapter.observe_calls == 0
    async with session_factory() as session:
        watches = list((await session.scalars(select(Watch).where(Watch.id.in_(watch_ids)))).all())
        assert len(watches) == 2
        assert {watch.status for watch in watches} == {WatchStatus.AUTH_REQUIRED}
        assert all(watch.next_check_at is None for watch in watches)
        assert all(watch.cooldown_until is None for watch in watches)
        observations_before_retry = await session.scalar(
            select(func.count()).select_from(SeatObservation)
        )

    await process_watch_group_observation(
        watch_ids,
        now + timedelta(minutes=5),
        provider=provider,
        adapter=adapter,
        lease_grant=None,
        dependencies=group_dependencies,
    )

    assert adapter.observe_calls == 0
    async with session_factory() as session:
        observations_after_retry = await session.scalar(
            select(func.count()).select_from(SeatObservation)
        )
        assert observations_after_retry == observations_before_retry == 0


async def test_auth_required_account_does_not_block_public_seat_observation(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext="test-ciphertext",
                last_auth_status="auth_required",
            )
        )
        await session.commit()
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="auth-required-public-observation",
        now=now,
        provider=Provider.KORAIL,
        reservation_policy=ReservationPolicy.NOTIFY_ONLY,
    )
    adapter = RecordingObservationAdapter(SeatObservationStatus.SOLD_OUT, provider=Provider.KORAIL)

    await process_watch_group_observation(
        [watch_id],
        now,
        provider=Provider.KORAIL,
        adapter=adapter,
        lease_grant=None,
        dependencies=dependencies(session_factory, []),
    )

    assert adapter.observe_calls == 1
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.status is WatchStatus.WATCHING


async def test_group_passes_the_exact_actionable_observation_to_the_status_transition(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="group-notification-observation",
        now=now,
    )
    adapter = RecordingObservationAdapter(SeatObservationStatus.AVAILABLE)
    reserved: list[ObservationTarget] = []
    transition_observations: list[SeatObservation | None] = []
    base_dependencies = dependencies(session_factory, reserved)

    async def record_transition(
        session,
        watch,
        target,
        *,
        reason=None,
        observation=None,
    ):
        if target is WatchStatus.SEAT_FOUND:
            transition_observations.append(observation)
        return await apply_watch_transition(
            session,
            watch,
            target,
            reason=reason,
            observation=observation,
        )

    await process_watch_group_observation(
        [watch_id],
        now,
        provider=Provider.MOCK,
        adapter=adapter,
        lease_grant=None,
        dependencies=replace(
            base_dependencies,
            apply_watch_transition=record_transition,
        ),
    )

    assert len(transition_observations) == 1
    assert transition_observations[0] is not None
    async with session_factory() as session:
        candidate_id = await session.scalar(
            select(WatchCandidate.id).where(WatchCandidate.watch_id == watch_id)
        )
    assert transition_observations[0].candidate_id == candidate_id


async def test_standing_only_reopens_seated_ticket_monitoring_without_reservation(app) -> None:
    session_factory = app.state.test_session_factory
    now = datetime.now(UTC)
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="standing-only-reopens-watch",
        now=now,
    )
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        assert watch is not None and candidate is not None
        watch.status = WatchStatus.SEAT_FOUND
        candidate.state = "seat_found"
        await session.commit()

    reserved: list[ObservationTarget] = []
    await process_watch_group_observation(
        [watch_id],
        now,
        provider=Provider.MOCK,
        adapter=RecordingObservationAdapter(
            SeatObservationStatus.STANDING_ONLY,
            reservation_once=True,
        ),
        lease_grant=None,
        dependencies=dependencies(session_factory, reserved),
    )

    assert reserved == []
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        observation = await session.scalar(select(SeatObservation))
        assert watch is not None and watch.status is WatchStatus.WATCHING
        assert candidate is not None and candidate.state == "observed"
        assert observation is not None
        assert observation.status is SeatObservationStatus.STANDING_ONLY


async def test_missing_matching_seat_class_is_persisted_as_fail_closed_error(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id = await persist_due_watch(session_factory, dedupe_key="group-no-match")
    adapter = RecordingObservationAdapter(
        SeatObservationStatus.AVAILABLE,
        return_matching_seat_class=False,
    )

    await process_watch_group_observation(
        [watch_id],
        datetime.now(UTC),
        provider=Provider.MOCK,
        adapter=adapter,
        lease_grant=None,
        dependencies=dependencies(session_factory, []),
    )

    async with session_factory() as session:
        observation = await session.scalar(select(SeatObservation))
        assert observation is not None
        assert observation.status is SeatObservationStatus.ERROR
        assert observation.error_category == "provider_unavailable"


async def test_actionable_result_delegates_one_episode_bound_winner(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id = await persist_due_watch(session_factory, dedupe_key="group-reservation-winner")
    adapter = RecordingObservationAdapter(
        SeatObservationStatus.AVAILABLE,
        reservation_once=True,
    )
    reserved: list[ObservationTarget] = []

    await process_watch_group_observation(
        [watch_id],
        datetime.now(UTC),
        provider=Provider.MOCK,
        adapter=adapter,
        lease_grant=None,
        dependencies=dependencies(session_factory, reserved),
    )

    assert len(reserved) == 1
    assert reserved[0].watch_id == watch_id
    assert reserved[0].reservation_episode_key is not None
    assert reserved[0].reservation_episode_key.startswith("availability:")


async def test_cancel_during_acquired_observation_fences_automatic_reservation(
    app,
    client,
) -> None:
    session_factory = app.state.test_session_factory
    watch_id = await persist_due_watch(
        session_factory,
        dedupe_key="cancel-during-acquired-observation",
    )

    class CancelDuringObservationAdapter(RecordingObservationAdapter):
        async def observe_seats(self, request) -> list[SeatObservationResult]:
            cancelled = await client.post(f"/api/v1/watches/{watch_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "expired"
            return await super().observe_seats(request)

    adapter = CancelDuringObservationAdapter(
        SeatObservationStatus.AVAILABLE,
        reservation_once=True,
    )
    reserved: list[ObservationTarget] = []

    await process_watch_group_observation(
        [watch_id],
        datetime.now(UTC),
        provider=Provider.MOCK,
        adapter=adapter,
        lease_grant=None,
        dependencies=dependencies(session_factory, reserved),
    )

    assert adapter.observe_calls == 1
    assert reserved == []
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.status is WatchStatus.EXPIRED
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0


async def test_lost_locked_lease_fences_prepare_before_watch_mutation(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id = await persist_due_watch(session_factory, dedupe_key="group-locked-fence")
    adapter = RecordingObservationAdapter(SeatObservationStatus.AVAILABLE)

    await process_watch_group_observation(
        [watch_id],
        datetime.now(UTC),
        provider=Provider.MOCK,
        adapter=adapter,
        lease_grant=object(),
        dependencies=dependencies(session_factory, [], locked=False),
    )

    assert adapter.observe_calls == 0
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.status is WatchStatus.SCHEDULED
        assert watch.next_check_at is not None
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0


async def test_persistence_failure_rolls_back_observation_and_summary_atomically(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id = await persist_due_watch(session_factory, dedupe_key="group-persist-rollback")
    adapter = RecordingObservationAdapter(SeatObservationStatus.SOLD_OUT)
    base_dependencies = dependencies(session_factory, [])

    async def fail_finish_cycle(*_args) -> None:
        raise RuntimeError("synthetic persistence failure")

    with pytest.raises(RuntimeError, match="synthetic persistence failure"):
        await process_watch_group_observation(
            [watch_id],
            datetime.now(UTC),
            provider=Provider.MOCK,
            adapter=adapter,
            lease_grant=None,
            dependencies=replace(
                base_dependencies,
                finish_observation_cycle=fail_finish_cycle,
            ),
        )

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        assert watch is not None and watch.status is WatchStatus.WATCHING
        assert watch.next_check_at is not None
        assert watch.next_check_at.replace(tzinfo=UTC) < datetime.now(UTC)
        assert watch.observation_in_flight_until is not None
        assert watch.observation_in_flight_until.replace(tzinfo=UTC) > datetime.now(UTC)
        assert candidate is not None and candidate.state == "active"
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0
