from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import WatchStatus
from .models import Watch


class WatchTransitionCommandNotFound(RuntimeError):
    """The watch disappeared before its transition row lock was acquired."""


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        idempotency_key: str | None = None,
        *,
        reason: str | None = None,
    ) -> Watch: ...


@dataclass(frozen=True, slots=True)
class WatchTransitionCommandDependencies:
    apply_watch_transition: ApplyWatchTransition


async def transition_watch(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
    dependencies: WatchTransitionCommandDependencies,
) -> Watch:
    """Lock and transition one watch in a self-contained command transaction."""
    locked_watch = await session.scalar(
        select(Watch)
        .where(Watch.id == watch.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked_watch is None:
        raise WatchTransitionCommandNotFound("watch not found")
    result = await dependencies.apply_watch_transition(
        session,
        locked_watch,
        target,
        idempotency_key,
        reason=reason,
    )
    await session.commit()
    await session.refresh(result)
    return result
