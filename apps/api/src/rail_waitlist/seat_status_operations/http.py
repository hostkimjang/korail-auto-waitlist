from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response

from ..auth import require_admin
from ..korail_sidecar.provider_cooldown import ProviderCooldown as AdmissionCooldown
from ..korail_sidecar.provider_cooldown import ProviderCooldownDeferred
from ..seat_status_cooldown import (
    KORAIL_BROWSER_COOLDOWN_KEY,
    KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY,
    SRT_COOLDOWN_KEY,
    ProviderCooldown,
)
from .schemas import SeatStatusSourceStatus

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


@router.get("/seat-status/status", response_model=list[SeatStatusSourceStatus])
async def seat_status_sources(
    request: Request,
    response: Response,
) -> list[SeatStatusSourceStatus]:
    """Expose every hold that prevents seat I/O, including authentication protection."""
    response.headers["Cache-Control"] = "no-store"
    cooldown_store = request.app.state.seat_status_cooldown_store
    korail_outage, korail_cooldown, srt_cooldown, korail_admission = await asyncio.gather(
        cooldown_store.get(KORAIL_BROWSER_OUTAGE_COOLDOWN_KEY),
        cooldown_store.get(KORAIL_BROWSER_COOLDOWN_KEY),
        cooldown_store.get(SRT_COOLDOWN_KEY),
        _korail_admission_cooldown(request),
    )
    korail_active = max(
        (item for item in (korail_outage, korail_cooldown, korail_admission) if item is not None),
        key=lambda item: (
            item.retry_after_seconds,
            item.reason == "provider_access_restricted",
        ),
        default=None,
    )
    return [
        _seat_status_source_status(
            "korail",
            "korail_browser",
            korail_active,
        ),
        _seat_status_source_status("srt", "srt_live", srt_cooldown),
    ]


async def _korail_admission_cooldown(request: Request) -> ProviderCooldown | None:
    if not request.app.state.korail_browser_seat_source.enabled:
        return None
    cooldown: AdmissionCooldown = request.app.state.korail_provider_cooldown
    try:
        await cooldown.check()
    except ProviderCooldownDeferred as error:
        return ProviderCooldown(
            reason=(
                "provider_access_restricted"
                if error.reason == "provider_access_restricted"
                else "source_unavailable"
            ),
            retry_after_seconds=error.retry_after_seconds,
        )
    return None


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
