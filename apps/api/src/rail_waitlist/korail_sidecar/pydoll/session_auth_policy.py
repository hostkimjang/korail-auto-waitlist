"""Resolve KORAIL session authentication without erasing probe uncertainty."""

from __future__ import annotations

from typing import Protocol

from ..browser_contracts import BrowserSourceUnavailable


class KorailSessionAuthenticationSignals(Protocol):
    async def _probe_official_authenticated_session(self) -> bool: ...

    async def _has_authenticated_header(self) -> bool: ...


async def is_korail_session_authenticated(
    session: KorailSessionAuthenticationSignals,
) -> bool:
    """Use the header as positive fallback evidence, never as uncertainty erasure."""

    try:
        officially_authenticated = await session._probe_official_authenticated_session()
    except BrowserSourceUnavailable:
        if await session._has_authenticated_header():
            return True
        raise

    if officially_authenticated:
        return True
    return await session._has_authenticated_header()
