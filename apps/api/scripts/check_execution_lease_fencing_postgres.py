from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rail_waitlist.config import get_settings
from rail_waitlist.domain import Provider
from rail_waitlist.models import ProviderExecutionLease
from rail_waitlist.provider_execution_lease import (
    ProviderExecutionLeaseService,
    lock_execution_lease_current,
)


async def verify() -> None:
    """Prove PostgreSQL blocks a newer lease epoch until the guarded commit."""

    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("database URL is not configured")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        await engine.dispose()
        raise RuntimeError("execution lease contention check requires PostgreSQL")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ProviderExecutionLeaseService(factory)
    scope = f"execution-lease-verification:{uuid4()}"
    started_at = datetime.now(UTC)
    first = None
    second = None
    try:
        first = await service.acquire(
            Provider.SRT,
            scope,
            "fencing-check-first-owner",
            now=started_at,
            expires_at=started_at + timedelta(seconds=5),
        )
        if first is None:
            raise AssertionError("failed to acquire the first verification lease")

        async with factory() as guarded_session:
            if not await lock_execution_lease_current(
                guarded_session,
                first,
                now=started_at + timedelta(seconds=1),
            ):
                raise AssertionError("current lease was rejected before the guarded commit")

            takeover = asyncio.create_task(
                service.acquire(
                    Provider.SRT,
                    scope,
                    "fencing-check-second-owner",
                    now=started_at + timedelta(seconds=6),
                    expires_at=started_at + timedelta(seconds=30),
                )
            )
            try:
                await asyncio.wait_for(asyncio.shield(takeover), timeout=0.25)
            except TimeoutError:
                pass
            else:
                raise AssertionError("new lease epoch was not blocked by the guarded row lock")
            await guarded_session.commit()
            second = await asyncio.wait_for(takeover, timeout=5)

        if second is None or second.fencing_token != first.fencing_token + 1:
            raise AssertionError("takeover did not advance the fencing token")
        async with factory() as session:
            if await lock_execution_lease_current(
                session, first, now=started_at + timedelta(seconds=7)
            ):
                raise AssertionError("stale lease epoch remained current after takeover")
            if not await lock_execution_lease_current(
                session,
                second,
                now=started_at + timedelta(seconds=7),
            ):
                raise AssertionError("new lease epoch was not current after takeover")
            await session.rollback()
    finally:
        async with factory.begin() as session:
            await session.execute(
                delete(ProviderExecutionLease).where(
                    ProviderExecutionLease.provider == Provider.SRT,
                    ProviderExecutionLease.account_scope == scope,
                )
            )
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
    print("PostgreSQL 실행 임대 fencing 경합 검증 통과")
