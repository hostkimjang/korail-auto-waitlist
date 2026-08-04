from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import OutboxStatus
from .models import OutboxEvent

OUTBOX_DEDUPE_KEY_MAX_LENGTH = 128


def normalize_outbox_dedupe_key(value: str) -> str:
    """Keep human-readable context while bounding the persistent idempotency key."""
    if len(value) <= OUTBOX_DEDUPE_KEY_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    prefix_length = OUTBOX_DEDUPE_KEY_MAX_LENGTH - len(digest) - 1
    return f"{value[:prefix_length]}:{digest}"


async def add_outbox_event(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
    dedupe_key: str,
) -> OutboxEvent:
    persistent_dedupe_key = normalize_outbox_dedupe_key(dedupe_key)
    existing = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.dedupe_key == persistent_dedupe_key)
    )
    if existing:
        return existing
    event = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        dedupe_key=persistent_dedupe_key,
        status=OutboxStatus.PENDING,
    )
    session.add(event)
    await session.flush()
    return event
