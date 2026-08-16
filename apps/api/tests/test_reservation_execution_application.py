from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    ReservationResultReasonCode,
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
from rail_waitlist.provider_call_context import current_request_id
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.contracts import ReservedSeat
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
    request_id: str | None = None
    target: object | None = None

    async def confirm_reservation(self, target):
        self.calls += 1
        self.request_id = current_request_id()
        self.target = target
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
    assert confirmed.request_id is None
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
    assert unresolved.request_id is not None
    assert adapter.request_id == unresolved.request_id
    assert adapter.target is not None
    assert adapter.target.purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
    assert adapter.target.confirmation_correlation_seats == ()


async def test_initial_confirmation_failure_is_safely_classified_in_correlated_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hidden_error = "private provider response body"

    class FailingConfirmationAdapter:
        request_id: str | None = None

        async def confirm_reservation(self, _target):
            self.request_id = current_request_id()
            raise RuntimeError(hidden_error)

    adapter = FailingConfirmationAdapter()
    caplog.set_level(
        logging.INFO,
        logger="rail_waitlist.reservations.execution_application",
    )

    unresolved = await confirm_provider_reservation_result(
        adapter,
        execution_target(Provider.KORAIL),
        "attempt-log-contract",
        reservation_result(ReservationOutcome.UNKNOWN),
        dependencies=dependencies(),
    )

    assert unresolved.confirmation is not None
    assert (
        unresolved.confirmation.diagnostic_code
        is ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE
    )
    assert adapter.request_id == unresolved.request_id
    classified = next(
        record.message
        for record in caplog.records
        if "event=reservation_confirmation_classified" in record.message
    )
    for field in (
        "phase=initial_confirmation",
        "provider=korail",
        "purpose=unknown_result_follow_up",
        "outcome=inconclusive",
        "confirmation_diagnostic_code=official_read_unavailable",
        "source=worker-initial-confirmation",
        "attempt_id=attempt-log-contract",
        f"request_id={unresolved.request_id}",
        "reconciliation_attempt=0",
    ):
        assert field in classified
    assert hidden_error not in caplog.text


async def test_korail_confirmation_preserves_all_reservation_progress_stages() -> None:
    progress_times = tuple(datetime(2026, 8, 10, 10, 0, second, tzinfo=UTC) for second in range(4))
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
        reserved_seats=(ReservedSeat(car_number="4", seat_number="8A"),),
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
    assert confirmed.result.reserved_seats == result.reserved_seats
    assert tuple(stage.occurred_at for stage in confirmed.result.progress_stages) == progress_times
    assert all(stage.occurred_at.tzinfo is UTC for stage in confirmed.result.progress_stages)


def post_request_progress() -> tuple[ReservationProgressStage, ...]:
    return (
        ReservationProgressStage(
            stage="reservation_requested",
            occurred_at=datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
        ),
    )


async def test_unknown_exact_correlation_uses_private_follow_up_and_paid_wins_auth_signal() -> None:
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
            source="korail-issued-ticket-list",
            observed_at=datetime(2026, 8, 10, 10, 1, tzinfo=UTC),
        )
    )
    private_seat = ReservedSeat(car_number="4", seat_number="8A")
    result = ReservationResult(
        outcome=ReservationOutcome.UNKNOWN,
        result_reason_code=ReservationResultReasonCode.AUTHENTICATION_REQUIRED,
        source="korail-pydoll-reservation",
        observed_at=datetime(2026, 8, 10, 10, 0, 1, tzinfo=UTC),
        credential_version=3,
        progress_stages=post_request_progress(),
        confirmation_correlation_seats=(private_seat,),
    )

    evaluation = await confirm_provider_reservation_result(
        adapter,
        execution_target(Provider.KORAIL),
        "attempt-private-paid",
        result,
        dependencies=dependencies(),
    )

    assert adapter.target is not None
    assert adapter.target.purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
    assert adapter.target.reserved_seats == ()
    assert adapter.target.confirmation_correlation_seats[0].seat_number == "8A"
    assert evaluation.outcome is ReservationOutcome.UNKNOWN
    assert evaluation.result.reserved_seats == ()
    assert evaluation.result.confirmation_correlation_seats == (private_seat,)
    assert evaluation.confirmation is not None
    assert evaluation.confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID


async def test_unconfirmed_actionable_result_moves_exact_seat_to_private_correlation() -> None:
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            diagnostic_code=ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE,
            source="korail-reservation-list",
            observed_at=datetime(2026, 8, 10, 10, 1, tzinfo=UTC),
        )
    )
    exact_seat = ReservedSeat(car_number="4", seat_number="8A")
    result = ReservationResult(
        outcome=ReservationOutcome.PAYMENT_REQUIRED,
        source="korail-pydoll-reservation",
        observed_at=datetime(2026, 8, 10, 10, 0, 1, tzinfo=UTC),
        credential_version=3,
        official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
        progress_stages=post_request_progress(),
        reserved_seats=(exact_seat,),
    )

    evaluation = await confirm_provider_reservation_result(
        adapter,
        execution_target(Provider.KORAIL),
        "attempt-private-unresolved",
        result,
        dependencies=dependencies(),
    )

    assert evaluation.outcome is ReservationOutcome.UNKNOWN
    assert evaluation.result.reserved_seats == ()
    assert evaluation.result.confirmation_correlation_seats == (exact_seat,)


async def test_confirmed_hold_promotes_exact_private_correlation_to_reserved_seat() -> None:
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="srtrain-reservation-list",
            observed_at=datetime(2026, 8, 10, 10, 1, tzinfo=UTC),
            official_handoff_url=(
                "https://etk.srail.kr/hpg/hra/02/selectReservationList.do?pageId=TK0102010000"
            ),
        )
    )
    private_seat = ReservedSeat(car_number="4", seat_number="8A")
    result = ReservationResult(
        outcome=ReservationOutcome.UNKNOWN,
        result_reason_code=ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
        source="srt-ambiguous-result",
        observed_at=datetime(2026, 8, 10, 10, 0, 1, tzinfo=UTC),
        credential_version=3,
        progress_stages=post_request_progress(),
        confirmation_correlation_seats=(private_seat,),
    )

    evaluation = await confirm_provider_reservation_result(
        adapter,
        execution_target(Provider.SRT),
        "attempt-private-hold",
        result,
        dependencies=dependencies(),
    )

    assert evaluation.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert evaluation.result.reserved_seats == (private_seat,)
    assert evaluation.result.confirmation_correlation_seats == ()


@pytest.mark.parametrize(
    ("provider", "confirmation_outcome", "with_correlation"),
    [
        (Provider.SRT, ReservationConfirmationOutcome.CONFIRMED_PAID, False),
        (Provider.SRT, ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED, False),
        (Provider.KORAIL, ReservationConfirmationOutcome.CONFIRMED_PAID, False),
        (Provider.KORAIL, ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED, False),
        (Provider.KORAIL, ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED, True),
    ],
)
async def test_unknown_confirmation_main_boundary_rejects_unsafe_positive_evidence(
    provider: Provider,
    confirmation_outcome: ReservationConfirmationOutcome,
    with_correlation: bool,
) -> None:
    confirmation = ReservationConfirmationResult(
        provider=provider,
        outcome=confirmation_outcome,
        source="unsafe-positive-fixture",
        observed_at=datetime(2026, 8, 10, 10, 1, tzinfo=UTC),
        **(
            {
                "official_handoff_url": (
                    "https://www.korail.com/ticket/mypage/mykorail"
                    if provider is Provider.KORAIL
                    else (
                        "https://etk.srail.kr/hpg/hra/02/"
                        "selectReservationList.do?pageId=TK0102010000"
                    )
                )
            }
            if confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
            else {}
        ),
    )
    adapter = StubConfirmationAdapter(confirmation)
    correlation_seats = (
        (ReservedSeat(car_number="4", seat_number="8A"),) if with_correlation else ()
    )
    result = ReservationResult(
        outcome=ReservationOutcome.UNKNOWN,
        result_reason_code=ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
        source="ambiguous-reservation-result",
        observed_at=datetime(2026, 8, 10, 10, 0, 1, tzinfo=UTC),
        credential_version=3,
        progress_stages=post_request_progress(),
        confirmation_correlation_seats=correlation_seats,
    )

    evaluation = await confirm_provider_reservation_result(
        adapter,
        execution_target(provider),
        "attempt-unsafe-positive",
        result,
        dependencies=dependencies(),
    )

    assert adapter.target is not None
    assert adapter.target.purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
    assert len(adapter.target.confirmation_correlation_seats) == int(with_correlation)
    assert evaluation.outcome is ReservationOutcome.UNKNOWN
    assert evaluation.result.reserved_seats == ()
    assert evaluation.confirmation is not None
    assert evaluation.confirmation.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert (
        evaluation.confirmation.diagnostic_code
        is ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT
    )


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


async def test_cancel_during_provider_io_is_rejected_and_result_is_preserved(app, client) -> None:
    target = await _persist_actionable_mock_target(app.state.test_session_factory)

    class CancelDuringReservationAdapter:
        calls = 0

        async def reserve_once(self, _request) -> ReservationResult:
            self.calls += 1
            cancelled = await client.post(f"/api/v1/watches/{target.watch_id}/cancel")
            assert cancelled.status_code == 409
            assert cancelled.json()["detail"] == (
                "예매 요청이 이미 시작되었거나 결제가 필요한 예약이 있어 대기를 취소할 수 "
                "없습니다. 공식 예약 내역을 확인해 주세요."
            )
            return ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source="mock",
                observed_at=datetime.now(UTC),
                payment_deadline=datetime.now(UTC) + timedelta(minutes=10),
                official_handoff_url="https://example.invalid/mock-booking",
            )

        async def confirm_reservation(self, _target):
            raise AssertionError("mock results must not be confirmed")

    adapter = CancelDuringReservationAdapter()

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.calls == 1
    async with app.state.test_session_factory() as session:
        watch = await session.get(Watch, target.watch_id)
        candidate = await session.get(WatchCandidate, target.candidate_id)
        attempt = await session.scalar(
            select(ReservationAttempt).where(ReservationAttempt.candidate_id == target.candidate_id)
        )
        assert watch is not None
        assert candidate is not None
        assert attempt is not None
        assert (watch.status, candidate.state, attempt.outcome) == (
            WatchStatus.PAYMENT_REQUIRED,
            "payment_required",
            ReservationOutcome.PAYMENT_REQUIRED,
        )
        assert watch.payment_deadline is not None
        assert attempt.payment_deadline is not None
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
        assert len(result_events) == 1


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
    def __init__(
        self,
        provider: Provider,
        confirmation_outcome: ReservationConfirmationOutcome = (
            ReservationConfirmationOutcome.INCONCLUSIVE
        ),
    ) -> None:
        self.provider = provider
        self.confirmation_outcome = confirmation_outcome
        self.confirmation_calls = 0
        self.confirmation_target = None

    async def reserve_once(self, request) -> ReservationResult:
        assert request.expected_credential_version == 4
        raise RuntimeError("fixture provider failure")

    async def confirm_reservation(self, target) -> ReservationConfirmationResult:
        self.confirmation_calls += 1
        self.confirmation_target = target
        return ReservationConfirmationResult(
            provider=self.provider,
            outcome=self.confirmation_outcome,
            diagnostic_code=(
                ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE
                if self.confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
                else None
            ),
            source=(
                "korail-reservation-list"
                if self.provider is Provider.KORAIL
                else "srtrain-reservation-list"
            ),
            observed_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("provider", [Provider.KORAIL, Provider.SRT])
@pytest.mark.parametrize(
    "confirmation_outcome",
    [
        ReservationConfirmationOutcome.INCONCLUSIVE,
        ReservationConfirmationOutcome.AUTH_REQUIRED,
        ReservationConfirmationOutcome.PROVIDER_BLOCKED,
    ],
)
async def test_external_provider_error_becomes_unknown_and_requires_confirmation(
    app,
    provider: Provider,
    confirmation_outcome: ReservationConfirmationOutcome,
) -> None:
    departure_at = datetime.now(UTC) + timedelta(days=1)
    async with app.state.test_session_factory() as session:
        account = RailProviderAccount(
            provider=provider,
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
            provider=provider,
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
            dedupe_key=f"execution-provider-error-generation-{provider.value}",
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

    adapter = FailingExternalReservationAdapter(provider, confirmation_outcome)
    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.confirmation_calls == 1
    assert adapter.confirmation_target is not None
    assert (
        adapter.confirmation_target.purpose
        is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
    )
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        watch = await session.get(Watch, target.watch_id)
        candidate = await session.get(WatchCandidate, target.candidate_id)
        result_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == target.watch_id,
                OutboxEvent.event_type == "watch.reservation_result",
            )
        )
        assert attempt is not None and attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.result_reason_code is ReservationResultReasonCode.PROVIDER_UNAVAILABLE
        assert attempt.credential_version == 4
        assert attempt.finished_at is not None
        assert (attempt.next_reconcile_at is not None) is (
            confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
        )
        assert attempt.confirmation_outcome is confirmation_outcome
        assert watch is not None
        assert watch.status is (
            WatchStatus.WATCHING
            if confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
            else WatchStatus.AUTH_REQUIRED
        )
        assert candidate is not None and candidate.state == "observed"
        assert result_event is not None
        assert result_event.payload["manual_check_required"] is True
        assert result_event.payload["retryable"] is False


class FailingMockReservationAdapter:
    async def reserve_once(self, _request) -> ReservationResult:
        raise RuntimeError("fixture mock failure")

    async def confirm_reservation(self, _target):
        raise AssertionError("mock failures must not trigger a confirmation read")


async def test_mock_provider_error_remains_conclusive_failed(app) -> None:
    target = await _persist_actionable_mock_target(app.state.test_session_factory)

    await execute_reservation(
        FailingMockReservationAdapter(),
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        result_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == target.watch_id,
                OutboxEvent.event_type == "watch.reservation_result",
            )
        )
        assert attempt is not None and attempt.outcome is ReservationOutcome.FAILED
        assert attempt.result_reason_code is ReservationResultReasonCode.PROVIDER_UNAVAILABLE
        assert attempt.progress_stages == []
        assert result_event is not None
        assert result_event.payload["manual_check_required"] is False


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


class PausingExactPaidKorailAdapter:
    def __init__(
        self,
        session_factory,
        target: ReservationExecutionTarget,
        *,
        landed_watch_status: WatchStatus = WatchStatus.PAUSED,
    ) -> None:
        self.session_factory = session_factory
        self.target = target
        self.landed_watch_status = landed_watch_status
        self.reserve_calls = 0
        self.confirmation_calls = 0

    async def reserve_once(self, _request) -> ReservationResult:
        raise AssertionError("KORAIL progress adapter must use reserve_once_with_progress")

    async def reserve_once_with_progress(self, request, _on_progress) -> ReservationResult:
        self.reserve_calls += 1
        async with self.session_factory() as session:
            watch = await session.get(Watch, self.target.watch_id)
            candidate = await session.get(WatchCandidate, self.target.candidate_id)
            attempt = await session.scalar(
                select(ReservationAttempt).where(
                    ReservationAttempt.candidate_id == self.target.candidate_id
                )
            )
            assert watch is not None and candidate is not None and attempt is not None
            watch.status = self.landed_watch_status
            watch.payment_deadline = datetime.now(UTC) + timedelta(minutes=10)
            watch.official_booking_url = "https://www.korail.com/ticket/mypage/mykorail"
            watch.next_check_at = datetime.now(UTC) + timedelta(minutes=1)
            watch.observation_in_flight_until = datetime.now(UTC) + timedelta(minutes=1)
            candidate.manual_rearm_source_attempt_id = attempt.id
            candidate.manual_rearm_authorized_at = datetime.now(UTC)
            session.add(
                WatchCandidate(
                    watch_id=watch.id,
                    train_number="00057",
                    departure_at=self.target.departure_at + timedelta(minutes=10),
                    scheduled_departure_at=self.target.departure_at + timedelta(minutes=10),
                    arrival_at=self.target.arrival_at + timedelta(minutes=10),
                    seat_class=SeatClass.STANDARD,
                    priority=2,
                    state="active",
                    manual_rearm_source_attempt_id=attempt.id,
                    manual_rearm_authorized_at=datetime.now(UTC),
                )
            )
            await session.commit()
        observed_at = datetime.now(UTC)
        return ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            result_reason_code=ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN,
            source="korail-pydoll-reservation",
            observed_at=observed_at,
            credential_version=request.expected_credential_version,
            progress_stages=(
                ReservationProgressStage(
                    stage="reservation_requested",
                    occurred_at=observed_at,
                ),
            ),
            confirmation_correlation_seats=(ReservedSeat(car_number="4", seat_number="8A"),),
        )

    async def confirm_reservation(self, target) -> ReservationConfirmationResult:
        self.confirmation_calls += 1
        assert target.purpose is ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAID,
            source="korail-issued-ticket-list",
            observed_at=datetime.now(UTC),
        )


async def test_late_exact_paid_preserves_pause_but_closes_all_retry_and_public_seat_state(
    app,
    client,
) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = PausingExactPaidKorailAdapter(app.state.test_session_factory, target)

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.reserve_calls == 1
    assert adapter.confirmation_calls == 1
    async with app.state.test_session_factory() as session:
        watch = await session.get(Watch, target.watch_id)
        attempt = await session.scalar(
            select(ReservationAttempt).where(ReservationAttempt.candidate_id == target.candidate_id)
        )
        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(WatchCandidate.watch_id == target.watch_id)
                )
            ).all()
        )
        event_types = list(
            (
                await session.scalars(
                    select(OutboxEvent.event_type).where(
                        OutboxEvent.aggregate_id == target.watch_id
                    )
                )
            ).all()
        )
        assert watch is not None and attempt is not None
        assert watch.status is WatchStatus.PAUSED
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None
        assert watch.next_check_at is None
        assert watch.observation_in_flight_until is None
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.confirmation_outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
        assert attempt.reserved_seats == []
        assert attempt.confirmation_correlation_seats == [{"car_number": "4", "seat_number": "8A"}]
        assert all(candidate.state == "expired" for candidate in candidates)
        assert all(
            candidate.manual_rearm_source_attempt_id is None
            and candidate.manual_rearm_authorized_at is None
            for candidate in candidates
        )
        assert event_types.count("watch.payment_completed") == 0
        assert event_types.count("watch.reservation_result_requires_manual_check") == 0
        assert event_types.count("watch.reservation_reconciled") == 1

    response = await client.get(f"/api/v1/watches/{target.watch_id}")
    assert response.status_code == 200
    latest_attempt = response.json()["candidates"][0]["latest_reservation_attempt"]
    assert "confirmation_correlation_seats" not in latest_attempt
    assert latest_attempt["manual_rearm_available"] is False


async def test_late_exact_paid_completes_a_watch_resumed_to_scheduled_during_provider_io(
    app,
) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = PausingExactPaidKorailAdapter(
        app.state.test_session_factory,
        target,
        landed_watch_status=WatchStatus.SCHEDULED,
    )

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.reserve_calls == 1
    assert adapter.confirmation_calls == 1
    async with app.state.test_session_factory() as session:
        watch = await session.get(Watch, target.watch_id)
        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(WatchCandidate.watch_id == target.watch_id)
                )
            ).all()
        )
        event_types = list(
            (
                await session.scalars(
                    select(OutboxEvent.event_type).where(
                        OutboxEvent.aggregate_id == target.watch_id
                    )
                )
            ).all()
        )
        payment_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == target.watch_id,
                OutboxEvent.event_type == "watch.payment_completed",
            )
        )
        assert watch is not None and payment_event is not None
        assert watch.status is WatchStatus.COMPLETED
        assert all(candidate.state == "expired" for candidate in candidates)
        assert event_types.count("watch.payment_completed") == 1
        assert event_types.count("watch.reservation_reconciled") == 0
        assert payment_event.payload["from"] == "watching"
        assert payment_event.payload["to"] == "completed"


class DeletingAuthRequiredKorailAdapter:
    def __init__(self, session_factory, target: ReservationExecutionTarget) -> None:
        self.session_factory = session_factory
        self.target = target
        self.reserve_calls = 0

    async def reserve_once(self, _request) -> ReservationResult:
        raise AssertionError("KORAIL progress adapter must use reserve_once_with_progress")

    async def reserve_once_with_progress(self, request, _on_progress) -> ReservationResult:
        self.reserve_calls += 1
        async with self.session_factory() as session:
            watch = await session.get(Watch, self.target.watch_id)
            assert watch is not None
            await session.execute(
                delete(ReservationAttempt).where(
                    ReservationAttempt.candidate_id == self.target.candidate_id
                )
            )
            await session.execute(
                delete(WatchCandidate).where(WatchCandidate.watch_id == self.target.watch_id)
            )
            await session.execute(delete(Watch).where(Watch.id == self.target.watch_id))
            await session.commit()
        return ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            result_reason_code=ReservationResultReasonCode.AUTHENTICATION_REQUIRED,
            source="korail-pydoll-reservation",
            observed_at=datetime.now(UTC),
            credential_version=request.expected_credential_version,
            progress_stages=post_request_progress(),
        )

    async def confirm_reservation(self, _target) -> ReservationConfirmationResult:
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.AUTH_REQUIRED,
            source="korail-reservation-list",
            observed_at=datetime.now(UTC),
        )


async def test_post_dispatch_auth_demotion_commits_when_watch_is_deleted_during_io(app) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = DeletingAuthRequiredKorailAdapter(app.state.test_session_factory, target)

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.reserve_calls == 1
    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        assert account.credential_version == 4
        assert account.last_auth_status == "auth_required"
        assert await session.get(Watch, target.watch_id) is None
        assert (
            await session.scalar(
                select(ReservationAttempt).where(
                    ReservationAttempt.candidate_id == target.candidate_id
                )
            )
            is None
        )
        result_event_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == target.watch_id,
                OutboxEvent.event_type.in_(
                    [
                        "watch.reservation_result",
                        "watch.reservation_result_requires_manual_check",
                    ]
                ),
            )
        )
        assert result_event_count == 0


class UnknownProviderUnavailableKorailAdapter:
    def __init__(self) -> None:
        self.progress_calls = 0
        self.confirmation_calls = 0
        self.confirmation_target = None
        self.confirmation_request_id: str | None = None

    async def reserve_once(self, _request) -> ReservationResult:
        raise AssertionError("KORAIL progress adapter must use reserve_once_with_progress")

    async def reserve_once_with_progress(self, request, _on_progress) -> ReservationResult:
        self.progress_calls += 1
        return ReservationResult(
            outcome=ReservationOutcome.UNKNOWN,
            result_reason_code=ReservationResultReasonCode.PROVIDER_UNAVAILABLE,
            source="korail-pydoll-reservation",
            observed_at=datetime.now(UTC),
            credential_version=request.expected_credential_version,
        )

    async def confirm_reservation(self, target) -> ReservationConfirmationResult:
        self.confirmation_calls += 1
        self.confirmation_target = target
        self.confirmation_request_id = current_request_id()
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            source="korail-reservation-list",
            observed_at=datetime.now(UTC),
        )


class ProgressThenFailingKorailAdapter:
    def __init__(self) -> None:
        self.reported: tuple[ReservationProgressStage, ...] = ()
        self.confirmation_calls = 0

    async def reserve_once(self, _request) -> ReservationResult:
        raise AssertionError("KORAIL progress adapter must use reserve_once_with_progress")

    async def reserve_once_with_progress(self, _request, on_progress) -> ReservationResult:
        started = datetime.now(UTC)
        self.reported = tuple(
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
        for stage in self.reported:
            await on_progress(stage)
        raise RuntimeError("fixture transport lost after reservation dispatch")

    async def confirm_reservation(self, _target) -> ReservationConfirmationResult:
        self.confirmation_calls += 1
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            diagnostic_code=(ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE),
            source="korail-reservation-list",
            observed_at=datetime.now(UTC),
        )


async def test_external_provider_error_preserves_progress_before_unknown_fence(app) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = ProgressThenFailingKorailAdapter()

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.confirmation_calls == 1
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        assert attempt is not None and attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.progress_stages == [
            {
                "stage": stage.stage,
                "occurred_at": stage.occurred_at.isoformat(),
            }
            for stage in adapter.reported
        ]
        assert attempt.next_reconcile_at is not None


async def test_korail_unknown_provider_failure_is_confirmed_and_persisted(
    app,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)
    adapter = UnknownProviderUnavailableKorailAdapter()
    caplog.set_level(
        logging.INFO,
        logger="rail_waitlist.reservations.execution_application",
    )

    await execute_reservation(
        adapter,
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    assert adapter.progress_calls == 1
    assert adapter.confirmation_calls == 1
    assert adapter.confirmation_target is not None
    assert adapter.confirmation_target.credential_version == 4
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "watch.reservation_result")
        )
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.result_reason_code is ReservationResultReasonCode.PROVIDER_UNAVAILABLE
        assert attempt.credential_version == 4
        assert attempt.confirmation_outcome is ReservationConfirmationOutcome.INCONCLUSIVE
        assert (
            attempt.confirmation_diagnostic_code
            is ReservationConfirmationDiagnosticCode.UNSPECIFIED
        )
        assert attempt.next_reconcile_at is not None
        assert event is not None
        assert event.payload["result_reason_code"] == "provider_unavailable"
        assert event.payload["confirmation_outcome"] == "inconclusive"
        assert event.payload["confirmation_diagnostic_code"] == "unspecified"
        assert event.payload["manual_check_required"] is True
        assert event.payload["retryable"] is False
        persisted = next(
            record.message
            for record in caplog.records
            if "event=reservation_confirmation_persisted" in record.message
        )
        for field in (
            "phase=initial_confirmation",
            "provider=korail",
            "purpose=unknown_result_follow_up",
            "outcome=inconclusive",
            "confirmation_diagnostic_code=unspecified",
            "source=korail-reservation-list",
            f"attempt_id={attempt.id}",
            f"request_id={adapter.confirmation_request_id}",
            "reconciliation_attempt=0",
            "reconciliation_attempt_count=0",
            "next_reconcile_at=",
        ):
            assert field in persisted


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


class ConfirmedSeatKorailAdapter:
    async def reserve_once(self, request) -> ReservationResult:
        return ReservationResult(
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            source="korail-pydoll-reservation",
            observed_at=datetime.now(UTC),
            credential_version=request.expected_credential_version,
            official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
            reserved_seats=(ReservedSeat(car_number="4", seat_number="8A"),),
        )

    async def confirm_reservation(self, target) -> ReservationConfirmationResult:
        return ReservationConfirmationResult(
            provider=Provider.KORAIL,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="korail-same-session-detail",
            observed_at=datetime.now(UTC),
            official_handoff_url="https://www.korail.com/ticket/mypage/mykorail",
        )


async def test_confirmed_reserved_seat_reaches_attempt_outbox_and_watch_read(
    app,
    client,
) -> None:
    target = await _persist_actionable_korail_target(app.state.test_session_factory)

    await execute_reservation(
        ConfirmedSeatKorailAdapter(),
        target,
        dependencies=dependencies(app.state.test_session_factory),
    )

    expected = [{"car_number": "4", "seat_number": "8A"}]
    async with app.state.test_session_factory() as session:
        attempt = await session.scalar(select(ReservationAttempt))
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == target.watch_id,
                OutboxEvent.event_type == "watch.reservation_result",
            )
        )
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempt.reserved_seats == expected
        assert event is not None and event.payload["reserved_seats"] == expected

    response = await client.get(f"/api/v1/watches/{target.watch_id}")
    assert response.status_code == 200, response.text
    latest = response.json()["candidates"][0]["latest_reservation_attempt"]
    assert latest["reserved_seats"] == expected


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
        assert len(events) == len(adapter.reported)
        for index, event in enumerate(events):
            expected = adapter.reported[index]
            assert event.dedupe_key == (f"reservation-progress:{attempt.id}:{expected.stage}")
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
