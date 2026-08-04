from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..models import KorailBrowserSnapshotBatch
from ..official_page_confirmations import upsert_official_page_confirmations
from ..schemas import (
    KorailBrowserSnapshotRevision,
    OfficialPageSeatConfirmationCreate,
    OfficialPageSeatConfirmationRead,
)

snapshot_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
confirmation_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])
Session = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)]


@snapshot_router.get(
    "/korail-browser-snapshot-revision",
    response_model=KorailBrowserSnapshotRevision,
)
async def korail_browser_snapshot_revision(
    response: Response, session: Session
) -> KorailBrowserSnapshotRevision:
    response.headers["Cache-Control"] = "no-store"
    revision = await session.scalar(
        select(func.max(KorailBrowserSnapshotBatch.observed_at)).where(
            KorailBrowserSnapshotBatch.fresh_until > datetime.now(timezone.utc)
        )
    )
    if revision is not None:
        revision = (
            revision.replace(tzinfo=timezone.utc)
            if revision.tzinfo is None or revision.utcoffset() is None
            else revision.astimezone(timezone.utc)
        )
    return KorailBrowserSnapshotRevision(revision=revision)


@confirmation_router.post(
    "/seat-observations/official-page-confirmations",
    response_model=OfficialPageSeatConfirmationRead,
    status_code=201,
)
async def official_page_confirmation_create(
    data: OfficialPageSeatConfirmationCreate,
    response: Response,
    session: Session,
    idempotency_key: IdempotencyKey = None,
) -> OfficialPageSeatConfirmationRead:
    response.headers["Cache-Control"] = "no-store"
    try:
        confirmations, created_count, replayed = await upsert_official_page_confirmations(
            session,
            data,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    await session.commit()
    for confirmation in confirmations:
        await session.refresh(confirmation)
    first = confirmations[0]
    return OfficialPageSeatConfirmationRead(
        provider=first.provider,
        origin_node_id=first.origin_node_id,
        destination_node_id=first.destination_node_id,
        train_number=first.train_number,
        departure_at=first.departure_at,
        passenger_count=first.passenger_count,
        seat_classes=[
            {
                "id": confirmation.id,
                "seat_class": confirmation.seat_class,
                "status": confirmation.status.value,
            }
            for confirmation in confirmations
        ],
        source=first.source,
        observed_at=first.observed_at,
        fresh_until=first.fresh_until,
        created_count=created_count,
        replayed=replayed,
    )
