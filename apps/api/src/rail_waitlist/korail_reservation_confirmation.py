"""Compatibility exports for KORAIL read-only reservation confirmation."""

from .reservations.provider_confirmation.korail import (
    KORAIL_CONFIRMATION_SOURCE as KORAIL_CONFIRMATION_SOURCE,
)
from .reservations.provider_confirmation.korail import (
    KORAIL_RESERVATION_HANDOFF_URL as KORAIL_RESERVATION_HANDOFF_URL,
)
from .reservations.provider_confirmation.korail import (
    KORAIL_RESERVATION_LIST_SOURCE as KORAIL_RESERVATION_LIST_SOURCE,
)
from .reservations.provider_confirmation.korail import (
    KorailSameSessionDetailConfirmationAdapter as KorailSameSessionDetailConfirmationAdapter,
)
from .reservations.provider_confirmation.korail import (
    KorailSameSessionDetailEvidence as KorailSameSessionDetailEvidence,
)
from .reservations.provider_confirmation.korail import (
    KorailSameSessionDetailProbe as KorailSameSessionDetailProbe,
)
from .reservations.provider_confirmation.korail import (
    normalize_korail_same_session_detail as normalize_korail_same_session_detail,
)

__all__ = (
    "KORAIL_CONFIRMATION_SOURCE",
    "KORAIL_RESERVATION_HANDOFF_URL",
    "KORAIL_RESERVATION_LIST_SOURCE",
    "KorailSameSessionDetailConfirmationAdapter",
    "KorailSameSessionDetailEvidence",
    "KorailSameSessionDetailProbe",
    "normalize_korail_same_session_detail",
)
