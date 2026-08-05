from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IdempotencyRecord


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was reused for a different request payload."""


class _JsonDumpable(Protocol):
    def model_dump(self, *, mode: Literal["json"]) -> object: ...


def request_hash(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = cast(_JsonDumpable, value).model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def get_idempotent_resource(
    session: AsyncSession, scope: str, key: str | None, payload_hash: str
) -> str | None:
    if not key:
        return None
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
        )
    )
    if record is None:
        return None
    if record.request_hash != payload_hash:
        raise IdempotencyConflict("Idempotency-Key was already used with a different request")
    return record.resource_id


async def remember_idempotency(
    session: AsyncSession, scope: str, key: str | None, resource_id: str, payload_hash: str
) -> None:
    if key:
        session.add(
            IdempotencyRecord(
                scope=scope, key=key, resource_id=resource_id, request_hash=payload_hash
            )
        )
