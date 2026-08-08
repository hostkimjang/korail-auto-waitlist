from __future__ import annotations

from typing import Protocol

from ..domain import Provider
from ..provider_contracts import ProviderLifecycle


class CategoricalWarningLogger(Protocol):
    def warning(self, message: str, *args: object) -> None: ...


async def close_execution_adapter_safely(
    adapter: ProviderLifecycle,
    provider: Provider,
    *,
    logger: CategoricalWarningLogger,
) -> None:
    try:
        await adapter.aclose()
    except Exception:
        # Provider exceptions can contain upstream response details. Cleanup must not
        # leak those details or prevent a separately owned lease from being released.
        logger.warning("execution adapter cleanup failed provider=%s", provider.value)


async def drain_execution_adapter_safely(
    adapter: ProviderLifecycle,
    provider: Provider,
    *,
    logger: CategoricalWarningLogger,
) -> None:
    try:
        await adapter.drain_pending_calls()
    except Exception:
        # Draining is best-effort during cleanup for the same fail-closed reason as
        # close. The caller remains responsible for lease release and adapter lifetime.
        logger.warning("execution adapter drain failed provider=%s", provider.value)
