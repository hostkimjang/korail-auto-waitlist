from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import rail_waitlist.services as services_module
from rail_waitlist.domain import (
    Provider,
    ReservationPolicy,
    SeatObservationMode,
    WatchStatus,
)
from rail_waitlist.models import OutboxEvent, Watch
from rail_waitlist.policy import build_watch_dedupe_key
from rail_waitlist.schemas import WatchUpdate
from rail_waitlist.services import (
    _ensure_focused_observation_capacity,
    update_watch,
    validate_channel_ids,
)
from rail_waitlist.watch_management.update_application import (
    MAX_FOCUSED_WATCHES_PER_PROVIDER,
    WatchCommandConflict,
    WatchCommandNotFound,
    WatchCommandValidationError,
    WatchUpdateDependencies,
    ensure_focused_observation_capacity,
)
from rail_waitlist.watch_management.update_application import (
    update_watch as update_watch_application,
)
from rail_waitlist.watch_management.update_application import (
    validate_channel_ids as validate_channel_ids_application,
)


def _make_watch(*, status: WatchStatus = WatchStatus.DRAFT, suffix: str = "one") -> Watch:
    return Watch(
        id=f"watch-update-{suffix}",
        provider=Provider.MOCK,
        origin="서울",
        origin_node_id="N-SEOUL",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2030, 8, 1),
        time_from=time(8),
        time_to=time(12),
        seat_class="standard",
        passenger_count=1,
        train_numbers=["KTX-001"],
        notification_channel_ids=[],
        mode="official",
        reservation_policy=ReservationPolicy.NOTIFY_ONLY,
        seat_observation_mode=SeatObservationMode.BALANCED,
        focused_observation_interval_seconds=25,
        status=status,
        dedupe_key=f"watch-update-{suffix}",
    )


async def _validate_channels(_session: AsyncSession, _channel_ids: list[str]) -> None:
    return None


async def _ensure_capacity(
    _session: AsyncSession,
    _provider: Provider,
    *,
    exclude_watch_id: str | None = None,
) -> None:
    del exclude_watch_id


def test_services_keeps_watch_update_command_compatibility_ownership() -> None:
    assert update_watch is services_module.update_watch
    assert validate_channel_ids is services_module.validate_channel_ids
    assert _ensure_focused_observation_capacity is (
        services_module._ensure_focused_observation_capacity
    )
    assert update_watch.__module__ == "rail_waitlist.services"
    assert update_watch_application.__module__.endswith("update_application")
    assert MAX_FOCUSED_WATCHES_PER_PROVIDER == 3


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (WatchCommandNotFound("watch not found"), 404),
        (WatchCommandConflict("update conflict"), 409),
        (WatchCommandValidationError("invalid update"), 422),
    ],
)
async def test_services_maps_only_watch_command_errors_to_existing_http_statuses(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    status_code: int,
) -> None:
    async def fail_update(*_args: object, **_kwargs: object) -> Watch:
        raise error

    monkeypatch.setattr(services_module, "update_watch_application", fail_update)

    with pytest.raises(HTTPException) as raised:
        await update_watch(
            cast(AsyncSession, object()),
            _make_watch(),
            WatchUpdate(passenger_count=2),
        )

    assert raised.value.status_code == status_code
    assert raised.value.detail == str(error)


async def test_services_keeps_create_validator_http_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_capacity(*_args: object, **_kwargs: object) -> None:
        raise WatchCommandConflict("focused conflict")

    async def fail_channels(*_args: object, **_kwargs: object) -> None:
        raise WatchCommandValidationError("channel invalid")

    monkeypatch.setattr(
        services_module,
        "ensure_focused_observation_capacity_application",
        fail_capacity,
    )
    monkeypatch.setattr(services_module, "validate_channel_ids_application", fail_channels)

    with pytest.raises(HTTPException) as capacity:
        await _ensure_focused_observation_capacity(
            cast(AsyncSession, object()),
            Provider.MOCK,
        )
    with pytest.raises(HTTPException) as channels:
        await validate_channel_ids(cast(AsyncSession, object()), ["missing"])

    assert (capacity.value.status_code, capacity.value.detail) == (409, "focused conflict")
    assert (channels.value.status_code, channels.value.detail) == (422, "channel invalid")


async def test_services_does_not_translate_unrelated_update_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_update(*_args: object, **_kwargs: object) -> Watch:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(services_module, "update_watch_application", fail_update)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await update_watch(
            cast(AsyncSession, object()),
            _make_watch(),
            WatchUpdate(passenger_count=2),
        )


async def test_services_update_resolves_preserved_validator_seams_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = _make_watch(suffix="validator-seams")
    session = _RecordingSession(watch)
    calls: list[tuple[str, object]] = []

    async def validate(_session: AsyncSession, channel_ids: list[str]) -> None:
        calls.append(("channels", channel_ids))

    async def ensure(
        _session: AsyncSession,
        provider: Provider,
        *,
        exclude_watch_id: str | None = None,
    ) -> None:
        calls.append(("capacity", (provider, exclude_watch_id)))

    async def outbox(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(services_module, "validate_channel_ids", validate)
    monkeypatch.setattr(services_module, "_ensure_focused_observation_capacity", ensure)
    monkeypatch.setattr(services_module, "add_outbox_event", outbox)

    await update_watch(
        cast(AsyncSession, session),
        watch,
        WatchUpdate(
            notification_channel_ids=["channel-1"],
            seat_observation_mode=SeatObservationMode.FOCUSED,
        ),
    )

    assert calls == [
        ("channels", ["channel-1"]),
        ("capacity", (Provider.MOCK, watch.id)),
    ]


class _ScalarRows:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def all(self) -> list[str]:
        return self.values


class _ValidationSession:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.statements: list[object] = []

    async def scalars(self, statement: object) -> _ScalarRows:
        self.statements.append(statement)
        return _ScalarRows(self.values)


async def test_channel_validation_deduplicates_ids_and_fails_closed() -> None:
    empty_session = _ValidationSession([])
    await validate_channel_ids_application(cast(AsyncSession, empty_session), [])
    assert empty_session.statements == []

    enabled_session = _ValidationSession(["enabled"])
    await validate_channel_ids_application(
        cast(AsyncSession, enabled_session),
        ["enabled", "enabled"],
    )
    assert len(enabled_session.statements) == 1

    missing_session = _ValidationSession(["enabled"])
    with pytest.raises(
        WatchCommandValidationError,
        match="notification channels must exist and be enabled",
    ):
        await validate_channel_ids_application(
            cast(AsyncSession, missing_session),
            ["enabled", "missing"],
        )


async def test_focused_capacity_query_keeps_provider_exclusion_limit_and_row_lock() -> None:
    session = _ValidationSession(["one", "two", "three"])

    with pytest.raises(WatchCommandConflict, match="up to 3"):
        await ensure_focused_observation_capacity(
            cast(AsyncSession, session),
            Provider.MOCK,
            exclude_watch_id="current-watch",
        )

    assert len(session.statements) == 1
    statement = session.statements[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "watches.provider = 'MOCK'" in compiled
    assert "watches.id != 'current-watch'" in compiled
    assert "watches.status NOT IN" in compiled
    assert "LIMIT 3 FOR UPDATE" in compiled


class _RecordingSession:
    def __init__(self, locked_watch: Watch | None) -> None:
        self.locked_watch = locked_watch
        self.calls: list[tuple[str, object]] = []

    async def scalar(self, statement: object) -> Watch | None:
        self.calls.append(("lock", statement))
        return self.locked_watch

    async def commit(self) -> None:
        self.calls.append(("commit", None))

    async def refresh(self, value: object) -> None:
        self.calls.append(("refresh", value))


async def test_update_reloads_with_lock_then_dedupes_outboxes_commits_and_refreshes() -> None:
    watch = _make_watch()
    session = _RecordingSession(watch)
    transition_at = datetime(2030, 8, 1, 3, 4, 5, tzinfo=UTC)

    def dedupe(*args: object) -> str:
        assert args[7] == 2
        session.calls.append(("dedupe", args))
        return "updated-dedupe"

    def now() -> datetime:
        session.calls.append(("clock", None))
        return transition_at

    async def outbox(*_args: object, **kwargs: object) -> object:
        session.calls.append(("outbox", kwargs))
        return object()

    result = await update_watch_application(
        cast(AsyncSession, session),
        watch,
        WatchUpdate(passenger_count=2),
        dependencies=WatchUpdateDependencies(
            build_watch_dedupe_key=dedupe,
            add_outbox_event=outbox,
            now=now,
            validate_channel_ids=_validate_channels,
            ensure_focused_observation_capacity=_ensure_capacity,
        ),
    )

    assert result is watch
    assert watch.passenger_count == 2
    assert watch.dedupe_key == "updated-dedupe"
    assert [name for name, _value in session.calls] == [
        "lock",
        "dedupe",
        "clock",
        "outbox",
        "commit",
        "refresh",
    ]
    statement = session.calls[0][1]
    assert statement.get_execution_options()["populate_existing"] is True
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    outbox_kwargs = cast(dict[str, object], session.calls[3][1])
    assert outbox_kwargs["event_type"] == "watch.updated"
    assert outbox_kwargs["dedupe_key"] == (f"watch:{watch.id}:updated:{transition_at.isoformat()}")


async def test_active_update_preserves_three_separate_clock_reads() -> None:
    watch = _make_watch(status=WatchStatus.SEAT_FOUND)
    watch.next_check_at = datetime(2030, 8, 2, tzinfo=UTC)
    session = _RecordingSession(watch)
    instants = [
        datetime(2030, 8, 1, 1, tzinfo=UTC),
        datetime(2030, 8, 1, 2, tzinfo=UTC),
        datetime(2030, 8, 1, 3, tzinfo=UTC),
    ]

    def fail_dedupe(*_args: object) -> str:
        raise AssertionError("active policy-only updates must preserve the dedupe key")

    def now() -> datetime:
        instant = instants[len([name for name, _value in session.calls if name == "clock"])]
        session.calls.append(("clock", instant))
        return instant

    async def outbox(*_args: object, **kwargs: object) -> object:
        session.calls.append(("outbox", kwargs))
        return object()

    await update_watch_application(
        cast(AsyncSession, session),
        watch,
        WatchUpdate(
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            focused_observation_interval_seconds=20,
        ),
        dependencies=WatchUpdateDependencies(
            build_watch_dedupe_key=fail_dedupe,
            add_outbox_event=outbox,
            now=now,
            validate_channel_ids=_validate_channels,
            ensure_focused_observation_capacity=_ensure_capacity,
        ),
    )

    assert watch.next_check_at == instants[1]
    assert [value for name, value in session.calls if name == "clock"] == instants
    outbox_call = next(value for name, value in session.calls if name == "outbox")
    assert cast(dict[str, object], outbox_call)["dedupe_key"] == (
        f"watch:{watch.id}:updated:{instants[2].isoformat()}"
    )


async def test_update_errors_before_mutation_and_side_effects() -> None:
    terminal_watch = _make_watch(status=WatchStatus.COMPLETED)
    terminal_session = _RecordingSession(terminal_watch)

    def fail_effect(*_args: object, **_kwargs: object):
        raise AssertionError("rejected update must not run dependencies")

    dependencies = WatchUpdateDependencies(
        build_watch_dedupe_key=fail_effect,
        add_outbox_event=fail_effect,
        now=fail_effect,
        validate_channel_ids=_validate_channels,
        ensure_focused_observation_capacity=_ensure_capacity,
    )
    with pytest.raises(WatchCommandConflict, match="active watches only allow"):
        await update_watch_application(
            cast(AsyncSession, terminal_session),
            terminal_watch,
            WatchUpdate(reservation_policy=ReservationPolicy.NOTIFY_ONLY),
            dependencies=dependencies,
        )

    missing_session = _RecordingSession(None)
    with pytest.raises(WatchCommandNotFound, match="watch not found"):
        await update_watch_application(
            cast(AsyncSession, missing_session),
            _make_watch(suffix="missing"),
            WatchUpdate(passenger_count=2),
            dependencies=dependencies,
        )
    assert terminal_watch.reservation_policy is ReservationPolicy.NOTIFY_ONLY
    assert [name for name, _value in terminal_session.calls] == ["lock"]
    assert [name for name, _value in missing_session.calls] == ["lock"]


async def test_outbox_failure_does_not_commit_or_refresh() -> None:
    watch = _make_watch(suffix="outbox-failure")
    session = _RecordingSession(watch)

    async def fail_outbox(*_args: object, **kwargs: object) -> object:
        session.calls.append(("outbox", kwargs))
        raise RuntimeError("outbox failed")

    with pytest.raises(RuntimeError, match="outbox failed"):
        await update_watch_application(
            cast(AsyncSession, session),
            watch,
            WatchUpdate(passenger_count=2),
            dependencies=WatchUpdateDependencies(
                build_watch_dedupe_key=lambda *_args: "changed-dedupe",
                add_outbox_event=fail_outbox,
                now=lambda: datetime(2030, 8, 1, tzinfo=UTC),
                validate_channel_ids=_validate_channels,
                ensure_focused_observation_capacity=_ensure_capacity,
            ),
        )

    assert [name for name, _value in session.calls] == ["lock", "outbox"]


async def test_update_and_outbox_commit_atomically(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = _make_watch(suffix="atomic")
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        await update_watch(session, watch, WatchUpdate(passenger_count=2))

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == watch_id,
                        OutboxEvent.event_type == "watch.updated",
                    )
                )
            ).all()
        )

    assert persisted is not None
    assert persisted.passenger_count == 2
    assert len(events) == 1
    assert events[0].payload == {"watch_id": watch_id}


async def test_update_refreshes_stale_watch_before_rebuilding_dedupe_key(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        watch = _make_watch(suffix="stale")
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    async with factory() as first_session, factory() as stale_session:
        first = await first_session.get(Watch, watch_id)
        stale = await stale_session.get(Watch, watch_id)
        assert first is not None
        assert stale is not None
        await update_watch(first_session, first, WatchUpdate(passenger_count=2))
        await update_watch(stale_session, stale, WatchUpdate(time_to=time(11, 30)))

    async with factory() as session:
        persisted = await session.get(Watch, watch_id)
        assert persisted is not None
        assert persisted.passenger_count == 2
        assert persisted.time_to == time(11, 30)
        assert persisted.dedupe_key == build_watch_dedupe_key(
            persisted.provider,
            persisted.origin,
            persisted.destination,
            persisted.travel_date,
            persisted.time_from,
            persisted.time_to,
            persisted.seat_class,
            persisted.passenger_count,
            persisted.train_numbers,
            persisted.origin_node_id,
            persisted.destination_node_id,
        )
