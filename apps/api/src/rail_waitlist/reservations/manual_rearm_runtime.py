from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..outbox import add_outbox_event
from ..provider_account_management.models import RailProviderAccount
from ..provider_registry.application import get_execution_provider
from ..watch_management.models import Watch
from .attempt_policy import is_unresolved_unknown_manual_rearm_source
from .manual_rearm_application import (
    ManualReservationRearmDependencies,
    ManualReservationRearmResult,
)
from .manual_rearm_application import (
    authorize_manual_reservation_rearm as authorize_manual_reservation_rearm_application,
)
from .manual_rearm_contracts import ManualReservationRearmReason
from .payment_hold_application import is_payment_hold_ended


async def _reservation_dispatch_credential_version(
    session: AsyncSession,
    watch: Watch,
) -> int | None:
    if not get_execution_provider(watch.provider).capabilities().reservation_once:
        return None
    return await session.scalar(
        select(RailProviderAccount.credential_version).where(
            RailProviderAccount.provider == watch.provider,
            RailProviderAccount.enabled.is_(True),
            RailProviderAccount.last_auth_status == "authenticated",
        )
    )


async def authorize_manual_reservation_rearm(
    session: AsyncSession,
    watch_id: str,
    *,
    reason: ManualReservationRearmReason | None = None,
    official_reservation_state_confirmed: bool = False,
) -> ManualReservationRearmResult:
    return await authorize_manual_reservation_rearm_application(
        session,
        watch_id,
        reason=reason,
        official_reservation_state_confirmed=official_reservation_state_confirmed,
        dependencies=ManualReservationRearmDependencies(
            reservation_dispatch_credential_version=(_reservation_dispatch_credential_version),
            is_payment_hold_ended=is_payment_hold_ended,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            add_outbox_event=add_outbox_event,
            now=lambda: datetime.now(UTC),
        ),
    )
