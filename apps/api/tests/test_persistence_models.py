from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import (
    Provider,
    ProviderCircuitState,
    ReservationOutcome,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    ProviderCircuit,
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)


def make_watch(*, unchanged_runs: int = 0) -> Watch:
    return Watch(
        provider=Provider.MOCK,
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 1),
        time_from=time(9),
        time_to=time(12),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["MOCK-001", "MOCK-002"],
        notification_channel_ids=[],
        mode="mock",
        status=WatchStatus.WATCHING,
        dedupe_key="persistence-test",
        unchanged_runs=unchanged_runs,
    )


def make_candidate(priority: int, train_number: str) -> WatchCandidate:
    return WatchCandidate(
        train_number=train_number,
        departure_at=datetime(2026, 8, 1, 1 + priority, tzinfo=timezone.utc),
        arrival_at=datetime(2026, 8, 1, 4 + priority, tzinfo=timezone.utc),
        seat_class="standard",
        priority=priority,
    )


@pytest.mark.asyncio
async def test_persistence_graph_cascades_without_raw_provider_payload(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime(2026, 7, 29, 5, tzinfo=timezone.utc)

    async with session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        watch = make_watch(unchanged_runs=3)
        candidate = make_candidate(1, "MOCK-001")
        observation = SeatObservation(
            status=SeatObservationStatus.WAITLIST_AVAILABLE,
            source="authorized-test-adapter",
            observed_at=observed_at,
            fresh_until=observed_at + timedelta(seconds=30),
            error_category=None,
        )
        candidate.observations.append(observation)
        candidate.reservation_attempt = ReservationAttempt(
            idempotency_key="reserve:mock-001",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=observed_at + timedelta(minutes=15),
            official_handoff_url="https://example.invalid/payment",
        )
        watch.candidates.append(candidate)
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.WATCHING,
                to_status=WatchStatus.PAYMENT_REQUIRED,
                reason="authorized_reservation_requires_payment",
                observation=observation,
            )
        )
        session.add(watch)
        await session.commit()

        assert watch.unchanged_runs == 3
        assert candidate.state == "active"
        assert observation.candidate is candidate
        assert candidate.reservation_attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert watch.transition_history[0].observation is observation

        await session.delete(watch)
        await session.commit()

        for model in (WatchCandidate, SeatObservation, ReservationAttempt, WatchTransitionHistory):
            assert await session.scalar(select(func.count()).select_from(model)) == 0

    assert set(SeatObservation.__table__.columns.keys()) == {
        "id",
        "candidate_id",
        "status",
        "source",
        "observed_at",
        "fresh_until",
        "error_category",
    }


@pytest.mark.asyncio
async def test_reservation_attempt_enforces_episode_sequence_and_global_idempotency_key(
    db_engine,
):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        watch = make_watch()
        first = make_candidate(1, "MOCK-001")
        second = make_candidate(2, "MOCK-002")
        watch.candidates.extend([first, second])
        session.add(watch)
        await session.commit()
        first_id = first.id
        second_id = second.id

        session.add(
            ReservationAttempt(
                candidate_id=first_id,
                attempt_sequence=1,
                episode_key="availability:first",
                idempotency_key="reservation-once",
                outcome=ReservationOutcome.RESERVED,
            )
        )
        await session.commit()

        session.add(
            ReservationAttempt(
                candidate_id=first_id,
                attempt_sequence=2,
                episode_key="availability:second",
                idempotency_key="different-key",
                outcome=ReservationOutcome.FAILED,
            )
        )
        await session.commit()

        session.add(
            ReservationAttempt(
                candidate_id=first_id,
                attempt_sequence=2,
                episode_key="availability:third",
                idempotency_key="duplicate-sequence",
                outcome=ReservationOutcome.FAILED,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add(
            ReservationAttempt(
                candidate_id=first_id,
                attempt_sequence=3,
                episode_key="availability:second",
                idempotency_key="duplicate-episode",
                outcome=ReservationOutcome.FAILED,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add(
            ReservationAttempt(
                candidate_id=second_id,
                attempt_sequence=1,
                episode_key="availability:other-candidate",
                idempotency_key="reservation-once",
                outcome=ReservationOutcome.FAILED,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    # RESERVED means a temporary provider-side hold; payment completion is a separate state.
    assert ReservationOutcome.RESERVED.value == "reserved"
    assert ReservationOutcome.RESERVED.value != WatchStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_candidate_state_checks_and_suppression_reference(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        watch = make_watch()
        winner = make_candidate(1, "MOCK-001")
        suppressed = make_candidate(2, "MOCK-002")
        suppressed.state = "suppressed_by_priority"
        suppressed.suppressed_by_candidate = winner
        watch.candidates.extend([winner, suppressed])
        session.add(watch)
        await session.commit()

        assert suppressed.suppressed_by_candidate_id == winner.id
        await session.delete(winner)
        await session.commit()
        await session.refresh(suppressed)
        assert suppressed.suppressed_by_candidate_id is None
        assert suppressed.state == "suppressed_by_priority"
        suppressed_id = suppressed.id

        suppressed.state = "not-a-state"
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        suppressed = await session.get(WatchCandidate, suppressed_id)
        assert suppressed is not None
        suppressed.suppressed_by_candidate_id = suppressed_id
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_temporal_and_nonnegative_checks_are_database_enforced(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime(2026, 7, 29, 5, tzinfo=timezone.utc)

    async with session_factory() as session:
        session.add(make_watch(unchanged_runs=-1))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        watch = make_watch()
        candidate = make_candidate(1, "MOCK-001")
        watch.candidates.append(candidate)
        session.add(watch)
        await session.commit()

        session.add(
            SeatObservation(
                candidate_id=candidate.id,
                status=SeatObservationStatus.STALE,
                source="authorized-test-adapter",
                observed_at=observed_at,
                fresh_until=observed_at - timedelta(seconds=1),
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add(
            ProviderCircuit(
                provider=Provider.MOCK,
                state=ProviderCircuitState.OPEN,
                reason="provider_rate_limited",
                opened_at=observed_at,
                cooldown_until=observed_at + timedelta(minutes=5),
                manual_resume_required=False,
                generation=-1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


def test_provider_circuit_half_open_never_implies_automatic_resume():
    circuit = ProviderCircuit(
        provider=Provider.MOCK,
        state=ProviderCircuitState.HALF_OPEN,
        reason="explicit_probe",
        manual_resume_required=True,
    )

    assert circuit.state is ProviderCircuitState.HALF_OPEN
    assert circuit.manual_resume_required is True
