from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..domain import (
    TERMINAL_STATUSES,
    Provider,
    ReservationPolicy,
    SeatObservationMode,
    WatchStatus,
)
from ..notification_management.models import NotificationChannel
from .models import Watch
from .schemas import WatchUpdate

MAX_FOCUSED_WATCHES_PER_PROVIDER = 3


class WatchCommandNotFound(RuntimeError):
    """A watch command could not reload its target row."""


class WatchCommandConflict(RuntimeError):
    """A watch command conflicts with the current persisted state."""


class WatchCommandValidationError(RuntimeError):
    """A watch command violates a persisted cross-field invariant."""


class BuildWatchDedupeKey(Protocol):
    def __call__(
        self,
        provider: Provider,
        origin: str,
        destination: str,
        travel_date: date,
        time_from: time,
        time_to: time,
        seat_class: str,
        passenger_count: int,
        train_numbers: list[str],
        origin_node_id: str | None = None,
        destination_node_id: str | None = None,
    ) -> str: ...


class AddOutboxEvent(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class ValidateChannelIds(Protocol):
    async def __call__(self, session: AsyncSession, channel_ids: list[str]) -> None: ...


class EnsureFocusedObservationCapacity(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        *,
        exclude_watch_id: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class WatchUpdateDependencies:
    build_watch_dedupe_key: BuildWatchDedupeKey
    add_outbox_event: AddOutboxEvent
    now: Clock
    validate_channel_ids: ValidateChannelIds
    ensure_focused_observation_capacity: EnsureFocusedObservationCapacity


async def ensure_focused_observation_capacity(
    session: AsyncSession,
    provider: Provider,
    *,
    exclude_watch_id: str | None = None,
) -> None:
    filters = [
        Watch.provider == provider,
        Watch.seat_observation_mode == SeatObservationMode.FOCUSED,
        Watch.status.not_in(list(TERMINAL_STATUSES)),
    ]
    if exclude_watch_id is not None:
        filters.append(Watch.id != exclude_watch_id)
    focused_ids = list(
        (
            await session.scalars(
                select(Watch.id)
                .where(*filters)
                .order_by(Watch.created_at, Watch.id)
                .limit(MAX_FOCUSED_WATCHES_PER_PROVIDER)
                .with_for_update()
            )
        ).all()
    )
    if len(focused_ids) >= MAX_FOCUSED_WATCHES_PER_PROVIDER:
        raise WatchCommandConflict(
            "focused observation allows up to 3 non-terminal watches per provider"
        )


async def validate_channel_ids(session: AsyncSession, channel_ids: list[str]) -> None:
    unique_ids = set(channel_ids)
    if not unique_ids:
        return
    found = set(
        (
            await session.scalars(
                select(NotificationChannel.id).where(
                    NotificationChannel.id.in_(unique_ids),
                    NotificationChannel.enabled.is_(True),
                )
            )
        ).all()
    )
    if unique_ids - found:
        raise WatchCommandValidationError("notification channels must exist and be enabled")


async def update_watch(
    session: AsyncSession,
    watch: Watch,
    data: WatchUpdate,
    *,
    dependencies: WatchUpdateDependencies,
) -> Watch:
    """Apply one locked watch update and commit its outbox in the same unit of work."""
    locked_watch = await session.scalar(
        select(Watch)
        .where(Watch.id == watch.id)
        .options(selectinload(Watch.candidates))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked_watch is None:
        raise WatchCommandNotFound("watch not found")
    watch = locked_watch
    values = data.model_dump(exclude_unset=True)
    previous_reservation_policy = watch.reservation_policy
    fully_editable_statuses = {WatchStatus.DRAFT, WatchStatus.PAUSED}
    policy_editable_statuses = {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
    }
    policy_only_active_update = watch.status not in fully_editable_statuses
    active_control_fields = {
        "reservation_policy",
        "seat_observation_mode",
        "focused_observation_interval_seconds",
    }
    if policy_only_active_update and (
        watch.status not in policy_editable_statuses
        or not values
        or not set(values).issubset(active_control_fields)
    ):
        raise WatchCommandConflict(
            "active watches only allow reservation_policy and observation policy updates"
        )
    if "notification_channel_ids" in values:
        await dependencies.validate_channel_ids(session, values["notification_channel_ids"])
    if watch.candidates and not policy_only_active_update:
        if "seat_class" in values and any(
            candidate.seat_class != values["seat_class"] for candidate in watch.candidates
        ):
            raise WatchCommandValidationError(
                "seat_class must remain consistent with persisted candidates"
            )
        if "train_numbers" in values and set(values["train_numbers"]) != {
            candidate.train_number for candidate in watch.candidates
        }:
            raise WatchCommandValidationError(
                "train_numbers must remain consistent with persisted candidates"
            )
    if not policy_only_active_update:
        next_time_from = values.get("time_from", watch.time_from)
        next_time_to = values.get("time_to", watch.time_to)
        if next_time_from >= next_time_to:
            raise WatchCommandValidationError("time_from must be earlier than time_to")
        seoul = ZoneInfo("Asia/Seoul")
        for candidate in watch.candidates:
            departure_at = candidate.departure_at
            if departure_at.tzinfo is None or departure_at.utcoffset() is None:
                departure_at = departure_at.replace(tzinfo=UTC)
            local_departure = departure_at.astimezone(seoul)
            local_time = local_departure.time().replace(tzinfo=None)
            if (
                local_departure.date() != watch.travel_date
                or not next_time_from <= local_time <= next_time_to
            ):
                raise WatchCommandValidationError(
                    "time window must remain consistent with persisted candidates"
                )
    next_observation_mode = values.get("seat_observation_mode", watch.seat_observation_mode)
    if (
        next_observation_mode is SeatObservationMode.FOCUSED
        and watch.seat_observation_mode is not SeatObservationMode.FOCUSED
    ):
        await dependencies.ensure_focused_observation_capacity(
            session,
            watch.provider,
            exclude_watch_id=watch.id,
        )
    for field, value in values.items():
        setattr(watch, field, value)
    if set(values).intersection(
        {"seat_observation_mode", "focused_observation_interval_seconds"}
    ) and watch.status in {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }:
        watch.next_check_at = dependencies.now()
    if (
        previous_reservation_policy is not ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and watch.reservation_policy is ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        and watch.status is WatchStatus.SEAT_FOUND
    ):
        # The immediate task uses the same due-watch observation and reservation fences as
        # the scheduler. Arm this already-actionable watch in the same transaction as the
        # policy change; otherwise a future next_check_at makes the best-effort task a no-op.
        watch.next_check_at = dependencies.now()
    if not policy_only_active_update:
        watch.dedupe_key = dependencies.build_watch_dedupe_key(
            watch.provider,
            watch.origin,
            watch.destination,
            watch.travel_date,
            watch.time_from,
            watch.time_to,
            watch.seat_class,
            watch.passenger_count,
            watch.train_numbers,
            watch.origin_node_id,
            watch.destination_node_id,
        )
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.updated",
        payload={"watch_id": watch.id},
        dedupe_key=f"watch:{watch.id}:updated:{dependencies.now().isoformat()}",
    )
    await session.commit()
    await session.refresh(watch)
    return watch
