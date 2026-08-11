from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..outbox import add_outbox_event
from ..provider_account_management.application import has_authenticated_provider_account
from ..provider_registry.application import get_execution_provider
from ..watch_management.models import Watch
from .manual_rearm_application import (
    ManualReservationRearmDependencies,
    ManualReservationRearmResult,
)
from .manual_rearm_application import (
    authorize_manual_reservation_rearm as authorize_manual_reservation_rearm_application,
)
from .payment_hold_application import is_payment_hold_ended


async def _reservation_dispatch_ready(session: AsyncSession, watch: Watch) -> bool:
    if not await has_authenticated_provider_account(session, watch.provider):
        return False
    return get_execution_provider(watch.provider).capabilities().reservation_once


async def authorize_manual_reservation_rearm(
    session: AsyncSession,
    watch_id: str,
) -> ManualReservationRearmResult:
    return await authorize_manual_reservation_rearm_application(
        session,
        watch_id,
        dependencies=ManualReservationRearmDependencies(
            reservation_dispatch_ready=_reservation_dispatch_ready,
            is_payment_hold_ended=is_payment_hold_ended,
            add_outbox_event=add_outbox_event,
            now=lambda: datetime.now(UTC),
        ),
    )
