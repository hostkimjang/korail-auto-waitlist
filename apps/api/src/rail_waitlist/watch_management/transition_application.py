from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider, WatchStatus
from ..provider_contracts import ProviderCapabilitySource
from .models import SeatObservation, Watch, WatchTransitionHistory
from .transition_policy import (
    AllowedWatchTransition,
    NextCheckPolicy,
    NoOpWatchTransition,
    RejectedWatchTransition,
    WatchTransitionDecision,
    WatchTransitionIdentity,
)


class WatchTransitionRejected(RuntimeError):
    """A requested watch status edge is not allowed by the domain policy."""


class RequestHash(Protocol):
    def __call__(self, value: object) -> str: ...


class GetIdempotentResource(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        scope: str,
        key: str | None,
        payload_hash: str,
    ) -> str | None: ...


class DecideWatchTransition(Protocol):
    def __call__(
        self,
        current_status: WatchStatus,
        target_status: WatchStatus,
    ) -> WatchTransitionDecision: ...


class GetExecutionProvider(Protocol):
    def __call__(self, provider: Provider) -> ProviderCapabilitySource: ...


class BuildWatchTransitionIdentity(Protocol):
    def __call__(
        self,
        transition: AllowedWatchTransition,
        *,
        watch_id: str,
        transition_at: datetime,
        reason: str | None,
    ) -> WatchTransitionIdentity: ...


class RememberIdempotency(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        scope: str,
        key: str | None,
        resource_id: str,
        payload_hash: str,
    ) -> None: ...


class AddOutboxEvent(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, object],
        dedupe_key: str,
    ) -> object: ...


class AddWatchNotifications(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        watch: Watch,
        target: WatchStatus,
        transition_token: str,
        *,
        reason: str | None = None,
    ) -> None: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class WatchTransitionDependencies:
    request_hash: RequestHash
    get_idempotent_resource: GetIdempotentResource
    decide_watch_transition: DecideWatchTransition
    get_execution_provider: GetExecutionProvider
    build_watch_transition_identity: BuildWatchTransitionIdentity
    remember_idempotency: RememberIdempotency
    add_outbox_event: AddOutboxEvent
    add_watch_notifications: AddWatchNotifications
    now: Clock


async def apply_watch_transition(
    session: AsyncSession,
    watch: Watch,
    target: WatchStatus,
    idempotency_key: str | None = None,
    *,
    reason: str | None = None,
    observation: SeatObservation | None = None,
    dependencies: WatchTransitionDependencies,
) -> Watch:
    """Apply transition artifacts inside the caller-owned unit of work."""
    payload_hash = dependencies.request_hash({"watch_id": watch.id, "target": target.value})
    scope = f"watch.transition.{target.value}"
    existing_id = await dependencies.get_idempotent_resource(
        session,
        scope,
        idempotency_key,
        payload_hash,
    )
    if existing_id:
        existing = await session.get(Watch, existing_id)
        if existing:
            return existing

    decision = dependencies.decide_watch_transition(watch.status, target)
    if isinstance(decision, NoOpWatchTransition):
        return watch
    if isinstance(decision, RejectedWatchTransition):
        raise WatchTransitionRejected(decision.detail)

    watch.status = decision.target_status
    transition_at = dependencies.now()
    watch.updated_at = transition_at
    if decision.clear_cooldown:
        watch.cooldown_until = None
    if decision.next_check_policy is NextCheckPolicy.TRANSITION_AT_IF_SEAT_MONITORING:
        execution_capabilities = dependencies.get_execution_provider(watch.provider).capabilities()
        watch.next_check_at = transition_at if execution_capabilities.seat_monitoring else None
    elif decision.next_check_policy is NextCheckPolicy.CLEAR:
        watch.next_check_at = None

    identity = dependencies.build_watch_transition_identity(
        decision,
        watch_id=watch.id,
        transition_at=transition_at,
        reason=reason,
    )
    session.add(
        WatchTransitionHistory(
            watch=watch,
            from_status=decision.previous_status,
            to_status=decision.target_status,
            reason=identity.reason,
            observation=observation,
        )
    )
    await dependencies.remember_idempotency(
        session,
        scope,
        idempotency_key,
        watch.id,
        payload_hash,
    )
    await dependencies.add_outbox_event(
        session,
        aggregate_type="watch",
        aggregate_id=watch.id,
        event_type="watch.status_changed",
        payload={
            "watch_id": watch.id,
            "from": decision.previous_status.value,
            "to": decision.target_status.value,
        },
        dedupe_key=identity.status_event_dedupe_key,
    )
    await dependencies.add_watch_notifications(
        session,
        watch,
        decision.target_status,
        identity.transition_token,
        reason=identity.reason,
    )
    return watch
