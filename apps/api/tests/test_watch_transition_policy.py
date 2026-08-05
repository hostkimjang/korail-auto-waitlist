from __future__ import annotations

from datetime import UTC, date, datetime, time
from itertools import product
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import rail_waitlist.services as services_module
from rail_waitlist.domain import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Provider,
    WatchStatus,
)
from rail_waitlist.models import IdempotencyRecord, OutboxEvent, Watch, WatchTransitionHistory
from rail_waitlist.services import apply_watch_transition, transition_watch
from rail_waitlist.watch_management.transition_policy import (
    AllowedWatchTransition,
    NextCheckPolicy,
    NoOpWatchTransition,
    RejectedWatchTransition,
    build_watch_transition_identity,
    decide_watch_transition,
)


def _make_watch(*, status: WatchStatus = WatchStatus.DRAFT) -> Watch:
    return Watch(
        id=f"transition-policy-{status.value}",
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
        dedupe_key=f"transition-policy-{status.value}",
    )


def test_public_transition_functions_remain_owned_by_the_services_facade() -> None:
    assert apply_watch_transition is services_module.apply_watch_transition
    assert transition_watch is services_module.transition_watch
    assert apply_watch_transition.__module__ == "rail_waitlist.services"
    assert transition_watch.__module__ == "rail_waitlist.services"


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    product(WatchStatus, repeat=2),
)
def test_transition_decision_covers_the_complete_status_matrix(
    current_status: WatchStatus,
    target_status: WatchStatus,
) -> None:
    decision = decide_watch_transition(current_status, target_status)

    if current_status is target_status:
        assert decision == NoOpWatchTransition(status=current_status)
    elif target_status in ALLOWED_TRANSITIONS[current_status]:
        assert isinstance(decision, AllowedWatchTransition)
        assert decision.previous_status is current_status
        assert decision.target_status is target_status
    else:
        assert decision == RejectedWatchTransition(
            previous_status=current_status,
            target_status=target_status,
            detail=f"cannot transition {current_status.value} to {target_status.value}",
        )


def test_transition_matrix_is_complete_for_every_watch_status() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(WatchStatus)
    assert all(
        target in WatchStatus for targets in ALLOWED_TRANSITIONS.values() for target in targets
    )


@pytest.mark.parametrize("current_status", list(WatchStatus))
@pytest.mark.parametrize("target_status", list(WatchStatus))
def test_allowed_transition_field_mutation_policy(
    current_status: WatchStatus,
    target_status: WatchStatus,
) -> None:
    decision = decide_watch_transition(current_status, target_status)
    if not isinstance(decision, AllowedWatchTransition):
        return

    if target_status is WatchStatus.SCHEDULED:
        assert decision.next_check_policy is NextCheckPolicy.TRANSITION_AT_IF_SEAT_MONITORING
        assert decision.clear_cooldown is True
    elif target_status is WatchStatus.PAUSED or target_status in TERMINAL_STATUSES:
        assert decision.next_check_policy is NextCheckPolicy.CLEAR
        assert decision.clear_cooldown is False
    else:
        assert decision.next_check_policy is NextCheckPolicy.PRESERVE
        assert decision.clear_cooldown is False


@pytest.mark.parametrize(
    ("reason", "expected_reason"),
    [
        (None, "transition_to_seat_found"),
        ("", "transition_to_seat_found"),
        ("   ", "   "),
        ("x" * 161, "x" * 160),
    ],
)
def test_transition_identity_preserves_reason_and_event_key_contract(
    reason: str | None,
    expected_reason: str,
) -> None:
    transition = decide_watch_transition(WatchStatus.WATCHING, WatchStatus.SEAT_FOUND)
    assert isinstance(transition, AllowedWatchTransition)
    transition_at = datetime(2030, 8, 1, 3, 4, 5, 6789, tzinfo=UTC)

    identity = build_watch_transition_identity(
        transition,
        watch_id="watch-1",
        transition_at=transition_at,
        reason=reason,
    )

    token = "watching:seat_found:2030-08-01T03:04:05.006789+00:00"
    assert identity.transition_at is transition_at
    assert identity.reason == expected_reason
    assert identity.transition_token == token
    assert identity.status_event_dedupe_key == f"watch:watch-1:transition:{token}"


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

    class Adapter:
        def capabilities(self) -> object:
            nonlocal provider_calls
            provider_calls += 1
            assert watch.status is WatchStatus.SCHEDULED
            assert watch.updated_at.tzinfo is UTC
            assert watch.cooldown_until is None
            return type("Capabilities", (), {"seat_monitoring": seat_monitoring})()

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(services_module, "get_execution_provider", lambda _provider: Adapter())
    monkeypatch.setattr(services_module, "remember_idempotency", no_op)
    monkeypatch.setattr(services_module, "add_outbox_event", no_op)
    monkeypatch.setattr(services_module, "add_watch_notifications", no_op)
    session = _RecordingSession()
    watch = _make_watch()
    watch.next_check_at = datetime(2030, 8, 1, tzinfo=UTC)
    watch.cooldown_until = datetime(2030, 8, 2, tzinfo=UTC)

    await apply_watch_transition(cast(AsyncSession, session), watch, WatchStatus.SCHEDULED)

    assert provider_calls == 1
    assert watch.status is WatchStatus.SCHEDULED
    assert watch.updated_at.tzinfo is UTC
    assert (watch.next_check_at == watch.updated_at) is expects_next_check
    assert watch.cooldown_until is None


async def test_transition_artifact_order_and_identity_remain_stable(monkeypatch) -> None:
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
    preserved_next_check = datetime(2030, 8, 1, tzinfo=UTC)
    preserved_cooldown = datetime(2030, 8, 2, tzinfo=UTC)
    watch.next_check_at = preserved_next_check
    watch.cooldown_until = preserved_cooldown

    await apply_watch_transition(
        cast(AsyncSession, session),
        watch,
        WatchStatus.SEAT_FOUND,
        reason="seat_found_reason",
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
    assert outbox_kwargs["dedupe_key"] == f"watch:{watch.id}:transition:{token}"
    assert notification_args[3] == token
    assert notification_kwargs["reason"] == "seat_found_reason"
    assert watch.next_check_at == preserved_next_check
    assert watch.cooldown_until == preserved_cooldown


@pytest.mark.parametrize("commit_transition", [True, False])
async def test_policy_wired_transition_shares_the_callers_unit_of_work(
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
            "policy-uow-key",
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
            .where(IdempotencyRecord.key == "policy-uow-key")
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
