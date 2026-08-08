from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..domain import Provider


@dataclass(frozen=True, slots=True)
class ExecutionLeaseGrant:
    provider: Provider
    account_scope: str
    owner_token: str = field(repr=False)
    fencing_token: int
    expires_at: datetime


class ExecutionLeaseService(Protocol):
    async def is_current(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool: ...

    async def release(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool: ...


class AcquireExecutionLease(Protocol):
    async def __call__(
        self,
        provider: Provider,
        now: datetime,
    ) -> tuple[ExecutionLeaseService, ExecutionLeaseGrant | None]: ...
