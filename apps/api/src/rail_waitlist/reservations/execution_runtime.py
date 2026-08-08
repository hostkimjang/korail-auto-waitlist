from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain import Provider
from ..provider_contracts import ReservationExecutionProvider
from .execution_application import (
    ReservationExecutionDependencies,
    ReservationExecutionTarget,
    execute_reservation,
)


class ReservationWinnerTarget(Protocol):
    """관측 owner와 역의존 없이 예약에 필요한 winner snapshot만 표현한다."""

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
    reservation_episode_key: str | None


async def reserve_observation_winner(
    adapter: ReservationExecutionProvider,
    target: ReservationWinnerTarget,
    *,
    dependencies: ReservationExecutionDependencies,
) -> None:
    """관측 winner snapshot을 canonical reservation application에 그대로 전달한다."""
    await execute_reservation(
        adapter,
        ReservationExecutionTarget(
            watch_id=target.watch_id,
            candidate_id=target.candidate_id,
            provider=target.provider,
            origin=target.origin,
            destination=target.destination,
            origin_node_id=target.origin_node_id,
            destination_node_id=target.destination_node_id,
            train_number=target.train_number,
            departure_at=target.departure_at,
            arrival_at=target.arrival_at,
            seat_class=target.seat_class,
            passenger_count=target.passenger_count,
            reservation_episode_key=target.reservation_episode_key,
        ),
        dependencies=dependencies,
    )
