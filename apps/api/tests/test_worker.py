from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import (
    NotificationKind,
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    NotificationChannel,
    OutboxEvent,
    ProviderCircuit,
    ProviderExecutionLease,
    RailProviderAccount,
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.observations.group_application import (
    ObservationGroupDependencies,
    ObservationTarget,
    defer_watch_group_observation,
    retryable_reservation_episode_key,
)
from rail_waitlist.provider_execution_lease import (
    ExecutionLeaseGrant,
    lock_execution_lease_current,
)
from rail_waitlist.providers import MockProviderAdapter
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.reconciliation_application import (
    _reservation_reconciliation_is_due,
)
from rail_waitlist.reservations.reconciliation_application import (
    reconcile_reservation_attempt as run_reservation_reconciliation,
)
from rail_waitlist.reservations.stale_attempt_recovery_application import (
    build_stale_reservation_attempts_query,
)
from rail_waitlist.schemas import (
    ProviderCapabilities,
    ReservationResult,
    SeatObservationResult,
)
from rail_waitlist.security import secret_box
from rail_waitlist.services import (
    add_outbox_event,
    apply_watch_transition,
    begin_reservation_attempt,
    finish_observation_cycle,
    get_or_create_provider_circuit,
    is_confirmed_absent_retry_source,
    is_payment_hold_ended,
    latest_observation_fingerprint,
    record_seat_observation,
    resume_watches_after_verified_provider_login,
)
from rail_waitlist.timetable_management.models import TimetableSeatEvidence
from rail_waitlist.watch_management import transition_runtime as transition_runtime_module
from rail_waitlist.worker import (
    _acquire_execution_lease,
    _arm_supported_provider_watches,
    _process_due_watches,
    _process_watch_group,
    _process_watch_now,
    _reserve_winner,
)


async def _retryable_reservation_episode_key(
    session,
    candidate: WatchCandidate,
    observation: SeatObservation,
    provider: Provider,
) -> str | None:
    return await retryable_reservation_episode_key(
        session,
        candidate,
        observation,
        provider,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        is_payment_hold_ended=is_payment_hold_ended,
    )


def _observation_dependencies(session_factory) -> ObservationGroupDependencies:
    async def lease_is_current(_grant: object, *, now: datetime) -> bool:
        return True

    async def reserve_winner(_target: ObservationTarget) -> None:
        return None

    return ObservationGroupDependencies(
        session_factory=session_factory,
        apply_watch_transition=apply_watch_transition,
        add_outbox_event=add_outbox_event,
        get_or_create_provider_circuit=get_or_create_provider_circuit,
        latest_observation_fingerprint=latest_observation_fingerprint,
        record_seat_observation=record_seat_observation,
        finish_observation_cycle=finish_observation_cycle,
        is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
        is_payment_hold_ended=is_payment_hold_ended,
        reserve_winner=reserve_winner,
        lease_is_current=lease_is_current,
        lease_is_current_in_session=lock_execution_lease_current,
        provider_call_errors=(RuntimeError, ValueError),
    )


async def test_worker_execution_lease_covers_adapter_timeout(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)

    service, grant = await _acquire_execution_lease(Provider.SRT, now)

    assert grant is not None
    assert grant.expires_at == now + timedelta(seconds=120)
    assert await service.release(grant, now=now + timedelta(seconds=1)) is True


class ReadOnlyReconciliationAdapter:
    provider = Provider.SRT

    def __init__(self, result: ReservationConfirmationResult) -> None:
        self.result = result
        self.targets = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=True,
        )

    async def confirm_reservation(self, target):
        self.targets.append(target)
        return self.result

    async def drain_pending_calls(self) -> None:
        return None


class CurrentReconciliationLeaseService:
    def __init__(self) -> None:
        self.released = 0

    async def is_current(self, _grant, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        return True

    async def release(self, _grant, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        self.released += 1
        return True


async def _run_reconciliation_application(
    attempt_id: str,
    *,
    adapter: ReadOnlyReconciliationAdapter,
) -> int:
    async def lease_is_current_in_session(_session, _grant, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        return True

    return await run_reservation_reconciliation(
        attempt_id,
        dependencies=replace(
            worker_module._reconciliation_dependencies(),
            lease_is_current_in_session=lease_is_current_in_session,
        ),
        adapter=adapter,
    )


async def test_worker_reconciliation_delegate_wires_runtime_dependencies(monkeypatch) -> None:
    adapter = object()
    captured = {}

    async def fake_reconciliation(attempt_id, *, dependencies, adapter):
        captured.update(
            attempt_id=attempt_id,
            dependencies=dependencies,
            adapter=adapter,
        )
        return 7

    monkeypatch.setattr(worker_module, "run_reservation_reconciliation", fake_reconciliation)

    assert await worker_module._reconcile_reservation_attempt("attempt-7", adapter=adapter) == 7
    dependencies = captured["dependencies"]
    assert captured["attempt_id"] == "attempt-7"
    assert captured["adapter"] is adapter
    assert dependencies.session_factory is worker_module.SessionFactory
    assert dependencies.acquire_execution_lease is worker_module._acquire_execution_lease
    assert dependencies.get_execution_provider is worker_module.get_execution_provider
    assert dependencies.drain_execution_adapter is worker_module._drain_execution_adapter
    assert dependencies.close_execution_adapter is worker_module._close_execution_adapter
    assert dependencies.provider_circuit_is_closed is worker_module._provider_circuit_is_closed
    assert dependencies.lease_is_current_in_session is lock_execution_lease_current
    assert dependencies.state_dependencies is None
    assert dependencies.apply_reconciliation is worker_module._apply_reservation_reconciliation

    state_dependencies = worker_module._reconciliation_state_dependencies()
    assert state_dependencies.apply_watch_transition is worker_module.apply_watch_transition
    assert state_dependencies.add_outbox_event is worker_module.add_outbox_event
    assert (
        state_dependencies.record_reservation_confirmation
        is worker_module.record_reservation_confirmation
    )
    assert state_dependencies.utc_instant is worker_module._utc_instant


def test_reconciliation_celery_task_preserves_name_route_and_delegate(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_reconciliation(attempt_id: str) -> int:
        calls.append(attempt_id)
        return 9

    monkeypatch.setattr(worker_module, "_reconcile_reservation_attempt", fake_reconciliation)

    assert worker_module.reconcile_reservation_attempt.name == (
        "rail_waitlist.worker.reconcile_reservation_attempt"
    )
    assert worker_module.reconcile_reservation_attempt.run("attempt-9") == 9
    assert calls == ["attempt-9"]
    assert worker_module.celery_app.conf.task_routes[
        "rail_waitlist.worker.reconcile_reservation_attempt"
    ] == {"queue": "rail"}


async def test_process_watch_now_uses_normal_single_watch_group_path(monkeypatch) -> None:
    calls: list[tuple[list[str], datetime]] = []

    async def fake_process_watch_group(watch_ids: list[str], now: datetime) -> None:
        calls.append((watch_ids, now))

    monkeypatch.setattr(worker_module, "_process_watch_group", fake_process_watch_group)

    assert await _process_watch_now("watch-now") == 1
    assert len(calls) == 1
    assert calls[0][0] == ["watch-now"]
    assert calls[0][1].tzinfo is not None


async def test_worker_due_pipeline_delegate_wires_runtime_dependencies(monkeypatch) -> None:
    captured = {}

    class RecordingMetric:
        def __init__(self) -> None:
            self.values: list[int] = []

        def inc(self, value: int) -> None:
            self.values.append(value)

    metric = RecordingMetric()

    async def fake_due_pipeline(providers_to_arm, *, dependencies) -> int:
        captured.update(providers_to_arm=providers_to_arm, dependencies=dependencies)
        return 7

    monkeypatch.setattr(worker_module, "process_due_pipeline", fake_due_pipeline)
    monkeypatch.setattr(worker_module, "get_settings", lambda: object())
    monkeypatch.setattr(
        worker_module, "korail_background_monitoring_enabled", lambda _settings: True
    )
    monkeypatch.setattr(worker_module, "WATCH_GROUPS", metric)

    assert await worker_module._process_due_watches() == 7
    assert captured["providers_to_arm"] == [Provider.SRT, Provider.KORAIL]
    dependencies = captured["dependencies"]
    assert dependencies.session_factory is worker_module.SessionFactory
    assert dependencies.get_execution_provider is worker_module.get_execution_provider
    assert dependencies.arm_provider_watches is worker_module._arm_supported_provider_watches
    assert dependencies.expire_elapsed_watches is worker_module._expire_elapsed_watches
    assert (
        worker_module._watch_expiry_dependencies().apply_watch_transition
        is worker_module.apply_watch_transition
    )
    assert (
        dependencies.recover_stale_reservation_attempts
        is worker_module._recover_stale_reservation_attempts
    )
    assert dependencies.process_watch_group is worker_module._process_watch_group
    assert (
        dependencies.reconcile_reservation_attempt is worker_module._reconcile_reservation_attempt
    )
    assert dependencies.close_execution_adapter is worker_module._close_execution_adapter
    assert (
        dependencies.reservation_reconciliation_due_clause
        is worker_module._reservation_reconciliation_due_clause
    )
    assert metric.values == [7]


def test_due_pipeline_celery_task_preserves_name_route_and_delegate(monkeypatch) -> None:
    calls = 0

    async def fake_due_pipeline() -> int:
        nonlocal calls
        calls += 1
        return 9

    monkeypatch.setattr(worker_module, "_process_due_watches", fake_due_pipeline)

    assert worker_module.process_due_watches.name == "rail_waitlist.worker.process_due_watches"
    assert worker_module.process_due_watches.run() == 9
    assert calls == 1
    assert worker_module.celery_app.conf.task_routes[
        "rail_waitlist.worker.process_due_watches"
    ] == {"queue": "rail"}


async def test_reconciliation_uses_same_generation_read_only_confirmation_once(
    app,
    db_engine,
    monkeypatch,
) -> None:
    observed_at = datetime.now(timezone.utc)
    deadline = observed_at + timedelta(minutes=12)
    adapter = ReadOnlyReconciliationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="srtrain-reservation-list",
            observed_at=observed_at,
            payment_deadline=deadline,
            official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
        )
    )
    lease_service = CurrentReconciliationLeaseService()

    async def acquire(_provider: Provider, now: datetime):
        assert now.tzinfo is not None
        return lease_service, object()

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "_acquire_execution_lease", acquire)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext="encrypted-outside-this-boundary",
                enabled=True,
                credential_version=3,
                last_auth_status="authenticated",
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="N-SUSEO",
            destination="부산",
            destination_node_id="N-BUSAN",
            travel_date=date(2026, 8, 3),
            time_from=time(12),
            time_to=time(18),
            seat_class="standard",
            passenger_count=1,
            train_numbers=["301"],
            notification_channel_ids=[],
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key="reconciliation-worker",
            reservation_attempted=True,
        )
        candidate = WatchCandidate(
            train_number="301",
            departure_at=datetime(2026, 8, 3, 3, tzinfo=timezone.utc),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:reconciliation-worker",
            started_at=observed_at - timedelta(minutes=2),
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=3,
            finished_at=observed_at - timedelta(minutes=1),
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        attempt_id = attempt.id
        candidate_id = candidate.id

    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 1
    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 0
    assert len(adapter.targets) == 1
    assert adapter.targets[0].candidate_id == candidate_id
    assert adapter.targets[0].credential_version == 3
    assert lease_service.released == 1

    async with factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempt.confirmation_outcome is (
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        )
        assert attempt.last_reconciled_at is not None
        assert attempt.reconciliation_attempt_count == 1
        assert attempt.next_reconcile_at is None
        assert attempt.payment_deadline == deadline.replace(tzinfo=None)
        reconciliation_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "watch.reservation_reconciled",
                    )
                )
            ).all()
        )
        assert len(reconciliation_events) == 1


async def test_unknown_inconclusive_reconciliation_uses_extended_bounded_schedule(
    app,
    db_engine,
    monkeypatch,
) -> None:
    observed_at = datetime.now(timezone.utc)
    adapter = ReadOnlyReconciliationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="srtrain-reservation-list",
            observed_at=observed_at,
        )
    )
    lease_service = CurrentReconciliationLeaseService()

    async def acquire(_provider: Provider, now: datetime):
        assert now.tzinfo is not None
        return lease_service, object()

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "_acquire_execution_lease", acquire)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext="encrypted-outside-this-boundary",
                enabled=True,
                credential_version=9,
                last_auth_status="authenticated",
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            destination="부산",
            travel_date=date(2026, 8, 3),
            time_from=time(12),
            time_to=time(18),
            seat_class="standard",
            passenger_count=1,
            train_numbers=["303"],
            notification_channel_ids=[],
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key="bounded-reconciliation-worker",
            reservation_attempted=True,
        )
        candidate = WatchCandidate(
            train_number="303",
            departure_at=datetime(2026, 8, 3, 4, tzinfo=timezone.utc),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:bounded-reconciliation-worker",
            started_at=observed_at - timedelta(minutes=2),
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=9,
            finished_at=observed_at - timedelta(minutes=1),
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        attempt_id = attempt.id

    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 1
    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 0

    expected_intervals = {
        1: timedelta(seconds=30),
        2: timedelta(seconds=30),
        3: timedelta(minutes=5),
        4: timedelta(minutes=15),
        5: timedelta(minutes=60),
        6: None,
    }
    async with factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.reconciliation_attempt_count == 1
        assert attempt.next_reconcile_at - attempt.last_reconciled_at == expected_intervals[1]

    for expected_count in (2, 3, 4, 5, 6):
        async with factory() as session:
            attempt = await session.get(ReservationAttempt, attempt_id)
            assert attempt is not None
            attempt.next_reconcile_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 1
        async with factory() as session:
            attempt = await session.get(ReservationAttempt, attempt_id)
            assert attempt is not None
            assert attempt.reconciliation_attempt_count == expected_count
            expected_interval = expected_intervals[expected_count]
            if expected_interval is None:
                assert attempt.next_reconcile_at is None
            else:
                assert attempt.next_reconcile_at - attempt.last_reconciled_at == expected_interval

    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 0
    assert len(adapter.targets) == 6
    assert lease_service.released == 6
    async with factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.UNKNOWN
    assert attempt.next_reconcile_at is None


def test_legacy_unknown_count_three_without_next_schedule_is_due_after_five_minutes() -> None:
    now = datetime.now(timezone.utc)
    watch = Watch(
        provider=Provider.SRT,
        origin="대전",
        destination="부산",
        travel_date=date(2026, 8, 4),
        time_from=time(12),
        time_to=time(18),
        status=WatchStatus.SEAT_FOUND,
        dedupe_key="legacy-unknown-extended-reconciliation",
    )
    attempt = ReservationAttempt(
        candidate_id="candidate-legacy-unknown",
        attempt_sequence=1,
        episode_key="availability:first",
        idempotency_key="reserve:legacy-unknown",
        outcome=ReservationOutcome.UNKNOWN,
        confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        confirmation_source="srtrain-reservation-list",
        confirmation_observed_at=now - timedelta(minutes=6),
        last_reconciled_at=now - timedelta(minutes=5, seconds=1),
        reconciliation_attempt_count=3,
        next_reconcile_at=None,
    )

    assert _reservation_reconciliation_is_due(attempt, watch, now) is True
    attempt.last_reconciled_at = now - timedelta(minutes=4, seconds=59)
    assert _reservation_reconciliation_is_due(attempt, watch, now) is False


async def test_legacy_unknown_count_three_without_next_schedule_is_selected(
    app,
    db_engine,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    adapter = ReadOnlyReconciliationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="srtrain-reservation-list",
            observed_at=now,
        )
    )
    lease_service = CurrentReconciliationLeaseService()

    async def acquire(_provider: Provider, _now: datetime):
        return lease_service, object()

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "_acquire_execution_lease", acquire)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext="encrypted-outside-this-boundary",
                enabled=True,
                credential_version=9,
                last_auth_status="authenticated",
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            destination="부산",
            travel_date=date(2026, 8, 4),
            time_from=time(12),
            time_to=time(18),
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="legacy-unknown-selection",
        )
        candidate = WatchCandidate(
            train_number="335",
            departure_at=now + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="seat_found",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:legacy-unknown-selection",
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=9,
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=now - timedelta(minutes=6),
            last_reconciled_at=now - timedelta(minutes=5, seconds=1),
            reconciliation_attempt_count=3,
            next_reconcile_at=None,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        attempt_id = attempt.id

    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 1
    async with factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.reconciliation_attempt_count == 4
        assert attempt.next_reconcile_at - attempt.last_reconciled_at == timedelta(minutes=15)


async def test_legacy_expired_confirmed_hold_is_selected_and_cleared(
    app,
    db_engine,
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=50)
    adapter = ReadOnlyReconciliationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="srtrain-reservation-list",
            observed_at=now,
            payment_deadline=deadline,
            official_handoff_url="https://etk.srail.kr/hpg/hra/02/selectReservationList.do",
        )
    )
    lease_service = CurrentReconciliationLeaseService()

    async def acquire(_provider: Provider, _now: datetime):
        return lease_service, object()

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "_acquire_execution_lease", acquire)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext="encrypted-outside-this-boundary",
                enabled=True,
                credential_version=9,
                last_auth_status="authenticated",
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            destination="수서",
            travel_date=date(2026, 8, 4),
            time_from=time(22),
            time_to=time(23, 59),
            status=WatchStatus.PAYMENT_REQUIRED,
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            payment_deadline=deadline,
            official_booking_url="https://etk.srail.kr/hpg/hra/02/selectReservationList.do",
            dedupe_key="legacy-expired-confirmed-hold-selection",
        )
        candidate = WatchCandidate(
            train_number="370",
            departure_at=now + timedelta(hours=12),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:legacy-expired-confirmed-hold-selection",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            credential_version=9,
            payment_deadline=deadline,
            confirmation_outcome=(ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=deadline + timedelta(minutes=1),
            last_reconciled_at=deadline + timedelta(minutes=1),
            reconciliation_attempt_count=3,
            next_reconcile_at=None,
            post_deadline_reconciled_at=deadline + timedelta(minutes=1),
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        attempt_id = attempt.id
        watch_id = watch.id

    assert await _run_reconciliation_application(attempt_id, adapter=adapter) == 1
    assert len(adapter.targets) == 1
    assert lease_service.released == 1
    async with factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        watch = await session.get(Watch, watch_id)
        assert attempt is not None
        assert watch is not None
        assert attempt.reconciliation_attempt_count == 4
        assert attempt.post_deadline_reconciled_at is not None
        reconciled_at = attempt.post_deadline_reconciled_at.replace(tzinfo=timezone.utc)
        assert reconciled_at >= now
        assert attempt.next_reconcile_at is None
        assert watch.status is WatchStatus.WATCHING
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None


def test_missing_deadline_payment_hold_is_due_for_bounded_legacy_refresh() -> None:
    now = datetime.now(timezone.utc)
    watch = Watch(
        provider=Provider.KORAIL,
        origin="대전",
        destination="서울",
        travel_date=date(2026, 8, 4),
        time_from=time(9),
        time_to=time(12),
        status=WatchStatus.PAYMENT_REQUIRED,
        dedupe_key="legacy-payment-deadline-refresh",
    )
    attempt = ReservationAttempt(
        candidate_id="candidate-legacy-payment",
        attempt_sequence=1,
        episode_key="availability:first",
        idempotency_key="reserve:legacy-payment",
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        reconciliation_attempt_count=1,
        next_reconcile_at=None,
    )

    assert _reservation_reconciliation_is_due(attempt, watch, now) is True

    attempt.reconciliation_attempt_count = 3
    assert _reservation_reconciliation_is_due(attempt, watch, now) is False


def test_expired_payment_hold_and_legacy_stuck_row_get_one_cleanup_read() -> None:
    now = datetime.now(timezone.utc)
    watch = Watch(
        provider=Provider.KORAIL,
        origin="대전",
        destination="서울",
        travel_date=date(2026, 8, 4),
        time_from=time(9),
        time_to=time(12),
        status=WatchStatus.PAYMENT_REQUIRED,
        payment_deadline=now - timedelta(seconds=1),
        dedupe_key="post-deadline-payment-refresh",
    )
    attempt = ReservationAttempt(
        candidate_id="candidate-post-deadline-payment",
        attempt_sequence=1,
        episode_key="availability:first",
        idempotency_key="reserve:post-deadline-payment",
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        payment_deadline=watch.payment_deadline,
        reconciliation_attempt_count=3,
        next_reconcile_at=None,
    )

    assert _reservation_reconciliation_is_due(attempt, watch, now) is True

    attempt.post_deadline_reconciled_at = now
    attempt.confirmation_outcome = ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert _reservation_reconciliation_is_due(attempt, watch, now) is True

    attempt.reconciliation_attempt_count = 4
    assert _reservation_reconciliation_is_due(attempt, watch, now) is False

    attempt.reconciliation_attempt_count = 3
    attempt.post_deadline_reconciled_at = None
    watch.payment_deadline = now + timedelta(minutes=1)
    assert _reservation_reconciliation_is_due(attempt, watch, now) is False


def test_stale_reservation_lock_query_does_not_lock_nullable_evidence() -> None:
    """Guard the PostgreSQL lock target while the eager LEFT JOIN remains present."""
    now = datetime(2026, 7, 30, 0, 5, tzinfo=timezone.utc)
    statement = build_stale_reservation_attempts_query(now)

    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "reservation_attempts.outcome = 'PENDING'" in compiled
    assert "reservation_attempts.started_at <= '2026-07-30 00:00:00+00:00'" in compiled
    assert "LEFT OUTER JOIN timetable_seat_evidence" in compiled
    assert "FOR UPDATE OF reservation_attempts, watch_candidates, watches SKIP LOCKED" in compiled

    candidate_lock = str(
        select(WatchCandidate)
        .where(WatchCandidate.id == "candidate-id")
        .with_for_update(of=WatchCandidate)
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN timetable_seat_evidence" in candidate_lock
    assert "FOR UPDATE OF watch_candidates" in candidate_lock


def watch_payload(provider: str = "korail") -> dict[str, object]:
    return {
        "provider": provider,
        "origin": "서울",
        "origin_node_id": "N1",
        "destination": "부산",
        "destination_node_id": "N2",
        "travel_date": (date.today() + timedelta(days=7)).isoformat(),
        "time_from": "08:00:00",
        "time_to": "12:00:00",
        "passenger_count": 1,
        "mode": "official",
    }


def mock_watch_payload(
    *, with_candidates: bool = True, seat_class: str = "standard"
) -> dict[str, object]:
    payload = watch_payload("mock")
    payload["origin_node_id"] = "MOCK-SEOUL"
    payload["destination_node_id"] = "MOCK-BUSAN"
    # MOCK is the provider; execution mode remains one of the public API modes.
    payload["mode"] = "official"
    payload["reservation_policy"] = "reserve_once_before_payment"
    payload["seat_class"] = seat_class
    if not with_candidates:
        return payload
    travel_date = str(payload["travel_date"])
    payload["train_numbers"] = ["MOCK-001", "MOCK-002"]
    payload["candidates"] = [
        {
            "train_number": "MOCK-001",
            "departure_at": f"{travel_date}T08:30:00+09:00",
            "arrival_at": f"{travel_date}T10:30:00+09:00",
            "seat_class": seat_class,
            "priority": 1,
        },
        {
            "train_number": "MOCK-002",
            "departure_at": f"{travel_date}T09:10:00+09:00",
            "arrival_at": f"{travel_date}T11:10:00+09:00",
            "seat_class": seat_class,
            "priority": 2,
        },
    ]
    return payload


class CountingMockAdapter(MockProviderAdapter):
    def __init__(self) -> None:
        self.observe_calls = 0
        self.reserve_calls = 0
        self.observation_requests = []

    async def observe_seats(self, request):
        self.observe_calls += 1
        self.observation_requests.append(request)
        return await super().observe_seats(request)

    async def reserve_once(self, request):
        self.reserve_calls += 1
        return await super().reserve_once(request)


class DisabledExecutionAdapter(CountingMockAdapter):
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=Provider.MOCK,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=False,
            reservation_once=False,
        )


class DeferredObservationAdapter(CountingMockAdapter):
    provider = Provider.SRT

    def __init__(self, deferred_until: datetime | None) -> None:
        super().__init__()
        self.deferred_until = deferred_until
        self.close_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=Provider.SRT,
            timetable=False,
            official_booking_link=False,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=False,
        )

    async def observation_deferred_until(self) -> datetime | None:
        return self.deferred_until

    async def aclose(self) -> None:
        self.close_calls += 1


class CooldownOpeningObservationAdapter(DeferredObservationAdapter):
    async def observe_seats(self, request):
        self.observe_calls += 1
        self.observation_requests.append(request)
        self.deferred_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        observed_at = datetime.now(timezone.utc)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=SeatObservationStatus.ERROR,
                source="authorized-provider",
                observed_at=observed_at,
                fresh_until=observed_at,
                error_category="provider_unavailable",
            )
        ]


class VaryingTimestampSoldOutAdapter(CountingMockAdapter):
    def __init__(self, provider: Provider = Provider.MOCK) -> None:
        super().__init__()
        self.provider = provider

    async def observe_seats(self, request):
        self.observe_calls += 1
        self.observation_requests.append(request)
        observed_at = datetime.now(timezone.utc) + timedelta(seconds=self.observe_calls)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=SeatObservationStatus.SOLD_OUT,
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
            )
        ]


class SequencedObservationAdapter(CountingMockAdapter):
    def __init__(self, statuses: list[SeatObservationStatus]) -> None:
        super().__init__()
        self.statuses = iter(statuses)

    def capabilities(self) -> ProviderCapabilities:
        return super().capabilities().model_copy(update={"reservation_once": False})

    async def observe_seats(self, request):
        self.observe_calls += 1
        self.observation_requests.append(request)
        observed_at = datetime.now(timezone.utc)
        status = next(self.statuses)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=status,
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
                error_category=(
                    "provider_unavailable" if status == SeatObservationStatus.ERROR else None
                ),
            )
        ]


class TaskScopedSoldOutAdapter(VaryingTimestampSoldOutAdapter):
    def __init__(
        self,
        provider: Provider,
        *,
        fail_close: bool = False,
        lifecycle_events: list[str] | None = None,
    ) -> None:
        super().__init__(provider)
        self.drain_calls = 0
        self.close_calls = 0
        self.fail_close = fail_close
        self.lifecycle_events = lifecycle_events

    def capabilities(self) -> ProviderCapabilities:
        capabilities = super().capabilities()
        return capabilities.model_copy(update={"reservation_once": False})

    async def drain_pending_calls(self) -> None:
        self.drain_calls += 1
        if self.lifecycle_events is not None:
            self.lifecycle_events.append("drain")

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.lifecycle_events is not None:
            self.lifecycle_events.append("close")
        if self.fail_close:
            raise RuntimeError("synthetic cleanup failure")


class DisabledTaskScopedAdapter(TaskScopedSoldOutAdapter):
    def capabilities(self) -> ProviderCapabilities:
        return super().capabilities().model_copy(update={"seat_monitoring": False})


class FailingObservationAdapter(CountingMockAdapter):
    async def observe_seats(self, request):
        self.observe_calls += 1
        raise RuntimeError("synthetic provider failure")


class CircuitOpeningObservationAdapter(CountingMockAdapter):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory

    async def observe_seats(self, request):
        self.observe_calls += 1
        if self.observe_calls == 1:
            async with self.session_factory() as session:
                circuit = await session.scalar(
                    select(ProviderCircuit).where(ProviderCircuit.provider == Provider.MOCK)
                )
                circuit.state = ProviderCircuitState.MANUAL_HOLD
                circuit.reason = "synthetic_mid_cycle_protection_signal"
                circuit.manual_resume_required = True
                circuit.generation += 1
                await session.commit()
        observed_at = datetime.now(timezone.utc)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=SeatObservationStatus.AVAILABLE,
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
            )
        ]


class ExpiringReservationAdapter(CountingMockAdapter):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory

    async def reserve_once(self, request):
        async with self.session_factory() as session:
            watch = await session.scalar(
                select(Watch).join(WatchCandidate).where(WatchCandidate.id == request.candidate_id)
            )
            watch.status = WatchStatus.EXPIRED
            watch.next_check_at = None
            await session.commit()
        return await super().reserve_once(request)


class ExpiredDeadlineReservationAdapter(CountingMockAdapter):
    async def reserve_once(self, request):
        self.reserve_calls += 1
        observed_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        return ReservationResult(
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            source="mock",
            observed_at=observed_at,
            payment_deadline=observed_at + timedelta(minutes=5),
            official_handoff_url=self.official_booking_url(),
        )


class UnknownReservationAdapter(CountingMockAdapter):
    async def reserve_once(self, request):
        self.reserve_calls += 1
        return ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            source="mock",
            observed_at=datetime.now(timezone.utc),
        )


class FailedReservationAdapter(CountingMockAdapter):
    async def reserve_once(self, request):
        self.reserve_calls += 1
        return ReservationResult(
            outcome=ReservationOutcome.FAILED,
            source="mock",
            observed_at=datetime.now(timezone.utc),
        )


class RetryableNotAvailableAdapter(CountingMockAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.statuses = iter(
            [
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.SOLD_OUT,
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.SOLD_OUT,
                SeatObservationStatus.AVAILABLE,
                SeatObservationStatus.AVAILABLE,
            ]
        )

    async def observe_seats(self, request):
        self.observe_calls += 1
        self.observation_requests.append(request)
        observed_at = datetime.now(timezone.utc)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=next(self.statuses),
                source="mock",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=5),
            )
        ]

    async def reserve_once(self, request):
        self.reserve_calls += 1
        return ReservationResult(
            outcome=ReservationOutcome.NOT_AVAILABLE,
            source="mock",
            observed_at=datetime.now(timezone.utc),
        )


class NewCredentialAuthRequiredKorailAdapter(CountingMockAdapter):
    provider = Provider.KORAIL

    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory

    async def reserve_once(self, request):
        self.reserve_calls += 1
        async with self.session_factory() as session:
            account = await session.scalar(
                select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
            )
            account.credential_version = 5
            account.last_auth_status = "authenticated"
            await session.commit()
        return ReservationResult(
            outcome=ReservationOutcome.AUTH_REQUIRED,
            source="authorized-provider",
            observed_at=datetime.now(timezone.utc),
            credential_version=5,
        )


class FirstFailedThenSuccessfulReservationAdapter(CountingMockAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.reservation_candidate_ids: list[str] = []

    async def reserve_once(self, request):
        self.reserve_calls += 1
        self.reservation_candidate_ids.append(request.candidate_id)
        if self.reserve_calls == 1:
            return ReservationResult(
                outcome=ReservationOutcome.FAILED,
                source="mock",
                observed_at=datetime.now(timezone.utc),
            )
        return await MockProviderAdapter.reserve_once(self, request)


async def test_seat_found_watch_keeps_observing_and_only_notifies_on_state_edges(
    app, db_engine, monkeypatch
):
    adapter = SequencedObservationAdapter(
        [
            SeatObservationStatus.AVAILABLE,
            SeatObservationStatus.AVAILABLE,
            SeatObservationStatus.SOLD_OUT,
            SeatObservationStatus.AVAILABLE,
            SeatObservationStatus.ERROR,
        ]
    )
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    first_cycle_at = datetime.now(timezone.utc).replace(microsecond=0)
    departure_at = first_cycle_at + timedelta(hours=2)
    local_departure = departure_at.astimezone(timezone(timedelta(hours=9)))
    async with factory() as session:
        channel = NotificationChannel(
            id="seat-found-channel",
            kind=NotificationKind.TELEGRAM,
            name="seat-found-edge-test",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="부산",
            destination_node_id="MOCK-BUSAN",
            travel_date=local_departure.date(),
            time_from=(local_departure - timedelta(hours=1)).time(),
            time_to=(local_departure + timedelta(hours=1)).time(),
            passenger_count=1,
            status=WatchStatus.WATCHING,
            mode="official",
            dedupe_key="seat-found-continuous-observation",
            next_check_at=first_cycle_at - timedelta(seconds=1),
            notification_channel_ids=["seat-found-channel"],
        )
        watch.candidates.append(
            WatchCandidate(
                train_number="MOCK-001",
                departure_at=departure_at,
                arrival_at=departure_at + timedelta(hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="observed",
            )
        )
        session.add_all([channel, watch])
        await session.commit()
        watch_id = watch.id

    cycle_at = first_cycle_at
    expected_statuses = [
        WatchStatus.SEAT_FOUND,
        WatchStatus.SEAT_FOUND,
        WatchStatus.WATCHING,
        WatchStatus.SEAT_FOUND,
        WatchStatus.SEAT_FOUND,
    ]
    for expected_status in expected_statuses:
        await _process_watch_group([watch_id], cycle_at, provider=Provider.MOCK, adapter=adapter)
        async with factory() as session:
            watch = await session.get(Watch, watch_id)
            assert watch.status is expected_status
            assert watch.next_check_at is not None
            cycle_at = watch.next_check_at.replace(tzinfo=timezone.utc) + timedelta(seconds=1)

    async with factory() as session:
        observations = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
        )
        seat_found_transitions = await session.scalar(
            select(func.count())
            .select_from(WatchTransitionHistory)
            .where(
                WatchTransitionHistory.watch_id == watch_id,
                WatchTransitionHistory.to_status == WatchStatus.SEAT_FOUND,
            )
        )
        notification_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == "seat-found-channel",
                        OutboxEvent.event_type == "notification.dispatch_requested",
                    )
                    .order_by(OutboxEvent.created_at)
                )
            ).all()
        )
        assert observations == 5
        assert seat_found_transitions == 2
        assert [event.payload["status"] for event in notification_events] == [
            "seat_found",
            "watching",
            "seat_found",
        ]
        assert "판매 불가 상태" in notification_events[1].payload["message"]
        assert adapter.reserve_calls == 0


async def test_official_waitlist_keeps_observing_and_summarizes_inventory_changes(
    app, db_engine, monkeypatch
):
    adapter = SequencedObservationAdapter(
        [
            SeatObservationStatus.WAITLIST_AVAILABLE,
            SeatObservationStatus.WAITLIST_AVAILABLE,
            SeatObservationStatus.LIMITED,
            SeatObservationStatus.WAITLIST_AVAILABLE,
            SeatObservationStatus.SOLD_OUT,
            SeatObservationStatus.ERROR,
        ]
    )
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    first_cycle_at = datetime.now(timezone.utc).replace(microsecond=0)
    departure_at = first_cycle_at + timedelta(hours=2)
    local_departure = departure_at.astimezone(timezone(timedelta(hours=9)))
    async with factory() as session:
        channel = NotificationChannel(
            id="official-waitlist-channel",
            kind=NotificationKind.TELEGRAM,
            name="official-waitlist-edge-test",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="부산",
            destination_node_id="MOCK-BUSAN",
            travel_date=local_departure.date(),
            time_from=(local_departure - timedelta(hours=1)).time(),
            time_to=(local_departure + timedelta(hours=1)).time(),
            passenger_count=1,
            status=WatchStatus.WATCHING,
            mode="official",
            dedupe_key="official-waitlist-continuous-observation",
            next_check_at=first_cycle_at - timedelta(seconds=1),
            notification_channel_ids=["official-waitlist-channel"],
        )
        watch.candidates.append(
            WatchCandidate(
                train_number="MOCK-002",
                departure_at=departure_at,
                arrival_at=departure_at + timedelta(hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="observed",
            )
        )
        session.add_all([channel, watch])
        await session.commit()
        watch_id = watch.id

    cycle_at = first_cycle_at
    expected_statuses = [
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.WATCHING,
        WatchStatus.WATCHING,
    ]
    for expected_status in expected_statuses:
        await _process_watch_group([watch_id], cycle_at, provider=Provider.MOCK, adapter=adapter)
        async with factory() as session:
            watch = await session.get(Watch, watch_id)
            assert watch.status is expected_status
            assert watch.next_check_at is not None
            cycle_at = watch.next_check_at.replace(tzinfo=timezone.utc) + timedelta(seconds=1)

    async with factory() as session:
        observations = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
        )
        notification_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == "official-waitlist-channel",
                        OutboxEvent.event_type == "notification.dispatch_requested",
                    )
                    .order_by(OutboxEvent.created_at)
                )
            ).all()
        )
        assert observations == 6
        assert [event.payload["status"] for event in notification_events] == [
            "official_waitlist",
            "seat_found",
            "official_waitlist",
            "watching",
        ]
        assert "판매 불가 상태" in notification_events[-1].payload["message"]
        assert adapter.reserve_calls == 0


async def test_seat_found_watch_with_null_schedule_is_rearmed(app, db_engine, monkeypatch):
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    adapter = TaskScopedSoldOutAdapter(Provider.SRT)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
    )
    monkeypatch.setattr(
        worker_module,
        "korail_background_monitoring_enabled",
        lambda settings: False,
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    departure_at = now + timedelta(hours=2)
    local_departure = departure_at.astimezone(timezone(timedelta(hours=9)))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="N-SUSEO",
            destination="부산",
            destination_node_id="N-BUSAN",
            travel_date=local_departure.date(),
            time_from=(local_departure - timedelta(hours=1)).time(),
            time_to=(local_departure + timedelta(hours=1)).time(),
            passenger_count=1,
            status=WatchStatus.SEAT_FOUND,
            mode="official",
            dedupe_key="rearm-seat-found",
            next_check_at=None,
        )
        watch.candidates.append(
            WatchCandidate(
                train_number="SRT-001",
                departure_at=departure_at,
                arrival_at=departure_at + timedelta(hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="seat_found",
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    assert await _arm_supported_provider_watches(Provider.SRT, now, adapter=adapter) == 1
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch.status is WatchStatus.SEAT_FOUND
        assert watch.next_check_at.replace(tzinfo=timezone.utc) == now

    assert await _process_due_watches() == 1
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observation = await session.scalar(
            select(SeatObservation).join(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        assert watch.status is WatchStatus.WATCHING
        assert watch.next_check_at is not None
        assert observation.status is SeatObservationStatus.SOLD_OUT


async def test_watch_api_exposes_unknown_operational_state_with_scheduled_identity(client):
    created = await client.post("/api/v1/watches", json=mock_watch_payload())

    assert created.status_code == 201
    candidate = created.json()["candidates"][0]
    assert candidate["scheduled_departure_at"] == candidate["departure_at"]
    assert candidate["estimated_departure_at"] is None
    assert candidate["actual_departure_at"] is None
    assert candidate["delay_minutes"] is None
    assert candidate["operational_status"] == "unknown"
    assert candidate["booking_window_status"] == "unknown"
    assert candidate["operational_source"] is None
    assert candidate["operational_observed_at"] is None
    assert candidate["operational_fresh_until"] is None


async def test_official_watch_without_execution_capability_never_becomes_watching(
    client,
    app,
    monkeypatch,
):
    disabled_adapter = DisabledExecutionAdapter()
    monkeypatch.setattr(
        transition_runtime_module,
        "get_execution_provider",
        lambda provider: disabled_adapter,
    )
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider: disabled_adapter,
    )
    created = await client.post("/api/v1/watches", json=watch_payload())
    watch_id = created.json()["id"]
    started = await client.post(f"/api/v1/watches/{watch_id}/start")
    assert started.json()["status"] == "scheduled"
    assert started.json()["next_check_at"] is None

    monkeypatch.setattr("rail_waitlist.worker.SessionFactory", app.state.test_session_factory)
    assert await _process_due_watches() == 0
    assert await _process_due_watches() == 0

    current = await client.get(f"/api/v1/watches/{watch_id}")
    assert current.json()["status"] == "scheduled"


async def test_candidate_less_mock_watch_pauses_fail_closed(client, app, monkeypatch):
    created = await client.post("/api/v1/watches", json=mock_watch_payload(with_candidates=False))
    watch_id = created.json()["id"]
    started = await client.post(f"/api/v1/watches/{watch_id}/start")
    assert started.json()["next_check_at"] is not None

    monkeypatch.setattr("rail_waitlist.worker.SessionFactory", app.state.test_session_factory)
    assert await _process_due_watches() == 1
    current = await client.get(f"/api/v1/watches/{watch_id}")
    assert current.json()["status"] == "paused"
    assert current.json()["next_check_at"] is None


async def test_immediate_watch_path_preserves_payment_handoff_and_db_fence(
    client,
    app,
    db_engine,
    monkeypatch,
) -> None:
    adapter = CountingMockAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda _provider, *args, **kwargs: adapter,
    )
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_watch_now(watch_id) == 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        attempt = await session.scalar(select(ReservationAttempt))
        assert watch.status is WatchStatus.PAYMENT_REQUIRED
        assert watch.payment_deadline is not None
        assert watch.official_booking_url is not None
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempt.payment_deadline == watch.payment_deadline
        assert attempt.official_handoff_url == watch.official_booking_url

    observe_calls = adapter.observe_calls
    reserve_calls = adapter.reserve_calls
    assert await _process_watch_now(watch_id) == 1
    assert adapter.observe_calls == observe_calls
    assert adapter.reserve_calls == reserve_calls == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 1


async def test_unknown_immediate_reservation_keeps_watching_without_second_attempt(
    client,
    app,
    db_engine,
    monkeypatch,
) -> None:
    adapter = UnknownReservationAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda _provider, *args, **kwargs: adapter,
    )
    payload = mock_watch_payload()
    payload["train_numbers"] = [payload["train_numbers"][0]]
    payload["candidates"] = [payload["candidates"][0]]
    created = await client.post("/api/v1/watches", json=payload)
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_watch_now(watch_id) == 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
            .order_by(WatchCandidate.priority)
        )
        attempt = await session.scalar(select(ReservationAttempt))
        assert watch.status is WatchStatus.WATCHING
        assert candidate.state == "observed"
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        watch.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await _process_watch_now(watch_id) == 1
    assert adapter.reserve_calls == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 1


async def test_failed_immediate_reservation_resumes_monitoring_without_second_attempt(
    client,
    app,
    db_engine,
    monkeypatch,
) -> None:
    adapter = FailedReservationAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda _provider, *args, **kwargs: adapter,
    )
    payload = mock_watch_payload()
    payload["train_numbers"] = [payload["train_numbers"][0]]
    payload["candidates"] = [payload["candidates"][0]]
    created = await client.post("/api/v1/watches", json=payload)
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_watch_now(watch_id) == 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
            .order_by(WatchCandidate.priority)
        )
        attempt = await session.scalar(select(ReservationAttempt))
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch_id,
                OutboxEvent.event_type == "watch.reservation_failed_monitoring_resumed",
            )
        )
        assert watch.status is WatchStatus.WATCHING
        assert candidate.state == "observed"
        assert attempt.outcome is ReservationOutcome.FAILED
        assert event is not None
        assert event.payload["monitoring_resumed"] is True
        watch.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await _process_watch_now(watch_id) == 1
    assert adapter.reserve_calls == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 1


async def test_not_available_retries_only_after_conclusive_unavailable_availability_edge(
    client,
    app,
    db_engine,
    monkeypatch,
) -> None:
    adapter = RetryableNotAvailableAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda _provider, *args, **kwargs: adapter,
    )
    payload = mock_watch_payload()
    payload["train_numbers"] = [payload["train_numbers"][0]]
    payload["candidates"] = [payload["candidates"][0]]
    created = await client.post("/api/v1/watches", json=payload)
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    expected_attempt_counts = [1, 1, 1, 1, 2, 2, 2, 2, 3, 3]
    actual_attempt_counts: list[int] = []
    for _expected_attempt_count in expected_attempt_counts:
        async with factory() as session:
            watch = await session.get(Watch, watch_id)
            watch.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        assert await _process_watch_now(watch_id) == 1
        async with factory() as session:
            actual_attempt_counts.append(
                await session.scalar(select(func.count()).select_from(ReservationAttempt))
            )
    assert actual_attempt_counts == expected_attempt_counts

    async with factory() as session:
        attempts = list(
            (
                await session.scalars(
                    select(ReservationAttempt).order_by(ReservationAttempt.attempt_sequence)
                )
            ).all()
        )
        result_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == watch_id,
                        OutboxEvent.event_type == "watch.reservation_result",
                    )
                    .order_by(OutboxEvent.created_at)
                )
            ).all()
        )
        status_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == watch_id,
                        OutboxEvent.event_type == "watch.status_changed",
                    )
                    .order_by(OutboxEvent.created_at)
                )
            ).all()
        )
        assert [attempt.attempt_sequence for attempt in attempts] == [1, 2, 3]
        assert all(attempt.outcome is ReservationOutcome.NOT_AVAILABLE for attempt in attempts)
        assert len({attempt.episode_key for attempt in attempts}) == 3
        assert attempts[0].episode_key.startswith("availability:")
        assert all(
            attempt.episode_key.startswith("availability-after:") for attempt in attempts[1:]
        )
        assert sum(event.payload.get("to") == "seat_found" for event in status_events) == 3
        assert all(event.payload["retryable"] is True for event in result_events)
        assert all(
            event.payload["retry_condition"] == "new_availability_episode"
            for event in result_events
        )
    assert adapter.reserve_calls == 3


@pytest.mark.parametrize(
    "outcome",
    [
        ReservationOutcome.PENDING,
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.RESERVED,
        ReservationOutcome.UNKNOWN,
        ReservationOutcome.PROVIDER_BLOCKED,
        ReservationOutcome.FAILED,
    ],
)
async def test_ambiguous_hold_or_generic_failure_never_rearms_candidate(
    db_engine,
    outcome: ReservationOutcome,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="부산",
            destination_node_id="MOCK-BUSAN",
            travel_date=now.date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"non-retryable-{outcome.value}",
        )
        candidate = WatchCandidate(
            train_number="MOCK-001",
            departure_at=now + timedelta(hours=3),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        session.add(
            ReservationAttempt(
                candidate_id=candidate.id,
                attempt_sequence=1,
                episode_key="availability:initial",
                idempotency_key=f"non-retryable:{outcome.value}",
                started_at=now - timedelta(minutes=2),
                finished_at=(None if outcome is ReservationOutcome.PENDING else now),
                outcome=outcome,
            )
        )
        observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="mock",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=5),
        )
        session.add(observation)
        await session.flush()

        assert (
            await _retryable_reservation_episode_key(
                session,
                candidate,
                observation,
                Provider.MOCK,
            )
            is None
        )


async def test_confirmed_absent_unknown_stays_fenced_during_continued_availability(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(timezone.utc)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            origin_node_id="N-DAEJEON",
            destination="부산",
            destination_node_id="N-BUSAN",
            travel_date=confirmed_at.date(),
            time_from=time(12),
            time_to=time(18),
            passenger_count=1,
            mode="official",
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="confirmed-absent-worker-retry",
        )
        candidate = WatchCandidate(
            train_number="335",
            departure_at=confirmed_at + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:confirmed-absent-worker:first",
            outcome=ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=confirmed_at,
        )
        observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=confirmed_at + timedelta(seconds=1),
            fresh_until=confirmed_at + timedelta(minutes=1),
        )
        session.add_all([attempt, observation])
        await session.flush()

        assert (
            await _retryable_reservation_episode_key(
                session,
                candidate,
                observation,
                Provider.SRT,
            )
            is None
        )


async def test_legacy_confirmed_absent_payment_hold_emits_one_retry_episode(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(timezone.utc)
    async with factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="N-DAEJEON",
            destination="서울",
            destination_node_id="N-SEOUL",
            travel_date=confirmed_at.date(),
            time_from=time(12),
            time_to=time(18),
            passenger_count=1,
            mode="official",
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="legacy-confirmed-absent-worker-retry",
        )
        candidate = WatchCandidate(
            train_number="238",
            departure_at=confirmed_at + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:legacy-confirmed-absent-worker:first",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=None,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=confirmed_at,
            post_deadline_reconciled_at=None,
        )
        observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.LIMITED,
            source="authorized-provider",
            observed_at=confirmed_at + timedelta(seconds=1),
            fresh_until=confirmed_at + timedelta(minutes=1),
        )
        session.add_all([attempt, observation])
        await session.flush()

        episode_key = await _retryable_reservation_episode_key(
            session,
            candidate,
            observation,
            Provider.KORAIL,
        )

        assert episode_key == f"confirmed-absent-retry:{attempt.id}"


@pytest.mark.parametrize(
    "confirmation_outcome",
    [
        ReservationConfirmationOutcome.NOT_FOUND,
        ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
    ],
)
async def test_ended_payment_hold_retries_only_after_new_unavailable_edge(
    db_engine,
    confirmation_outcome: ReservationConfirmationOutcome,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    hold_ended_at = now - timedelta(minutes=2)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            origin_node_id="N-DAEJEON",
            destination="수서",
            destination_node_id="N-SUSEO",
            travel_date=now.date(),
            time_from=time(12),
            time_to=time(18),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"ended-hold-retry-{confirmation_outcome.value}",
        )
        candidate = WatchCandidate(
            train_number="374",
            departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        payment_deadline = (
            hold_ended_at - timedelta(seconds=1)
            if confirmation_outcome
            is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
            else None
        )
        first_attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key=f"reserve:ended-hold:{confirmation_outcome.value}",
            started_at=hold_ended_at - timedelta(minutes=2),
            finished_at=hold_ended_at - timedelta(minutes=1),
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=payment_deadline,
            confirmation_outcome=confirmation_outcome,
            confirmation_source="official-reservation-list",
            confirmation_observed_at=hold_ended_at,
            post_deadline_reconciled_at=hold_ended_at,
        )
        continued_availability = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=hold_ended_at + timedelta(seconds=10),
            fresh_until=hold_ended_at + timedelta(minutes=1),
        )
        session.add_all([first_attempt, continued_availability])
        await session.flush()

        assert (
            await _retryable_reservation_episode_key(
                session,
                candidate,
                continued_availability,
                Provider.SRT,
            )
            is None
        )

        unavailable = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.SOLD_OUT,
            source="authorized-provider",
            observed_at=hold_ended_at + timedelta(seconds=20),
            fresh_until=hold_ended_at + timedelta(minutes=1),
        )
        rediscovered = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.LIMITED,
            source="authorized-provider",
            observed_at=hold_ended_at + timedelta(seconds=30),
            fresh_until=hold_ended_at + timedelta(minutes=1),
        )
        session.add_all([unavailable, rediscovered])
        await session.flush()

        episode_key = await _retryable_reservation_episode_key(
            session,
            candidate,
            rediscovered,
            Provider.SRT,
        )
        assert episode_key == (
            f"availability-after-hold:{first_attempt.id}:{unavailable.id}"
        )

        second_attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            f"reserve:second:{confirmation_outcome.value}",
            episode_key=episode_key,
            retry_authorized=True,
        )
        assert created is True
        assert second_attempt.attempt_sequence == 2


async def test_watch_payment_hold_fences_reactivated_lower_candidate_without_local_attempt(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    hold_ended_at = now - timedelta(minutes=2)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            origin_node_id="N-DAEJEON",
            destination="수서",
            destination_node_id="N-SUSEO",
            travel_date=now.date(),
            time_from=time(12),
            time_to=time(18),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key="watch-scoped-ended-hold-retry",
        )
        primary = WatchCandidate(
            train_number="374",
            departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        lower = WatchCandidate(
            train_number="376",
            departure_at=now + timedelta(days=1, minutes=10),
            seat_class=SeatClass.STANDARD,
            priority=2,
            state="suppressed_by_priority",
        )
        watch.candidates.extend([primary, lower])
        session.add(watch)
        await session.flush()
        lower.suppressed_by_candidate_id = primary.id
        hold = ReservationAttempt(
            candidate_id=primary.id,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:watch-scoped-ended-hold",
            started_at=hold_ended_at - timedelta(minutes=2),
            finished_at=hold_ended_at - timedelta(minutes=1),
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="official-reservation-list",
            confirmation_observed_at=hold_ended_at,
            post_deadline_reconciled_at=hold_ended_at,
        )
        session.add(hold)
        await session.flush()

        # The reconciliation path reactivates a previously suppressed lower candidate.
        lower.state = "observed"
        lower.suppressed_by_candidate_id = None
        continued_availability = SeatObservation(
            candidate=lower,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=hold_ended_at + timedelta(seconds=10),
            fresh_until=hold_ended_at + timedelta(minutes=1),
        )
        session.add(continued_availability)
        await session.flush()

        assert (
            await _retryable_reservation_episode_key(
                session,
                lower,
                continued_availability,
                Provider.SRT,
            )
            is None
        )

        unavailable = SeatObservation(
            candidate=lower,
            status=SeatObservationStatus.SOLD_OUT,
            source="authorized-provider",
            observed_at=hold_ended_at + timedelta(seconds=20),
            fresh_until=hold_ended_at + timedelta(minutes=1),
        )
        rediscovered = SeatObservation(
            candidate=lower,
            status=SeatObservationStatus.LIMITED,
            source="authorized-provider",
            observed_at=hold_ended_at + timedelta(seconds=30),
            fresh_until=hold_ended_at + timedelta(minutes=1),
        )
        session.add_all([unavailable, rediscovered])
        await session.flush()

        assert await _retryable_reservation_episode_key(
            session,
            lower,
            rediscovered,
            Provider.SRT,
        ) == f"availability-after-hold:{hold.id}:{unavailable.id}"


@pytest.mark.parametrize(
    ("initial_outcome", "transition_reason"),
    [
        (ReservationOutcome.AUTH_REQUIRED, "reservation_auth_required"),
        (ReservationOutcome.PROVIDER_BLOCKED, "reservation_provider_blocked"),
    ],
)
async def test_auth_failure_rearms_once_after_newer_verified_account_generation(
    app,
    db_engine,
    monkeypatch,
    initial_outcome: ReservationOutcome,
    transition_reason: str,
) -> None:
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    adapter = NewCredentialAuthRequiredKorailAdapter(factory)
    now = datetime.now(timezone.utc)
    first_attempt_finished_at = now - timedelta(minutes=5)
    authenticated_at = now - timedelta(minutes=1)
    departure_at = now + timedelta(hours=3)
    async with factory() as session:
        account = RailProviderAccount(
            provider=Provider.KORAIL,
            credentials_ciphertext=secret_box.encrypt_dict(
                {
                    "login_method": "membership_number",
                    "login_id": "test-account",
                    "password": "test-password",
                }
            ),
            enabled=True,
            credential_version=4,
            last_auth_status="authenticated",
            last_authenticated_at=authenticated_at,
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key=f"auth-failure-generation-retry:{initial_outcome.value}",
        )
        candidate = WatchCandidate(
            train_number="00055",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="failed",
        )
        watch.candidates.append(candidate)
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.RESERVING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason=transition_reason,
                created_at=first_attempt_finished_at,
            )
        )
        session.add_all([account, watch])
        await session.flush()
        session.add(
            ReservationAttempt(
                candidate_id=candidate.id,
                attempt_sequence=1,
                episode_key="availability:initial",
                idempotency_key="reserve:00055:first",
                started_at=first_attempt_finished_at - timedelta(seconds=1),
                finished_at=first_attempt_finished_at,
                outcome=initial_outcome,
            )
        )
        await session.flush()
        resumed = await resume_watches_after_verified_provider_login(
            session,
            Provider.KORAIL,
            authenticated_at,
        )
        assert resumed == [watch.id]
        assert watch.status is WatchStatus.SCHEDULED
        assert candidate.state == "observed"

        watch.status = WatchStatus.SEAT_FOUND
        candidate.state = "seat_found"
        observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=now,
            fresh_until=now + timedelta(minutes=5),
        )
        session.add(observation)
        await session.flush()
        episode_key = await _retryable_reservation_episode_key(
            session,
            candidate,
            observation,
            Provider.KORAIL,
        )
        assert episode_key is not None and episode_key.startswith("auth:4:")
        target = ObservationTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=Provider.KORAIL,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=candidate.departure_at,
            arrival_at=candidate.arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            priority=candidate.priority,
            reservation_episode_key=episode_key,
        )
        watch_id = watch.id
        candidate_id = candidate.id
        await session.commit()

    await _reserve_winner(adapter, target)

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        attempts = list(
            (
                await session.scalars(
                    select(ReservationAttempt)
                    .where(ReservationAttempt.candidate_id == candidate_id)
                    .order_by(ReservationAttempt.attempt_sequence)
                )
            ).all()
        )
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert watch.status is WatchStatus.AUTH_REQUIRED
        assert candidate.state == "failed"
        assert [attempt.attempt_sequence for attempt in attempts] == [1, 2]
        assert attempts[-1].outcome is ReservationOutcome.AUTH_REQUIRED
        assert adapter.reserve_calls == 1
        assert account.credential_version == 5
        assert account.last_auth_status == "auth_required"

        # The same successful login generation predates attempt 2 and cannot arm 3.
        account.last_auth_status = "authenticated"
        account.last_authenticated_at = authenticated_at
        watch.status = WatchStatus.SEAT_FOUND
        candidate.state = "seat_found"
        repeated_observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=5),
        )
        session.add(repeated_observation)
        await session.flush()
        assert (
            await _retryable_reservation_episode_key(
                session,
                candidate,
                repeated_observation,
                Provider.KORAIL,
            )
            is None
        )


async def test_preflight_auth_required_resumes_without_changing_unclaimed_attempt_fence(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    authenticated_at = now - timedelta(minutes=1)
    departure_at = now + timedelta(hours=3)
    async with factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="preflight-auth-required-resume",
        )
        candidate = WatchCandidate(
            train_number="00056",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.SEAT_FOUND,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="provider_account_not_authenticated_before_reservation",
                created_at=now - timedelta(minutes=5),
            )
        )
        session.add(watch)
        await session.flush()

        resumed = await resume_watches_after_verified_provider_login(
            session,
            Provider.KORAIL,
            authenticated_at,
        )

        assert resumed == [watch.id]
        assert watch.status is WatchStatus.SCHEDULED
        # The preflight stopped before an attempt was created, so this remains the
        # initial availability episode rather than a reset/retry of an old attempt.
        assert candidate.state == "seat_found"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ReservationAttempt)
                .where(ReservationAttempt.candidate_id == candidate.id)
            )
            == 0
        )
        observation = SeatObservation(
            candidate=candidate,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=now,
            fresh_until=now + timedelta(minutes=5),
        )
        session.add(observation)
        await session.flush()
        episode_key = await _retryable_reservation_episode_key(
            session,
            candidate,
            observation,
            Provider.KORAIL,
        )
        assert episode_key is not None and episode_key.startswith("availability:")
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch.id)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(1)
        )
        assert latest_transition is not None
        assert latest_transition.reason == "provider_login_reverified_before_reservation"


async def test_failed_higher_priority_candidate_does_not_block_next_candidate(
    client,
    app,
    db_engine,
    monkeypatch,
) -> None:
    adapter = FirstFailedThenSuccessfulReservationAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda _provider, *args, **kwargs: adapter,
    )
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_watch_now(watch_id) == 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                    .order_by(WatchCandidate.priority)
                )
            ).all()
        )
        first_attempt = await session.scalar(
            select(ReservationAttempt).where(ReservationAttempt.candidate_id == candidates[0].id)
        )
        watch = await session.get(Watch, watch_id)
        assert first_attempt.outcome is ReservationOutcome.FAILED
        assert candidates[0].state == "observed"
        assert watch.status is WatchStatus.WATCHING
        watch.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await _process_watch_now(watch_id) == 1

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                    .order_by(WatchCandidate.priority)
                )
            ).all()
        )
        attempts = list(
            (
                await session.scalars(
                    select(ReservationAttempt).order_by(ReservationAttempt.started_at)
                )
            ).all()
        )
        assert watch.status is WatchStatus.PAYMENT_REQUIRED
        assert adapter.reservation_candidate_ids == [candidates[0].id, candidates[1].id]
        assert [attempt.candidate_id for attempt in attempts] == [
            candidates[0].id,
            candidates[1].id,
        ]
        assert [attempt.outcome for attempt in attempts] == [
            ReservationOutcome.FAILED,
            ReservationOutcome.PAYMENT_REQUIRED,
        ]
        # A fresh observation can mark the failed candidate seat_found again, but
        # its persisted attempt fence keeps it out of winner selection.
        assert candidates[0].state == "seat_found"
        assert candidates[1].state == "payment_required"

    assert await _process_watch_now(watch_id) == 1
    assert adapter.reserve_calls == 2


async def test_mock_candidate_flow_persists_evidence_and_reserves_only_once(
    client, app, db_engine, monkeypatch
):
    adapter = CountingMockAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    assert created.status_code == 201, created.text
    watch_id = created.json()["id"]
    started = await client.post(f"/api/v1/watches/{watch_id}/start")
    assert started.json()["status"] == "scheduled"

    assert await _process_due_watches() == 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                    .order_by(WatchCandidate.priority)
                )
            ).all()
        )
        observations = list(
            (
                await session.scalars(
                    select(SeatObservation)
                    .join(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                )
            ).all()
        )
        attempts = list(
            (
                await session.scalars(
                    select(ReservationAttempt)
                    .join(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                )
            ).all()
        )
        transitions = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory)
                    .where(WatchTransitionHistory.watch_id == watch_id)
                    .order_by(WatchTransitionHistory.created_at)
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "watch",
                        OutboxEvent.aggregate_id == watch_id,
                    )
                )
            ).all()
        )

        assert watch.status is WatchStatus.PAYMENT_REQUIRED
        assert watch.payment_deadline is not None
        assert watch.reservation_attempted is True
        assert len(observations) >= 1
        assert observations[0].status is SeatObservationStatus.AVAILABLE
        assert len(attempts) == 1
        assert attempts[0].candidate_id == candidates[0].id
        assert attempts[0].outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempts[0].finished_at is not None
        assert candidates[0].state == "payment_required"
        assert candidates[1].state == "suppressed_by_priority"
        assert candidates[1].suppressed_by_candidate_id == candidates[0].id
        transition_targets = {item.to_status for item in transitions}
        assert {
            WatchStatus.WATCHING,
            WatchStatus.SEAT_FOUND,
            WatchStatus.RESERVING,
            WatchStatus.PAYMENT_REQUIRED,
        }.issubset(transition_targets)
        assert any(
            event.event_type == "watch.status_changed"
            and event.payload.get("to") == "payment_required"
            for event in events
        )

    assert adapter.observe_calls >= 1
    assert adapter.reserve_calls == 1

    # Even if a stale scheduler timestamp remains due, PAYMENT_REQUIRED is not eligible
    # for another observation/reservation pass and the unique attempt remains intact.
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        watch.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    observe_calls = adapter.observe_calls
    reserve_calls = adapter.reserve_calls
    assert await _process_due_watches() == 0
    assert adapter.observe_calls == observe_calls
    assert adapter.reserve_calls == reserve_calls
    async with factory() as session:
        assert (await session.scalar(select(func.count()).select_from(ReservationAttempt))) == 1


async def test_notify_only_policy_keeps_monitoring_without_a_reservation_attempt(
    client, app, db_engine, monkeypatch
):
    adapter = CountingMockAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    payload = mock_watch_payload()
    payload["reservation_policy"] = "notify_only"
    created = await client.post("/api/v1/watches", json=payload)
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_due_watches() == 1

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch.status is WatchStatus.SEAT_FOUND
        assert watch.next_check_at is not None
        assert watch.reservation_attempted is False
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0
    assert adapter.reserve_calls == 0


async def test_reservation_rechecks_verified_account_before_creating_attempt(
    app, db_engine, monkeypatch
):
    adapter = CountingMockAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    departure_at = datetime.now(timezone.utc) + timedelta(days=1)
    arrival_at = departure_at + timedelta(hours=2)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="서울",
            origin_node_id="NAT010000",
            destination="부산",
            destination_node_id="NAT014445",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            seat_class="standard",
            passenger_count=1,
            train_numbers=["KTX-001"],
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="missing-verified-account-before-reservation",
            next_check_at=datetime.now(timezone.utc),
        )
        session.add(watch)
        await session.flush()
        candidate = WatchCandidate(
            watch_id=watch.id,
            train_number="KTX-001",
            departure_at=departure_at,
            arrival_at=arrival_at,
            seat_class="standard",
            priority=1,
            state="seat_found",
        )
        session.add(candidate)
        await session.commit()
        target = ObservationTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=Provider.KORAIL,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=departure_at,
            arrival_at=arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            priority=candidate.priority,
        )
        watch_id = watch.id

    await _reserve_winner(adapter, target)

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch.status is WatchStatus.AUTH_REQUIRED
        assert watch.next_check_at is None
        assert watch.reservation_attempted is False
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0
    assert adapter.reserve_calls == 0


async def test_observation_failure_is_normalized_without_aborting_due_cycle(
    client, app, db_engine, monkeypatch
):
    adapter = FailingObservationAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", lambda *args, **kwargs: adapter)
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_due_watches() == 1
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observations = list(
            (
                await session.scalars(
                    select(SeatObservation)
                    .join(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                )
            ).all()
        )
        assert watch.status is WatchStatus.WATCHING
        assert {item.status for item in observations} == {SeatObservationStatus.ERROR}
        assert {item.error_category for item in observations} == {"provider_unavailable"}
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0
    assert adapter.reserve_calls == 0


async def test_circuit_opened_after_observation_blocks_reservation_call(
    client, app, db_engine, monkeypatch
):
    adapter = CircuitOpeningObservationAdapter(app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", lambda *args, **kwargs: adapter)
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_due_watches() == 1
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        circuit = await session.scalar(
            select(ProviderCircuit).where(ProviderCircuit.provider == Provider.MOCK)
        )
        assert circuit.state is ProviderCircuitState.MANUAL_HOLD
        assert watch.status is WatchStatus.AUTH_REQUIRED
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0
    assert adapter.observe_calls == 1
    assert adapter.reserve_calls == 0


async def test_terminal_watch_fences_late_reservation_result(client, app, db_engine, monkeypatch):
    adapter = ExpiringReservationAdapter(app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", lambda *args, **kwargs: adapter)
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_due_watches() == 1
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.scalar(
            select(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
            .order_by(WatchCandidate.priority)
        )
        attempt = await session.scalar(select(ReservationAttempt))
        fenced_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch_id,
                OutboxEvent.event_type == "watch.reservation_result_requires_manual_check",
            )
        )
        assert watch.status is WatchStatus.EXPIRED
        assert watch.payment_deadline is None
        assert candidate.state == "expired"
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.payment_deadline is None
        assert fenced_event.payload["reason"] == "watch_state_changed_during_provider_call"
    assert adapter.reserve_calls == 1


async def test_elapsed_payment_deadline_is_fenced_as_unknown(client, app, db_engine, monkeypatch):
    adapter = ExpiredDeadlineReservationAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", lambda *args, **kwargs: adapter)
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_due_watches() == 1
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        attempt = await session.scalar(select(ReservationAttempt))
        assert watch.status is WatchStatus.WATCHING
        assert watch.payment_deadline is None
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.payment_deadline is None
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "watch.reservation_result_requires_manual_check",
                OutboxEvent.aggregate_id == watch_id,
            )
        )
        assert event.payload["reason"] == "payment_deadline_already_elapsed"


async def test_stale_pending_attempt_recovers_without_second_provider_call(
    client, app, db_engine, monkeypatch
):
    adapter = CountingMockAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", lambda *args, **kwargs: adapter)
    payload = mock_watch_payload()
    payload["train_numbers"] = [payload["train_numbers"][0]]
    payload["candidates"] = [payload["candidates"][0]]
    created = await client.post("/api/v1/watches", json=payload)
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=watching")
    await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=seat_found")
    reserving = await client.post(f"/api/v1/watches/{watch_id}/mock-transition?target=reserving")
    assert reserving.status_code == 200

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    assert await _process_due_watches() == 0
    async with factory() as session:
        fresh_watch = await session.get(Watch, watch_id)
        attempt = await session.scalar(select(ReservationAttempt))
        assert fresh_watch.status is WatchStatus.RESERVING
        assert attempt.outcome is ReservationOutcome.PENDING
        now = datetime.now(timezone.utc)
        attempt.started_at = now - timedelta(minutes=6)
        fresh_watch.next_check_at = now - timedelta(seconds=1)
        await session.commit()

    assert await _process_due_watches() == 1
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        attempt = await session.scalar(select(ReservationAttempt))
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "watch.reservation_attempt_recovery_required",
                OutboxEvent.aggregate_id == watch_id,
            )
        )
        candidate = await session.scalar(select(WatchCandidate))
        assert watch.status is WatchStatus.WATCHING
        assert watch.next_check_at is not None
        assert candidate.state == "observed"
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.finished_at is not None
        assert event.payload["reason"] == "reservation_attempt_result_unknown_after_restart"
    assert adapter.reserve_calls == 0


async def test_unchanged_runs_uses_status_vector_not_observation_timestamp(
    client, app, db_engine, monkeypatch
):
    adapter = VaryingTimestampSoldOutAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module, "get_execution_provider", lambda provider, *args, **kwargs: adapter
    )
    created = await client.post("/api/v1/watches", json=mock_watch_payload(seat_class="first"))
    assert created.status_code == 201, created.text
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    assert await _process_due_watches() == 1
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        first_cycle = await session.get(Watch, watch_id)
        assert first_cycle.status is WatchStatus.WATCHING
        assert first_cycle.unchanged_runs == 0
        first_cycle.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await _process_due_watches() == 1
    async with factory() as session:
        second_cycle = await session.get(Watch, watch_id)
        observations = list(
            (
                await session.scalars(
                    select(SeatObservation)
                    .join(WatchCandidate)
                    .where(WatchCandidate.watch_id == watch_id)
                    .order_by(SeatObservation.observed_at)
                )
            ).all()
        )
        assert second_cycle.status is WatchStatus.WATCHING
        assert second_cycle.unchanged_runs == 1
        assert len(observations) == 4
        assert {item.status for item in observations} == {SeatObservationStatus.SOLD_OUT}
        assert len({item.observed_at for item in observations}) > 1
    assert adapter.observe_calls == 4
    assert adapter.reserve_calls == 0


async def test_same_condition_watches_merge_observation_calls_per_candidate(
    client, app, db_engine, monkeypatch
):
    adapter = VaryingTimestampSoldOutAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module, "get_execution_provider", lambda provider, *args, **kwargs: adapter
    )
    watch_ids: list[str] = []
    for _ in range(2):
        created = await client.post("/api/v1/watches", json=mock_watch_payload(seat_class="first"))
        assert created.status_code == 201, created.text
        watch_ids.append(created.json()["id"])
        await client.post(f"/api/v1/watches/{watch_ids[-1]}/start")

    # One dedupe group, two distinct candidate conditions, and two watch consumers.
    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 2
    assert adapter.reserve_calls == 0

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watches = list((await session.scalars(select(Watch).where(Watch.id.in_(watch_ids)))).all())
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id.in_(watch_ids))
        )
        assert {watch.status for watch in watches} == {WatchStatus.WATCHING}
        assert observation_count == 4


async def test_disabled_official_providers_never_become_due(client, app, db_engine, monkeypatch):
    adapter = DisabledExecutionAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    monkeypatch.setattr(
        transition_runtime_module,
        "get_execution_provider",
        lambda provider: adapter,
    )
    watch_ids: list[str] = []
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for provider in ("korail", "srt"):
        payload = watch_payload(provider)
        travel_date = str(payload["travel_date"])
        departure_at = datetime.fromisoformat(f"{travel_date}T08:30:00+09:00")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        async with factory() as session:
            evidence = TimetableSeatEvidence(
                evidence_hash=(provider + "0" * 64)[:64],
                provider=Provider(provider),
                origin_node_id="N1",
                destination_node_id="N2",
                canonical_train_number=f"{provider.upper()}-001",
                departure_at=departure_at.astimezone(timezone.utc),
                passenger_count=1,
                seat_class=SeatClass.STANDARD,
                status=SeatObservationStatus.SOLD_OUT,
                provenance_kind="official_provider",
                source="worker-policy-test",
                observed_at=now,
                registration_allowed=True,
                created_at=now,
                registration_valid_until=now + timedelta(minutes=5),
            )
            session.add(evidence)
            await session.commit()
        payload["train_numbers"] = [f"{provider.upper()}-001"]
        payload["candidates"] = [
            {
                "train_number": f"{provider.upper()}-001",
                "departure_at": f"{travel_date}T08:30:00+09:00",
                "arrival_at": f"{travel_date}T10:30:00+09:00",
                "seat_class": "standard",
                "priority": 1,
                "registration_evidence_id": evidence.id,
            }
        ]
        created = await client.post("/api/v1/watches", json=payload)
        assert created.status_code == 201, created.text
        watch_ids.append(created.json()["id"])
        await client.post(f"/api/v1/watches/{watch_ids[-1]}/start")

    assert await _process_due_watches() == 0
    assert adapter.observe_calls == 0
    assert adapter.reserve_calls == 0
    async with factory() as session:
        persisted = list(
            (await session.scalars(select(Watch).where(Watch.id.in_(watch_ids)))).all()
        )
        assert {watch.status for watch in persisted} == {WatchStatus.SCHEDULED}
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0


async def test_existing_srt_watch_is_armed_observed_and_releases_execution_lease(
    client, app, db_engine, monkeypatch
):
    adapter = VaryingTimestampSoldOutAdapter(Provider.SRT)
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    payload = watch_payload("srt")
    travel_date = str(payload["travel_date"])
    departure_at = datetime.fromisoformat(f"{travel_date}T08:30:00+09:00")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        evidence = TimetableSeatEvidence(
            evidence_hash="srt-worker-execution".ljust(64, "0"),
            provider=Provider.SRT,
            origin_node_id="N1",
            destination_node_id="N2",
            canonical_train_number="SRT-001",
            departure_at=departure_at.astimezone(timezone.utc),
            passenger_count=1,
            seat_class=SeatClass.STANDARD,
            status=SeatObservationStatus.SOLD_OUT,
            provenance_kind="official_provider",
            source="worker-execution-test",
            observed_at=now,
            registration_allowed=True,
            created_at=now,
            registration_valid_until=now + timedelta(minutes=5),
        )
        session.add(evidence)
        await session.commit()

    payload["train_numbers"] = ["SRT-001"]
    payload["candidates"] = [
        {
            "train_number": "SRT-001",
            "departure_at": f"{travel_date}T08:30:00+09:00",
            "arrival_at": f"{travel_date}T10:30:00+09:00",
            "seat_class": "standard",
            "priority": 1,
            "registration_evidence_id": evidence.id,
        }
    ]
    created = await client.post("/api/v1/watches", json=payload)
    assert created.status_code == 201, created.text
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    # Simulate a watch created before approved SRT execution was enabled.
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        watch.next_check_at = None
        await session.commit()

    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 1
    assert adapter.reserve_calls == 0
    assert adapter.observation_requests[0].origin == "서울"
    assert adapter.observation_requests[0].destination == "부산"
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observation = await session.scalar(
            select(SeatObservation).join(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        lease = await session.get(
            ProviderExecutionLease,
            (Provider.SRT, "anonymous/public"),
        )
        assert watch.status is WatchStatus.WATCHING
        assert watch.next_check_at is not None
        assert observation.status is SeatObservationStatus.SOLD_OUT
        assert lease.fencing_token == 1
        assert lease.owner_token is None
        assert lease.expires_at is None


async def test_existing_korail_watch_with_null_next_check_is_armed_only_when_enabled(
    app, db_engine, monkeypatch
):
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    now = datetime.now(timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        enabled_watch = Watch(
            provider=Provider.KORAIL,
            origin="서울",
            origin_node_id="NAT010000",
            destination="부산",
            destination_node_id="NAT014445",
            travel_date=date.today() + timedelta(days=7),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="existing-korail-enabled",
            next_check_at=None,
        )
        disabled_watch = Watch(
            provider=Provider.KORAIL,
            origin="서울",
            origin_node_id="NAT010000",
            destination="부산",
            destination_node_id="NAT014445",
            travel_date=date.today() + timedelta(days=8),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="existing-korail-disabled",
            next_check_at=None,
        )
        session.add_all([enabled_watch, disabled_watch])
        await session.commit()
        enabled_watch_id = enabled_watch.id
        disabled_watch_id = disabled_watch.id

    enabled_adapter = TaskScopedSoldOutAdapter(Provider.KORAIL)
    assert (
        await _arm_supported_provider_watches(
            Provider.KORAIL,
            now,
            adapter=enabled_adapter,
        )
        == 2
    )
    async with factory() as session:
        first = await session.get(Watch, enabled_watch_id)
        second = await session.get(Watch, disabled_watch_id)
        assert first.next_check_at.replace(tzinfo=timezone.utc) == now
        assert second.next_check_at.replace(tzinfo=timezone.utc) == now
        second.next_check_at = None
        await session.commit()

    disabled_adapter = DisabledTaskScopedAdapter(Provider.KORAIL)
    assert (
        await _arm_supported_provider_watches(
            Provider.KORAIL,
            now + timedelta(minutes=1),
            adapter=disabled_adapter,
        )
        == 0
    )
    async with factory() as session:
        disabled = await session.get(Watch, disabled_watch_id)
        assert disabled.next_check_at is None
    assert enabled_adapter.observe_calls == 0
    assert disabled_adapter.observe_calls == 0


async def test_lost_srt_execution_lease_fences_observation_persistence(app, db_engine, monkeypatch):
    adapter = VaryingTimestampSoldOutAdapter(Provider.SRT)
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    now = datetime.now(timezone.utc)
    grant = ExecutionLeaseGrant(
        provider=Provider.SRT,
        account_scope="anonymous/public",
        owner_token="worker-a",
        fencing_token=1,
        expires_at=now + timedelta(minutes=5),
    )

    class LosingLeaseService:
        def __init__(self) -> None:
            self.current_checks = 0
            self.released = False

        async def is_current(self, current_grant, *, now):
            assert current_grant == grant
            self.current_checks += 1
            return self.current_checks < 3

        async def release(self, current_grant, *, now):
            assert current_grant == grant
            self.released = True
            return False

    lease_service = LosingLeaseService()

    async def acquire_execution_lease(provider, acquired_at):
        assert provider is Provider.SRT
        return lease_service, grant

    monkeypatch.setattr(
        worker_module,
        "_acquire_execution_lease",
        acquire_execution_lease,
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="서울",
            origin_node_id="N1",
            destination="부산",
            destination_node_id="N2",
            travel_date=(date.today() + timedelta(days=7)),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="lost-srt-execution-lease",
            next_check_at=now - timedelta(seconds=1),
        )
        session.add(watch)
        session.add(
            ProviderExecutionLease(
                provider=Provider.SRT,
                account_scope=grant.account_scope,
                owner_token=grant.owner_token,
                fencing_token=grant.fencing_token,
                expires_at=grant.expires_at,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            WatchCandidate(
                watch_id=watch.id,
                train_number="SRT-001",
                departure_at=now + timedelta(days=7),
                arrival_at=now + timedelta(days=7, hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="active",
            )
        )
        await session.commit()
        watch_id = watch.id

    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 1
    assert lease_service.current_checks == 3
    assert lease_service.released is True
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
        )
        assert watch.status is WatchStatus.WATCHING
        assert observation_count == 0


async def test_due_task_reuses_one_srt_adapter_across_dedupe_groups(app, db_engine, monkeypatch):
    adapter = TaskScopedSoldOutAdapter(Provider.SRT)
    provider_requests: list[Provider] = []

    def execution_provider(provider, *args, **kwargs):
        provider_requests.append(provider)
        assert provider is Provider.SRT
        return adapter

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", execution_provider)
    now = datetime.now(timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    watch_ids: list[str] = []
    async with factory() as session:
        for index in range(2):
            watch = Watch(
                provider=Provider.SRT,
                origin="수서",
                origin_node_id="N1",
                destination="부산",
                destination_node_id="N2",
                travel_date=date.today() + timedelta(days=7),
                time_from=time(8),
                time_to=time(12),
                passenger_count=1,
                status=WatchStatus.SCHEDULED,
                mode="official",
                dedupe_key=f"task-scoped-srt-{index}",
                next_check_at=now - timedelta(seconds=1),
            )
            session.add(watch)
            await session.flush()
            session.add(
                WatchCandidate(
                    watch_id=watch.id,
                    train_number=f"SRT-{index + 1:03d}",
                    departure_at=now + timedelta(days=7, minutes=index),
                    arrival_at=now + timedelta(days=7, hours=2, minutes=index),
                    seat_class=SeatClass.STANDARD,
                    priority=1,
                    state="active",
                )
            )
            watch_ids.append(watch.id)
        await session.commit()

    assert await _process_due_watches() == 2
    assert provider_requests == [Provider.SRT]
    assert adapter.observe_calls == 2
    assert adapter.drain_calls == 2
    assert adapter.close_calls == 1

    async with factory() as session:
        watches = list((await session.scalars(select(Watch).where(Watch.id.in_(watch_ids)))).all())
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id.in_(watch_ids))
        )
        lease = await session.get(
            ProviderExecutionLease,
            (Provider.SRT, "anonymous/public"),
        )
        assert {watch.status for watch in watches} == {WatchStatus.WATCHING}
        assert observation_count == 2
        assert lease is not None
        assert lease.fencing_token == 2
        assert lease.owner_token is None
        assert lease.expires_at is None


async def test_task_scoped_adapter_drains_before_lease_release_and_closes_afterward(
    app, db_engine, monkeypatch
):
    lifecycle_events: list[str] = []
    adapter = TaskScopedSoldOutAdapter(
        Provider.SRT,
        lifecycle_events=lifecycle_events,
    )
    now = datetime.now(timezone.utc)
    grant = ExecutionLeaseGrant(
        provider=Provider.SRT,
        account_scope="anonymous/public",
        owner_token="task-scoped-worker",
        fencing_token=1,
        expires_at=now + timedelta(minutes=5),
    )

    class OrderedLeaseService:
        async def is_current(self, current_grant, *, now):
            assert current_grant == grant
            return True

        async def release(self, current_grant, *, now):
            assert current_grant == grant
            lifecycle_events.append("release")
            return True

    async def acquire_execution_lease(provider, acquired_at):
        assert provider is Provider.SRT
        return OrderedLeaseService(), grant

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
    )
    monkeypatch.setattr(
        worker_module,
        "_acquire_execution_lease",
        acquire_execution_lease,
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="N1",
            destination="부산",
            destination_node_id="N2",
            travel_date=date.today() + timedelta(days=7),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="ordered-task-scoped-cleanup",
            next_check_at=now - timedelta(seconds=1),
        )
        session.add(watch)
        await session.flush()
        session.add(
            WatchCandidate(
                watch_id=watch.id,
                train_number="SRT-ORDERED",
                departure_at=now + timedelta(days=7),
                arrival_at=now + timedelta(days=7, hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="active",
            )
        )
        await session.commit()

    assert await _process_due_watches() == 1
    assert lifecycle_events == ["drain", "release", "close"]


async def test_adapter_cleanup_error_does_not_skip_other_cleanup_or_lease_release(
    app, db_engine, monkeypatch
):
    srt_adapter = TaskScopedSoldOutAdapter(Provider.SRT, fail_close=True)
    mock_adapter = TaskScopedSoldOutAdapter(Provider.MOCK)
    warning_messages: list[str] = []

    def execution_provider(provider, *args, **kwargs):
        return {
            Provider.SRT: srt_adapter,
            Provider.MOCK: mock_adapter,
        }[provider]

    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", execution_provider)
    monkeypatch.setattr(
        worker_module.LOGGER,
        "warning",
        lambda message, *args: warning_messages.append(message % args),
    )
    now = datetime.now(timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    watch_ids: list[str] = []
    async with factory() as session:
        for provider in (Provider.SRT, Provider.MOCK):
            watch = Watch(
                provider=provider,
                origin="수서" if provider is Provider.SRT else "서울",
                origin_node_id="N1" if provider is Provider.SRT else "MOCK-SEOUL",
                destination="부산",
                destination_node_id="N2" if provider is Provider.SRT else "MOCK-BUSAN",
                travel_date=date.today() + timedelta(days=7),
                time_from=time(8),
                time_to=time(12),
                passenger_count=1,
                status=WatchStatus.SCHEDULED,
                mode="official",
                dedupe_key=f"cleanup-{provider.value}",
                next_check_at=now - timedelta(seconds=1),
            )
            session.add(watch)
            await session.flush()
            session.add(
                WatchCandidate(
                    watch_id=watch.id,
                    train_number=f"{provider.value.upper()}-001",
                    departure_at=now + timedelta(days=7),
                    arrival_at=now + timedelta(days=7, hours=2),
                    seat_class=SeatClass.STANDARD,
                    priority=1,
                    state="active",
                )
            )
            watch_ids.append(watch.id)
        await session.commit()

    assert await _process_due_watches() == 2
    assert srt_adapter.observe_calls == 1
    assert mock_adapter.observe_calls == 1
    assert srt_adapter.drain_calls == 1
    assert mock_adapter.drain_calls == 1
    assert srt_adapter.close_calls == 1
    assert mock_adapter.close_calls == 1
    assert warning_messages == ["execution adapter cleanup failed provider=srt"]
    assert all("synthetic cleanup failure" not in message for message in warning_messages)

    async with factory() as session:
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id.in_(watch_ids))
        )
        lease = await session.get(
            ProviderExecutionLease,
            (Provider.SRT, "anonymous/public"),
        )
        assert observation_count == 2
        assert lease is not None
        assert lease.owner_token is None
        assert lease.expires_at is None


async def test_srt_source_cooldown_defers_due_watch_without_error_observation(
    app, db_engine, monkeypatch
):
    now = datetime.now(timezone.utc)
    deferred_until = now + timedelta(minutes=5)
    adapter = DeferredObservationAdapter(deferred_until)
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="서울",
            origin_node_id="N1",
            destination="부산",
            destination_node_id="N2",
            travel_date=date.today() + timedelta(days=7),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="deferred-srt-source-cooldown",
            next_check_at=now - timedelta(seconds=1),
        )
        session.add(watch)
        await session.flush()
        session.add(
            WatchCandidate(
                watch_id=watch.id,
                train_number="SRT-001",
                departure_at=now + timedelta(days=7),
                arrival_at=now + timedelta(days=7, hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="active",
            )
        )
        await session.commit()
        watch_id = watch.id

    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 0
    assert adapter.close_calls == 1
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
        )
        assert watch.status is WatchStatus.SCHEDULED
        assert watch.cooldown_until.replace(tzinfo=timezone.utc) == deferred_until
        assert watch.next_check_at.replace(tzinfo=timezone.utc) == deferred_until
        assert observation_count == 0
        lease = await session.get(
            ProviderExecutionLease,
            (Provider.SRT, "anonymous/public"),
        )
        assert lease is not None
        assert lease.fencing_token >= 1
        assert lease.owner_token is None
        assert lease.expires_at is None

    adapter.deferred_until = None
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        watch.next_check_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 1
    assert adapter.close_calls == 2
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
        )
        assert watch.cooldown_until is None
        observation = await session.scalar(
            select(SeatObservation).join(WatchCandidate).where(WatchCandidate.watch_id == watch_id)
        )
        assert watch.status is WatchStatus.SEAT_FOUND
        assert observation_count == 1
        assert observation.status is SeatObservationStatus.AVAILABLE


@pytest.mark.parametrize(
    ("stored_owner", "stored_fencing_token", "stored_expiry", "grant_owner"),
    [
        ("new-worker", 1, timedelta(minutes=5), "old-worker"),
        ("same-worker", 2, timedelta(minutes=5), "same-worker"),
        ("same-worker", 1, timedelta(seconds=-1), "same-worker"),
    ],
)
async def test_invalid_srt_lease_cannot_write_source_cooldown_deferral(
    app,
    db_engine,
    monkeypatch,
    stored_owner,
    stored_fencing_token,
    stored_expiry,
    grant_owner,
):
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    now = datetime.now(timezone.utc)
    original_next_check = now - timedelta(seconds=1)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="서울",
            origin_node_id="N1",
            destination="부산",
            destination_node_id="N2",
            travel_date=date.today() + timedelta(days=7),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="stale-srt-deferral-lease",
            next_check_at=original_next_check,
        )
        session.add(watch)
        session.add(
            ProviderExecutionLease(
                provider=Provider.SRT,
                account_scope="anonymous/public",
                owner_token=stored_owner,
                fencing_token=stored_fencing_token,
                expires_at=now + stored_expiry,
                updated_at=now,
            )
        )
        await session.commit()
        watch_id = watch.id

    stale_grant = ExecutionLeaseGrant(
        provider=Provider.SRT,
        account_scope="anonymous/public",
        owner_token=grant_owner,
        fencing_token=1,
        expires_at=now + timedelta(minutes=1),
    )
    await defer_watch_group_observation(
        [watch_id],
        now + timedelta(minutes=10),
        now,
        lease_grant=stale_grant,
        prepared=False,
        dependencies=_observation_dependencies(app.state.test_session_factory),
    )

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch.cooldown_until is None
        assert watch.next_check_at.replace(tzinfo=timezone.utc) == original_next_check


async def test_srt_cooldown_opened_after_preflight_defers_without_persisting_error(
    app, db_engine, monkeypatch
):
    adapter = CooldownOpeningObservationAdapter(None)
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    now = datetime.now(timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.SRT,
            origin="서울",
            origin_node_id="N1",
            destination="부산",
            destination_node_id="N2",
            travel_date=date.today() + timedelta(days=7),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            status=WatchStatus.SCHEDULED,
            mode="official",
            dedupe_key="srt-cooldown-opened-after-preflight",
            next_check_at=now - timedelta(seconds=1),
        )
        session.add(watch)
        await session.flush()
        session.add(
            WatchCandidate(
                watch_id=watch.id,
                train_number="SRT-001",
                departure_at=now + timedelta(days=7),
                arrival_at=now + timedelta(days=7, hours=2),
                seat_class=SeatClass.STANDARD,
                priority=1,
                state="active",
            )
        )
        await session.commit()
        watch_id = watch.id

    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 1
    assert adapter.close_calls == 1
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        observation_count = await session.scalar(
            select(func.count())
            .select_from(SeatObservation)
            .join(WatchCandidate)
            .where(WatchCandidate.watch_id == watch_id)
        )
        assert watch.status is WatchStatus.WATCHING
        assert watch.cooldown_until is not None
        assert watch.next_check_at == watch.cooldown_until
        assert observation_count == 0


@pytest.mark.parametrize(
    ("circuit_state", "expected_watch_status"),
    [
        (ProviderCircuitState.MANUAL_HOLD, WatchStatus.AUTH_REQUIRED),
        (ProviderCircuitState.HALF_OPEN, WatchStatus.AUTH_REQUIRED),
        (ProviderCircuitState.OPEN, WatchStatus.COOLDOWN),
    ],
)
async def test_provider_circuit_blocks_mock_adapter_calls(
    circuit_state,
    expected_watch_status,
    client,
    app,
    db_engine,
    monkeypatch,
):
    adapter = CountingMockAdapter()
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(
        worker_module,
        "get_execution_provider",
        lambda provider, *args, **kwargs: adapter,
        raising=False,
    )
    created = await client.post("/api/v1/watches", json=mock_watch_payload())
    assert created.status_code == 201, created.text
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")

    now = datetime.now(timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ProviderCircuit(
                provider=Provider.MOCK,
                state=circuit_state,
                reason="test_provider_guard",
                opened_at=now,
                cooldown_until=(now + timedelta(minutes=10))
                if circuit_state is ProviderCircuitState.OPEN
                else None,
                manual_resume_required=circuit_state
                in {ProviderCircuitState.MANUAL_HOLD, ProviderCircuitState.HALF_OPEN},
            )
        )
        await session.commit()

    assert await _process_due_watches() == 1
    assert adapter.observe_calls == 0
    assert adapter.reserve_calls == 0
    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch.status is expected_watch_status
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0


async def test_elapsed_active_watches_expire_without_provider_work(app, db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    elapsed_date = date(2000, 1, 1)
    active_statuses = {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.RESERVING,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.PAUSED,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
    }
    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="elapsed-watch",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
        )
        session.add(channel)
        await session.flush()
        watches = [
            Watch(
                provider=Provider.KORAIL if index % 2 == 0 else Provider.MOCK,
                origin="서울",
                destination="부산",
                travel_date=elapsed_date,
                time_from=time(8),
                time_to=time(12),
                status=status,
                mode="official" if index % 2 == 0 else "mock",
                dedupe_key=f"elapsed-{status.value}",
                next_check_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                notification_channel_ids=[channel.id],
            )
            for index, status in enumerate(active_statuses)
        ]
        draft = Watch(
            provider=Provider.MOCK,
            origin="서울",
            destination="부산",
            travel_date=elapsed_date,
            time_from=time(8),
            time_to=time(12),
            status=WatchStatus.DRAFT,
            mode="mock",
            dedupe_key="elapsed-draft",
            next_check_at=None,
        )
        session.add_all([*watches, draft])
        await session.commit()
        watch_ids = [watch.id for watch in watches]
        draft_id = draft.id

    monkeypatch.setattr("rail_waitlist.worker.SessionFactory", app.state.test_session_factory)
    assert await _process_due_watches() == 0

    async with factory() as session:
        persisted = list(
            (
                await session.scalars(
                    select(Watch).where(Watch.id.in_(watch_ids)).order_by(Watch.id)
                )
            ).all()
        )
        assert {watch.status for watch in persisted} == {WatchStatus.EXPIRED}
        assert all(watch.next_check_at is None for watch in persisted)
        assert (await session.get(Watch, draft_id)).status == WatchStatus.DRAFT

        status_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "watch",
                        OutboxEvent.event_type == "watch.status_changed",
                    )
                )
            ).all()
        )
        assert len(status_events) == len(active_statuses)
        assert {event.payload["to"] for event in status_events} == {"expired"}

        notifications = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notification.dispatch_requested"
                    )
                )
            ).all()
        )
        assert len(notifications) == len(active_statuses)
        assert {event.payload["status"] for event in notifications} == {"expired"}
