from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from rail_waitlist.domain import Provider, ReservationOutcome, ReservationPolicy, WatchStatus
from rail_waitlist.models import (
    OutboxEvent,
    RailProviderAccount,
    ReservationAttempt,
    Watch,
    WatchCandidate,
)
from rail_waitlist.provider_call_context import current_request_id
from rail_waitlist.provider_execution_lease import ExecutionLeaseGrant
from rail_waitlist.providers import ProviderUnavailable
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)
from rail_waitlist.reservations.reconciliation_application import (
    ReconciliationDependencies,
    reconcile_reservation_attempt,
)
from rail_waitlist.reservations.reconciliation_state_application import (
    ReservationReconciliationStateDependencies,
)
from rail_waitlist.reservations.reconciliation_state_runtime import (
    reservation_reconciliation_state_dependencies,
)
from rail_waitlist.schemas import ProviderCapabilities
from rail_waitlist.security import secret_box
from rail_waitlist.services import apply_reservation_reconciliation

NOW = datetime(2026, 8, 5, 6, tzinfo=timezone.utc)
LEASE_GRANT = ExecutionLeaseGrant(
    provider=Provider.SRT,
    account_scope="test-account",
    owner_token="test-owner",
    fencing_token=7,
    expires_at=NOW + timedelta(minutes=2),
)


class RecordingLeaseService:
    def __init__(self, events: list[str], *, current: bool = True) -> None:
        self.events = events
        self.current = current

    async def is_current(self, _grant: ExecutionLeaseGrant, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        self.events.append("lease-current")
        return self.current

    async def release(self, _grant: ExecutionLeaseGrant, *, now: datetime) -> bool:
        assert now.tzinfo is not None
        self.events.append("release")
        return True


class RecordingAdapter:
    provider = Provider.SRT

    def __init__(
        self,
        events: list[str],
        *,
        reservation_once: bool,
        result: ReservationConfirmationResult,
        on_confirm: Callable[[ReservationConfirmationTarget], Awaitable[None]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.reservation_once = reservation_once
        self.result = result
        self.on_confirm = on_confirm
        self.error = error
        self.targets: list[ReservationConfirmationTarget] = []
        self.request_ids: list[str | None] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=self.reservation_once,
        )

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        self.events.append("confirm")
        self.targets.append(target)
        self.request_ids.append(current_request_id())
        if self.on_confirm is not None:
            await self.on_confirm(target)
        if self.error is not None:
            raise self.error
        return self.result


async def _seed_due_attempt(session_factory, *, credential_version: int = 3) -> str:
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext="test-ciphertext",
                enabled=True,
                credential_version=credential_version,
                last_auth_status="authenticated",
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            destination="부산",
            travel_date=date(2026, 8, 5),
            time_from=time(12),
            time_to=time(18),
            seat_class="standard",
            passenger_count=1,
            train_numbers=["301"],
            notification_channel_ids=[],
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key=f"reconciliation-application-{credential_version}",
            reservation_attempted=True,
        )
        candidate = WatchCandidate(
            train_number="301",
            departure_at=NOW + timedelta(hours=6),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key=f"reserve:reconciliation-application-{credential_version}",
            started_at=NOW - timedelta(minutes=2),
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=credential_version,
            finished_at=NOW - timedelta(minutes=1),
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        return attempt.id


async def _seed_due_payment_hold(session_factory, *, credential_version: int = 3) -> str:
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext="test-payment-hold-ciphertext",
                enabled=True,
                credential_version=credential_version,
                last_auth_status="authenticated",
            )
        )
        deadline = NOW + timedelta(minutes=30)
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            destination="부산",
            travel_date=date(2026, 8, 5),
            time_from=time(12),
            time_to=time(18),
            seat_class="standard",
            passenger_count=1,
            train_numbers=["301"],
            notification_channel_ids=[],
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.PAYMENT_REQUIRED,
            payment_deadline=deadline,
            official_booking_url="https://etk.srail.kr/hpg/hra/02/selectReservationList.do",
            dedupe_key=f"payment-follow-up-{credential_version}",
            reservation_attempted=True,
        )
        candidate = WatchCandidate(
            train_number="301",
            departure_at=NOW + timedelta(hours=6),
            arrival_at=NOW + timedelta(hours=8),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:payment-hold",
            idempotency_key=f"reserve:payment-follow-up-{credential_version}",
            started_at=NOW - timedelta(minutes=2),
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=deadline,
            official_handoff_url=watch.official_booking_url,
            credential_version=credential_version,
            confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=NOW - timedelta(minutes=1),
            last_reconciled_at=NOW - timedelta(minutes=1),
            reconciliation_attempt_count=1,
            next_reconcile_at=NOW - timedelta(seconds=1),
            finished_at=NOW - timedelta(minutes=1),
            reserved_seats=[{"car_number": "4", "seat_number": "8A"}],
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        return attempt.id


def _dependencies(
    session_factory,
    adapter: RecordingAdapter,
    lease_service: RecordingLeaseService,
    events: list[str],
    *,
    circuit_closed: bool = True,
    lease_granted: bool = True,
    locked_current: bool | tuple[bool, ...] = True,
    apply_reconciliation: Callable[..., Awaitable[None]] | None = None,
    state_dependencies: ReservationReconciliationStateDependencies | None = None,
    now: Callable[[], datetime] | None = None,
) -> ReconciliationDependencies:
    locked_results = locked_current if isinstance(locked_current, tuple) else (locked_current,)
    locked_check_count = 0

    async def acquire_execution_lease(_provider: Provider, now: datetime):
        assert now.tzinfo is not None
        events.append("acquire")
        return lease_service, LEASE_GRANT if lease_granted else None

    def get_execution_provider(_provider: Provider):
        events.append("get-adapter")
        return adapter

    async def drain_execution_adapter(_adapter, _provider: Provider) -> None:
        assert _adapter is adapter
        events.append("drain")

    async def close_execution_adapter(_adapter, _provider: Provider) -> None:
        assert _adapter is adapter
        events.append("close")

    async def provider_circuit_is_closed(_provider: Provider) -> bool:
        events.append("circuit")
        return circuit_closed

    async def lease_is_current_in_session(
        _session,
        grant: ExecutionLeaseGrant,
        *,
        now: datetime,
    ) -> bool:
        nonlocal locked_check_count
        assert grant is LEASE_GRANT
        assert now.tzinfo is not None
        locked_check_count += 1
        events.append(f"lease-current-locked:{locked_check_count}")
        return locked_results[min(locked_check_count - 1, len(locked_results) - 1)]

    async def fail_if_applied(*_args, **_kwargs) -> None:
        raise AssertionError("reconciliation state must not be applied")

    return ReconciliationDependencies(
        session_factory=session_factory,
        acquire_execution_lease=acquire_execution_lease,
        get_execution_provider=get_execution_provider,
        drain_execution_adapter=drain_execution_adapter,
        close_execution_adapter=close_execution_adapter,
        provider_circuit_is_closed=provider_circuit_is_closed,
        lease_is_current_in_session=lease_is_current_in_session,
        state_dependencies=state_dependencies,
        apply_reconciliation=apply_reconciliation or fail_if_applied,
        now=now or (lambda: NOW),
    )


async def _attempt_state(session_factory, attempt_id: str) -> tuple[ReservationOutcome, int, int]:
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxEvent))
        return attempt.outcome, attempt.reconciliation_attempt_count, outbox_count or 0


async def _add_legacy_exact_paid_fence(session_factory, source_attempt_id: str) -> None:
    async with session_factory() as session:
        source_attempt = await session.get(ReservationAttempt, source_attempt_id)
        assert source_attempt is not None
        source_candidate = await session.get(WatchCandidate, source_attempt.candidate_id)
        assert source_candidate is not None
        paid_candidate = WatchCandidate(
            watch_id=source_candidate.watch_id,
            train_number="303",
            departure_at=NOW + timedelta(hours=6, minutes=10),
            scheduled_departure_at=NOW + timedelta(hours=6, minutes=10),
            seat_class="standard",
            priority=2,
            state="expired",
        )
        session.add(
            ReservationAttempt(
                candidate=paid_candidate,
                attempt_sequence=1,
                episode_key="availability:legacy-paid-fence",
                idempotency_key=f"legacy-paid-fence:{source_attempt_id}",
                started_at=NOW - timedelta(minutes=3),
                finished_at=NOW - timedelta(minutes=2),
                outcome=ReservationOutcome.RESERVED,
                confirmation_outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
                confirmation_source="official-reservation-list",
                confirmation_observed_at=NOW - timedelta(minutes=2),
            )
        )
        await session.commit()


async def test_watch_wide_exact_paid_fence_stops_other_candidate_before_provider_read(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    await _add_legacy_exact_paid_fence(session_factory, attempt_id)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source="must-not-be-read",
            observed_at=NOW,
        ),
    )

    applied = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            RecordingLeaseService(events),
            events,
        ),
        adapter=adapter,
    )

    assert applied == 0
    assert adapter.targets == []
    assert events == []
    assert await _attempt_state(session_factory, attempt_id) == (
        ReservationOutcome.UNKNOWN,
        0,
        0,
    )


async def test_paid_fence_inserted_during_confirmation_blocks_locked_state_write(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)

    async def add_paid_fence(_target: ReservationConfirmationTarget) -> None:
        await _add_legacy_exact_paid_fence(session_factory, attempt_id)

    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source="raced-confirmation",
            observed_at=NOW,
        ),
        on_confirm=add_paid_fence,
    )

    applied = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            RecordingLeaseService(events),
            events,
        ),
        adapter=adapter,
    )

    assert applied == 0
    assert len(adapter.targets) == 1
    assert await _attempt_state(session_factory, attempt_id) == (
        ReservationOutcome.UNKNOWN,
        0,
        0,
    )


async def test_auth_demotion_commits_when_watch_is_deleted_during_reconciliation_io(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    async with session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        assert account is not None
        account.credentials_ciphertext = secret_box.encrypt_dict(
            {
                "login_method": "membership_number",
                "login_id": "0987654321",
                "password": "test-password",
            }
        )
        await session.commit()

    async def delete_watch(_target: ReservationConfirmationTarget) -> None:
        async with session_factory() as session:
            attempt = await session.get(ReservationAttempt, attempt_id)
            assert attempt is not None
            candidate = await session.get(WatchCandidate, attempt.candidate_id)
            assert candidate is not None
            watch = await session.get(Watch, candidate.watch_id)
            assert watch is not None
            await session.execute(
                delete(ReservationAttempt).where(ReservationAttempt.candidate_id == candidate.id)
            )
            await session.execute(delete(WatchCandidate).where(WatchCandidate.watch_id == watch.id))
            await session.execute(delete(Watch).where(Watch.id == watch.id))
            await session.commit()

    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source="deleted-owner-auth-evidence",
            observed_at=NOW,
        ),
        on_confirm=delete_watch,
    )

    applied = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            RecordingLeaseService(events),
            events,
            state_dependencies=reservation_reconciliation_state_dependencies(),
        ),
        adapter=adapter,
    )

    assert applied == 0
    assert len(adapter.targets) == 1
    async with session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        assert account is not None
        assert account.credential_version == 3
        assert account.last_auth_status == "auth_required"
        assert await session.get(ReservationAttempt, attempt_id) is None
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


async def test_due_unknown_not_found_runs_one_scheduled_confirmation_then_stops(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        attempt.confirmation_outcome = ReservationConfirmationOutcome.NOT_FOUND
        attempt.confirmation_source = "test-unknown-not-found"
        attempt.confirmation_observed_at = NOW - timedelta(seconds=31)
        attempt.last_reconciled_at = NOW - timedelta(seconds=31)
        attempt.reconciliation_attempt_count = 3
        attempt.next_reconcile_at = NOW - timedelta(seconds=1)
        await session.commit()

    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="test-unknown-not-found",
            observed_at=NOW,
        ),
    )
    dependencies = _dependencies(
        session_factory,
        adapter,
        RecordingLeaseService(events),
        events,
        apply_reconciliation=apply_reservation_reconciliation,
    )

    assert (
        await reconcile_reservation_attempt(
            attempt_id,
            dependencies=dependencies,
            adapter=adapter,
        )
        == 1
    )
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
        assert attempt.reconciliation_attempt_count == 4
        assert attempt.next_reconcile_at is None

    assert (
        await reconcile_reservation_attempt(
            attempt_id,
            dependencies=dependencies,
            adapter=adapter,
        )
        == 0
    )
    assert len(adapter.targets) == 1


async def test_uncorrelated_unknown_follow_up_rejects_positive_provider_result(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="unsafe-positive-fixture",
            observed_at=NOW,
            official_handoff_url=(
                "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
            ),
        ),
    )

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            RecordingLeaseService(events),
            events,
            apply_reconciliation=apply_reservation_reconciliation,
        ),
        adapter=adapter,
    )

    assert reconciled == 1
    assert len(adapter.targets) == 1
    assert adapter.targets[0].purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
    assert adapter.targets[0].confirmation_correlation_seats == ()
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
        assert (
            attempt.confirmation_diagnostic_code
            is ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT
        )
        assert attempt.reserved_seats == []
        watch = await session.scalar(
            select(Watch).join(WatchCandidate).where(WatchCandidate.id == attempt.candidate_id)
        )
        assert watch is not None
        assert watch.status is WatchStatus.WATCHING


async def test_known_payment_hold_remains_follow_up_after_inconclusive_read(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_payment_hold(session_factory)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="test-payment-follow-up",
            observed_at=NOW,
        ),
    )
    lease_service = RecordingLeaseService(events)
    dependencies = _dependencies(
        session_factory,
        adapter,
        lease_service,
        events,
        apply_reconciliation=apply_reservation_reconciliation,
    )

    assert (
        await reconcile_reservation_attempt(
            attempt_id,
            dependencies=dependencies,
            adapter=adapter,
        )
        == 1
    )
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
        assert attempt.reconciliation_attempt_count == 2
        assert attempt.next_reconcile_at == attempt.last_reconciled_at + timedelta(seconds=30)
        attempt.next_reconcile_at = NOW - timedelta(seconds=1)
        await session.commit()

    assert (
        await reconcile_reservation_attempt(
            attempt_id,
            dependencies=dependencies,
            adapter=adapter,
        )
        == 1
    )

    assert len(adapter.targets) == 2
    for target in adapter.targets:
        assert target.purpose is ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
        assert target.reserved_seats == (
            ReservationConfirmationSeat(car_number="4", seat_number="8A"),
        )
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        assert attempt.reconciliation_attempt_count == 3
        assert attempt.next_reconcile_at == attempt.last_reconciled_at + timedelta(minutes=2)


async def test_elapsed_payment_hold_final_read_remains_payment_follow_up(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_payment_hold(session_factory)
    elapsed_deadline = NOW - timedelta(seconds=1)
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        watch = await session.scalar(
            select(Watch).join(WatchCandidate).where(WatchCandidate.id == attempt.candidate_id)
        )
        assert watch is not None
        watch.payment_deadline = elapsed_deadline
        attempt.payment_deadline = elapsed_deadline
        attempt.confirmation_outcome = ReservationConfirmationOutcome.INCONCLUSIVE
        attempt.reconciliation_attempt_count = 6
        attempt.next_reconcile_at = None
        await session.commit()

    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="test-payment-follow-up",
            observed_at=NOW,
        ),
    )
    lease_service = RecordingLeaseService(events)

    assert (
        await reconcile_reservation_attempt(
            attempt_id,
            dependencies=_dependencies(
                session_factory,
                adapter,
                lease_service,
                events,
                apply_reconciliation=apply_reservation_reconciliation,
            ),
            adapter=adapter,
        )
        == 1
    )
    assert len(adapter.targets) == 1
    assert adapter.targets[0].purpose is ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP
    assert adapter.targets[0].reserved_seats == (
        ReservationConfirmationSeat(car_number="4", seat_number="8A"),
    )
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        watch = await session.scalar(
            select(Watch).join(WatchCandidate).where(WatchCandidate.id == attempt.candidate_id)
        )
        assert watch is not None
        assert attempt.confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
        assert attempt.post_deadline_reconciled_at is not None
        assert attempt.post_deadline_reconciled_at.replace(tzinfo=timezone.utc) == NOW
        assert watch.status is WatchStatus.WATCHING
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None


@pytest.mark.parametrize("owns_adapter", [False, True])
async def test_capability_false_preserves_state_and_respects_adapter_ownership(
    app,
    owns_adapter: bool,
) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    result = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
        source="test",
        observed_at=NOW,
    )
    adapter = RecordingAdapter(events, reservation_once=False, result=result)
    lease_service = RecordingLeaseService(events)
    dependencies = _dependencies(session_factory, adapter, lease_service, events)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=dependencies,
        adapter=None if owns_adapter else adapter,
    )

    assert reconciled == 0
    assert adapter.targets == []
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 0, 0)
    expected_middle = ["get-adapter"] if owns_adapter else []
    expected_cleanup = ["drain", "close", "release"] if owns_adapter else ["drain", "release"]
    assert events == ["circuit", "acquire", *expected_middle, *expected_cleanup]


async def test_lost_lease_after_confirmation_does_not_apply_result(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    result = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="test",
        observed_at=NOW,
        payment_deadline=NOW + timedelta(minutes=10),
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    )
    adapter = RecordingAdapter(events, reservation_once=True, result=result)
    lease_service = RecordingLeaseService(events, current=False)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(session_factory, adapter, lease_service, events),
        adapter=adapter,
    )

    assert reconciled == 0
    assert len(adapter.targets) == 1
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 0, 0)
    assert events == [
        "circuit",
        "acquire",
        "confirm",
        "lease-current",
        "drain",
        "release",
    ]


async def test_credential_generation_change_during_provider_io_fails_closed(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory, credential_version=7)
    events: list[str] = []

    async def rotate_credentials(_target: ReservationConfirmationTarget) -> None:
        async with session_factory() as session:
            account = await session.scalar(
                select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
            )
            assert account is not None
            account.credential_version = 8
            await session.commit()

    result = ReservationConfirmationResult(
        provider=Provider.SRT,
        outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        source="test",
        observed_at=NOW,
        payment_deadline=NOW + timedelta(minutes=10),
        official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    )
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=result,
        on_confirm=rotate_credentials,
    )
    lease_service = RecordingLeaseService(events)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(session_factory, adapter, lease_service, events),
        adapter=adapter,
    )

    assert reconciled == 0
    assert adapter.targets[0].credential_version == 7
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 0, 0)
    assert events == [
        "circuit",
        "acquire",
        "confirm",
        "lease-current",
        "lease-current-locked:1",
        "drain",
        "release",
    ]


async def test_attempt_removed_during_provider_io_stops_without_dereferencing_missing_rows(
    app,
) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []

    async def remove_attempt(_target: ReservationConfirmationTarget) -> None:
        async with session_factory() as session:
            attempt = await session.get(ReservationAttempt, attempt_id)
            assert attempt is not None
            await session.delete(attempt)
            await session.commit()

    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="test",
            observed_at=NOW,
        ),
        on_confirm=remove_attempt,
    )
    lease_service = RecordingLeaseService(events)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(session_factory, adapter, lease_service, events),
        adapter=adapter,
    )

    assert reconciled == 0
    async with session_factory() as session:
        assert await session.get(ReservationAttempt, attempt_id) is None
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert events == [
        "circuit",
        "acquire",
        "confirm",
        "lease-current",
        "lease-current-locked:1",
        "drain",
        "release",
    ]


async def test_lock_wait_uses_fresh_reconciliation_time_for_expired_payment_handoff(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    payment_deadline = NOW + timedelta(seconds=5)
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="test",
            observed_at=NOW,
            payment_deadline=payment_deadline,
            official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
        ),
    )
    lease_service = RecordingLeaseService(events)
    clock_calls = 0

    def advancing_clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        # Initial selection, lease acquisition, the first current check, and the
        # transaction lease lock all happen before the simulated row-lock wait ends.
        return NOW if clock_calls <= 4 else NOW + timedelta(seconds=10)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            lease_service,
            events,
            apply_reconciliation=apply_reservation_reconciliation,
            now=advancing_clock,
        ),
        adapter=adapter,
    )

    assert reconciled == 1
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 1, 1)
    assert events == [
        "circuit",
        "acquire",
        "confirm",
        "lease-current",
        "lease-current-locked:1",
        "lease-current-locked:2",
        "drain",
        "release",
    ]


@pytest.mark.parametrize(
    ("circuit_closed", "lease_granted", "expected_events"),
    [
        (False, True, ["circuit"]),
        (True, False, ["circuit", "acquire"]),
    ],
)
async def test_provider_guards_stop_before_adapter_work(
    app,
    circuit_closed: bool,
    lease_granted: bool,
    expected_events: list[str],
) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="test",
            observed_at=NOW,
        ),
    )
    lease_service = RecordingLeaseService(events)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            lease_service,
            events,
            circuit_closed=circuit_closed,
            lease_granted=lease_granted,
        ),
        adapter=adapter,
    )

    assert reconciled == 0
    assert adapter.targets == []
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 0, 0)
    assert events == expected_events


async def test_lease_epoch_is_fenced_again_after_domain_rows_are_locked(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="test",
            observed_at=NOW,
            payment_deadline=NOW + timedelta(minutes=10),
            official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
        ),
    )
    lease_service = RecordingLeaseService(events)

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            lease_service,
            events,
            locked_current=(True, False),
        ),
        adapter=adapter,
    )

    assert reconciled == 0
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 0, 0)
    assert events == [
        "circuit",
        "acquire",
        "confirm",
        "lease-current",
        "lease-current-locked:1",
        "lease-current-locked:2",
        "drain",
        "release",
    ]


@pytest.mark.parametrize("provider_error", [False, True])
async def test_owned_adapter_cleanup_wraps_success_and_provider_failure(
    app,
    provider_error: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="test",
            observed_at=NOW,
        ),
        error=(ProviderUnavailable("private provider response body") if provider_error else None),
    )
    lease_service = RecordingLeaseService(events)

    captured_confirmation: ReservationConfirmationResult | None = None

    async def apply_reconciliation(*args, **_kwargs) -> None:
        nonlocal captured_confirmation
        captured_confirmation = args[4]
        events.append("apply")

    caplog.set_level(
        logging.INFO,
        logger="rail_waitlist.reservations.reconciliation_application",
    )

    reconciled = await reconcile_reservation_attempt(
        attempt_id,
        dependencies=_dependencies(
            session_factory,
            adapter,
            lease_service,
            events,
            apply_reconciliation=apply_reconciliation,
        ),
    )

    assert reconciled == 1
    assert captured_confirmation is not None
    expected_diagnostic = (
        ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE
        if provider_error
        else ReservationConfirmationDiagnosticCode.UNSPECIFIED
    )
    expected_source = "worker-reconciliation" if provider_error else "test"
    assert captured_confirmation.diagnostic_code is expected_diagnostic
    assert adapter.request_ids[-1] is not None
    classified = next(
        record.message
        for record in caplog.records
        if "event=reservation_confirmation_classified" in record.message
    )
    persisted = next(
        record.message
        for record in caplog.records
        if "event=reservation_confirmation_persisted" in record.message
    )
    for message in (classified, persisted):
        for field in (
            "phase=worker_reconciliation",
            "provider=srt",
            "purpose=unknown_result_follow_up",
            "outcome=inconclusive",
            f"confirmation_diagnostic_code={expected_diagnostic.value}",
            f"source={expected_source}",
            f"attempt_id={attempt_id}",
            f"request_id={adapter.request_ids[-1]}",
            "reconciliation_attempt=1",
        ):
            assert field in message
    assert "reconciliation_attempt_count=0" in persisted
    assert "next_reconcile_at=none" in persisted
    assert "private provider response body" not in caplog.text
    assert events == [
        "circuit",
        "acquire",
        "get-adapter",
        "confirm",
        "lease-current",
        "lease-current-locked:1",
        "lease-current-locked:2",
        "apply",
        "drain",
        "close",
        "release",
    ]


async def test_apply_failure_rolls_back_state_and_outbox_atomically(app) -> None:
    session_factory = app.state.test_session_factory
    attempt_id = await _seed_due_attempt(session_factory)
    events: list[str] = []
    adapter = RecordingAdapter(
        events,
        reservation_once=True,
        result=ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="test",
            observed_at=NOW,
        ),
    )
    lease_service = RecordingLeaseService(events)

    async def failing_apply(session, _watch, _candidate, attempt, _confirmation, **_kwargs):
        attempt.reconciliation_attempt_count = 1
        session.add(
            OutboxEvent(
                aggregate_type="watch",
                aggregate_id="rollback-test",
                event_type="watch.reservation_reconciled",
                payload={"must": "rollback"},
                dedupe_key="rollback-test",
            )
        )
        await session.flush()
        raise RuntimeError("synthetic apply failure")

    with pytest.raises(RuntimeError, match="synthetic apply failure"):
        await reconcile_reservation_attempt(
            attempt_id,
            dependencies=_dependencies(
                session_factory,
                adapter,
                lease_service,
                events,
                apply_reconciliation=failing_apply,
            ),
            adapter=adapter,
        )

    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 0, 0)
    assert events[-2:] == ["drain", "release"]
