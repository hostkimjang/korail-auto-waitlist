from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import true
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import Provider, ReservationOutcome, WatchStatus
from rail_waitlist.models import ReservationAttempt, Watch, WatchCandidate
from rail_waitlist.observations.due_pipeline_application import (
    DuePipelineDependencies,
    process_due_pipeline,
    process_provider_due_pipeline,
    process_provider_due_pipelines,
)
from rail_waitlist.reservations.reconciliation_application import (
    _reservation_reconciliation_due_clause,
)

NOW = datetime(2026, 8, 5, 1, 2, 3, tzinfo=timezone.utc)


class FakeRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class FakeSession:
    def __init__(self, row_sets: list[list[tuple[object, ...]]], events: list[str]) -> None:
        self._row_sets = iter(row_sets)
        self._events = events
        self._query_number = 0

    async def __aenter__(self) -> FakeSession:
        self._events.append("session-enter")
        return self

    async def __aexit__(self, *_args) -> None:
        self._events.append("session-exit")

    async def execute(self, _statement) -> FakeRows:
        self._query_number += 1
        self._events.append(f"query-{self._query_number}")
        return FakeRows(next(self._row_sets))


def _dependencies(
    *,
    process_watch_group,
    reconcile_reservation_attempt,
    now=lambda: NOW,
    session_factory=lambda: FakeSession([[], []], []),
    get_execution_provider=lambda _provider: object(),
    arm_provider_watches=None,
    expire_elapsed_watches=None,
    recover_stale_reservation_attempts=None,
    close_execution_adapter=None,
) -> DuePipelineDependencies:
    async def no_op_arm(_provider, _now, *, adapter=None) -> int:
        return 0

    async def no_op_lifecycle(_session, _now) -> int:
        return 0

    async def no_op_close(_adapter, _provider) -> None:
        return None

    return DuePipelineDependencies(
        session_factory=session_factory,
        get_execution_provider=get_execution_provider,
        arm_provider_watches=arm_provider_watches or no_op_arm,
        expire_elapsed_watches=expire_elapsed_watches or no_op_lifecycle,
        recover_stale_reservation_attempts=(recover_stale_reservation_attempts or no_op_lifecycle),
        process_watch_group=process_watch_group,
        reconcile_reservation_attempt=reconcile_reservation_attempt,
        close_execution_adapter=close_execution_adapter or no_op_close,
        reservation_reconciliation_due_clause=lambda _now: true(),
        now=now,
    )


async def test_provider_pipeline_keeps_groups_serial_and_uses_fresh_group_clock() -> None:
    calls: list[tuple[str, object]] = []
    adapter = object()
    group_times = iter([NOW, NOW + timedelta(seconds=8)])

    async def process_group(watch_ids, now, *, provider, adapter) -> None:
        calls.append(("watch", (provider, watch_ids, now, adapter)))
        await asyncio.sleep(0)

    async def reconcile(attempt_id, *, adapter) -> int:
        calls.append(("reconcile", (attempt_id, adapter)))
        return 1

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
        now=lambda: next(group_times),
    )

    await process_provider_due_pipeline(
        Provider.SRT,
        [["watch-1"], ["watch-2"]],
        ["attempt-1"],
        adapter,
        dependencies=dependencies,
    )

    assert calls == [
        ("watch", (Provider.SRT, ["watch-1"], NOW, adapter)),
        ("watch", (Provider.SRT, ["watch-2"], NOW + timedelta(seconds=8), adapter)),
        ("reconcile", ("attempt-1", adapter)),
    ]


async def test_provider_pipelines_overlap_and_raise_only_after_peers_finish() -> None:
    srt_started = asyncio.Event()
    korail_started = asyncio.Event()
    korail_completed = asyncio.Event()
    adapters = {Provider.SRT: object(), Provider.KORAIL: object()}

    async def process_group(_watch_ids, _now, *, provider, adapter) -> None:
        assert adapter is adapters[provider]
        if provider is Provider.SRT:
            srt_started.set()
            await asyncio.wait_for(korail_started.wait(), timeout=1)
            raise RuntimeError("synthetic SRT failure")
        korail_started.set()
        await asyncio.wait_for(srt_started.wait(), timeout=1)
        korail_completed.set()

    async def reconcile(_attempt_id, *, adapter) -> int:
        return 0

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
    )

    with pytest.raises(RuntimeError, match="synthetic SRT failure"):
        await process_provider_due_pipelines(
            [Provider.SRT, Provider.KORAIL],
            {Provider.SRT: [["srt-watch"]], Provider.KORAIL: [["korail-watch"]]},
            {},
            adapters,
            dependencies=dependencies,
        )

    assert korail_completed.is_set()


async def test_provider_pipelines_normalize_duplicate_provider_order() -> None:
    calls: list[tuple[Provider, list[str]]] = []
    adapter = object()

    async def process_group(watch_ids, _now, *, provider, adapter) -> None:
        calls.append((provider, watch_ids))

    async def reconcile(_attempt_id, *, adapter) -> int:
        return 0

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
    )

    await process_provider_due_pipelines(
        [Provider.SRT, Provider.SRT],
        {Provider.SRT: [["watch-1"]]},
        {},
        {Provider.SRT: adapter},
        dependencies=dependencies,
    )

    assert calls == [(Provider.SRT, ["watch-1"])]


async def test_due_pipeline_preserves_sweep_order_grouping_reuse_and_unique_close() -> None:
    events: list[str] = []
    provider_requests: list[Provider] = []
    srt_adapter = object()
    shared_adapter = object()
    adapters = {
        Provider.SRT: srt_adapter,
        Provider.MOCK: shared_adapter,
        Provider.KORAIL: shared_adapter,
    }
    session = FakeSession(
        [
            [
                ("attempt-srt", Provider.SRT),
                ("attempt-korail", Provider.KORAIL),
            ],
            [
                ("watch-1", "same", Provider.SRT),
                ("watch-2", "same", Provider.SRT),
                ("watch-mock", "mock", Provider.MOCK),
            ],
        ],
        events,
    )

    def get_provider(provider: Provider):
        provider_requests.append(provider)
        events.append(f"get-{provider.value}")
        return adapters[provider]

    async def arm(provider, _now, *, adapter=None) -> int:
        assert adapter is adapters[provider]
        events.append(f"arm-{provider.value}")
        return 1

    async def expire(_session, _now) -> int:
        events.append("expire")
        return 0

    async def recover(_session, _now) -> int:
        events.append("recover")
        return 0

    provider_events: list[tuple[str, Provider, object]] = []

    async def process_group(watch_ids, _now, *, provider, adapter) -> None:
        provider_events.append((f"watch:{','.join(watch_ids)}", provider, adapter))

    async def reconcile(attempt_id, *, adapter) -> int:
        provider = Provider.SRT if attempt_id == "attempt-srt" else Provider.KORAIL
        provider_events.append((f"reconcile:{attempt_id}", provider, adapter))
        return 1

    closed: list[tuple[Provider, object]] = []

    async def close(adapter, provider) -> None:
        closed.append((provider, adapter))

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
        session_factory=lambda: session,
        get_execution_provider=get_provider,
        arm_provider_watches=arm,
        expire_elapsed_watches=expire,
        recover_stale_reservation_attempts=recover,
        close_execution_adapter=close,
    )

    group_count = await process_due_pipeline(
        [Provider.SRT, Provider.SRT],
        dependencies=dependencies,
    )

    assert group_count == 2
    assert events[:10] == [
        "session-enter",
        "expire",
        "recover",
        "session-exit",
        "get-srt",
        "arm-srt",
        "session-enter",
        "query-1",
        "query-2",
        "session-exit",
    ]
    assert provider_requests == [Provider.SRT, Provider.MOCK, Provider.KORAIL]
    srt_events = [event for event in provider_events if event[1] is Provider.SRT]
    assert srt_events == [
        ("watch:watch-1,watch-2", Provider.SRT, srt_adapter),
        ("reconcile:attempt-srt", Provider.SRT, srt_adapter),
    ]
    assert ("watch:watch-mock", Provider.MOCK, shared_adapter) in provider_events
    assert ("reconcile:attempt-korail", Provider.KORAIL, shared_adapter) in provider_events
    assert closed == [(Provider.SRT, srt_adapter), (Provider.MOCK, shared_adapter)]


async def test_reconciliation_only_sweep_counts_zero_and_closes_adapter() -> None:
    adapter = object()
    session = FakeSession([[("attempt-1", Provider.SRT)], []], [])
    closed: list[tuple[Provider, object]] = []

    async def process_group(_watch_ids, _now, *, provider, adapter) -> None:
        raise AssertionError("no observation group should be processed")

    async def reconcile(_attempt_id, *, adapter) -> int:
        return 1

    async def close(target_adapter, provider) -> None:
        closed.append((provider, target_adapter))

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
        session_factory=lambda: session,
        get_execution_provider=lambda _provider: adapter,
        close_execution_adapter=close,
    )

    assert await process_due_pipeline([], dependencies=dependencies) == 0

    assert closed == [(Provider.SRT, adapter)]


async def test_due_pipeline_closes_created_adapter_when_provider_pipeline_fails() -> None:
    adapter = object()
    session = FakeSession([[], [("watch-1", "group-1", Provider.MOCK)]], [])
    closed: list[tuple[Provider, object]] = []

    async def process_group(_watch_ids, _now, *, provider, adapter) -> None:
        raise RuntimeError("synthetic observation failure")

    async def reconcile(_attempt_id, *, adapter) -> int:
        return 0

    async def close(target_adapter, provider) -> None:
        closed.append((provider, target_adapter))

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
        session_factory=lambda: session,
        get_execution_provider=lambda _provider: adapter,
        close_execution_adapter=close,
    )

    with pytest.raises(RuntimeError, match="synthetic observation failure"):
        await process_due_pipeline([], dependencies=dependencies)

    assert closed == [(Provider.MOCK, adapter)]


async def test_db_recovery_runs_before_provider_adapter_creation_failure() -> None:
    events: list[str] = []
    session = FakeSession([], events)

    async def expire(_session, _now) -> int:
        events.append("expire")
        return 0

    async def recover(_session, _now) -> int:
        events.append("recover")
        return 1

    def fail_provider(provider: Provider) -> object:
        events.append(f"get-{provider.value.lower()}")
        raise RuntimeError("synthetic provider construction failure")

    async def unexpected_group(*_args, **_kwargs) -> None:
        raise AssertionError("provider groups must not run")

    async def unexpected_reconciliation(*_args, **_kwargs) -> int:
        raise AssertionError("reconciliation must not run")

    dependencies = _dependencies(
        process_watch_group=unexpected_group,
        reconcile_reservation_attempt=unexpected_reconciliation,
        session_factory=lambda: session,
        get_execution_provider=fail_provider,
        expire_elapsed_watches=expire,
        recover_stale_reservation_attempts=recover,
    )

    with pytest.raises(RuntimeError, match="synthetic provider construction failure"):
        await process_due_pipeline([Provider.KORAIL], dependencies=dependencies)

    assert events == [
        "session-enter",
        "expire",
        "recover",
        "session-exit",
        "get-korail",
    ]


async def test_due_pipeline_queries_real_db_with_exact_predicates_and_order(
    app,
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    def watch(
        identifier: str,
        *,
        provider: Provider = Provider.SRT,
        status: WatchStatus = WatchStatus.WATCHING,
        next_check_at: datetime | None = None,
        created_at: datetime = NOW,
    ) -> Watch:
        return Watch(
            id=identifier,
            provider=provider,
            origin="서울",
            destination="부산",
            travel_date=date(2026, 8, 6),
            time_from=time(8),
            time_to=time(12),
            status=status,
            mode="official",
            dedupe_key=f"dedupe-{identifier}",
            next_check_at=next_check_at,
            created_at=created_at,
        )

    def attempt(
        identifier: str,
        target_watch: Watch,
        *,
        outcome: ReservationOutcome,
        credential_version: int | None,
        finished_at: datetime,
        reconciliation_attempt_count: int = 0,
        next_reconcile_at: datetime | None = None,
    ) -> ReservationAttempt:
        candidate = WatchCandidate(
            id=f"candidate-{identifier}",
            train_number=identifier,
            departure_at=NOW + timedelta(hours=2),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        target_watch.candidates.append(candidate)
        return ReservationAttempt(
            id=identifier,
            candidate=candidate,
            attempt_sequence=1,
            episode_key=f"episode-{identifier}",
            idempotency_key=f"idempotency-{identifier}",
            started_at=finished_at - timedelta(minutes=1),
            finished_at=finished_at,
            outcome=outcome,
            credential_version=credential_version,
            reconciliation_attempt_count=reconciliation_attempt_count,
            next_reconcile_at=next_reconcile_at,
        )

    due_watch_early = watch(
        "due-watch-early",
        status=WatchStatus.SCHEDULED,
        next_check_at=NOW - timedelta(seconds=1),
        created_at=NOW - timedelta(minutes=20),
    )
    due_watch_late = watch(
        "due-watch-late",
        next_check_at=NOW,
        created_at=NOW - timedelta(minutes=10),
    )
    excluded_watches = [
        watch(
            "excluded-status",
            status=WatchStatus.DRAFT,
            next_check_at=NOW - timedelta(minutes=1),
        ),
        watch("excluded-null-next-check"),
        watch("excluded-future", next_check_at=NOW + timedelta(minutes=1)),
    ]

    reconciliation_watches = [
        watch("reconcile-early-watch"),
        watch("reconcile-late-watch", status=WatchStatus.PAYMENT_REQUIRED),
        watch("excluded-outcome-watch"),
        watch("excluded-credential-watch"),
        watch("excluded-not-due-watch"),
        watch("excluded-provider-watch", provider=Provider.MOCK),
    ]
    attempts = [
        attempt(
            "reconcile-early",
            reconciliation_watches[0],
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=1,
            finished_at=NOW - timedelta(minutes=30),
        ),
        attempt(
            "reconcile-late",
            reconciliation_watches[1],
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            credential_version=1,
            finished_at=NOW - timedelta(minutes=20),
        ),
        attempt(
            "excluded-outcome",
            reconciliation_watches[2],
            outcome=ReservationOutcome.RESERVED,
            credential_version=1,
            finished_at=NOW - timedelta(minutes=40),
        ),
        attempt(
            "excluded-credential",
            reconciliation_watches[3],
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=None,
            finished_at=NOW - timedelta(minutes=40),
        ),
        attempt(
            "excluded-not-due",
            reconciliation_watches[4],
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=1,
            finished_at=NOW - timedelta(minutes=40),
            reconciliation_attempt_count=1,
            next_reconcile_at=NOW + timedelta(minutes=1),
        ),
        attempt(
            "excluded-provider",
            reconciliation_watches[5],
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=1,
            finished_at=NOW - timedelta(minutes=40),
        ),
    ]

    async with factory() as session:
        session.add_all(
            [
                due_watch_early,
                due_watch_late,
                *excluded_watches,
                *reconciliation_watches,
                *attempts,
            ]
        )
        await session.commit()

    selected_watch_groups: list[list[str]] = []
    selected_attempts: list[str] = []
    adapter = object()

    async def process_group(watch_ids, _now, *, provider, adapter) -> None:
        assert provider is Provider.SRT
        selected_watch_groups.append(watch_ids)

    async def reconcile(attempt_id, *, adapter) -> int:
        selected_attempts.append(attempt_id)
        return 1

    dependencies = _dependencies(
        process_watch_group=process_group,
        reconcile_reservation_attempt=reconcile,
        session_factory=app.state.test_session_factory,
        get_execution_provider=lambda _provider: adapter,
    )
    dependencies = replace(
        dependencies,
        reservation_reconciliation_due_clause=_reservation_reconciliation_due_clause,
    )

    assert await process_due_pipeline([], dependencies=dependencies) == 2
    assert selected_watch_groups == [["due-watch-early"], ["due-watch-late"]]
    assert selected_attempts == ["reconcile-early", "reconcile-late"]
