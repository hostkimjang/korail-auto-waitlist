from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationPolicy, WatchStatus
from ..provider_account_management.application import has_authenticated_provider_account
from ..provider_registry.application import get_execution_provider
from .models import Watch


async def _reservation_dispatch_ready(session: AsyncSession, watch: Watch) -> bool:
    """Resolve the account gate before the provider capability, matching dispatch order."""
    if not await has_authenticated_provider_account(session, watch.provider):
        return False
    return get_execution_provider(watch.provider).capabilities().reservation_once


async def should_enqueue_after_policy_update(
    session: AsyncSession,
    requested_policy: ReservationPolicy | None,
    watch: Watch,
) -> bool:
    """Return whether a committed policy update should wake the durable watch pipeline."""
    if requested_policy is not ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT:
        return False
    if watch.reservation_policy is not ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT:
        return False
    if watch.status is not WatchStatus.SEAT_FOUND:
        return False
    if watch.provider not in {Provider.KORAIL, Provider.SRT}:
        return False
    return await _reservation_dispatch_ready(session, watch)


async def should_enqueue_after_start(
    session: AsyncSession,
    previous_status: WatchStatus,
    watch: Watch,
) -> bool:
    """Return whether a committed start transition should wake the durable pipeline."""
    if previous_status is WatchStatus.SCHEDULED:
        return False
    if watch.status is not WatchStatus.SCHEDULED:
        return False
    if watch.next_check_at is None:
        return False
    if watch.provider not in {Provider.KORAIL, Provider.SRT}:
        return False
    if watch.reservation_policy is not ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT:
        return False
    return await _reservation_dispatch_ready(session, watch)
