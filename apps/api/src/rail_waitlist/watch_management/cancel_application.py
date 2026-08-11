from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import WatchStatus
from .models import Watch
from .transition_command_application import WatchTransitionCommandNotFound


class WatchCancellationInProgress(RuntimeError):
    """The provider call was already claimed and cannot be cancelled safely."""


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
class CancelWatchDependencies:
    apply_watch_transition: ApplyWatchTransition


async def cancel_watch(
    session: AsyncSession,
    watch: Watch,
    idempotency_key: str | None = None,
    *,
    dependencies: CancelWatchDependencies,
) -> Watch:
    """Cancel under the same row lock used by the reservation claim."""
    locked_watch = await session.scalar(
        select(Watch)
        .where(Watch.id == watch.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked_watch is None:
        raise WatchTransitionCommandNotFound("watch not found")
    if locked_watch.status in {WatchStatus.RESERVING, WatchStatus.PAYMENT_REQUIRED}:
        raise WatchCancellationInProgress(
            "예매 요청이 이미 시작되었거나 결제가 필요한 예약이 있어 대기를 취소할 수 "
            "없습니다. 공식 예약 내역을 확인해 주세요."
        )
    result = await dependencies.apply_watch_transition(
        session,
        locked_watch,
        WatchStatus.EXPIRED,
        idempotency_key,
        reason="user_cancelled_watch",
    )
    await session.commit()
    await session.refresh(result)
    return result
