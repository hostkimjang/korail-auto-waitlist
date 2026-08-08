from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..domain import Provider, ReservationOutcome, WatchStatus
from ..provider_contracts import ExecutionProvider, ProviderLifecycle
from ..watch_management.models import ReservationAttempt, Watch, WatchCandidate

LOGGER = logging.getLogger(__name__)
EXTERNAL_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})
OBSERVATION_WATCH_STATUSES = frozenset(
    {
        WatchStatus.SCHEDULED,
        WatchStatus.WATCHING,
        WatchStatus.OFFICIAL_WAITLIST,
        WatchStatus.SEAT_FOUND,
    }
)


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


class ProviderGetter(Protocol):
    def __call__(self, provider: Provider) -> ExecutionProvider: ...


class ArmProviderWatches(Protocol):
    async def __call__(
        self,
        provider: Provider,
        now: datetime,
        *,
        adapter: ExecutionProvider | None = None,
    ) -> int: ...


class SessionLifecycleOperation(Protocol):
    async def __call__(self, session: AsyncSession, now: datetime) -> int: ...


class ProcessWatchGroup(Protocol):
    async def __call__(
        self,
        watch_ids: list[str],
        now: datetime,
        *,
        provider: Provider | None = None,
        adapter: ExecutionProvider | None = None,
    ) -> None: ...


class ReconcileReservationAttempt(Protocol):
    async def __call__(
        self,
        attempt_id: str,
        *,
        adapter: ExecutionProvider | None = None,
    ) -> int: ...


class CloseAdapter(Protocol):
    async def __call__(
        self,
        adapter: ProviderLifecycle,
        provider: Provider,
    ) -> None: ...


ReconciliationDueClause = Callable[[datetime], ColumnElement[bool]]
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unique_providers(providers: Sequence[Provider]) -> list[Provider]:
    return list(dict.fromkeys(providers))


@dataclass(frozen=True)
class DuePipelineDependencies:
    session_factory: AsyncSessionFactory
    get_execution_provider: ProviderGetter
    arm_provider_watches: ArmProviderWatches
    expire_elapsed_watches: SessionLifecycleOperation
    recover_stale_reservation_attempts: SessionLifecycleOperation
    process_watch_group: ProcessWatchGroup
    reconcile_reservation_attempt: ReconcileReservationAttempt
    close_execution_adapter: CloseAdapter
    reservation_reconciliation_due_clause: ReconciliationDueClause
    now: Clock = _now


async def process_provider_due_pipeline(
    provider: Provider,
    watch_groups: list[list[str]],
    reconciliation_attempt_ids: list[str],
    adapter: ExecutionProvider,
    *,
    dependencies: DuePipelineDependencies,
) -> None:
    """Process one provider serially while other providers may make progress."""

    for watch_ids in watch_groups:
        # A busy cycle can process many groups. Lease epochs and next-check scheduling
        # must use the group's actual start time rather than the stale initial sweep time.
        await dependencies.process_watch_group(
            watch_ids,
            dependencies.now(),
            provider=provider,
            adapter=adapter,
        )
    # Historical read-only confirmation remains lower priority than newly due
    # cancellation-seat observation and its single reservation attempt.
    for attempt_id in reconciliation_attempt_ids:
        await dependencies.reconcile_reservation_attempt(attempt_id, adapter=adapter)


async def process_provider_due_pipelines(
    provider_order: list[Provider],
    watch_groups: dict[Provider, list[list[str]]],
    reconciliation_attempt_ids: dict[Provider, list[str]],
    adapters: dict[Provider, ExecutionProvider],
    *,
    dependencies: DuePipelineDependencies,
) -> None:
    """Run providers concurrently without weakening each provider/account fence."""

    provider_order = _unique_providers(provider_order)
    results = await asyncio.gather(
        *(
            process_provider_due_pipeline(
                provider,
                watch_groups.get(provider, []),
                reconciliation_attempt_ids.get(provider, []),
                adapters[provider],
                dependencies=dependencies,
            )
            for provider in provider_order
        ),
        return_exceptions=True,
    )
    failures: list[BaseException] = []
    for provider, result in zip(provider_order, results, strict=True):
        if isinstance(result, BaseException):
            LOGGER.error(
                "provider due pipeline failed provider=%s error=%s",
                provider.value,
                type(result).__name__,
            )
            failures.append(result)
    if failures:
        # gather(return_exceptions=True) lets every provider finish first. Preserve the
        # task's fail-fast contract only after the independent work has settled.
        raise failures[0]


async def process_due_pipeline(
    providers_to_arm: Sequence[Provider],
    *,
    dependencies: DuePipelineDependencies,
) -> int:
    """Select and process one due sweep while keeping provider resources task-scoped."""

    now = dependencies.now()
    adapters: dict[Provider, ExecutionProvider] = {}
    groups: dict[tuple[Provider, str], list[str]] = {}
    reconciliation_rows: list[tuple[str, Provider]] = []
    try:
        # External adapters are task-scoped. Reusing one adapter per provider keeps
        # Redis/HTTP resources on this task's event loop and shares a service-day cache.
        for provider in _unique_providers(providers_to_arm):
            execution_adapter = dependencies.get_execution_provider(provider)
            adapters[provider] = execution_adapter
            await dependencies.arm_provider_watches(
                provider,
                now,
                adapter=execution_adapter,
            )
        async with dependencies.session_factory() as session:
            await dependencies.expire_elapsed_watches(session, now)
            await dependencies.recover_stale_reservation_attempts(session, now)
            reconciliation_rows = list(
                (
                    await session.execute(
                        select(ReservationAttempt.id, Watch.provider)
                        .join(
                            WatchCandidate,
                            WatchCandidate.id == ReservationAttempt.candidate_id,
                        )
                        .join(Watch, Watch.id == WatchCandidate.watch_id)
                        .where(
                            ReservationAttempt.outcome.in_(
                                [
                                    ReservationOutcome.PAYMENT_REQUIRED,
                                    ReservationOutcome.UNKNOWN,
                                ]
                            ),
                            ReservationAttempt.credential_version.is_not(None),
                            dependencies.reservation_reconciliation_due_clause(now),
                            Watch.provider.in_(EXTERNAL_PROVIDERS),
                        )
                        .order_by(ReservationAttempt.finished_at)
                    )
                ).all()
            )
            rows = list(
                (
                    await session.execute(
                        select(Watch.id, Watch.dedupe_key, Watch.provider)
                        .where(
                            Watch.status.in_(OBSERVATION_WATCH_STATUSES),
                            Watch.next_check_at.is_not(None),
                            Watch.next_check_at <= now,
                        )
                        .order_by(Watch.created_at)
                    )
                ).all()
            )
        for watch_id, dedupe_key, provider in rows:
            groups.setdefault((provider, dedupe_key), []).append(watch_id)
        watch_groups_by_provider: dict[Provider, list[list[str]]] = {}
        provider_order: list[Provider] = []
        for (provider, _), watch_ids in groups.items():
            if provider not in watch_groups_by_provider:
                watch_groups_by_provider[provider] = []
                provider_order.append(provider)
            watch_groups_by_provider[provider].append(watch_ids)
        reconciliation_by_provider: dict[Provider, list[str]] = {}
        for attempt_id, provider in reconciliation_rows:
            if provider not in reconciliation_by_provider:
                reconciliation_by_provider[provider] = []
                if provider not in provider_order:
                    provider_order.append(provider)
            reconciliation_by_provider[provider].append(attempt_id)
        for provider in provider_order:
            adapter = adapters.get(provider)
            if adapter is None:
                adapter = dependencies.get_execution_provider(provider)
                adapters[provider] = adapter
        await process_provider_due_pipelines(
            provider_order,
            watch_groups_by_provider,
            reconciliation_by_provider,
            adapters,
            dependencies=dependencies,
        )
    finally:
        closed_adapter_ids: set[int] = set()
        for provider, adapter in adapters.items():
            if id(adapter) in closed_adapter_ids:
                continue
            closed_adapter_ids.add(id(adapter))
            await dependencies.close_execution_adapter(adapter, provider)
    return len(groups)
