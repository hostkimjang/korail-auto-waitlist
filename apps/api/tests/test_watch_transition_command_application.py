from __future__ import annotations

from datetime import date, time
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

import rail_waitlist.services as services_module
import rail_waitlist.watch_management.http as watch_http_module
import rail_waitlist.watch_management.transition_runtime as transition_runtime_module
import rail_waitlist.worker as worker_module
from rail_waitlist.domain import Provider, WatchStatus
from rail_waitlist.models import Watch
from rail_waitlist.services import transition_watch
from rail_waitlist.watch_management.transition_application import WatchTransitionRejected
from rail_waitlist.watch_management.transition_command_application import (
    WatchTransitionCommandDependencies,
    WatchTransitionCommandNotFound,
)
from rail_waitlist.watch_management.transition_command_application import (
    transition_watch as transition_watch_application,
)


def make_watch(
    *,
    watch_id: str = "watch-command",
    status: WatchStatus = WatchStatus.DRAFT,
) -> Watch:
    return Watch(
        id=watch_id,
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
        dedupe_key=watch_id,
    )


class RecordingSession:
    def __init__(
        self,
        locked_watch: Watch | None,
        *,
        commit_error: BaseException | None = None,
        refresh_error: BaseException | None = None,
    ) -> None:
        self.locked_watch = locked_watch
        self.commit_error = commit_error
        self.refresh_error = refresh_error
        self.statement: object | None = None
        self.events: list[str] = []
        self.refreshed: object | None = None

    async def scalar(self, statement: object) -> Watch | None:
        self.statement = statement
        self.events.append("scalar")
        return self.locked_watch

    async def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    async def refresh(self, value: object) -> None:
        self.refreshed = value
        self.events.append("refresh")
        if self.refresh_error is not None:
            raise self.refresh_error

    async def rollback(self) -> None:
        self.events.append("rollback")


async def test_transition_command_locks_fresh_row_then_applies_commits_and_refreshes() -> None:
    stale = make_watch(status=WatchStatus.DRAFT)
    locked = make_watch(status=WatchStatus.SCHEDULED)
    replay = make_watch(status=WatchStatus.SCHEDULED)
    session = RecordingSession(locked)
    calls: list[tuple[AsyncSession, Watch, WatchStatus, str | None, str | None]] = []

    async def apply_transition(
        current_session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
    ) -> Watch:
        session.events.append("apply")
        calls.append((current_session, watch, target, idempotency_key, reason))
        return replay

    result = await transition_watch_application(
        cast(AsyncSession, session),
        stale,
        WatchStatus.SCHEDULED,
        "transition-key",
        reason="manual_start",
        dependencies=WatchTransitionCommandDependencies(
            apply_watch_transition=apply_transition,
        ),
    )

    assert result is replay
    assert calls == [
        (
            cast(AsyncSession, session),
            locked,
            WatchStatus.SCHEDULED,
            "transition-key",
            "manual_start",
        )
    ]
    assert session.events == ["scalar", "apply", "commit", "refresh"]
    assert session.refreshed is replay
    statement = cast(Select[tuple[Watch]], session.statement)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "WHERE watches.id = 'watch-command'" in compiled
    assert compiled.endswith("FOR UPDATE")
    assert "SKIP LOCKED" not in compiled
    assert "NOWAIT" not in compiled
    assert statement.get_execution_options()["populate_existing"] is True


async def test_transition_command_missing_row_raises_domain_error_without_uow_end() -> None:
    session = RecordingSession(None)

    async def unexpected_apply(*_args: object, **_kwargs: object) -> Watch:
        raise AssertionError("missing command must not apply a transition")

    with pytest.raises(WatchTransitionCommandNotFound, match="watch not found"):
        await transition_watch_application(
            cast(AsyncSession, session),
            make_watch(),
            WatchStatus.SCHEDULED,
            dependencies=WatchTransitionCommandDependencies(
                apply_watch_transition=unexpected_apply,
            ),
        )

    assert session.events == ["scalar"]


@pytest.mark.parametrize("failure_stage", ["apply", "commit", "refresh"])
async def test_transition_command_preserves_failure_and_does_not_rollback(
    failure_stage: str,
) -> None:
    error = RuntimeError(f"{failure_stage}-failed")
    session = RecordingSession(
        make_watch(),
        commit_error=error if failure_stage == "commit" else None,
        refresh_error=error if failure_stage == "refresh" else None,
    )

    async def apply_transition(
        _session: AsyncSession,
        watch: Watch,
        _target: WatchStatus,
        _idempotency_key: str | None = None,
        *,
        reason: str | None = None,
    ) -> Watch:
        assert reason is None
        session.events.append("apply")
        if failure_stage == "apply":
            raise error
        return watch

    with pytest.raises(RuntimeError) as raised:
        await transition_watch_application(
            cast(AsyncSession, session),
            make_watch(),
            WatchStatus.SCHEDULED,
            dependencies=WatchTransitionCommandDependencies(
                apply_watch_transition=apply_transition,
            ),
        )

    assert raised.value is error
    expected_events = {
        "apply": ["scalar", "apply"],
        "commit": ["scalar", "apply", "commit"],
        "refresh": ["scalar", "apply", "commit", "refresh"],
    }
    assert session.events == expected_events[failure_stage]
    assert "rollback" not in session.events


async def test_services_command_wrapper_preserves_current_apply_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[AsyncSession, Watch, WatchStatus, str | None, str | None, object]] = []
    sentinel = make_watch(status=WatchStatus.SCHEDULED)

    async def current_apply(*_args: object, **_kwargs: object) -> Watch:
        return sentinel

    async def command(
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
        dependencies: WatchTransitionCommandDependencies,
    ) -> Watch:
        calls.append(
            (
                session,
                watch,
                target,
                idempotency_key,
                reason,
                dependencies.apply_watch_transition,
            )
        )
        return sentinel

    monkeypatch.setattr(services_module, "apply_watch_transition", current_apply)
    monkeypatch.setattr(services_module, "transition_watch_application", command)
    session = cast(AsyncSession, object())
    watch = make_watch()

    result = await transition_watch(
        session,
        watch,
        WatchStatus.SCHEDULED,
        "transition-key",
        reason="manual_start",
    )

    assert result is sentinel
    assert calls == [
        (
            session,
            watch,
            WatchStatus.SCHEDULED,
            "transition-key",
            "manual_start",
            current_apply,
        )
    ]
    assert transition_watch is services_module.transition_watch
    assert transition_watch.__module__ == "rail_waitlist.services"


async def test_services_command_wrapper_maps_only_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing(*_args: object, **_kwargs: object) -> Watch:
        raise WatchTransitionCommandNotFound("watch not found")

    monkeypatch.setattr(services_module, "transition_watch_application", missing)

    with pytest.raises(HTTPException) as raised:
        await transition_watch(
            cast(AsyncSession, object()),
            make_watch(),
            WatchStatus.SCHEDULED,
        )

    assert raised.value.status_code == 404
    assert raised.value.detail == "watch not found"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (WatchTransitionCommandNotFound("watch not found"), 404),
        (WatchTransitionRejected("cannot transition draft to completed"), 409),
    ],
)
async def test_feature_http_maps_transition_domain_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: RuntimeError,
    status_code: int,
) -> None:
    async def rejected(*_args: object, **_kwargs: object) -> Watch:
        raise error

    monkeypatch.setattr(watch_http_module, "transition_watch_runtime", rejected)

    with pytest.raises(HTTPException) as raised:
        await watch_http_module.transition_watch(
            cast(AsyncSession, object()),
            make_watch(),
            WatchStatus.COMPLETED,
        )

    assert raised.value.status_code == status_code
    assert raised.value.detail == str(error)


def test_production_transition_wiring_uses_feature_runtime() -> None:
    assert worker_module.apply_watch_transition is transition_runtime_module.apply_watch_transition
    assert watch_http_module.transition_watch_runtime is transition_runtime_module.transition_watch
