from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import rail_waitlist.services as services_module
from rail_waitlist.domain import Provider, SeatObservationStatus, WatchStatus
from rail_waitlist.models import (
    IdempotencyRecord,
    OutboxEvent,
    SeatObservation,
    Watch,
    WatchTransitionHistory,
)
from rail_waitlist.services import apply_watch_transition, transition_watch
from rail_waitlist.watch_management.transition_application import (
    WatchTransitionDependencies,
    WatchTransitionRejected,
)
from rail_waitlist.watch_management.transition_application import (
    apply_watch_transition as apply_watch_transition_application,
)
from rail_waitlist.watch_management.transition_policy import RejectedWatchTransition


def _make_watch(*, status: WatchStatus = WatchStatus.DRAFT) -> Watch:
    return Watch(
        id=f"transition-application-{status.value}",
        provider=Provider.MOCK,
        origin="서울",
        origin_node_id="N-SEOUL",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2030, 8, 1),
        time_from=time(8),
        time_to=time(12),
        train_numbers=["KTX-001"],
        notification_channel_ids=[],
        mode="official",
        status=status,
        dedupe_key=f"transition-application-{status.value}",
    )


def test_public_transition_functions_remain_owned_by_the_services_facade() -> None:
    assert apply_watch_transition is services_module.apply_watch_transition
    assert transition_watch is services_module.transition_watch
    assert apply_watch_transition.__module__ == "rail_waitlist.services"
    assert transition_watch.__module__ == "rail_waitlist.services"
    assert apply_watch_transition_application.__module__.endswith("transition_application")


class _RecordingSession:
    def __init__(self, replay: Watch | None = None) -> None:
        self.added: list[object] = []
        self.replay = replay

    def add(self, value: object) -> None:
        self.added.append(value)

    async def get(self, _model: object, _resource_id: str) -> Watch | None:
        return self.replay


async def test_replay_precedes_policy_and_returns_the_existing_resource(monkeypatch) -> None:
    stale = _make_watch(status=WatchStatus.EXPIRED)
    replay = _make_watch(status=WatchStatus.SCHEDULED)
    session = _RecordingSession(replay=replay)

    async def replay_resource(*_args: object, **_kwargs: object) -> str:
        return replay.id

    def fail_decision(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("policy must not run for an idempotent replay")

    monkeypatch.setattr(services_module, "get_idempotent_resource", replay_resource)
    monkeypatch.setattr(services_module, "decide_watch_transition", fail_decision)

    result = await apply_watch_transition(
        cast(AsyncSession, session), stale, WatchStatus.COMPLETED, "replay-key"
    )

    assert result is replay
    assert session.added == []


async def test_missing_replay_resource_falls_through_to_policy(monkeypatch) -> None:
    decision_calls = 0

    async def missing_replay(*_args: object, **_kwargs: object) -> str:
        return "missing-watch"

    def record_decision(current: WatchStatus, target: WatchStatus):
        nonlocal decision_calls
        decision_calls += 1
        return original_decision(current, target)

    original_decision = services_module.decide_watch_transition
    monkeypatch.setattr(services_module, "get_idempotent_resource", missing_replay)
    monkeypatch.setattr(services_module, "decide_watch_transition", record_decision)
    session = _RecordingSession(replay=None)
    watch = _make_watch()

    result = await apply_watch_transition(
        cast(AsyncSession, session), watch, WatchStatus.DRAFT, "replay-key"
    )

    assert result is watch
    assert decision_calls == 1
    assert session.added == []


async def test_noop_and_rejected_transitions_do_not_resolve_provider(monkeypatch) -> None:
    provider_calls = 0

    def fail_provider(_provider: Provider) -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must stay lazy")

    monkeypatch.setattr(services_module, "get_execution_provider", fail_provider)
    session = _RecordingSession()
    watch = _make_watch()

    result = await apply_watch_transition(cast(AsyncSession, session), watch, WatchStatus.DRAFT)
    assert result is watch

    with pytest.raises(HTTPException) as rejected:
        await apply_watch_transition(cast(AsyncSession, session), watch, WatchStatus.COMPLETED)

    assert rejected.value.status_code == 409
    assert rejected.value.detail == "cannot transition draft to completed"
    assert provider_calls == 0
    assert session.added == []


async def test_canonical_rejection_is_transport_independent() -> None:
    async def no_replay(*_args: object, **_kwargs: object) -> None:
        return None

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    def reject(current: WatchStatus, target: WatchStatus) -> RejectedWatchTransition:
        return RejectedWatchTransition(current, target, "rejected without transport")

    def unused(*_args: object, **_kwargs: object):
        raise AssertionError("rejected transition must not call later dependencies")

    dependencies = WatchTransitionDependencies(
        request_hash=lambda _value: "payload-hash",
        get_idempotent_resource=no_replay,
        decide_watch_transition=reject,
        get_execution_provider=unused,
        build_watch_transition_identity=unused,
        remember_idempotency=no_op,
        add_outbox_event=no_op,
        add_watch_notifications=no_op,
        now=unused,
    )

    with pytest.raises(WatchTransitionRejected, match="rejected without transport"):
        await apply_watch_transition_application(
            cast(AsyncSession, _RecordingSession()),
            _make_watch(),
            WatchStatus.COMPLETED,
            dependencies=dependencies,
        )


@pytest.mark.parametrize(
    ("seat_monitoring", "expects_next_check"),
    [(True, True), (False, False)],
)
async def test_allowed_scheduled_transition_resolves_provider_once_and_arms_due_time(
    monkeypatch,
    seat_monitoring: bool,
    expects_next_check: bool,
) -> None:
    provider_calls = 0
    clock_calls = 0
    transition_at = datetime(2030, 8, 1, 3, 4, 5, tzinfo=UTC)

    class FrozenDateTime:
        @classmethod
        def now(cls, timezone) -> datetime:
            nonlocal clock_calls
            clock_calls += 1
            assert timezone is UTC
            assert watch.status is WatchStatus.SCHEDULED
            assert watch.cooldown_until == datetime(2030, 8, 2, tzinfo=UTC)
            return transition_at

    class Adapter:
        def capabilities(self) -> object:
            nonlocal provider_calls
            provider_calls += 1
            assert watch.status is WatchStatus.SCHEDULED
            assert watch.updated_at == transition_at
            assert watch.cooldown_until is None
            return type("Capabilities", (), {"seat_monitoring": seat_monitoring})()

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(services_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(services_module, "get_execution_provider", lambda _provider: Adapter())
    monkeypatch.setattr(services_module, "remember_idempotency", no_op)
    monkeypatch.setattr(services_module, "add_outbox_event", no_op)
    monkeypatch.setattr(services_module, "add_watch_notifications", no_op)
    session = _RecordingSession()
    watch = _make_watch()
    watch.next_check_at = datetime(2030, 8, 1, tzinfo=UTC)
    watch.cooldown_until = datetime(2030, 8, 2, tzinfo=UTC)

    await apply_watch_transition(cast(AsyncSession, session), watch, WatchStatus.SCHEDULED)

    assert clock_calls == 1
    assert provider_calls == 1
    assert watch.status is WatchStatus.SCHEDULED
    assert watch.updated_at == transition_at
    assert (watch.next_check_at == watch.updated_at) is expects_next_check
    assert watch.cooldown_until is None


async def test_transition_artifact_order_identity_and_observation_remain_stable(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fail_provider(_provider: Provider) -> object:
        raise AssertionError("non-scheduled transitions must not resolve a provider")

    async def remember(*_args: object, **_kwargs: object) -> None:
        calls.append(("idempotency", None))

    async def add_outbox(*_args: object, **kwargs: object) -> None:
        calls.append(("status_outbox", kwargs))

    async def notify(*args: object, **kwargs: object) -> None:
        calls.append(("notification", (args, kwargs)))

    class OrderedSession(_RecordingSession):
        def add(self, value: object) -> None:
            super().add(value)
            calls.append(("history", value))

    monkeypatch.setattr(services_module, "get_execution_provider", fail_provider)
    monkeypatch.setattr(services_module, "remember_idempotency", remember)
    monkeypatch.setattr(services_module, "add_outbox_event", add_outbox)
    monkeypatch.setattr(services_module, "add_watch_notifications", notify)
    session = OrderedSession()
    watch = _make_watch(status=WatchStatus.WATCHING)
    observed_at = datetime(2030, 8, 1, tzinfo=UTC)
    observation = SeatObservation(
        candidate_id="candidate-1",
        status=SeatObservationStatus.AVAILABLE,
        source="test",
        observed_at=observed_at,
        fresh_until=observed_at + timedelta(minutes=1),
    )
    preserved_next_check = observed_at
    preserved_cooldown = observed_at + timedelta(days=1)
    watch.next_check_at = preserved_next_check
    watch.cooldown_until = preserved_cooldown

    await apply_watch_transition(
        cast(AsyncSession, session),
        watch,
        WatchStatus.SEAT_FOUND,
        reason="seat_found_reason",
        observation=observation,
    )

    assert [name for name, _value in calls] == [
        "history",
        "idempotency",
        "status_outbox",
        "notification",
    ]
    history = cast(WatchTransitionHistory, calls[0][1])
    outbox_kwargs = cast(dict[str, object], calls[2][1])
    notification_args, notification_kwargs = cast(
        tuple[tuple[object, ...], dict[str, object]], calls[3][1]
    )
    token = f"watching:seat_found:{watch.updated_at.isoformat()}"
    assert history.from_status is WatchStatus.WATCHING
    assert history.to_status is WatchStatus.SEAT_FOUND
    assert history.reason == "seat_found_reason"
    assert history.observation is observation
    assert outbox_kwargs["dedupe_key"] == f"watch:{watch.id}:transition:{token}"
    assert notification_args[3] == token
    assert notification_kwargs["reason"] == "seat_found_reason"
    assert watch.next_check_at == preserved_next_check
    assert watch.cooldown_until == preserved_cooldown


@pytest.mark.parametrize("commit_transition", [True, False])
async def test_transition_application_shares_the_callers_unit_of_work(
    db_engine,
    monkeypatch,
    commit_transition: bool,
) -> None:
    class Adapter:
        def capabilities(self) -> object:
            return type("Capabilities", (), {"seat_monitoring": True})()

    monkeypatch.setattr(services_module, "get_execution_provider", lambda _provider: Adapter())
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = _make_watch()
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        await apply_watch_transition(
            session,
            watch,
            WatchStatus.SCHEDULED,
            "application-uow-key",
        )
        if commit_transition:
            await session.commit()
        else:
            await session.rollback()

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)
        assert persisted is not None
        history_count = await session.scalar(
            select(func.count())
            .select_from(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch_id)
        )
        idempotency_count = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.key == "application-uow-key")
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == watch_id)
        )

    if commit_transition:
        assert persisted.status is WatchStatus.SCHEDULED
        assert persisted.next_check_at is not None
        assert history_count == idempotency_count == outbox_count == 1
    else:
        assert persisted.status is WatchStatus.DRAFT
        assert persisted.next_check_at is None
        assert history_count == idempotency_count == outbox_count == 0
