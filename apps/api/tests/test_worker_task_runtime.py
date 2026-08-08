from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from rail_waitlist import worker as worker_module
from rail_waitlist import worker_task_runtime as canonical

API_ROOT = Path(__file__).resolve().parents[1]


async def test_task_runtime_returns_result_then_disposes_once() -> None:
    events: list[str] = []

    async def operation() -> int:
        events.append("operation")
        return 7

    async def dispose() -> None:
        events.append("dispose")

    assert await canonical.run_task_isolated(operation(), dispose_engine=dispose) == 7
    assert events == ["operation", "dispose"]


async def test_task_runtime_preserves_operation_error_identity() -> None:
    error = RuntimeError("operation failed")
    dispose_calls = 0

    async def operation() -> int:
        raise error

    async def dispose() -> None:
        nonlocal dispose_calls
        dispose_calls += 1

    with pytest.raises(RuntimeError) as caught:
        await canonical.run_task_isolated(operation(), dispose_engine=dispose)

    assert caught.value is error
    assert dispose_calls == 1


async def test_task_runtime_preserves_dispose_error_identity() -> None:
    error = RuntimeError("dispose failed")

    async def operation() -> int:
        return 11

    async def dispose() -> None:
        raise error

    with pytest.raises(RuntimeError) as caught:
        await canonical.run_task_isolated(operation(), dispose_engine=dispose)

    assert caught.value is error


async def test_task_runtime_dispose_error_masks_operation_error_with_context() -> None:
    operation_error = RuntimeError("operation failed")
    dispose_error = RuntimeError("dispose failed")

    async def operation() -> int:
        raise operation_error

    async def dispose() -> None:
        raise dispose_error

    with pytest.raises(RuntimeError) as caught:
        await canonical.run_task_isolated(operation(), dispose_engine=dispose)

    assert caught.value is dispose_error
    assert caught.value.__context__ is operation_error


async def test_task_runtime_preserves_cancellation_after_dispose() -> None:
    dispose_calls = 0
    started = asyncio.Event()

    async def operation() -> int:
        started.set()
        await asyncio.Future()
        raise AssertionError("the pending operation must be cancelled")

    async def dispose() -> None:
        nonlocal dispose_calls
        dispose_calls += 1

    task = asyncio.create_task(canonical.run_task_isolated(operation(), dispose_engine=dispose))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert dispose_calls == 1
    assert task.cancelled()


async def test_worker_wrapper_injects_current_engine_disposer_and_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def dispose() -> None:
        raise AssertionError("the injected canonical seam owns when cleanup runs")

    async def run(
        operation: Awaitable[int],
        *,
        dispose_engine: Callable[[], Awaitable[None]],
    ) -> int:
        captured["operation"] = operation
        captured["dispose_engine"] = dispose_engine
        return await operation

    async def operation() -> int:
        return 13

    operation_awaitable = operation()
    engine = SimpleNamespace(dispose=dispose)
    monkeypatch.setattr(worker_module, "engine", engine)
    monkeypatch.setattr(worker_module, "run_task_isolated", run)

    assert await worker_module._run_isolated(operation_awaitable) == 13
    assert captured == {
        "dispose_engine": dispose,
        "operation": operation_awaitable,
    }


@pytest.mark.parametrize("import_order", ["canonical-first", "worker-first"])
def test_worker_task_runtime_import_orders_keep_one_canonical_function(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist import worker_task_runtime as canonical
    from rail_waitlist import worker
else:
    from rail_waitlist import worker
    from rail_waitlist import worker_task_runtime as canonical

print(json.dumps({
    "binding": worker.run_task_isolated is canonical.run_task_isolated,
    "canonical_module": canonical.run_task_isolated.__module__,
    "wrapper_module": worker._run_isolated.__module__,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "binding": True,
        "canonical_module": "rail_waitlist.worker_task_runtime",
        "wrapper_module": "rail_waitlist.worker",
    }
