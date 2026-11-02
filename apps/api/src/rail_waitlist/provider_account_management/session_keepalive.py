"""Decide whether an idle KORAIL login session is worth an official keepalive probe."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain import Provider, ReservationPolicy, WatchStatus
from ..watch_management.models import Watch

LOGGER = logging.getLogger(__name__)

# Watches that are observing, or return to observing on their own, and may still book
# automatically. Only these justify probing an idle session ahead of a booking; paused,
# terminal, payment and notify-only watches never start a reservation login.
KEEPALIVE_DEMAND_WATCH_STATUSES: frozenset[WatchStatus] = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.COOLDOWN,
    }
)


async def has_session_keepalive_demand(
    session_factory: async_sessionmaker[AsyncSession],
    provider: Provider,
) -> bool | None:
    """Return whether an active watch could still book automatically on this provider.

    ``None`` means the read failed. The caller then skips only this tick's probe and keeps
    the retry clock of an earlier failed probe. The ordinary refresh before the local reuse
    deadline still applies, so the account never loses its session over it.
    """

    try:
        async with session_factory() as session:
            watch_id = await session.scalar(
                select(Watch.id)
                .where(
                    Watch.provider == provider,
                    Watch.reservation_policy == ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
                    Watch.status.in_(KEEPALIVE_DEMAND_WATCH_STATUSES),
                )
                .limit(1)
            )
            await session.rollback()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- database details stay redacted.
        LOGGER.warning(
            "Provider runtime session keepalive demand check failed provider=%s",
            provider.value,
        )
        return None
    return watch_id is not None
