from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import rail_waitlist.reservations.execution_runtime as runtime_module
import rail_waitlist.worker as worker_module
from rail_waitlist.domain import Provider
from rail_waitlist.provider_contracts import ReservationExecutionProvider
from rail_waitlist.reservations.execution_application import (
    ReservationExecutionDependencies,
    ReservationExecutionTarget,
)

API_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class WinnerSnapshot:
    watch_id: str
    candidate_id: str
    provider: Provider
    origin: str
    destination: str
    origin_node_id: str
    destination_node_id: str
    train_number: str
    departure_at: datetime
    arrival_at: datetime | None
    seat_class: str
    passenger_count: int
    priority: int
    reservation_episode_key: str | None


def _winner_snapshot(
    *,
    arrival_at: datetime | None = datetime(2026, 8, 7, 2, 3, tzinfo=UTC),
    reservation_episode_key: str | None = "availability:episode-17",
) -> WinnerSnapshot:
    return WinnerSnapshot(
        watch_id="watch-runtime-1",
        candidate_id="candidate-runtime-2",
        provider=Provider.KORAIL,
        origin="대전",
        destination="서울",
        origin_node_id="0010",
        destination_node_id="0001",
        train_number="KTX-002",
        departure_at=datetime(2026, 8, 7, 1, 2, tzinfo=UTC),
        arrival_at=arrival_at,
        seat_class="first",
        passenger_count=3,
        priority=97,
        reservation_episode_key=reservation_episode_key,
    )


@pytest.mark.parametrize(
    ("arrival_at", "reservation_episode_key"),
    [
        (datetime(2026, 8, 7, 2, 3, tzinfo=UTC), "availability:episode-17"),
        (None, None),
    ],
)
async def test_reservation_runtime_maps_all_execution_fields_and_discards_priority(
    monkeypatch,
    arrival_at: datetime | None,
    reservation_episode_key: str | None,
) -> None:
    source = _winner_snapshot(
        arrival_at=arrival_at,
        reservation_episode_key=reservation_episode_key,
    )
    adapter = cast(ReservationExecutionProvider, object())
    dependencies = cast(ReservationExecutionDependencies, object())
    calls: list[tuple[object, ReservationExecutionTarget, object]] = []

    async def execute(
        received_adapter,
        target: ReservationExecutionTarget,
        *,
        dependencies: ReservationExecutionDependencies,
    ) -> None:
        calls.append((received_adapter, target, dependencies))

    monkeypatch.setattr(runtime_module, "execute_reservation", execute)

    await runtime_module.reserve_observation_winner(
        adapter,
        source,
        dependencies=dependencies,
    )

    assert len(calls) == 1
    received_adapter, target, received_dependencies = calls[0]
    assert received_adapter is adapter
    assert received_dependencies is dependencies
    assert tuple(field.name for field in fields(target)) == (
        "watch_id",
        "candidate_id",
        "provider",
        "origin",
        "destination",
        "origin_node_id",
        "destination_node_id",
        "train_number",
        "departure_at",
        "arrival_at",
        "seat_class",
        "passenger_count",
        "reservation_episode_key",
    )
    assert target == ReservationExecutionTarget(
        watch_id=source.watch_id,
        candidate_id=source.candidate_id,
        provider=source.provider,
        origin=source.origin,
        destination=source.destination,
        origin_node_id=source.origin_node_id,
        destination_node_id=source.destination_node_id,
        train_number=source.train_number,
        departure_at=source.departure_at,
        arrival_at=source.arrival_at,
        seat_class=source.seat_class,
        passenger_count=source.passenger_count,
        reservation_episode_key=source.reservation_episode_key,
    )
    assert not hasattr(target, "priority")
    assert target.departure_at is source.departure_at
    assert target.arrival_at is source.arrival_at


async def test_reservation_runtime_propagates_the_executor_error_identity(monkeypatch) -> None:
    expected = LookupError("execution failed")

    async def execute(*_args, **_kwargs) -> None:
        raise expected

    monkeypatch.setattr(runtime_module, "execute_reservation", execute)

    with pytest.raises(LookupError) as caught:
        await runtime_module.reserve_observation_winner(
            cast(ReservationExecutionProvider, object()),
            _winner_snapshot(),
            dependencies=cast(ReservationExecutionDependencies, object()),
        )

    assert caught.value is expected


async def test_worker_wrapper_uses_current_dependency_factory_and_canonical_binding(
    monkeypatch,
) -> None:
    source = _winner_snapshot()
    adapter = cast(ReservationExecutionProvider, object())
    dependencies = cast(ReservationExecutionDependencies, object())
    captured: dict[str, object] = {}

    async def canonical(
        received_adapter,
        received_target,
        *,
        dependencies: ReservationExecutionDependencies,
    ) -> None:
        captured.update(
            adapter=received_adapter,
            target=received_target,
            dependencies=dependencies,
        )

    monkeypatch.setattr(worker_module, "_reservation_execution_dependencies", lambda: dependencies)
    monkeypatch.setattr(worker_module, "reserve_observation_winner_application", canonical)

    await worker_module._reserve_winner(adapter, source)

    assert captured == {
        "adapter": adapter,
        "target": source,
        "dependencies": dependencies,
    }


def test_worker_builds_the_complete_reservation_dependency_contract_lazily() -> None:
    dependencies = worker_module._reservation_execution_dependencies()

    assert dependencies.session_factory is worker_module.SessionFactory
    assert (
        dependencies.get_or_create_provider_circuit is worker_module.get_or_create_provider_circuit
    )
    assert dependencies.apply_watch_transition is worker_module.apply_watch_transition
    assert dependencies.begin_reservation_attempt is worker_module.begin_reservation_attempt
    assert dependencies.add_outbox_event is worker_module.add_outbox_event
    assert dependencies.complete_reservation_attempt is worker_module.complete_reservation_attempt
    assert (
        dependencies.record_reservation_confirmation
        is worker_module.record_reservation_confirmation
    )
    assert (
        dependencies.update_provider_auth_status
        is worker_module._update_provider_auth_status_in_reservation_transaction
    )
    assert dependencies.provider_call_errors == (
        worker_module.ProviderUnavailable,
        RuntimeError,
        ValueError,
    )
    assert dependencies.srt_exact_reservation_source == worker_module.SRT_RESERVATION_SOURCE


async def test_worker_wrapper_propagates_the_canonical_error_identity(monkeypatch) -> None:
    expected = LookupError("bridge failed")

    async def canonical(*_args, **_kwargs) -> None:
        raise expected

    monkeypatch.setattr(worker_module, "reserve_observation_winner_application", canonical)

    with pytest.raises(LookupError) as caught:
        await worker_module._reserve_winner(
            cast(ReservationExecutionProvider, object()),
            _winner_snapshot(),
        )

    assert caught.value is expected


def test_reservation_runtime_import_orders_preserve_canonical_worker_wiring() -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "runtime-first":
    import rail_waitlist.reservations.execution_runtime as Runtime
    import rail_waitlist.worker as Worker
    import rail_waitlist.reservations.execution_application as Application
elif sys.argv[1] == "worker-first":
    import rail_waitlist.worker as Worker
    import rail_waitlist.reservations.execution_runtime as Runtime
    import rail_waitlist.reservations.execution_application as Application
else:
    import rail_waitlist.reservations.execution_application as Application
    import rail_waitlist.reservations.execution_runtime as Runtime
    import rail_waitlist.worker as Worker

print(json.dumps({
    "application_binding": Runtime.execute_reservation is Application.execute_reservation,
    "canonical_module": Runtime.reserve_observation_winner.__module__,
    "worker_binding": (
        Worker.reserve_observation_winner_application is Runtime.reserve_observation_winner
    ),
    "worker_wrapper_is_distinct": Worker._reserve_winner is not Runtime.reserve_observation_winner,
}, sort_keys=True))
"""

    for import_order in ("runtime-first", "worker-first", "application-first"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, import_order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "application_binding": True,
            "canonical_module": "rail_waitlist.reservations.execution_runtime",
            "worker_binding": True,
            "worker_wrapper_is_distinct": True,
        }
