"""Task-local asynchronous cleanup used by synchronous worker shells."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import iscoroutine

from .maintenance.gate import MaintenanceClosed, MaintenanceGate


async def run_task_isolated(
    operation: Awaitable[int],
    *,
    dispose_engine: Callable[[], Awaitable[None]],
) -> int:
    """Finish engine cleanup before the caller closes its task event loop."""

    started = False
    try:
        with MaintenanceGate.configured().operation():
            started = True
            try:
                return await operation
            finally:
                await dispose_engine()
    except MaintenanceClosed:
        if started:
            raise
        if iscoroutine(operation):
            operation.close()
        await dispose_engine()
        return 0
