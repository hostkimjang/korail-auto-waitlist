from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    ReservationResultReasonCode,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.observations.contracts import SeatObservationRequest, SeatObservationResult
from rail_waitlist.outbox import add_outbox_event
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.provider_registry.contracts import ProviderCapabilities
from rail_waitlist.reservations.attempt_policy import is_unresolved_unknown_manual_rearm_source
from rail_waitlist.reservations.manual_rearm_application import (
    ManualReservationRearmDependencies,
    authorize_manual_reservation_rearm,
)
from rail_waitlist.reservations.manual_rearm_contracts import ManualReservationRearmReason
from rail_waitlist.reservations.payment_hold_application import is_payment_hold_ended
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from rail_waitlist.watch_management.models import (
    ReservationAttempt,
    Watch,
    WatchCandidate,
)


class BlockingHoldReconciliationAdapter:
    provider = Provider.SRT

    def __init__(self) -> None:
        self.confirm_started = asyncio.Event()
        self.release_confirmation = asyncio.Event()
        self.observation_calls = 0
        self.reserve_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=True,
            official_waitlist_link=False,
            seat_monitoring=True,
            reservation_once=True,
        )

    async def observation_deferred_until(self) -> datetime | None:
        return None

    async def observe_seats(
        self,
        request: SeatObservationRequest,
    ) -> list[SeatObservationResult]:
        self.observation_calls += 1
        observed_at = datetime.now(UTC)
        return [
            SeatObservationResult(
                seat_class=request.seat_class,
                status=SeatObservationStatus.AVAILABLE,
                source="srtrain-2.6.7-accountless",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(minutes=1),
            )
        ]

    async def reserve_once(self, _request):
        self.reserve_calls += 1
        raise AssertionError("reconciliation lease and confirmed hold must prevent reservation")

    async def confirm_reservation(
        self,
        _target: ReservationConfirmationTarget,
    ) -> ReservationConfirmationResult:
        self.confirm_started.set()
        await self.release_confirmation.wait()
        observed_at = datetime.now(UTC)
        return ReservationConfirmationResult(
            provider=self.provider,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="test-reconciliation",
            observed_at=observed_at,
            payment_deadline=observed_at + timedelta(minutes=10),
            official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
        )

    async def drain_pending_calls(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


async def _seed_due_unknown(session_factory) -> tuple[str, str, str, datetime]:
    now = datetime.now(UTC)
    departure_at = now + timedelta(days=1)
    async with session_factory() as session:
        account = RailProviderAccount(
            provider=Provider.SRT,
            credentials_ciphertext="not-read-by-lease-race-test",
            credential_version=1,
            last_auth_status="authenticated",
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            origin_node_id="0551",
            destination="부산",
            destination_node_id="0020",
            travel_date=departure_at.date(),
            time_from=departure_at.time().replace(tzinfo=None),
            time_to=(departure_at + timedelta(hours=1)).time().replace(tzinfo=None),
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            train_numbers=["223"],
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key=f"manual-rearm-reconciliation-race-{now.timestamp()}",
            next_check_at=now - timedelta(seconds=1),
        )
        candidate = WatchCandidate(
            train_number="223",
            departure_at=departure_at,
            scheduled_departure_at=departure_at,
            arrival_at=departure_at + timedelta(hours=2),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:unknown-before-manual-rearm",
            idempotency_key=f"reserve-unknown-{now.timestamp()}",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            outcome=ReservationOutcome.UNKNOWN,
            result_reason_code=(ReservationResultReasonCode.RESERVATION_REQUEST_RESULT_UNKNOWN),
            credential_version=1,
            progress_stages=[
                {
                    "stage": "reservation_requested",
                    "occurred_at": (now - timedelta(minutes=19)).isoformat(),
                }
            ],
            confirmation_correlation_seats=[{"car_number": "4", "seat_number": "8A"}],
            confirmation_outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
            confirmation_source="srt-reservation-list",
            confirmation_observed_at=now - timedelta(minutes=5),
            last_reconciled_at=now - timedelta(minutes=5),
            reconciliation_attempt_count=3,
            next_reconcile_at=now - timedelta(seconds=1),
        )
        watch.candidates.append(candidate)
        session.add_all([account, watch, attempt])
        await session.commit()
        return watch.id, candidate.id, attempt.id, now


async def test_reconciliation_lease_blocks_manual_unknown_reservation_until_hold_is_persisted(
    app,
    monkeypatch,
) -> None:
    session_factory = app.state.test_session_factory
    monkeypatch.setattr(worker_module, "SessionFactory", session_factory)
    watch_id, candidate_id, attempt_id, seeded_at = await _seed_due_unknown(session_factory)
    adapter = BlockingHoldReconciliationAdapter()

    reconciliation = asyncio.create_task(
        worker_module._reconcile_reservation_attempt(attempt_id, adapter=adapter)
    )
    try:
        await asyncio.wait_for(adapter.confirm_started.wait(), timeout=2)

        async def current_credential_version(_session, _watch) -> int:
            return 1

        authorized_at = max(datetime.now(UTC), seeded_at + timedelta(seconds=1))
        async with session_factory() as session:
            result = await authorize_manual_reservation_rearm(
                session,
                watch_id,
                reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
                official_reservation_state_confirmed=True,
                dependencies=ManualReservationRearmDependencies(
                    reservation_dispatch_credential_version=current_credential_version,
                    is_payment_hold_ended=is_payment_hold_ended,
                    is_unresolved_unknown_manual_rearm_source=(
                        is_unresolved_unknown_manual_rearm_source
                    ),
                    add_outbox_event=add_outbox_event,
                    now=lambda: authorized_at,
                ),
            )
        assert result.created is True

        # The read-only reconciliation still owns the provider-wide execution epoch.
        # A queued immediate observation must fail to acquire that epoch and therefore
        # cannot consume the just-written manual authorization or call reserve_once.
        await worker_module._process_watch_group(
            [watch_id],
            authorized_at,
            provider=Provider.SRT,
            adapter=adapter,
        )
        assert adapter.observation_calls == 0
        assert adapter.reserve_calls == 0
    finally:
        adapter.release_confirmation.set()

    assert await asyncio.wait_for(reconciliation, timeout=2) == 1

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        attempt = await session.get(ReservationAttempt, attempt_id)
        attempt_count = await session.scalar(
            select(func.count())
            .select_from(ReservationAttempt)
            .where(ReservationAttempt.candidate_id == candidate_id)
        )
        assert watch is not None and watch.status is WatchStatus.PAYMENT_REQUIRED
        assert candidate is not None and candidate.state == "payment_required"
        assert attempt is not None
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert (
            attempt.confirmation_outcome
            is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        )
        assert candidate.manual_rearm_source_attempt_id == attempt_id
        assert attempt_count == 1

    # The same available adapter now represents a fresh follow-up cycle. The persisted
    # official hold makes the watch non-observable and leaves the stale approval unable
    # to create or execute a second attempt after the reconciliation lease is released.
    await worker_module._process_watch_group(
        [watch_id],
        datetime.now(UTC),
        provider=Provider.SRT,
        adapter=adapter,
    )
    assert adapter.observation_calls == 0
    assert adapter.reserve_calls == 0

    async with session_factory() as session:
        attempt_count = await session.scalar(
            select(func.count())
            .select_from(ReservationAttempt)
            .where(ReservationAttempt.candidate_id == candidate_id)
        )
    assert attempt_count == 1
