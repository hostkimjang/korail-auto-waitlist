from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    WatchStatus,
)
from rail_waitlist.models import (
    OutboxEvent,
    RailProviderAccount,
    ReservationAttempt,
    Watch,
    WatchCandidate,
)
from rail_waitlist.provider_account_management.application import update_provider_auth_status
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.execution_application import (
    ReservationExecutionDependencies,
    ReservationExecutionTarget,
    _locked_attempt_query,
    _locked_authenticated_credential_version_query,
    _locked_candidate_query,
    _locked_provider_account_credential_version_query,
    _locked_watch_query,
    confirm_provider_reservation_result,
    execute_reservation,
    provider_auth_status_for_reservation_outcome,
)
from rail_waitlist.reservations.progress_application import record_reservation_progress
from rail_waitlist.schemas import (
    RailProviderAuthStatus,
    ReservationProgressStage,
    ReservationResult,
)
from rail_waitlist.security import secret_box
from rail_waitlist.services import (
    add_outbox_event,
    apply_watch_transition,
    begin_reservation_attempt,
    complete_reservation_attempt,
    get_or_create_provider_circuit,
    record_reservation_confirmation,
    request_hash,
)
from rail_waitlist.srt_reservation import SRT_RESERVATION_SOURCE


@dataclass
class StubConfirmationAdapter:
    result: ReservationConfirmationResult
    calls: int = 0

    async def confirm_reservation(self, target):
        self.calls += 1
        return self.result


def execution_target(provider: Provider = Provider.SRT) -> ReservationExecutionTarget:
    departure = datetime(2026, 8, 3, 13, 9, tzinfo=UTC)
    return ReservationExecutionTarget(
        watch_id="watch-1",
        candidate_id="candidate-1",
        provider=provider,
        origin="대전",
        destination="부산",
        origin_node_id="origin-node",
        destination_node_id="destination-node",
        train_number="329",
        departure_at=departure,
        arrival_at=departure + timedelta(hours=2),
        seat_class=SeatClass.STANDARD.value,
        passenger_count=1,
        reservation_episode_key="episode-1",
    )


def reservation_result(
    outcome: ReservationOutcome,
    *,
    payment_deadline: datetime | None = None,
) -> ReservationResult:
    return ReservationResult(
        outcome=outcome,
        source=SRT_RESERVATION_SOURCE,
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        credential_version=3,
        payment_deadline=payment_deadline,
        **(
            {
                "official_handoff_url": (
                    "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
                )
            }
            if outcome in {ReservationOutcome.PAYMENT_REQUIRED, ReservationOutcome.RESERVED}
            else {}
        ),
    )


async def update_provider_auth_status_in_transaction(
    session: AsyncSession,
    provider: Provider,
    status: RailProviderAuthStatus,
    *,
    expected_credential_version: int,
) -> None:
    await update_provider_auth_status(
        session,
        provider,
        status,
        expected_credential_version=expected_credential_version,
        commit=False,
    )


def dependencies(session_factory=None, **overrides) -> ReservationExecutionDependencies:
    values = {
        "session_factory": session_factory,
        "get_or_create_provider_circuit": get_or_create_provider_circuit,
        "apply_watch_transition": apply_watch_transition,
        "begin_reservation_attempt": begin_reservation_attempt,
        "add_outbox_event": add_outbox_event,
        "complete_reservation_attempt": complete_reservation_attempt,
        "record_reservation_confirmation": record_reservation_confirmation,
        "update_provider_auth_status": update_provider_auth_status_in_transaction,
        "provider_call_errors": (RuntimeError, ValueError),
        "srt_exact_reservation_source": SRT_RESERVATION_SOURCE,
    }
    values.update(overrides)
    return ReservationExecutionDependencies(**values)


def _postgresql_sql(query) -> str:
    return str(query.compile(dialect=postgresql.dialect()))


def test_reservation_transactions_keep_explicit_postgresql_row_locks() -> None:
    account_claim_sql = _postgresql_sql(
        _locked_authenticated_credential_version_query(Provider.SRT)
    )
    account_result_sql = _postgresql_sql(
        _locked_provider_account_credential_version_query(Provider.SRT)
    )
    watch_sql = _postgresql_sql(_locked_watch_query("watch-1"))
    candidate_sql = _postgresql_sql(_locked_candidate_query("candidate-1"))
    attempt_sql = _postgresql_sql(_locked_attempt_query("attempt-1"))

    assert "rail_provider_accounts" in account_claim_sql
    assert "FOR UPDATE" in account_claim_sql
    assert "rail_provider_accounts" in account_result_sql
    assert "FOR UPDATE" in account_result_sql
    assert "watches" in watch_sql and "FOR UPDATE" in watch_sql
    assert "FOR UPDATE OF watch_candidates" in candidate_sql
    assert "reservation_attempts" in attempt_sql and "FOR UPDATE" in attempt_sql


async def test_exact_srt_reserve_result_skips_a_second_list_call() -> None:
    deadline = datetime(2026, 8, 3, 12, 20, tzinfo=UTC)
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="srtrain-reservation-list",
            observed_at=datetime(2026, 8, 3, 12, 1, tzinfo=UTC),
            payment_deadline=deadline,
            official_handoff_url=(
                "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
            ),
        )
    )

    confirmed = await confirm_provider_reservation_result(
        adapter,
        execution_target(),
        "attempt-1",
        reservation_result(ReservationOutcome.PAYMENT_REQUIRED, payment_deadline=deadline),
        dependencies=dependencies(),
    )

    assert adapter.calls == 0
    assert confirmed.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert confirmed.source == SRT_RESERVATION_SOURCE
    assert confirmed.payment_deadline == deadline
    assert confirmed.confirmation is not None
    assert (
        confirmed.confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    )


async def test_not_found_or_inconclusive_remains_an_ambiguous_fence() -> None:
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="srtrain-reservation-list",
            observed_at=datetime(2026, 8, 3, 12, 1, tzinfo=UTC),
        )
    )

    unresolved = await confirm_provider_reservation_result(
        adapter,
        execution_target(),
        "attempt-1",
        reservation_result(ReservationOutcome.UNKNOWN),
        dependencies=dependencies(),
    )

    assert adapter.calls == 1
    assert unresolved.outcome is ReservationOutcome.UNKNOWN
    assert unresolved.official_handoff_url is None
    assert unresolved.payment_deadline is None


async def test_korail_confirmation_preserves_all_reservation_progress_stages() -> None:
    progress_times = tuple(
        datetime(2026, 8, 10, 10, 0, second, tzinfo=UTC) for second in range(4)
    )
    progress_stages = tuple(
        ReservationProgressStage(stage=stage, occurred_at=occurred_at)
        for stage, occurred_at in zip(
            (
                "authenticated_session_ready",
                "target_rechecked",
                "seat_selected",
                "reservation_requested",
            ),
            progress_times,
            strict=True,
        )
    )
    confirmation_time = datetime(2026, 8, 10, 10, 0, 5, tzinfo=UTC)
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="korail-same-session-detail",
            observed_at=confirmation_time,
            official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
        )
    )
    result = ReservationResult(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        source="korail-pydoll-reservation",
        observed_at=datetime(2026, 8, 10, 10, 0, 4, tzinfo=UTC),
        credential_version=3,
        official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
        progress_stages=progress_stages,
    )

    confirmed = await confirm_provider_reservation_result(
        adapter,
        execution_target(Provider.KORAIL),
        "attempt-210",
        result,
        dependencies=dependencies(),
    )

    assert adapter.calls == 1
    assert confirmed.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert confirmed.result.progress_stages == progress_stages
    assert tuple(stage.occurred_at for stage in confirmed.result.progress_stages) == progress_times
    assert all(stage.occurred_at.tzinfo is UTC for stage in confirmed.result.progress_stages)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (ReservationOutcome.PAYMENT_REQUIRED, "authenticated"),
        (ReservationOutcome.NOT_AVAILABLE, "authenticated"),
        (ReservationOutcome.AUTH_REQUIRED, "auth_required"),
        (ReservationOutcome.PROVIDER_BLOCKED, "provider_blocked"),
        (ReservationOutcome.FAILED, None),
        (ReservationOutcome.UNKNOWN, None),
    ],
)
def test_only_conclusive_outcomes_update_provider_auth_status(
    outcome: ReservationOutcome,
    expected: str | None,
) -> None:
    assert provider_auth_status_for_reservation_outcome(outcome) == expected


async def _persist_actionable_mock_target(session_factory) -> ReservationExecutionTarget:
    departure_at = datetime.now(UTC) + timedelta(days=1)
    async with session_factory() as session:
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="부산",
            destination_node_id="MOCK-BUSAN",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"execution-application-{departure_at.timestamp()}",
        )
        candidate = WatchCandidate(
            train_number="MOCK-001",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=2),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()
        return ReservationExecutionTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=watch.provider,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=candidate.departure_at,
            arrival_at=candidate.arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            reservation_episode_key="availability:observation-1",
        )


class ClaimInspectingAdapter:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls = 0
        self.requests = []

    async def reserve_once(self, request) -> ReservationResult:
        self.calls += 1
        self.requests.append(request)
        async with self.session_factory() as session:
            attempt = await session.scalar(
                select(ReservationAttempt).where(
                    ReservationAttempt.candidate_id == request.candidate_id
                )
            )
            candidate = await session.get(WatchCandidate, request.candidate_id)
            assert attempt is not None and attempt.outcome is ReservationOutcome.PENDING
            assert candidate is not None and candidate.state == "reservation_attempted"
            claimed_watch = await session.get(Watch, candidate.watch_id)
            assert claimed_watch is not None and claimed_watch.status is WatchStatus.RESERVING
            assert claimed_watch.reservation_attempted is True
            claim_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == claimed_watch.id,
                    OutboxEvent.event_type == "watch.reservation_attempted",
                )
            )
            assert claim_event is not None
        return ReservationResult(
            outcome=ReservationOutcome.FAILED,
            source="mock",
            observed_at=datetime.now(UTC),
        )

    async def confirm_reservation(self, target):
        raise AssertionError("mock results must not be confirmed")


async def test_claim_is_committed_before_provider_io_and_episode_runs_exactly_once(app) -> None:
    target = await _persist_actionable_mock_target(app.state.test_session_factory)
    adapter = ClaimInspectingAdapter(app.state.test_session_factory)

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        assert attempt is not None
        expected_key = (
            f"reserve:{target.candidate_id}:{request_hash(target.reservation_episode_key)[:32]}"
        )
        assert attempt.idempotency_key == expected_key
        assert adapter.requests[0].idempotency_key == expected_key
        candidate = await session.get(WatchCandidate, target.candidate_id)
        watch = await session.get(Watch, target.watch_id)
        assert candidate is not None
        assert watch is not None
        candidate.state = "seat_found"
        watch.status = WatchStatus.SEAT_FOUND
        await session.commit()

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.calls == 1
    async with app.state.test_session_factory() as session:
        attempts = list((await session.scalars(select(ReservationAttempt))).all())
        claim_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == target.watch_id,
                        OutboxEvent.event_type == "watch.reservation_attempted",
                    )
                )
            ).all()
        )
        assert len(attempts) == 1
        assert len(claim_events) == 1


class ResultWriteFailure(Exception):
    pass


async def test_result_transaction_rollback_preserves_pre_io_claim(app) -> None:
    target = await _persist_actionable_mock_target(app.state.test_session_factory)
    adapter = ClaimInspectingAdapter(app.state.test_session_factory)

    async def fail_after_result_mutation(*args, **kwargs) -> None:
        await complete_reservation_attempt(*args, **kwargs)
        raise ResultWriteFailure

    with pytest.raises(ResultWriteFailure):
        await execute_reservation(
            adapter,
            target,
            dependencies=dependencies(
                app.state.test_session_factory,
                complete_reservation_attempt=fail_after_result_mutation,
            ),
        )

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )
    assert adapter.calls == 1

    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        candidate = await session.get(WatchCandidate, target.candidate_id)
        watch = await session.get(Watch, target.watch_id)
        assert attempt is not None and attempt.outcome is ReservationOutcome.PENDING
        assert attempt.finished_at is None
        assert candidate is not None and candidate.state == "reservation_attempted"
        assert watch is not None and watch.status is WatchStatus.RESERVING
        result_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == target.watch_id,
                        OutboxEvent.event_type == "watch.reservation_failed_monitoring_resumed",
                    )
                )
            ).all()
        )
        assert result_events == []


class StaleCredentialResultAdapter:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls = 0

    async def reserve_once(self, request) -> ReservationResult:
        self.calls += 1
        assert request.expected_credential_version == 4
        async with self.session_factory() as session:
            account = await session.scalar(
                select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
            )
            assert account is not None
            account.credential_version = 5
            account.last_auth_status = "authenticated"
            await session.commit()
        return ReservationResult(
            outcome=ReservationOutcome.AUTH_REQUIRED,
            source="authorized-provider",
            observed_at=datetime.now(UTC),
            credential_version=4,
        )

    async def confirm_reservation(self, target):
        raise AssertionError("AUTH_REQUIRED must not trigger a confirmation read")


async def test_stale_credential_result_cannot_write_new_generation_state(app) -> None:
    departure_at = datetime.now(UTC) + timedelta(days=1)
    async with app.state.test_session_factory() as session:
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
            last_authenticated_at=datetime.now(UTC),
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="execution-stale-credential-result",
        )
        candidate = WatchCandidate(
            train_number="00055",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add_all([account, watch])
        await session.commit()
        target = ReservationExecutionTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=watch.provider,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=candidate.departure_at,
            arrival_at=candidate.arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            reservation_episode_key="availability:credential-4",
        )

    adapter = StaleCredentialResultAdapter(app.state.test_session_factory)
    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.calls == 1
    async with app.state.test_session_factory() as session:
        account = await session.scalar(select(RailProviderAccount))
        attempt = await session.scalar(select(ReservationAttempt))
        watch = await session.get(Watch, target.watch_id)
        candidate = await session.get(WatchCandidate, target.candidate_id)
        assert account is not None
        assert account.credential_version == 5
        assert account.last_auth_status == "authenticated"
        assert attempt is not None and attempt.outcome is ReservationOutcome.PENDING
        assert attempt.credential_version == 4
        assert attempt.finished_at is None
        assert watch is not None and watch.status is WatchStatus.RESERVING
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None
        assert candidate is not None and candidate.state == "reservation_attempted"
        result_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == target.watch_id,
                        OutboxEvent.event_type == "watch.reservation_result",
                    )
                )
            ).all()
        )
        assert result_events == []


class FailingExternalReservationAdapter:
    async def reserve_once(self, request) -> ReservationResult:
        assert request.expected_credential_version == 4
        raise RuntimeError("fixture provider failure")

    async def confirm_reservation(self, target):
        raise AssertionError("FAILED must not trigger a confirmation read")


async def test_external_provider_error_retains_claim_generation_and_completes(app) -> None:
    departure_at = datetime.now(UTC) + timedelta(days=1)
    async with app.state.test_session_factory() as session:
        account = RailProviderAccount(
            provider=Provider.KORAIL,
            credentials_ciphertext=secret_box.encrypt_dict(
                {
                    "login_method": "membership_number",
                    "login_id": "failing-account",
                    "password": "failing-password",
                }
            ),
            enabled=True,
            credential_version=4,
            last_auth_status="authenticated",
            last_authenticated_at=datetime.now(UTC),
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key="execution-provider-error-generation",
        )
        candidate = WatchCandidate(
            train_number="00055",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add_all([account, watch])
        await session.commit()
        target = ReservationExecutionTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=watch.provider,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=candidate.departure_at,
            arrival_at=candidate.arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            reservation_episode_key="availability:provider-error",
        )

    await execute_reservation(
        FailingExternalReservationAdapter(),
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        watch = await session.get(Watch, target.watch_id)
        candidate = await session.get(WatchCandidate, target.candidate_id)
        assert attempt is not None and attempt.outcome is ReservationOutcome.FAILED
        assert attempt.credential_version == 4
        assert attempt.finished_at is not None
        assert watch is not None and watch.status is WatchStatus.WATCHING
        assert candidate is not None and candidate.state == "observed"


async def _persist_actionable_korail_target(session_factory) -> ReservationExecutionTarget:
    departure_at = datetime.now(UTC) + timedelta(days=1)
    async with session_factory() as session:
        account = RailProviderAccount(
            provider=Provider.KORAIL,
            credentials_ciphertext=secret_box.encrypt_dict(
                {
                    "login_method": "membership_number",
                    "login_id": "progress-fixture-account",
                    "password": "progress-fixture-password",
                }
            ),
            enabled=True,
            credential_version=4,
            last_auth_status="authenticated",
            last_authenticated_at=datetime.now(UTC),
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="NAT011668",
            destination="서울",
            destination_node_id="NAT010000",
            travel_date=departure_at.date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.SEAT_FOUND,
            dedupe_key=f"execution-progress-{departure_at.timestamp()}",
        )
        candidate = WatchCandidate(
            train_number="00055",
            departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add_all([account, watch])
        await session.commit()
        return ReservationExecutionTarget(
            watch_id=watch.id,
            candidate_id=candidate.id,
            provider=watch.provider,
            origin=watch.origin,
            destination=watch.destination,
            origin_node_id=watch.origin_node_id or "",
            destination_node_id=watch.destination_node_id or "",
            train_number=candidate.train_number,
            departure_at=candidate.departure_at,
            arrival_at=candidate.arrival_at,
            seat_class=candidate.seat_class,
            passenger_count=watch.passenger_count,
            reservation_episode_key="availability:progress-observation",
        )


class ProgressReportingKorailAdapter:
    def __init__(self) -> None:
        self.progress_calls = 0
        self.legacy_calls = 0
        self.reported: tuple[ReservationProgressStage, ...] = ()

    async def reserve_once(self, request) -> ReservationResult:
        self.legacy_calls += 1
        raise AssertionError("KORAIL progress adapter must use reserve_once_with_progress")

    async def reserve_once_with_progress(self, request, on_progress) -> ReservationResult:
        self.progress_calls += 1
        started = datetime.now(UTC)
        stages = tuple(
            ReservationProgressStage(
                stage=stage,
                occurred_at=started + timedelta(milliseconds=index),
            )
            for index, stage in enumerate(
                (
                    "authenticated_session_ready",
                    "target_rechecked",
                    "seat_selected",
                    "reservation_requested",
                )
            )
        )
        for stage in stages:
            await on_progress(stage)
        await on_progress(stages[1])
        await on_progress(
            ReservationProgressStage(
                stage="target_rechecked",
                occurred_at=started + timedelta(seconds=1),
            )
        )
        self.reported = stages
        return ReservationResult(
            outcome=ReservationOutcome.FAILED,
            source="korail-progress-fixture",
            observed_at=started + timedelta(seconds=2),
            credential_version=request.expected_credential_version,
            progress_stages=stages,
        )

    async def confirm_reservation(self, target):
        raise AssertionError("FAILED must not trigger a confirmation read")


async def test_korail_progress_callback_persists_cumulative_idempotent_snapshots(app) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = ProgressReportingKorailAdapter()

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.progress_calls == 1
    assert adapter.legacy_calls == 0
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        assert attempt is not None and attempt.outcome is ReservationOutcome.FAILED
        assert attempt.progress_stages == [
            {
                "stage": stage.stage,
                "occurred_at": stage.occurred_at.isoformat(),
            }
            for stage in adapter.reported
        ]
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.event_type == "watch.reservation_progressed")
                    .order_by(OutboxEvent.created_at, OutboxEvent.id)
                )
            ).all()
        )
        assert len(events) == 4
        for index, event in enumerate(events):
            expected = adapter.reported[index]
            assert event.dedupe_key == (
                f"reservation-progress:{attempt.id}:{expected.stage}"
            )
            assert event.payload["watch_id"] == target.watch_id
            assert event.payload["candidate_id"] == target.candidate_id
            assert event.payload["attempt_id"] == attempt.id
            assert event.payload["attempt_sequence"] == 1
            assert event.payload["seat_detected_at"] is None
            assert event.payload["attempt_started_at"] == attempt.started_at.isoformat()
            assert event.payload["stage"] == expected.stage
            assert event.payload["occurred_at"] == expected.occurred_at.isoformat()
            assert event.payload["progress_stages"] == [
                {
                    "stage": stage.stage,
                    "occurred_at": stage.occurred_at.isoformat(),
                }
                for stage in adapter.reported[: index + 1]
            ]

        late_progress = (*adapter.reported,)
        persisted = await record_reservation_progress(
            session_factory=app.state.test_session_factory,
            add_outbox_event=add_outbox_event,
            watch_id=target.watch_id,
            candidate_id=target.candidate_id,
            attempt_id=attempt.id,
            expected_credential_version=4,
            cumulative_progress=late_progress,
        )
        assert persisted is False


async def test_progress_persistence_failure_does_not_cancel_korail_reservation(app) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = ProgressReportingKorailAdapter()

    async def fail_progress_only(session, **kwargs):
        if kwargs["event_type"] == "watch.reservation_progressed":
            raise RuntimeError("progress persistence fixture failure")
        return await add_outbox_event(session, **kwargs)

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(
            app.state.test_session_factory,
            add_outbox_event=fail_progress_only,
        ),
    )

    assert adapter.progress_calls == 1
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        assert attempt is not None and attempt.outcome is ReservationOutcome.FAILED
        progress_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "watch.reservation_progressed"
                    )
                )
            ).all()
        )
        result_event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "watch.reservation_result")
        )
        assert progress_events == []
        assert result_event is not None
