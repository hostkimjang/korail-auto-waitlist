from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Self, cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.worker as worker_module
from rail_waitlist.domain import Provider
from rail_waitlist.provider_contracts import ExecutionProvider
from rail_waitlist.watch_management.arming_application import (
    ARMABLE_WATCH_STATUSES,
    EXTERNAL_ARMING_PROVIDERS,
    WatchArmingDependencies,
    arm_supported_provider_watches,
)


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class ArmingSession:
    def __init__(self, rows: list[object], *, commit_error: Exception | None = None) -> None:
        self.rows = rows
        self.commit_error = commit_error
        self.statements: list[object] = []
        self.commit_count = 0
        self.exit_error_type: type[BaseException] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: object,
    ) -> None:
        self.exit_error_type = error_type

    async def scalars(self, statement: object) -> ScalarRows:
        self.statements.append(statement)
        return ScalarRows(self.rows)

    async def commit(self) -> None:
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error


class ArmingSessionFactory:
    def __init__(self, sessions: list[ArmingSession]) -> None:
        self._sessions = iter(sessions)
        self.call_count = 0

    def __call__(self) -> AsyncSession:
        self.call_count += 1
        return cast(AsyncSession, next(self._sessions))


def adapter(*, seat_monitoring: bool) -> ExecutionProvider:
    return cast(
        ExecutionProvider,
        SimpleNamespace(
            capabilities=lambda: SimpleNamespace(seat_monitoring=seat_monitoring),
        ),
    )


async def test_arming_short_circuits_before_provider_or_database_resolution() -> None:
    factory = ArmingSessionFactory([])
    provider_calls: list[Provider] = []

    def get_provider(provider: Provider) -> ExecutionProvider:
        provider_calls.append(provider)
        return adapter(seat_monitoring=True)

    dependencies = WatchArmingDependencies(
        session_factory=factory,
        get_execution_provider=get_provider,
    )
    now = datetime(2026, 8, 6, 9, tzinfo=UTC)

    assert await arm_supported_provider_watches(Provider.MOCK, now, dependencies=dependencies) == 0
    assert provider_calls == []
    assert factory.call_count == 0

    assert (
        await arm_supported_provider_watches(
            Provider.SRT,
            now,
            adapter=adapter(seat_monitoring=False),
            dependencies=dependencies,
        )
        == 0
    )
    assert provider_calls == []
    assert factory.call_count == 0


async def test_arming_updates_locked_rows_and_commits_only_when_rows_exist() -> None:
    now = datetime(2026, 8, 6, 9, tzinfo=UTC)
    first = SimpleNamespace(next_check_at=None)
    second = SimpleNamespace(next_check_at=None)
    changed = ArmingSession([first, second])
    unchanged = ArmingSession([])
    factory = ArmingSessionFactory([changed, unchanged])
    dependencies = WatchArmingDependencies(
        session_factory=factory,
        get_execution_provider=lambda _provider: adapter(seat_monitoring=True),
    )

    assert (
        await arm_supported_provider_watches(
            Provider.KORAIL,
            now,
            adapter=adapter(seat_monitoring=True),
            dependencies=dependencies,
        )
        == 2
    )
    assert first.next_check_at == now
    assert second.next_check_at == now
    assert changed.commit_count == 1
    assert changed.exit_error_type is None

    statement = changed.statements[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "watches.provider = 'KORAIL'" in sql
    assert "watches.mode = 'official'" in sql
    assert "watches.status IN ('SCHEDULED', 'OFFICIAL_WAITLIST', 'SEAT_FOUND')" in sql
    assert "watches.next_check_at IS NULL" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql

    assert (
        await arm_supported_provider_watches(
            Provider.KORAIL,
            now,
            adapter=adapter(seat_monitoring=True),
            dependencies=dependencies,
        )
        == 0
    )
    assert unchanged.commit_count == 0
    assert unchanged.exit_error_type is None
    assert EXTERNAL_ARMING_PROVIDERS == frozenset({Provider.KORAIL, Provider.SRT})
    assert {status.value for status in ARMABLE_WATCH_STATUSES} == {
        "scheduled",
        "official_waitlist",
        "seat_found",
    }


async def test_arming_propagates_commit_failure_through_the_session_context() -> None:
    error = RuntimeError("commit failed")
    session = ArmingSession([SimpleNamespace(next_check_at=None)], commit_error=error)
    dependencies = WatchArmingDependencies(
        session_factory=ArmingSessionFactory([session]),
        get_execution_provider=lambda _provider: adapter(seat_monitoring=True),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await arm_supported_provider_watches(
            Provider.SRT,
            datetime(2026, 8, 6, 9, tzinfo=UTC),
            adapter=adapter(seat_monitoring=True),
            dependencies=dependencies,
        )

    assert session.commit_count == 1
    assert session.exit_error_type is RuntimeError


async def test_worker_wrapper_assembles_current_runtime_dependencies(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel_factory = cast(object, object())
    sentinel_adapter = adapter(seat_monitoring=True)

    def provider_getter(_provider: Provider) -> ExecutionProvider:
        raise AssertionError("canonical application is replaced in this wiring test")

    async def application(*args: object, **kwargs: object) -> int:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 7

    monkeypatch.setattr(worker_module, "SessionFactory", sentinel_factory)
    monkeypatch.setattr(worker_module, "get_execution_provider", provider_getter)
    monkeypatch.setattr(worker_module, "arm_supported_provider_watches_application", application)
    now = datetime(2026, 8, 6, 9, tzinfo=UTC)

    assert (
        await worker_module._arm_supported_provider_watches(
            Provider.SRT,
            now,
            adapter=sentinel_adapter,
        )
        == 7
    )
    assert captured["args"] == (Provider.SRT, now)
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["adapter"] is sentinel_adapter
    dependencies = kwargs["dependencies"]
    assert isinstance(dependencies, WatchArmingDependencies)
    assert dependencies.session_factory is sentinel_factory
    assert dependencies.get_execution_provider is provider_getter
