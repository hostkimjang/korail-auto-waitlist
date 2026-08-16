from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import ReservationAttempt, SeatObservation, Watch, WatchCandidate
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
)
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
)
from rail_waitlist.reservations.retry_fence_contracts import (
    AutomaticReservationRetryFenceReason,
)
from rail_waitlist.watch_management.read_model import watch_read

NOW = datetime(2026, 8, 11, 5, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "in_flight_until", "expected"),
    [
        (WatchStatus.WATCHING, NOW + timedelta(seconds=30), "in_progress"),
        (WatchStatus.WATCHING, NOW, "idle"),
        (WatchStatus.WATCHING, NOW - timedelta(seconds=1), "idle"),
        (WatchStatus.PAUSED, NOW + timedelta(seconds=30), "idle"),
    ],
)
async def test_watch_read_projects_only_an_active_unexpired_observation_claim_as_in_progress(
    db_engine,
    status: WatchStatus,
    in_flight_until: datetime,
    expected: str,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            destination="부산",
            travel_date=date(2026, 8, 12),
            time_from=time(8),
            time_to=time(12),
            status=status,
            mode="official",
            dedupe_key=f"read-observation-{status.value}-{expected}",
            next_check_at=NOW - timedelta(seconds=1),
            observation_in_flight_until=in_flight_until,
            candidates=[],
        )
        session.add(watch)
        await session.flush()

        projected = await watch_read(
            session,
            watch,
            latest_observations={},
            latest_reservation_attempts={},
            manual_rearm_account_versions={},
            read_at=NOW,
        )

    assert projected.observation_execution_state == expected
    assert projected.next_check_at == NOW - timedelta(seconds=1)


@pytest.mark.parametrize("provider", [Provider.KORAIL, Provider.SRT])
async def test_watch_read_projects_consumed_confirmed_absent_recovery_as_closed_fence(
    db_engine,
    provider: Provider,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = Watch(
            provider=provider,
            origin="대전",
            destination="서울",
            travel_date=date(2026, 8, 12),
            time_from=time(8),
            time_to=time(12),
            status=WatchStatus.WATCHING,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            dedupe_key=f"read-confirmed-absent-retry-fence-{provider.value}",
        )
        candidate = WatchCandidate(
            train_number="240",
            departure_at=NOW + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        source_attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=1,
            episode_key="availability:source-observation",
            idempotency_key=f"read-confirmed-absent-source-{provider.value}",
            started_at=NOW - timedelta(minutes=3),
            finished_at=NOW - timedelta(minutes=2, seconds=50),
            outcome=ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="official-reservation-list",
            confirmation_observed_at=NOW - timedelta(minutes=2, seconds=40),
            last_reconciled_at=NOW - timedelta(minutes=2),
            reconciliation_attempt_count=1,
            reconciliation_resolution=ReservationReconciliationResolution.CONFIRMED_ABSENT,
        )
        session.add(source_attempt)
        await session.flush()

        recovery_attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=2,
            episode_key=f"confirmed-absent-retry:{source_attempt.id}",
            idempotency_key=f"read-confirmed-absent-recovery-{provider.value}",
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW - timedelta(seconds=50),
            outcome=ReservationOutcome.UNKNOWN,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="official-reservation-list",
            confirmation_observed_at=NOW - timedelta(seconds=40),
            last_reconciled_at=NOW - timedelta(seconds=10),
            reconciliation_attempt_count=1,
            reconciliation_resolution=ReservationReconciliationResolution.CONFIRMED_ABSENT,
        )
        latest_observation = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.AVAILABLE,
            source="official-seat-source",
            observed_at=NOW,
            fresh_until=NOW + timedelta(minutes=1),
        )
        session.add_all([recovery_attempt, latest_observation])
        await session.flush()

        projected = await watch_read(
            session,
            watch,
            read_at=NOW,
        )

    attempt = projected.candidates[0].latest_reservation_attempt
    assert attempt is not None
    assert (
        attempt.automatic_reservation_retry_fence_reason
        is AutomaticReservationRetryFenceReason.CONFIRMED_ABSENT_RECOVERY_CONSUMED
    )
    assert attempt.manual_check_required is False
    assert (
        projected.model_dump(mode="json")["candidates"][0]["latest_reservation_attempt"][
            "automatic_reservation_retry_fence_reason"
        ]
        == "confirmed_absent_recovery_consumed"
    )
    assert (
        "episode_key"
        not in projected.model_dump(mode="json")["candidates"][0]["latest_reservation_attempt"]
    )
