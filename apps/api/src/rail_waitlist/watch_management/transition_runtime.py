from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import WatchStatus
from ..idempotency.application import (
    get_idempotent_resource,
    remember_idempotency,
    request_hash,
)
from ..notification_management.watch_transition_application import add_watch_notifications
from ..outbox import add_outbox_event
from ..provider_registry.application import get_execution_provider
from .models import SeatObservation, Watch
from .transition_application import WatchTransitionDependencies
from .transition_application import apply_watch_transition as apply_watch_transition_application
from .transition_command_application import WatchTransitionCommandDependencies
from .transition_command_application import transition_watch as transition_watch_application
from .transition_policy import build_watch_transition_identity, decide_watch_transition


def watch_transition_dependencies() -> WatchTransitionDependencies:
    """Compose the feature-owned production dependencies for one transition."""
    return WatchTransitionDependencies(
        request_hash=request_hash,
        get_idempotent_resource=get_idempotent_resource,
        decide_watch_transition=decide_watch_transition,
        get_execution_provider=get_execution_provider,
        build_watch_transition_identity=build_watch_transition_identity,
        remember_idempotency=remember_idempotency,
        add_outbox_event=add_outbox_event,
        add_watch_notifications=add_watch_notifications,
        now=lambda: datetime.now(UTC),
    )


async def apply_watch_transition(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
    observation: SeatObservation | None = None,
) -> Watch:
    """Apply transition artifacts without ending the caller-owned unit of work."""
    return await apply_watch_transition_application(
        session,
        watch,
        target,
        idempotency_key,
        reason=reason,
        observation=observation,
        dependencies=watch_transition_dependencies(),
    )


async def transition_watch(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
) -> Watch:
    """Run the feature-owned locking command without transport error mapping."""
    return await transition_watch_application(
        session,
        watch,
        target,
        idempotency_key,
        reason=reason,
        dependencies=WatchTransitionCommandDependencies(
            apply_watch_transition=apply_watch_transition,
        ),
    )
