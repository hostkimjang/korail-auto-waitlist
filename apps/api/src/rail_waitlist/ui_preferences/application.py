from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..domain import WatchStatus
from ..models import AdminAccount, ProviderExecutionLease, Watch
from ..policy import next_interval
from ..provider_execution_lease import ANONYMOUS_PUBLIC_ACCOUNT_SCOPE
from .schemas import UiPreferencesUpdate

ACTIVE_OBSERVATION_STATUSES = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }
)
OBSERVABLE_OBSERVATION_CANDIDATE_STATES = frozenset({"active", "observed", "seat_found"})


def _utc_instant(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _watch_departure_at(watch: Watch) -> datetime:
    observable_departures = [
        candidate.departure_at
        for candidate in watch.candidates
        if candidate.state in OBSERVABLE_OBSERVATION_CANDIDATE_STATES
    ]
    departure_at = (
        min(observable_departures)
        if observable_departures
        else datetime.combine(
            watch.travel_date, watch.time_from, tzinfo=ZoneInfo("Asia/Seoul")
        ).astimezone(UTC)
    )
    return departure_at.replace(tzinfo=UTC) if departure_at.tzinfo is None else departure_at


async def update_admin_ui_preferences(
    session: AsyncSession,
    account: AdminAccount,
    data: UiPreferencesUpdate,
    *,
    now: datetime | None = None,
) -> AdminAccount:
    """Persist UI/backend cadence settings and re-arm idle active watches.

    A provider lease means an observation is already in flight. Those watches keep their
    current schedule so this settings write cannot create a duplicate provider call; the
    completing cycle reads the freshly persisted preference before calculating its next run.
    """

    changed = data.model_dump(exclude_unset=True, exclude_none=True)
    # Keep 0025 rolling clients compatible without letting their split values affect the
    # unified cadence. Only the 0026 field changes scheduling.
    changed.pop("balanced_observation_interval_seconds", None)
    changed.pop("focused_observation_interval_seconds", None)
    changed_observation_policy = "observation_interval_seconds" in changed
    normalized_now = (now or datetime.now(UTC)).astimezone(UTC)
    for field, value in changed.items():
        setattr(account, field, value)
    account.preferences_updated_at = normalized_now

    if changed_observation_policy:
        active_leased_providers = set(
            (
                await session.scalars(
                    select(ProviderExecutionLease.provider).where(
                        ProviderExecutionLease.account_scope == ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
                        ProviderExecutionLease.owner_token.is_not(None),
                        ProviderExecutionLease.expires_at.is_not(None),
                        ProviderExecutionLease.expires_at > normalized_now,
                    )
                )
            ).all()
        )
        watches = list(
            (
                await session.scalars(
                    select(Watch)
                    .where(Watch.status.in_(ACTIVE_OBSERVATION_STATUSES))
                    .options(selectinload(Watch.candidates))
                    .with_for_update()
                )
            ).all()
        )
        for watch in watches:
            cooldown_until = watch.cooldown_until
            if cooldown_until is not None:
                cooldown_until = (
                    cooldown_until.replace(tzinfo=UTC)
                    if cooldown_until.tzinfo is None
                    else cooldown_until.astimezone(UTC)
                )
            if (
                watch.provider in active_leased_providers
                or (
                    watch.next_check_at is not None
                    and _utc_instant(watch.next_check_at) <= normalized_now
                )
                or (cooldown_until is not None and cooldown_until > normalized_now)
            ):
                continue
            watch.next_check_at = normalized_now + next_interval(
                normalized_now,
                _watch_departure_at(watch),
                watch.unchanged_runs,
                observation_interval_seconds=account.observation_interval_seconds,
            )

    await session.commit()
    await session.refresh(account)
    return account
