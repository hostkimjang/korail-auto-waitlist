from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..domain import Provider
from .schemas import RailProviderAccountRead, RailProviderAuthStatus


class PersistProviderAuthStatus(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        provider: Provider,
        status: RailProviderAuthStatus,
        *,
        expected_credential_version: int | None = None,
        commit: bool = True,
    ) -> RailProviderAccountRead | None: ...


async def update_provider_auth_status_in_reservation_transaction(
    session: AsyncSession,
    provider: Provider,
    status: RailProviderAuthStatus,
    *,
    expected_credential_version: int,
    persist_auth_status: PersistProviderAuthStatus,
) -> None:
    # Reservation state and provider authentication metadata must commit or roll back
    # together. The persistence dependency owns locking/flush details; this adapter
    # fixes the transaction boundary without depending on the legacy service module.
    await persist_auth_status(
        session,
        provider,
        status,
        expected_credential_version=expected_credential_version,
        commit=False,
    )
