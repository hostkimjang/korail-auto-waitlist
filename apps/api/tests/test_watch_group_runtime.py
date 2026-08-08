from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import Provider
from rail_waitlist.observations.group_runtime import (
    WatchGroupRuntimeDependencies,
    process_watch_group_runtime,
)
from rail_waitlist.provider_execution_lease import ExecutionLeaseGrant

STARTED_AT = datetime(2026, 8, 6, 3, tzinfo=UTC)
RELEASED_AT = STARTED_AT + timedelta(seconds=7)


@dataclass
class FakeAdapter:
    provider: Provider


@dataclass
class RuntimeHarness:
    resolved_provider: Provider | None = Provider.SRT
    grant_available: bool = True
    adapter_error: Exception | None = None
    process_error: Exception | None = None
    drain_error: Exception | None = None
    close_error: Exception | None = None
    events: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.adapter = FakeAdapter(self.resolved_provider or Provider.SRT)
        grant_provider = (
            self.adapter.provider
            if self.adapter.provider in {Provider.KORAIL, Provider.SRT}
            else Provider.SRT
        )
        self.session_factory = object()
        self.observation_dependencies = object()
        self.grant = ExecutionLeaseGrant(
            provider=grant_provider,
            account_scope="anonymous/public",
            owner_token="runtime-owner",
            fencing_token=3,
            expires_at=STARTED_AT + timedelta(minutes=2),
        )
        self.observed_process_arguments: dict[str, object] = {}
        self.observed_dependency_arguments: tuple[object, object] | None = None
        self.released_at: datetime | None = None

    async def resolve_provider(self, watch_ids, *, session_factory):
        self.events.append("resolve")
        assert watch_ids == ["watch-1"]
        assert session_factory is self.session_factory
        return self.resolved_provider

    async def acquire_lease(self, provider, now):
        self.events.append("acquire")
        assert provider is self.grant.provider
        assert now == STARTED_AT
        return self, self.grant if self.grant_available else None

    async def is_current(self, grant, *, now):
        return True

    async def release(self, grant, *, now):
        self.events.append("release")
        assert grant is self.grant
        self.released_at = now
        return True

    def get_adapter(self, provider):
        self.events.append("get")
        assert provider is self.adapter.provider
        if self.adapter_error is not None:
            raise self.adapter_error
        return self.adapter

    def build_observation_dependencies(self, lease_service=None, adapter=None):
        self.events.append("build")
        self.observed_dependency_arguments = (lease_service, adapter)
        return self.observation_dependencies

    async def process_observation(
        self,
        watch_ids,
        now,
        *,
        provider,
        adapter,
        lease_grant,
        dependencies,
    ):
        self.events.append("process")
        self.observed_process_arguments = {
            "watch_ids": watch_ids,
            "now": now,
            "provider": provider,
            "adapter": adapter,
            "lease_grant": lease_grant,
            "dependencies": dependencies,
        }
        if self.process_error is not None:
            raise self.process_error

    async def drain(self, adapter, provider):
        self.events.append("drain")
        assert adapter is self.adapter
        assert provider is self.adapter.provider
        if self.drain_error is not None:
            raise self.drain_error

    async def close(self, adapter, provider):
        self.events.append("close")
        assert adapter is self.adapter
        assert provider is self.adapter.provider
        if self.close_error is not None:
            raise self.close_error

    def now(self):
        self.events.append("clock")
        return RELEASED_AT

    def dependencies(self) -> WatchGroupRuntimeDependencies:
        return WatchGroupRuntimeDependencies(
            session_factory=self.session_factory,
            watch_group_provider=self.resolve_provider,
            acquire_execution_lease=self.acquire_lease,
            get_execution_provider=self.get_adapter,
            observation_group_dependencies=self.build_observation_dependencies,
            process_watch_group_observation=self.process_observation,
            drain_execution_adapter=self.drain,
            close_execution_adapter=self.close,
            now=self.now,
        )


async def test_unresolved_watch_group_stops_before_provider_runtime_work() -> None:
    harness = RuntimeHarness(resolved_provider=None)

    await process_watch_group_runtime(
        ["watch-1"],
        STARTED_AT,
        dependencies=harness.dependencies(),
    )

    assert harness.events == ["resolve"]


async def test_mock_group_skips_provider_resolution_and_execution_lease() -> None:
    harness = RuntimeHarness(resolved_provider=Provider.MOCK)
    harness.adapter = FakeAdapter(Provider.MOCK)

    await process_watch_group_runtime(
        ["watch-1"],
        STARTED_AT,
        provider=Provider.MOCK,
        dependencies=harness.dependencies(),
    )

    assert harness.events == ["get", "build", "process", "drain", "close"]
    assert harness.observed_dependency_arguments == (None, harness.adapter)
    assert harness.observed_process_arguments["lease_grant"] is None


@pytest.mark.parametrize("provider", [Provider.KORAIL, Provider.SRT])
async def test_external_group_without_lease_stops_before_adapter_creation(
    provider: Provider,
) -> None:
    harness = RuntimeHarness(resolved_provider=provider, grant_available=False)

    await process_watch_group_runtime(
        ["watch-1"],
        STARTED_AT,
        provider=provider,
        dependencies=harness.dependencies(),
    )

    assert harness.events == ["acquire"]


async def test_supplied_external_adapter_drains_then_releases_without_closing() -> None:
    harness = RuntimeHarness()

    await process_watch_group_runtime(
        ["watch-1"],
        STARTED_AT,
        provider=Provider.SRT,
        adapter=harness.adapter,
        dependencies=harness.dependencies(),
    )

    assert harness.events == ["acquire", "build", "process", "drain", "clock", "release"]
    assert harness.observed_dependency_arguments == (harness, harness.adapter)
    assert harness.observed_process_arguments == {
        "watch_ids": ["watch-1"],
        "now": STARTED_AT,
        "provider": Provider.SRT,
        "adapter": harness.adapter,
        "lease_grant": harness.grant,
        "dependencies": harness.observation_dependencies,
    }
    assert harness.released_at == RELEASED_AT


async def test_owned_external_adapter_drains_closes_then_releases() -> None:
    harness = RuntimeHarness()

    await process_watch_group_runtime(
        ["watch-1"],
        STARTED_AT,
        provider=Provider.SRT,
        dependencies=harness.dependencies(),
    )

    assert harness.events == [
        "acquire",
        "get",
        "build",
        "process",
        "drain",
        "close",
        "clock",
        "release",
    ]


async def test_adapter_factory_failure_still_releases_acquired_lease() -> None:
    failure = RuntimeError("adapter factory failed")
    harness = RuntimeHarness(adapter_error=failure)

    with pytest.raises(RuntimeError, match="adapter factory failed") as raised:
        await process_watch_group_runtime(
            ["watch-1"],
            STARTED_AT,
            provider=Provider.SRT,
            dependencies=harness.dependencies(),
        )

    assert raised.value is failure
    assert harness.events == ["acquire", "get", "clock", "release"]


async def test_observation_failure_cleans_up_owned_adapter_and_releases_lease() -> None:
    failure = RuntimeError("observation failed")
    harness = RuntimeHarness(process_error=failure)

    with pytest.raises(RuntimeError, match="observation failed") as raised:
        await process_watch_group_runtime(
            ["watch-1"],
            STARTED_AT,
            provider=Provider.SRT,
            dependencies=harness.dependencies(),
        )

    assert raised.value is failure
    assert harness.events == [
        "acquire",
        "get",
        "build",
        "process",
        "drain",
        "close",
        "clock",
        "release",
    ]


async def test_drain_failure_still_closes_owned_adapter_and_releases_lease() -> None:
    failure = RuntimeError("drain failed")
    harness = RuntimeHarness(drain_error=failure)

    with pytest.raises(RuntimeError, match="drain failed") as raised:
        await process_watch_group_runtime(
            ["watch-1"],
            STARTED_AT,
            provider=Provider.SRT,
            dependencies=harness.dependencies(),
        )

    assert raised.value is failure
    assert harness.events[-4:] == ["drain", "close", "clock", "release"]


async def test_close_failure_still_releases_acquired_lease() -> None:
    failure = RuntimeError("close failed")
    harness = RuntimeHarness(close_error=failure)

    with pytest.raises(RuntimeError, match="close failed") as raised:
        await process_watch_group_runtime(
            ["watch-1"],
            STARTED_AT,
            provider=Provider.SRT,
            dependencies=harness.dependencies(),
        )

    assert raised.value is failure
    assert harness.events[-3:] == ["close", "clock", "release"]


async def test_worker_wrapper_wires_current_runtime_dependencies(monkeypatch) -> None:
    captured: dict[str, object] = {}
    supplied_adapter = FakeAdapter(Provider.SRT)

    async def fake_process_watch_group_runtime(
        watch_ids,
        started_at,
        *,
        dependencies,
        provider=None,
        adapter=None,
    ):
        captured.update(
            watch_ids=watch_ids,
            started_at=started_at,
            dependencies=dependencies,
            provider=provider,
            adapter=adapter,
        )

    monkeypatch.setattr(
        worker_module,
        "process_watch_group_runtime_application",
        fake_process_watch_group_runtime,
    )

    await worker_module._process_watch_group(
        ["watch-1"],
        STARTED_AT,
        provider=Provider.SRT,
        adapter=supplied_adapter,
    )

    dependencies = captured["dependencies"]
    assert isinstance(dependencies, WatchGroupRuntimeDependencies)
    assert captured == {
        "watch_ids": ["watch-1"],
        "started_at": STARTED_AT,
        "dependencies": dependencies,
        "provider": Provider.SRT,
        "adapter": supplied_adapter,
    }
    assert dependencies.session_factory is worker_module.SessionFactory
    assert dependencies.watch_group_provider is worker_module.watch_group_provider
    assert dependencies.acquire_execution_lease is worker_module._acquire_execution_lease
    assert dependencies.get_execution_provider is worker_module.get_execution_provider
    assert (
        dependencies.observation_group_dependencies is worker_module._observation_group_dependencies
    )
    assert (
        dependencies.process_watch_group_observation
        is worker_module.process_watch_group_observation
    )
    assert dependencies.drain_execution_adapter is worker_module._drain_execution_adapter
    assert dependencies.close_execution_adapter is worker_module._close_execution_adapter
    assert dependencies.now().tzinfo is not None
