from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextvars import Context
from functools import wraps
from typing import ParamSpec, TypeVar

from .gate import MaintenanceGate

P = ParamSpec("P")
T = TypeVar("T")


def create_admitted_task(
    operation: Coroutine[None, None, T], *, context: Context | None = None
) -> asyncio.Task[T]:
    guard = MaintenanceGate.configured().operation()
    try:
        guard.__enter__()
    except BaseException:
        operation.close()
        raise
    try:
        task = asyncio.create_task(operation, context=context)
    except BaseException:
        operation.close()
        guard.__exit__(None, None, None)
        raise

    def release(_task: asyncio.Task[T]) -> None:
        # Also runs when a task is cancelled before its coroutine ever starts.
        guard.__exit__(None, None, None)

    task.add_done_callback(release)
    return task


def admitted_sync(operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        # Own the lock in the real worker thread: cancelling its asyncio waiter cannot release it.
        with MaintenanceGate.configured().operation():
            return operation(*args, **kwargs)

    return guarded
