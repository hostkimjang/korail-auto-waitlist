from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from rail_waitlist.domain import (
    Provider,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import SeatObservation, Watch, WatchCandidate
from rail_waitlist.observations.group_application import (
    ObservationGroupDependencies,
    ObservationTarget,
    _locked_deferred_watches_query,
    process_watch_group_observation,
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
    provider = Provider.MOCK

    def __init__(
        self,
        status: SeatObservationStatus,
        *,
        reservation_once: bool = False,
        return_matching_seat_class: bool = True,
    ) -> None:
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
) -> str:
    now = now or datetime.now(UTC)
    async with session_factory() as session:
        watch = Watch(
            provider=Provider.MOCK,
            origin="서울",
            origin_node_id="MOCK-SEOUL",
            destination="부산",
            destination_node_id="MOCK-BUSAN",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(8),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
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
        assert observation_count == 2


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
        assert candidate is not None and candidate.state == "active"
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0
