from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import Provider
from rail_waitlist.models import ProviderExecutionLease
from rail_waitlist.provider_execution_lease import (
    ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
    ExecutionLeaseGrant,
    ProviderExecutionLeaseService,
    lock_execution_lease_current,
)


async def test_two_replicas_allow_only_one_active_owner(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    first_replica = ProviderExecutionLeaseService(factory)
    second_replica = ProviderExecutionLeaseService(factory)
    now = datetime(2026, 7, 30, 1, tzinfo=UTC)

    first, second = await asyncio.gather(
        first_replica.acquire(
            Provider.SRT,
            ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
            "replica-a",
            now=now,
            expires_at=now + timedelta(seconds=30),
        ),
        second_replica.acquire(
            Provider.SRT,
            ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
            "replica-b",
            now=now,
            expires_at=now + timedelta(seconds=30),
        ),
    )

    grants = [grant for grant in (first, second) if grant is not None]
    assert len(grants) == 1
    assert grants[0].fencing_token == 1


async def test_expiry_takeover_increments_fence_and_rejects_stale_owner(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    old_replica = ProviderExecutionLeaseService(factory)
    new_replica = ProviderExecutionLeaseService(factory)
    started = datetime(2026, 7, 30, 2, tzinfo=UTC)
    old = await old_replica.acquire(
        Provider.SRT,
        ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        "old-replica",
        now=started,
        expires_at=started + timedelta(seconds=10),
    )
    assert old is not None

    takeover_at = started + timedelta(seconds=11)
    assert (
        await old_replica.renew(
            old,
            now=takeover_at,
            expires_at=takeover_at + timedelta(minutes=1),
        )
        is None
    )
    assert not await old_replica.release(old, now=takeover_at)
    new = await new_replica.acquire(
        Provider.SRT,
        ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        "new-replica",
        now=takeover_at,
        expires_at=takeover_at + timedelta(seconds=30),
    )

    assert new is not None
    assert new.fencing_token == old.fencing_token + 1
    assert not await old_replica.is_current(old, now=takeover_at)
    assert (
        await old_replica.renew(
            old,
            now=takeover_at,
            expires_at=takeover_at + timedelta(minutes=1),
        )
        is None
    )
    assert not await old_replica.release(old, now=takeover_at)
    assert await new_replica.is_current(new, now=takeover_at)


async def test_release_retains_monotonic_fence_and_stale_grant_cannot_mutate(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service = ProviderExecutionLeaseService(factory)
    started = datetime(2026, 7, 30, 3, tzinfo=UTC)
    first = await service.acquire(
        Provider.KORAIL,
        "account:primary",
        "replica-a",
        now=started,
        expires_at=started + timedelta(minutes=1),
    )
    assert first is not None
    assert await service.release(first, now=started + timedelta(seconds=1))
    assert not await service.release(first, now=started + timedelta(seconds=2))

    second = await service.acquire(
        Provider.KORAIL,
        "account:primary",
        "replica-b",
        now=started + timedelta(seconds=2),
        expires_at=started + timedelta(minutes=2),
    )
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1
    assert (
        await service.renew(
            first,
            now=started + timedelta(seconds=3),
            expires_at=started + timedelta(minutes=3),
        )
        is None
    )

    async with factory() as session:
        stored = await session.scalar(
            select(ProviderExecutionLease).where(
                ProviderExecutionLease.provider == Provider.KORAIL,
                ProviderExecutionLease.account_scope == "account:primary",
            )
        )
    assert stored is not None
    assert stored.owner_token == "replica-b"
    assert stored.fencing_token == second.fencing_token


async def test_renew_requires_live_owner_and_preserves_fencing_token(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service = ProviderExecutionLeaseService(factory)
    now = datetime(2026, 7, 30, 4, tzinfo=UTC)
    grant = await service.acquire(
        Provider.SRT,
        ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        "replica-a",
        now=now,
        expires_at=now + timedelta(seconds=10),
    )
    assert grant is not None

    renewed = await service.renew(
        grant,
        now=now + timedelta(seconds=5),
        expires_at=now + timedelta(seconds=20),
    )
    assert renewed is not None
    assert renewed.fencing_token == grant.fencing_token
    assert renewed.expires_at == now + timedelta(seconds=20)
    assert (
        await service.renew(
            renewed,
            now=now + timedelta(seconds=21),
            expires_at=now + timedelta(seconds=30),
        )
        is None
    )


async def test_transaction_fence_accepts_only_the_live_matching_epoch(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service = ProviderExecutionLeaseService(factory)
    now = datetime(2026, 7, 30, 5, tzinfo=UTC)
    grant = await service.acquire(
        Provider.SRT,
        ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        "replica-a",
        now=now,
        expires_at=now + timedelta(seconds=30),
    )
    assert grant is not None

    async with factory() as session:
        assert await lock_execution_lease_current(
            session,
            grant,
            now=now + timedelta(seconds=1),
        )
        await session.commit()

    stale_grant = ExecutionLeaseGrant(
        provider=grant.provider,
        account_scope=grant.account_scope,
        owner_token=grant.owner_token,
        fencing_token=grant.fencing_token + 1,
        expires_at=grant.expires_at,
    )
    async with factory() as session:
        assert not await lock_execution_lease_current(
            session,
            stale_grant,
            now=now + timedelta(seconds=1),
        )
        assert not await lock_execution_lease_current(
            session,
            grant,
            now=now + timedelta(seconds=31),
        )


async def test_transaction_fence_statement_uses_postgresql_row_lock_and_full_epoch_identity():
    now = datetime(2026, 7, 30, 6, tzinfo=UTC)
    grant = ExecutionLeaseGrant(
        provider=Provider.KORAIL,
        account_scope="account:primary",
        owner_token="replica-a",
        fencing_token=11,
        expires_at=now + timedelta(minutes=1),
    )

    class CapturingSession:
        statement = None

        async def scalar(self, statement):
            self.statement = statement
            return grant.fencing_token

    session = CapturingSession()
    assert await lock_execution_lease_current(session, grant, now=now)
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "FOR UPDATE" in sql
    assert "provider_execution_leases.provider" in sql
    assert "provider_execution_leases.account_scope" in sql
    assert "provider_execution_leases.owner_token" in sql
    assert "provider_execution_leases.fencing_token" in sql
    assert "provider_execution_leases.expires_at" in sql
    assert grant.account_scope in compiled.params.values()
    assert grant.owner_token in compiled.params.values()
    assert grant.fencing_token in compiled.params.values()


async def test_transaction_fence_rejects_naive_timestamp_without_querying():
    now = datetime(2026, 7, 30, 6)
    grant = ExecutionLeaseGrant(
        provider=Provider.SRT,
        account_scope=ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        owner_token="replica-a",
        fencing_token=1,
        expires_at=now.replace(tzinfo=UTC) + timedelta(minutes=1),
    )

    class FailIfQueriedSession:
        async def scalar(self, _statement):
            raise AssertionError("naive time must fail before querying")

    with pytest.raises(ValueError, match="timezone-aware"):
        await lock_execution_lease_current(FailIfQueriedSession(), grant, now=now)


@pytest.mark.parametrize(
    ("now", "expires_at"),
    [
        (
            datetime(2026, 7, 30, 1, tzinfo=UTC).replace(tzinfo=None),
            datetime(2026, 7, 30, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        ),
        (
            datetime(2026, 7, 30, 1, tzinfo=UTC),
            datetime(2026, 7, 30, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        ),
    ],
)
async def test_acquire_rejects_naive_timestamps(db_engine, now, expires_at):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    service = ProviderExecutionLeaseService(factory)
    with pytest.raises(ValueError, match="timezone-aware"):
        await service.acquire(
            Provider.SRT,
            ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
            "replica",
            now=now,
            expires_at=expires_at,
        )
