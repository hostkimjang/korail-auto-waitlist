from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .cancel_application import CancelWatchDependencies
from .cancel_application import cancel_watch as cancel_watch_application
from .models import Watch
from .transition_runtime import apply_watch_transition


async def cancel_watch(
    session: AsyncSession,
    watch: Watch,
    idempotency_key: str | None = None,
) -> Watch:
    return await cancel_watch_application(
        session,
        watch,
        idempotency_key,
        dependencies=CancelWatchDependencies(
            apply_watch_transition=apply_watch_transition,
        ),
    )
