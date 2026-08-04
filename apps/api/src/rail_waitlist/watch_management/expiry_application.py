from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import WatchStatus
from ..models import Watch
from ..operational import decide_operational_expiry

EXPIRABLE_WATCH_STATUSES = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
        WatchStatus.RESERVING,
        WatchStatus.PAYMENT_REQUIRED,
        WatchStatus.PAUSED,
        WatchStatus.COOLDOWN,
        WatchStatus.AUTH_REQUIRED,
    }
)
EXPIRY_CANDIDATE_STATES = frozenset({"active", "observed", "seat_found"})


class ApplyWatchTransition(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        *,
        reason: str | None = None,
    ) -> Watch: ...


@dataclass(frozen=True)
class WatchExpiryDependencies:
    apply_watch_transition: ApplyWatchTransition


def _watch_monitoring_deadline(watch: Watch) -> datetime:
    """Use the legacy KST window only when a watch has no observable candidates."""
    local_window_start = datetime.combine(
        watch.travel_date, watch.time_from, tzinfo=ZoneInfo("Asia/Seoul")
    )
    local_window_end = datetime.combine(
        watch.travel_date, watch.time_to, tzinfo=ZoneInfo("Asia/Seoul")
    )
    if local_window_end <= local_window_start:
        local_window_end += timedelta(days=1)
    return local_window_end.astimezone(timezone.utc)


def _expirable_watch_ids_query():
    return select(Watch.id).where(Watch.status.in_(EXPIRABLE_WATCH_STATUSES)).order_by(Watch.id)


def _locked_expirable_watch_query(watch_id: str):
    return (
        select(Watch)
        .where(
            Watch.id == watch_id,
            Watch.status.in_(EXPIRABLE_WATCH_STATUSES),
        )
        .with_for_update()
    )


async def expire_elapsed_watches(
    session: AsyncSession,
    now: datetime,
    *,
    dependencies: WatchExpiryDependencies,
) -> int:
    """Expire operationally terminal watches in one ordered, atomic pass."""

    try:
        # ``travel_date`` is not an expiry eligibility gate. Near KST midnight, a fresh
        # official CLOSED/CANCELLED signal can precede the candidate's next service date.
        # The per-candidate operational decision remains the authoritative boundary and
        # preserves future, delayed, boarding, OPEN, and WAITLIST services.
        watch_ids = list((await session.scalars(_expirable_watch_ids_query())).all())
        expired = 0
        mutated = False
        for watch_id in watch_ids:
            watch = await session.scalar(_locked_expirable_watch_query(watch_id))
            if watch is None:
                continue
            candidates = [
                candidate
                for candidate in watch.candidates
                if candidate.state in EXPIRY_CANDIDATE_STATES
            ]
            if candidates:
                active_candidates = []
                for candidate in candidates:
                    decision = decide_operational_expiry(candidate, now)
                    if decision.expire:
                        candidate.state = "expired"
                        mutated = True
                    else:
                        active_candidates.append(candidate)
                if active_candidates:
                    continue
                await dependencies.apply_watch_transition(
                    session,
                    watch,
                    WatchStatus.EXPIRED,
                    reason="all_candidates_operationally_terminal_or_horizon_elapsed",
                )
                mutated = True
                expired += 1
                continue
            if _watch_monitoring_deadline(watch) > now:
                continue
            await dependencies.apply_watch_transition(session, watch, WatchStatus.EXPIRED)
            mutated = True
            expired += 1
        if mutated:
            await session.commit()
        return expired
    except Exception:
        await session.rollback()
        raise
