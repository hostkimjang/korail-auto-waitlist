from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .domain import Provider
from .models import ProviderExecutionLease

ANONYMOUS_PUBLIC_ACCOUNT_SCOPE = "anonymous/public"
_EXTERNAL_PROVIDERS = frozenset({Provider.KORAIL, Provider.SRT})


@dataclass(frozen=True, slots=True)
class ExecutionLeaseGrant:
    provider: Provider
    account_scope: str
    owner_token: str = field(repr=False)
    fencing_token: int
    expires_at: datetime


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


def _grant_from_row(row: object) -> ExecutionLeaseGrant:
    provider, account_scope, owner_token, fencing_token, expires_at = row
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
    """Atomically serializes one external provider/account execution epoch.

    Fencing tokens increase on every successful acquisition, including acquisition
    after an explicit release. Renew and release always match both owner and fencing
    token so a delayed process cannot mutate a newer owner's lease.
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
                insert = postgresql_insert(ProviderExecutionLease)
            elif bind.dialect.name == "sqlite":
                insert = sqlite_insert(ProviderExecutionLease)
            else:
                raise RuntimeError(f"unsupported execution lease dialect: {bind.dialect.name}")

            statement = (
                insert.values(
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
            row = (await session.execute(statement)).first()
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
            statement = (
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
            row = (await session.execute(statement)).first()
        return None if row is None else _grant_from_row(row)

    async def release(self, grant: ExecutionLeaseGrant, *, now: datetime) -> bool:
        normalized_now = _aware_utc(now, label="now")
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(ProviderExecutionLease)
                .where(*self._current_owner_predicates(grant, normalized_now))
                .values(owner_token=None, expires_at=None, updated_at=normalized_now)
            )
        return result.rowcount == 1

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
        grant: ExecutionLeaseGrant, now: datetime
    ) -> tuple[object, ...]:
        return (
            ProviderExecutionLease.provider == grant.provider,
            ProviderExecutionLease.account_scope == grant.account_scope,
            ProviderExecutionLease.owner_token == grant.owner_token,
            ProviderExecutionLease.fencing_token == grant.fencing_token,
            ProviderExecutionLease.expires_at.is_not(None),
            ProviderExecutionLease.expires_at > now,
        )
