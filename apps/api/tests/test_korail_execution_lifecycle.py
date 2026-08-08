from __future__ import annotations

import asyncio

import pytest

from rail_waitlist.korail_execution import ManagedKorailSeatObserver


async def test_managed_korail_observer_closes_source_before_redis() -> None:
    source_close_started = asyncio.Event()
    release_source_close = asyncio.Event()
    redis_closed = asyncio.Event()

    class BlockingSource:
        async def close(self) -> None:
            source_close_started.set()
            await release_source_close.wait()

    class FakeRedis:
        async def aclose(self) -> None:
            redis_closed.set()

    observer = ManagedKorailSeatObserver(
        source=BlockingSource(),
        redis=FakeRedis(),
    )

    close_task = asyncio.create_task(observer.aclose())
    await source_close_started.wait()
    assert not redis_closed.is_set()
    assert not close_task.done()

    release_source_close.set()
    await asyncio.wait_for(close_task, timeout=1)
    assert redis_closed.is_set()


async def test_managed_korail_observer_closes_redis_when_source_close_fails() -> None:
    calls: list[str] = []

    class FailingSource:
        async def close(self) -> None:
            calls.append("source")
            raise RuntimeError("source close failed")

    class FakeRedis:
        async def aclose(self) -> None:
            calls.append("redis")

    observer = ManagedKorailSeatObserver(
        source=FailingSource(),
        redis=FakeRedis(),
    )

    with pytest.raises(RuntimeError, match="source close failed"):
        await observer.aclose()

    assert calls == ["source", "redis"]
