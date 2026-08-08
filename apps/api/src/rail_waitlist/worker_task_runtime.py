"""Task-local asynchronous cleanup used by synchronous worker shells."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


async def run_task_isolated(
    operation: Awaitable[int],
    *,
    dispose_engine: Callable[[], Awaitable[None]],
) -> int:
    """Finish engine cleanup before the caller closes its task event loop."""

    try:
        return await operation
    finally:
        await dispose_engine()
