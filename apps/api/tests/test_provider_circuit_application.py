from __future__ import annotations

from types import TracebackType
from typing import Self, cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ClauseElement

import rail_waitlist.services as services_module
import rail_waitlist.worker as worker_module
from rail_waitlist.domain import Provider, ProviderCircuitState
from rail_waitlist.models import ProviderCircuit
from rail_waitlist.provider_circuit.application import (
    get_or_create_provider_circuit as get_or_create_provider_circuit_application,
)
from rail_waitlist.services import get_or_create_provider_circuit


class NestedTransaction:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self) -> Self:
        self.events.append("savepoint-enter")
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        self.events.append(
            "savepoint-exit:error" if exception_type is not None else "savepoint-exit:ok"
        )
        return False


class RecordingSession:
    def __init__(
        self,
        scalar_results: list[ProviderCircuit | None],
        *,
        flush_error: IntegrityError | None = None,
    ) -> None:
        self.scalar_results = scalar_results
        self.flush_error = flush_error
        self.scalar_statements: list[object] = []
        self.events: list[str] = []
        self.added: ProviderCircuit | None = None

    async def scalar(self, statement: object) -> ProviderCircuit | None:
        self.scalar_statements.append(statement)
        self.events.append("scalar")
        return self.scalar_results.pop(0)

    def begin_nested(self) -> NestedTransaction:
        self.events.append("begin-nested")
        return NestedTransaction(self.events)

    def add(self, value: object) -> None:
        assert isinstance(value, ProviderCircuit)
        self.added = value
        self.events.append("add")

    async def flush(self) -> None:
        self.events.append("flush")
        if self.flush_error is not None:
            raise self.flush_error


def query_sql(statement: object) -> str:
    compile_statement = cast(ClauseElement, statement)
    return str(
        compile_statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.parametrize("lock", [False, True])
async def test_circuit_owner_returns_existing_state_without_resetting_it(lock: bool) -> None:
    existing = ProviderCircuit(
        id="circuit-existing",
        provider=Provider.KORAIL,
        state=ProviderCircuitState.MANUAL_HOLD,
        reason="operator_hold",
        manual_resume_required=True,
        generation=7,
    )
    session = RecordingSession([existing])

    returned = await get_or_create_provider_circuit_application(
        cast(AsyncSession, session),
        Provider.KORAIL,
        lock=lock,
    )

    assert returned is existing
    assert existing.state is ProviderCircuitState.MANUAL_HOLD
    assert existing.reason == "operator_hold"
    assert existing.manual_resume_required is True
    assert existing.generation == 7
    assert session.events == ["scalar"]
    assert ("FOR UPDATE" in query_sql(session.scalar_statements[0])) is lock


async def test_circuit_owner_creates_closed_initial_row_inside_savepoint() -> None:
    session = RecordingSession([None])

    created = await get_or_create_provider_circuit_application(
        cast(AsyncSession, session),
        Provider.SRT,
    )

    assert created is session.added
    assert created.provider is Provider.SRT
    assert created.state is ProviderCircuitState.CLOSED
    assert created.generation == 0
    assert created.manual_resume_required is False
    assert created.reason is None
    assert created.opened_at is None
    assert created.cooldown_until is None
    assert session.events == [
        "scalar",
        "begin-nested",
        "savepoint-enter",
        "add",
        "flush",
        "savepoint-exit:ok",
    ]


async def test_circuit_owner_returns_locked_race_winner_after_insert_conflict() -> None:
    error = IntegrityError("insert", {}, RuntimeError("duplicate provider"))
    winner = ProviderCircuit(
        id="circuit-winner",
        provider=Provider.MOCK,
        state=ProviderCircuitState.OPEN,
        reason="provider_rate_limited",
        manual_resume_required=False,
        generation=3,
    )
    session = RecordingSession([None, winner], flush_error=error)

    returned = await get_or_create_provider_circuit_application(
        cast(AsyncSession, session),
        Provider.MOCK,
        lock=True,
    )

    assert returned is winner
    assert session.events == [
        "scalar",
        "begin-nested",
        "savepoint-enter",
        "add",
        "flush",
        "savepoint-exit:error",
        "scalar",
    ]
    assert len(session.scalar_statements) == 2
    assert all("FOR UPDATE" in query_sql(query) for query in session.scalar_statements)


async def test_circuit_owner_reraises_insert_conflict_when_winner_is_missing() -> None:
    error = IntegrityError("insert", {}, RuntimeError("duplicate provider"))
    session = RecordingSession([None, None], flush_error=error)

    with pytest.raises(IntegrityError) as raised:
        await get_or_create_provider_circuit_application(
            cast(AsyncSession, session),
            Provider.MOCK,
        )

    assert raised.value is error
    assert session.events[-1] == "scalar"
    assert len(session.scalar_statements) == 2


async def test_services_circuit_wrapper_preserves_identity_and_current_application_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[AsyncSession, Provider, bool]] = []
    sentinel = cast(ProviderCircuit, object())

    async def application(
        session: AsyncSession,
        provider: Provider,
        *,
        lock: bool = False,
    ) -> ProviderCircuit:
        calls.append((session, provider, lock))
        return sentinel

    monkeypatch.setattr(
        services_module,
        "get_or_create_provider_circuit_application",
        application,
    )
    session = cast(AsyncSession, object())

    returned = await get_or_create_provider_circuit(
        session,
        Provider.KORAIL,
        lock=True,
    )

    assert returned is sentinel
    assert get_or_create_provider_circuit is services_module.get_or_create_provider_circuit
    assert get_or_create_provider_circuit.__module__ == "rail_waitlist.services"
    assert calls == [(session, Provider.KORAIL, True)]


def test_worker_uses_canonical_circuit_owner_for_observation_and_reservation() -> None:
    reservation_dependencies = worker_module._reservation_execution_dependencies()
    observation_dependencies = worker_module._observation_group_dependencies()

    assert reservation_dependencies.get_or_create_provider_circuit is (
        get_or_create_provider_circuit_application
    )
    assert observation_dependencies.get_or_create_provider_circuit is (
        get_or_create_provider_circuit_application
    )
