from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from ..domain import Provider
from .contracts import ExecutionLeaseGrant
from .models import ProviderExecutionLease

ANONYMOUS_PUBLIC_ACCOUNT_SCOPE = "anonymous/public"
PROVIDER_EXECUTION_LEASE_DURATION = timedelta(minutes=2)
_EXTERNAL_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})


def _new_owner_token() -> str:
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class ExecutionLeaseAcquisitionDependencies:
    session_factory: async_sessionmaker[AsyncSession]
    owner_token_factory: Callable[[], str] = _new_owner_token


def _aware_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    # SQLite intentionally drops timezone metadata even for timezone=True columns.
    # All writes are normalized to UTC, so restoring UTC here preserves portable tests
    # without weakening the timezone-aware public method contract.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounded_nonempty(value: str, *, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters")
    return normalized


def _validate_provider(provider: Provider) -> None:
    if provider not in _EXTERNAL_PROVIDERS:
        raise ValueError("execution leases are only available for external providers")


def _validate_window(now: datetime, expires_at: datetime) -> tuple[datetime, datetime]:
    normalized_now = _aware_utc(now, label="now")
    normalized_expiry = _aware_utc(expires_at, label="expires_at")
    if normalized_expiry <= normalized_now:
        raise ValueError("expires_at must be later than now")
    return normalized_now, normalized_expiry


def _grant_from_row(
    row: Row[tuple[Provider, str, str | None, int, datetime | None]],
) -> ExecutionLeaseGrant:
    provider, account_scope, owner_token, fencing_token, expires_at = row._t
    if owner_token is None or expires_at is None:
        raise RuntimeError("database returned an unowned execution lease")
    return ExecutionLeaseGrant(
        provider=provider,
        account_scope=account_scope,
        owner_token=owner_token,
        fencing_token=fencing_token,
        expires_at=_stored_utc(expires_at),
    )


class ProviderExecutionLeaseService:
    """Atomically serialize one external provider/account execution epoch.

    Every successful takeover increments the fencing token. Callers must verify that
    token inside the transaction that persists provider-derived state, so an expired
    owner cannot commit stale observations or reservations after a newer owner starts.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def acquire(
        self,
        provider: Provider,
        account_scope: str,
        owner_token: str,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ExecutionLeaseGrant | None:
        _validate_provider(provider)
        scope = _bounded_nonempty(account_scope, label="account_scope", maximum=128)
        owner = _bounded_nonempty(owner_token, label="owner_token", maximum=128)
        normalized_now, normalized_expiry = _validate_window(now, expires_at)

        async with self._session_factory.begin() as session:
            bind = session.get_bind()
            if bind.dialect.name == "postgresql":
                row = (
                    await session.execute(
                        postgresql_insert(ProviderExecutionLease)
                        .values(
                            provider=provider,
                            account_scope=scope,
                            owner_token=owner,
                            fencing_token=1,
                            expires_at=normalized_expiry,
                            updated_at=normalized_now,
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                ProviderExecutionLease.provider,
                                ProviderExecutionLease.account_scope,
                            ],
                            set_={
                                "owner_token": owner,
                                "fencing_token": ProviderExecutionLease.fencing_token + 1,
                                "expires_at": normalized_expiry,
                                "updated_at": normalized_now,
                            },
                            where=or_(
                                ProviderExecutionLease.owner_token.is_(None),
                                ProviderExecutionLease.expires_at <= normalized_now,
                            ),
                        )
                        .returning(
                            ProviderExecutionLease.provider,
                            ProviderExecutionLease.account_scope,
                            ProviderExecutionLease.owner_token,
                            ProviderExecutionLease.fencing_token,
                            ProviderExecutionLease.expires_at,
                        )
                    )
                ).first()
            elif bind.dialect.name == "sqlite":
                row = (
                    await session.execute(
                        sqlite_insert(ProviderExecutionLease)
                        .values(
                            provider=provider,
                            account_scope=scope,
                            owner_token=owner,
                            fencing_token=1,
                            expires_at=normalized_expiry,
                            updated_at=normalized_now,
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                ProviderExecutionLease.provider,
                                ProviderExecutionLease.account_scope,
                            ],
                            set_={
                                "owner_token": owner,
                                "fencing_token": ProviderExecutionLease.fencing_token + 1,
                                "expires_at": normalized_expiry,
                                "updated_at": normalized_now,
                            },
                            where=or_(
                                ProviderExecutionLease.owner_token.is_(None),
                                ProviderExecutionLease.expires_at <= normalized_now,
                            ),
                        )
                        .returning(
                            ProviderExecutionLease.provider,
                            ProviderExecutionLease.account_scope,
                            ProviderExecutionLease.owner_token,
                            ProviderExecutionLease.fencing_token,
                            ProviderExecutionLease.expires_at,
                        )
                    )
                ).first()
            else:
                raise RuntimeError(f"unsupported execution lease dialect: {bind.dialect.name}")
        return None if row is None else _grant_from_row(row)

    async def renew(
        self,
        grant: ExecutionLeaseGrant,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ExecutionLeaseGrant | None:
        normalized_now, normalized_expiry = _validate_window(now, expires_at)
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    update(ProviderExecutionLease)
                    .where(*self._current_owner_predicates(grant, normalized_now))
                    .values(expires_at=normalized_expiry, updated_at=normalized_now)
                    .returning(
                        ProviderExecutionLease.provider,
                        ProviderExecutionLease.account_scope,
                        ProviderExecutionLease.owner_token,
                        ProviderExecutionLease.fencing_token,
                        ProviderExecutionLease.expires_at,
                    )
                )
            ).first()
        return None if row is None else _grant_from_row(row)

    async def release(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool:
        normalized_now = _aware_utc(now, label="now")
        async with self._session_factory.begin() as session:
            released_fencing_token = await session.scalar(
                update(ProviderExecutionLease)
                .where(*self._current_owner_predicates(grant, normalized_now))
                .values(owner_token=None, expires_at=None, updated_at=normalized_now)
                .returning(ProviderExecutionLease.fencing_token)
            )
        return released_fencing_token is not None

    async def is_current(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool:
        normalized_now = _aware_utc(now, label="now")
        async with self._session_factory() as session:
            current = await session.scalar(
                select(ProviderExecutionLease.fencing_token).where(
                    *self._current_owner_predicates(grant, normalized_now)
                )
            )
        return current == grant.fencing_token

    @staticmethod
    def _current_owner_predicates(
        grant: ExecutionLeaseGrant,
        now: datetime,
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            ProviderExecutionLease.provider == grant.provider,
            ProviderExecutionLease.account_scope == grant.account_scope,
            ProviderExecutionLease.owner_token == grant.owner_token,
            ProviderExecutionLease.fencing_token == grant.fencing_token,
            ProviderExecutionLease.expires_at.is_not(None),
            ProviderExecutionLease.expires_at > now,
        )


async def lock_execution_lease_current(
    session: AsyncSession,
    grant: ExecutionLeaseGrant,
    *,
    now: datetime,
) -> bool:
    """Lock and verify the matching execution epoch in the caller's transaction.

    The row lock keeps ownership stable until the caller commits or rolls back its
    provider-derived write, closing the gap between a separate lease check and commit.
    """

    normalized_now = _aware_utc(now, label="now")
    current = await session.scalar(
        select(ProviderExecutionLease.fencing_token)
        .where(*ProviderExecutionLeaseService._current_owner_predicates(grant, normalized_now))
        .with_for_update()
    )
    return current == grant.fencing_token


async def acquire_anonymous_public_execution_lease(
    provider: Provider,
    now: datetime,
    *,
    dependencies: ExecutionLeaseAcquisitionDependencies,
) -> tuple[ProviderExecutionLeaseService, ExecutionLeaseGrant | None]:
    """Acquire the canonical two-minute public execution epoch for one provider.

    A fresh owner token is generated for every attempt, including a busy result. This
    keeps retries independent and preserves the worker's anonymous/public lease policy.
    """

    service = ProviderExecutionLeaseService(dependencies.session_factory)
    grant = await service.acquire(
        provider,
        ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        dependencies.owner_token_factory(),
        now=now,
        expires_at=now + PROVIDER_EXECUTION_LEASE_DURATION,
    )
    return service, grant
