"""Compatibility exports for the official-page confirmation bounded context."""

from .official_page_confirmation.application import (
    CONFIRMATION_FRESHNESS,
    IDEMPOTENCY_SCOPE,
    overlay_official_page_confirmations,
    upsert_official_page_confirmations,
)

__all__ = (
    "CONFIRMATION_FRESHNESS",
    "IDEMPOTENCY_SCOPE",
    "overlay_official_page_confirmations",
    "upsert_official_page_confirmations",
)
