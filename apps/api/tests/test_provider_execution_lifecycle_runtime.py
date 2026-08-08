from __future__ import annotations

import asyncio

import pytest

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import Provider
from rail_waitlist.provider_execution.lifecycle_runtime import (
    close_execution_adapter_safely,
    drain_execution_adapter_safely,
)


class RecordingAdapter:
    def __init__(
        self,
        *,
        drain_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.drain_error = drain_error
        self.close_error = close_error
        self.drain_calls = 0
        self.close_calls = 0

    async def drain_pending_calls(self) -> None:
        self.drain_calls += 1
        if self.drain_error is not None:
            raise self.drain_error

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


@pytest.mark.asyncio
async def test_lifecycle_cleanup_success_calls_each_adapter_operation_without_warning() -> None:
    adapter = RecordingAdapter()
    logger = RecordingLogger()

    await drain_execution_adapter_safely(adapter, Provider.SRT, logger=logger)
    await close_execution_adapter_safely(adapter, Provider.SRT, logger=logger)

    assert adapter.drain_calls == 1
    assert adapter.close_calls == 1
    assert logger.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "error_keyword", "expected_warning"),
    [
        (
            "drain",
            "sensitive drain response",
            "execution adapter drain failed provider=korail",
        ),
        (
            "close",
            "sensitive close response",
            "execution adapter cleanup failed provider=korail",
        ),
    ],
)
async def test_lifecycle_cleanup_swallows_provider_error_with_categorical_warning(
    operation: str,
    error_keyword: str,
    expected_warning: str,
) -> None:
    adapter = RecordingAdapter(
        drain_error=RuntimeError(error_keyword) if operation == "drain" else None,
        close_error=RuntimeError(error_keyword) if operation == "close" else None,
    )
    logger = RecordingLogger()

    if operation == "drain":
        await drain_execution_adapter_safely(adapter, Provider.KORAIL, logger=logger)
    else:
        await close_execution_adapter_safely(adapter, Provider.KORAIL, logger=logger)

    assert logger.messages == [expected_warning]
    assert error_keyword not in logger.messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["drain", "close"])
async def test_lifecycle_cleanup_does_not_swallow_cancellation(operation: str) -> None:
    adapter = RecordingAdapter(
        drain_error=asyncio.CancelledError() if operation == "drain" else None,
        close_error=asyncio.CancelledError() if operation == "close" else None,
    )
    logger = RecordingLogger()

    with pytest.raises(asyncio.CancelledError):
        if operation == "drain":
            await drain_execution_adapter_safely(adapter, Provider.SRT, logger=logger)
        else:
            await close_execution_adapter_safely(adapter, Provider.SRT, logger=logger)

    assert logger.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wrapper_name", "canonical_name"),
    [
        ("_drain_execution_adapter", "drain_execution_adapter_safely"),
        ("_close_execution_adapter", "close_execution_adapter_safely"),
    ],
)
async def test_worker_lifecycle_wrapper_injects_current_logger_and_canonical_owner(
    monkeypatch: pytest.MonkeyPatch,
    wrapper_name: str,
    canonical_name: str,
) -> None:
    adapter = RecordingAdapter()
    logger = RecordingLogger()
    captured: dict[str, object] = {}

    async def canonical(adapter_arg, provider_arg, *, logger):
        captured.update(adapter=adapter_arg, provider=provider_arg, logger=logger)

    monkeypatch.setattr(worker_module, canonical_name, canonical)
    monkeypatch.setattr(worker_module, "LOGGER", logger)

    await getattr(worker_module, wrapper_name)(adapter, Provider.SRT)

    assert captured == {
        "adapter": adapter,
        "provider": Provider.SRT,
        "logger": logger,
    }
