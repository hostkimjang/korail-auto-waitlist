from __future__ import annotations

import asyncio

import pytest

from rail_waitlist.srt_execution import ManagedSrtSeatObserver


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
