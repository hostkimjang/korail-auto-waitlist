from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAccount
from .schemas import UsernamePasswordCredentials

password_hasher = PasswordHasher(type=Type.ID)


async def count_accounts(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(AdminAccount)) or 0)


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(password_hasher.hash, password)


async def add_initial_account(
    session: AsyncSession, credentials: UsernamePasswordCredentials
) -> None:
    # The caller owns the transaction; singleton_slot also closes concurrent first registration.
    session.add(
        AdminAccount(
            singleton_slot=1,
            username=credentials.username,
            password_hash=await hash_password(credentials.password),
        )
    )
