from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ..domain import Provider
from ..provider_contracts import ExecutionProvider, ProviderLifecycle
from ..provider_execution.contracts import (
    AcquireExecutionLease,
    ExecutionLeaseGrant,
    ExecutionLeaseService,
)
from .group_application import (
    AsyncSessionFactory,
    ObservationGroupDependencies,
)

LEASED_OBSERVATION_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})


class WatchGroupProviderResolver(Protocol):
    async def __call__(
        self,
        watch_ids: list[str],
        *,
        session_factory: AsyncSessionFactory,
    ) -> Provider | None: ...


class ExecutionProviderGetter(Protocol):
    def __call__(self, provider: Provider) -> ExecutionProvider: ...


class ObservationGroupDependenciesFactory(Protocol):
    def __call__(
        self,
        lease_service: ExecutionLeaseService | None = None,
        adapter: ExecutionProvider | None = None,
    ) -> ObservationGroupDependencies: ...


class WatchGroupObservationProcessor(Protocol):
    async def __call__(
        self,
        watch_ids: list[str],
        now: datetime,
        *,
        provider: Provider,
        adapter: ExecutionProvider,
        lease_grant: object | None,
        dependencies: ObservationGroupDependencies,
    ) -> None: ...


class AdapterLifecycleOperation(Protocol):
    async def __call__(
        self,
        adapter: ProviderLifecycle,
        provider: Provider,
    ) -> None: ...


class UtcNow(Protocol):
    def __call__(self) -> datetime: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class WatchGroupRuntimeDependencies:
    session_factory: AsyncSessionFactory
    watch_group_provider: WatchGroupProviderResolver
    acquire_execution_lease: AcquireExecutionLease
    get_execution_provider: ExecutionProviderGetter
    observation_group_dependencies: ObservationGroupDependenciesFactory
    process_watch_group_observation: WatchGroupObservationProcessor
    drain_execution_adapter: AdapterLifecycleOperation
    close_execution_adapter: AdapterLifecycleOperation
    now: UtcNow = _utc_now


async def process_watch_group_runtime(
    watch_ids: list[str],
    started_at: datetime,
    *,
    dependencies: WatchGroupRuntimeDependencies,
    provider: Provider | None = None,
    adapter: ExecutionProvider | None = None,
) -> None:
    """Own one watch group's provider adapter and execution-lease lifecycle."""

    provider = provider or await dependencies.watch_group_provider(
        watch_ids,
        session_factory=dependencies.session_factory,
    )
    if provider is None:
        return

    owns_adapter = adapter is None
    lease_service: ExecutionLeaseService | None = None
    lease_grant: ExecutionLeaseGrant | None = None
    if provider in LEASED_OBSERVATION_PROVIDERS:
        lease_service, lease_grant = await dependencies.acquire_execution_lease(
            provider,
            started_at,
        )
        if lease_grant is None:
            return

    try:
        if adapter is None:
            adapter = dependencies.get_execution_provider(provider)
        await dependencies.process_watch_group_observation(
            watch_ids,
            started_at,
            provider=provider,
            adapter=adapter,
            lease_grant=lease_grant,
            dependencies=dependencies.observation_group_dependencies(lease_service, adapter),
        )
    finally:
        try:
            if adapter is not None:
                await dependencies.drain_execution_adapter(adapter, provider)
        finally:
            try:
                if owns_adapter and adapter is not None:
                    await dependencies.close_execution_adapter(adapter, provider)
            finally:
                if lease_grant is not None:
                    if lease_service is None:
                        raise RuntimeError("execution lease service is unavailable")
                    await lease_service.release(lease_grant, now=dependencies.now())
