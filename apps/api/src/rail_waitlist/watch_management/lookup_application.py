from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Watch


class WatchLookupNotFound(LookupError):
    pass


async def find_watch(session: AsyncSession, watch_id: str) -> Watch:
    """Join the caller's unit of work and return the existing watch identity."""
    watch = await session.get(Watch, watch_id)
    if watch is None:
        raise WatchLookupNotFound("watch not found")
    return watch
