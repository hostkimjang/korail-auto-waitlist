from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.observations.contracts import SeatObservationRequest
from rail_waitlist.observations.group_application import (
    AsyncSessionFactory,
    ObservationGroupDependencies,
)
from rail_waitlist.observations.group_runtime import (
    WatchGroupRuntimeDependencies,
    process_watch_group_runtime,
)
from rail_waitlist.provider_call_context import bind_request_id
from rail_waitlist.provider_execution.contracts import ExecutionLeaseGrant
from rail_waitlist.srt_execution import ManagedSrtSeatObserver
from rail_waitlist.srt_sidecar.client import (
    SRT_PROVIDER_ADAPTER_ORIGIN,
    SrtProviderAdapterClient,
)

TOKEN = "srt-sidecar-contract-token-value-32-bytes"


async def test_managed_observer_drains_source_before_closing_redis():
    drain_started = asyncio.Event()
    release_drain = asyncio.Event()
    redis_closed = asyncio.Event()

    class BlockingDrainSource:
        async def drain_pending_calls(self) -> None:
            drain_started.set()
            await release_drain.wait()

    class FakeRedis:
        async def aclose(self) -> None:
            redis_closed.set()

    observer = ManagedSrtSeatObserver(
        source=BlockingDrainSource(),
        redis=FakeRedis(),
    )

    close_task = asyncio.create_task(observer.aclose())
    await drain_started.wait()
    assert not redis_closed.is_set()
    assert not close_task.done()

    release_drain.set()
    await asyncio.wait_for(close_task, timeout=1)
    assert redis_closed.is_set()


async def test_managed_observer_closes_redis_when_source_drain_fails() -> None:
    calls: list[str] = []

    class FailingDrainSource:
        async def drain_pending_calls(self) -> None:
            calls.append("source")
            raise RuntimeError("source drain failed")

    class FakeRedis:
        async def aclose(self) -> None:
            calls.append("redis")

    observer = ManagedSrtSeatObserver(
        source=FailingDrainSource(),
        redis=FakeRedis(),
    )

    with pytest.raises(RuntimeError, match="source drain failed"):
        await observer.aclose()

    assert calls == ["source", "redis"]


async def test_watch_group_keeps_execution_lease_until_srt_sidecar_drain_finishes() -> None:
    provider_terminal = asyncio.Event()
    pending_status_polled = asyncio.Event()
    lease_released = asyncio.Event()
    instance_id = "768e0ce66bce4cc2af9ef152ea25d831"
    request_id = "5790e635307c4549a7728d01455bf92c"
    status_calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        if request.url.path == "/v1/read-only-call-register":
            registration = json.loads(request.content)
            assert registration["request_id"] == request_id
            return httpx.Response(
                200,
                json={"accepted": True, "instance_id": instance_id},
            )
        if request.url.path == "/v1/observe":
            return httpx.Response(200, json={"observations": []})
        if request.url.path == "/v1/read-only-call-status":
            status_calls += 1
            if status_calls == 1:
                raise httpx.ConnectError("temporary status failure", request=request)
            pending_status_polled.set()
            return httpx.Response(
                200,
                json={
                    "state": "terminal" if provider_terminal.is_set() else "pending",
                    "instance_id": instance_id,
                },
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    adapter = SrtProviderAdapterClient(
        SRT_PROVIDER_ADAPTER_ORIGIN,
        10,
        TOKEN,
        transport=httpx.MockTransport(respond),
    )
    grant = ExecutionLeaseGrant(
        provider=Provider.SRT,
        account_scope="anonymous/public",
        owner_token="owner-token",
        fencing_token=7,
        expires_at=datetime(2026, 8, 13, tzinfo=UTC) + timedelta(minutes=2),
    )

    class LeaseService:
        async def is_current(self, current_grant, *, now) -> bool:
            return current_grant is grant

        async def release(self, current_grant, *, now) -> bool:
            assert current_grant is grant
            lease_released.set()
            return True

    lease_service = LeaseService()

    async def watch_group_provider(_watch_ids, *, session_factory) -> Provider:
        return Provider.SRT

    async def acquire_execution_lease(_provider, _started_at):
        return lease_service, grant

    async def process_observation(
        _watch_ids,
        _started_at,
        *,
        provider,
        adapter,
        lease_grant,
        dependencies,
    ) -> None:
        request = SeatObservationRequest(
            provider=Provider.SRT,
            origin_node_id="0017",
            destination_node_id="0020",
            origin="수서",
            destination="부산",
            train_number="329",
            departure_at=datetime(2026, 8, 13, 12, 30, tzinfo=UTC),
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
        )
        with bind_request_id(request_id):
            await adapter.observe(request, origin="수서", destination="부산")

    async def drain(current_adapter, _provider) -> None:
        await current_adapter.drain_pending_calls()

    async def close(current_adapter, _provider) -> None:
        await current_adapter.aclose()

    def unused_session_factory() -> AsyncSession:
        raise AssertionError("session factory must not be called")

    session_factory = cast(AsyncSessionFactory, unused_session_factory)
    observation_dependencies = cast(ObservationGroupDependencies, object())

    dependencies = WatchGroupRuntimeDependencies(
        session_factory=session_factory,
        watch_group_provider=watch_group_provider,
        acquire_execution_lease=acquire_execution_lease,
        get_execution_provider=lambda _provider: adapter,
        observation_group_dependencies=lambda *_args: observation_dependencies,
        process_watch_group_observation=process_observation,
        drain_execution_adapter=drain,
        close_execution_adapter=close,
        now=lambda: datetime(2026, 8, 13, 0, 1, tzinfo=UTC),
    )

    runtime = asyncio.create_task(
        process_watch_group_runtime(
            ["watch-srt"],
            datetime(2026, 8, 13, tzinfo=UTC),
            provider=Provider.SRT,
            dependencies=dependencies,
        )
    )
    await asyncio.wait_for(pending_status_polled.wait(), timeout=1)

    runtime.cancel()
    await asyncio.sleep(0)
    runtime.cancel()
    await asyncio.sleep(0)

    assert not lease_released.is_set()
    assert not runtime.done()

    provider_terminal.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runtime, timeout=1)

    assert lease_released.is_set()
    assert status_calls >= 3
