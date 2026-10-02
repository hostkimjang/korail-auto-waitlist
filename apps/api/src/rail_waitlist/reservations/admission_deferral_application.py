"""Release only a new claim proved to have stopped before provider admission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, ReservationOutcome, WatchStatus
from ..korail_sidecar.provider_cooldown import ProviderCooldownDeferred
from ..provider_account_management.models import RailProviderAccount
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate


class DeferralDependencies(Protocol):
    def session_factory(self) -> AsyncSession: ...

    def now(self) -> datetime: ...

    async def apply_watch_transition(
        self, session: AsyncSession, watch: Watch, target: WatchStatus, *, reason: str | None = None
    ) -> Watch: ...

    async def add_outbox_event(
        self,
        session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object: ...


async def release_unadmitted_claim(
    *,
    watch_id: str,
    candidate_id: str,
    attempt_id: str,
    episode_key: str,
    credential_version: int | None,
    prior_reservation_attempted: bool,
    deferred: ProviderCooldownDeferred,
    dependencies: DeferralDependencies,
) -> bool:
    """Keep old attempts and history; a deferred claim is not an executed command.

    This function is called only for a validated, progress-free admission denial.
    Every durable identity is checked under the normal account/watch/candidate
    lock order. Changed identities or any provider progress keep the fence intact.
    """
    async with dependencies.session_factory() as session:
        try:
            account = await session.scalar(
                select(RailProviderAccount)
                .where(RailProviderAccount.provider == Provider.KORAIL)
                .with_for_update()
            )
            watch = await session.scalar(
                select(Watch).where(Watch.id == watch_id).with_for_update()
            )
            candidate = await session.scalar(
                select(WatchCandidate)
                .where(WatchCandidate.id == candidate_id)
                .with_for_update(of=WatchCandidate)
            )
            attempt = await session.scalar(
                select(ReservationAttempt)
                .where(ReservationAttempt.id == attempt_id)
                .with_for_update()
            )
            if (
                credential_version is None
                or account is None
                or not account.enabled
                or account.credential_version != credential_version
                or account.last_auth_status != "authenticated"
                or watch is None
                or watch.provider != Provider.KORAIL
                or watch.status != WatchStatus.RESERVING
                or candidate is None
                or candidate.watch_id != watch.id
                or candidate.state != "reservation_attempted"
                or attempt is None
                or attempt.candidate_id != candidate.id
                or attempt.episode_key != episode_key
                or attempt.credential_version != credential_version
                or attempt.outcome != ReservationOutcome.PENDING
                or attempt.finished_at is not None
                or attempt.progress_stages
                or attempt.reserved_seats
                or attempt.confirmation_correlation_seats
                or attempt.confirmation_outcome is not None
                or attempt.reconciliation_attempt_count
                or attempt.last_reconciled_at is not None
                or attempt.next_reconcile_at is not None
            ):
                return False
            latest_id = await session.scalar(
                select(ReservationAttempt.id)
                .where(ReservationAttempt.candidate_id == candidate.id)
                .order_by(ReservationAttempt.attempt_sequence.desc())
                .limit(1)
            )
            if latest_id != attempt.id:
                return False
            await session.delete(attempt)
            await session.flush()
            watch.reservation_attempted = prior_reservation_attempted
            # Force the normal fresh observation path instead of replaying old availability.
            candidate.state = "observed"
            retry_at = dependencies.now() + timedelta(seconds=deferred.retry_after_seconds)
            if watch.cooldown_until is not None:
                previous = watch.cooldown_until
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=UTC)
                retry_at = max(previous, retry_at)
            watch.cooldown_until = retry_at
            watch.observation_in_flight_until = None
            await dependencies.apply_watch_transition(
                session,
                watch,
                WatchStatus.COOLDOWN,
                reason="reservation_provider_admission_deferred",
            )
            watch.next_check_at = retry_at
            await dependencies.add_outbox_event(
                session,
                aggregate_type="watch",
                aggregate_id=watch.id,
                event_type="watch.reservation_deferred",
                payload={
                    "watch_id": watch.id,
                    "candidate_id": candidate.id,
                    "reason": deferred.reason,
                    "retry_at": retry_at.isoformat(),
                },
                dedupe_key=f"reservation-admission-deferred:{attempt_id}",
            )
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise
