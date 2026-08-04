from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response

from ..auth import require_admin
from ..schemas import SeatStatusSourceStatus
from ..seat_status_cooldown import ProviderCooldown

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


@router.get("/seat-status/status", response_model=list[SeatStatusSourceStatus])
async def seat_status_sources(
    request: Request,
    response: Response,
) -> list[SeatStatusSourceStatus]:
    """Expose only the active seat-source cooldowns, not worker provider circuits."""
    response.headers["Cache-Control"] = "no-store"
    cooldown_store = request.app.state.seat_status_cooldown_store
    korail_cooldown, srt_cooldown = await asyncio.gather(
        cooldown_store.get("korail-browser"),
        cooldown_store.get("srt"),
    )
    return [
        _seat_status_source_status("korail", "korail_browser", korail_cooldown),
        _seat_status_source_status("srt", "srt_live", srt_cooldown),
    ]


def _seat_status_source_status(
    provider: Literal["korail", "srt"],
    source: Literal["korail_browser", "srt_live"],
    cooldown: ProviderCooldown | None,
) -> SeatStatusSourceStatus:
    if cooldown is None:
        return SeatStatusSourceStatus(
            provider=provider,
            source=source,
            state="ready",
        )
    return SeatStatusSourceStatus(
        provider=provider,
        source=source,
        state="cooldown",
        cause=cooldown.reason,
        retry_after_seconds=cooldown.retry_after_seconds,
    )
