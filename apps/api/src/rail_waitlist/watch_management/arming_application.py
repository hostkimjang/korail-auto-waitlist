from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, WatchStatus
from ..provider_contracts import ExecutionProvider
from .models import Watch

EXTERNAL_ARMING_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})
ARMABLE_WATCH_STATUSES = (
    WatchStatus.SCHEDULED,
    WatchStatus.OFFICIAL_WAITLIST,
    WatchStatus.SEAT_FOUND,
)


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


class ExecutionProviderGetter(Protocol):
    def __call__(self, provider: Provider) -> ExecutionProvider: ...


@dataclass(frozen=True, slots=True)
class WatchArmingDependencies:
    session_factory: AsyncSessionFactory
    get_execution_provider: ExecutionProviderGetter


async def arm_supported_provider_watches(
    provider: Provider,
    now: datetime,
    *,
    adapter: ExecutionProvider | None = None,
    dependencies: WatchArmingDependencies,
) -> int:
    """Arm eligible watches when a provider gains seat-monitoring capability."""
    if provider not in EXTERNAL_ARMING_PROVIDERS:
        return 0
    execution_adapter = adapter or dependencies.get_execution_provider(provider)
    if not execution_adapter.capabilities().seat_monitoring:
        return 0
    async with dependencies.session_factory() as session:
        watches = list(
            (
                await session.scalars(
                    select(Watch)
                    .where(
                        Watch.provider == provider,
                        Watch.mode == "official",
                        Watch.status.in_(ARMABLE_WATCH_STATUSES),
                        Watch.next_check_at.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for watch in watches:
            watch.next_check_at = now
        if watches:
            await session.commit()
        return len(watches)
