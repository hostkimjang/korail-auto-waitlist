from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select

from rail_waitlist.domain import Provider, ReservationOutcome, ReservationPolicy, WatchStatus
from rail_waitlist.models import (
    OutboxEvent,
    RailProviderAccount,
    ReservationAttempt,
    Watch,
    WatchCandidate,
)
from rail_waitlist.provider_execution_lease import ExecutionLeaseGrant
from rail_waitlist.providers import ProviderUnavailable
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from rail_waitlist.reservations.reconciliation_application import (
    ReconciliationDependencies,
    reconcile_reservation_attempt,
)
from rail_waitlist.schemas import ProviderCapabilities
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
        apply_reconciliation=apply_reconciliation or fail_if_applied,
        now=now or (lambda: NOW),
    )


async def _attempt_state(session_factory, attempt_id: str) -> tuple[ReservationOutcome, int, int]:
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxEvent))
        return attempt.outcome, attempt.reconciliation_attempt_count, outbox_count or 0


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
    assert await _attempt_state(session_factory, attempt_id) == (ReservationOutcome.UNKNOWN, 1, 0)
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
        error=ProviderUnavailable("synthetic provider failure") if provider_error else None,
    )
    lease_service = RecordingLeaseService(events)

    async def apply_reconciliation(*_args, **_kwargs) -> None:
        events.append("apply")

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
