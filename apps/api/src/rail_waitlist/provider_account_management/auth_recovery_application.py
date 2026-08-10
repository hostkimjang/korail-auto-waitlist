from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationOutcome, WatchStatus
from ..watch_management.models import (
    ReservationAttempt,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)


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
class ProviderAuthRecoveryDependencies:
    apply_watch_transition: ApplyWatchTransition


async def resume_watches_after_verified_provider_login(
    session: AsyncSession,
    provider: Provider,
    authenticated_at: datetime,
    *,
    dependencies: ProviderAuthRecoveryDependencies,
) -> list[str]:
    """Resume authentication-stalled watches inside the caller-owned account transaction."""
    watch_ids = list(
        (
            await session.scalars(
                select(Watch.id).where(
                    Watch.provider == provider,
                    Watch.status == WatchStatus.AUTH_REQUIRED,
                )
            )
        ).all()
    )
    resumed: list[str] = []
    for watch_id in watch_ids:
        watch = await session.scalar(
            select(Watch)
            .where(
                Watch.id == watch_id,
                Watch.status == WatchStatus.AUTH_REQUIRED,
            )
            .with_for_update()
        )
        if watch is None:
            continue
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch.id)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(1)
        )
        if latest_transition is None:
            continue

        transition_at = latest_transition.created_at
        if transition_at.tzinfo is None or transition_at.utcoffset() is None:
            transition_at = transition_at.replace(tzinfo=UTC)
        verified_at = authenticated_at
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            verified_at = verified_at.replace(tzinfo=UTC)
        auth_failure_reverified = (
            latest_transition.reason
            in {"reservation_auth_required", "reservation_provider_blocked"}
            and transition_at <= verified_at
        )
        preflight_auth_reverified = (
            latest_transition.reason
            in {
                "provider_account_not_authenticated_before_reservation",
                "provider_account_provider_blocked_before_observation",
            }
            and transition_at <= verified_at
        )
        non_auth_unknown = latest_transition.reason == "reservation_unknown"
        if not (auth_failure_reverified or preflight_auth_reverified or non_auth_unknown):
            continue

        candidates = list(
            (
                await session.scalars(
                    select(WatchCandidate).where(WatchCandidate.watch_id == watch.id)
                )
            ).all()
        )
        for candidate in candidates:
            if candidate.state != "failed":
                continue
            attempt = await session.scalar(
                select(ReservationAttempt)
                .where(ReservationAttempt.candidate_id == candidate.id)
                .order_by(ReservationAttempt.attempt_sequence.desc())
                .limit(1)
            )
            if auth_failure_reverified:
                if attempt is not None and attempt.outcome in {
                    ReservationOutcome.AUTH_REQUIRED,
                    ReservationOutcome.PROVIDER_BLOCKED,
                }:
                    candidate.state = "observed"
            elif (
                non_auth_unknown
                and attempt is not None
                and attempt.outcome is ReservationOutcome.UNKNOWN
            ):
                candidate.state = "observed"
            # ``provider_account_not_authenticated_before_reservation`` is recorded
            # before ``begin_reservation_attempt``. Keep its candidate state intact:
            # there is no completed attempt to clear and the next observation decides
            # whether the still-unclaimed initial episode can be attempted.

        await dependencies.apply_watch_transition(
            session,
            watch,
            WatchStatus.SCHEDULED,
            reason=(
                (
                    "provider_login_reverified_after_provider_block"
                    if latest_transition.reason == "reservation_provider_blocked"
                    else "provider_login_reverified"
                )
                if auth_failure_reverified
                else (
                    (
                        "provider_login_reverified_before_observation"
                        if latest_transition.reason.endswith("_before_observation")
                        else "provider_login_reverified_before_reservation"
                    )
                    if preflight_auth_reverified
                    else "reservation_unknown_monitoring_resumed"
                )
            ),
        )
        resumed.append(watch.id)
    return resumed
