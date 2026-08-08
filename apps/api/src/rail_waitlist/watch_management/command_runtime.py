from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..idempotency.application import (
    get_idempotent_resource,
    remember_idempotency,
    request_hash,
)
from ..outbox import add_outbox_event
from ..policy import build_watch_dedupe_key
from ..provider_registry.application import get_timetable_provider
from .create_application import WatchCreateDependencies
from .create_application import create_watch as create_watch_application
from .models import Watch
from .schemas import WatchCreate, WatchUpdate
from .update_application import (
    WatchUpdateDependencies,
    ensure_focused_observation_capacity,
    validate_channel_ids,
)
from .update_application import update_watch as update_watch_application


def _experimental_rail_enabled() -> bool:
    from ..config import get_settings

    return get_settings().experimental_rail_enabled


def watch_create_dependencies() -> WatchCreateDependencies:
    """Resolve production create dependencies at the call boundary."""
    return WatchCreateDependencies(
        request_hash=request_hash,
        get_idempotent_resource=get_idempotent_resource,
        ensure_focused_observation_capacity=ensure_focused_observation_capacity,
        experimental_rail_enabled=_experimental_rail_enabled,
        validate_channel_ids=validate_channel_ids,
        build_watch_dedupe_key=build_watch_dedupe_key,
        official_booking_url_for_provider=lambda provider: get_timetable_provider(
            provider
        ).official_booking_url(),
        remember_idempotency=remember_idempotency,
        add_outbox_event=add_outbox_event,
        now=lambda: datetime.now(UTC),
    )


def watch_update_dependencies() -> WatchUpdateDependencies:
    """Resolve production update dependencies at the call boundary."""
    return WatchUpdateDependencies(
        build_watch_dedupe_key=build_watch_dedupe_key,
        add_outbox_event=add_outbox_event,
        now=lambda: datetime.now(UTC),
        validate_channel_ids=validate_channel_ids,
        ensure_focused_observation_capacity=ensure_focused_observation_capacity,
    )


async def create_watch(
    session: AsyncSession,
    data: WatchCreate,
    idempotency_key: str | None = None,
) -> Watch:
    return await create_watch_application(
        session,
        data,
        idempotency_key,
        dependencies=watch_create_dependencies(),
    )


async def update_watch(session: AsyncSession, watch: Watch, data: WatchUpdate) -> Watch:
    return await update_watch_application(
        session,
        watch,
        data,
        dependencies=watch_update_dependencies(),
    )
