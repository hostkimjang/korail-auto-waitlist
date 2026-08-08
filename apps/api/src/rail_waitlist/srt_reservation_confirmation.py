"""Compatibility exports for SRT reservation confirmation."""

from .provider_adapters.srt_identity import normalize_srt_date as normalize_srt_date
from .provider_adapters.srt_identity import normalize_srt_time as normalize_srt_time
from .provider_adapters.srt_identity import (
    normalize_srt_train_number as normalize_srt_train_number,
)
from .reservations.provider_confirmation.srt import (
    SRT_RESERVATION_HANDOFF_URL as SRT_RESERVATION_HANDOFF_URL,
)
from .reservations.provider_confirmation.srt import (
    SRT_RESERVATION_LIST_SOURCE as SRT_RESERVATION_LIST_SOURCE,
)
from .reservations.provider_confirmation.srt import (
    SRT_RESERVE_RESULT_SOURCE as SRT_RESERVE_RESULT_SOURCE,
)
from .reservations.provider_confirmation.srt import (
    SrtReadOnlyReservationListProbe as SrtReadOnlyReservationListProbe,
)
from .reservations.provider_confirmation.srt import (
    SrtReservationListConfirmationAdapter as SrtReservationListConfirmationAdapter,
)
from .reservations.provider_confirmation.srt import (
    SrtReservationListEvidence as SrtReservationListEvidence,
)
from .reservations.provider_confirmation.srt import (
    SrtReservationRecord as SrtReservationRecord,
)
from .reservations.provider_confirmation.srt import (
    normalize_srt_reservation_records as normalize_srt_reservation_records,
)
from .reservations.provider_confirmation.srt import (
    normalize_srt_reserve_result as normalize_srt_reserve_result,
)

__all__ = (
    "SRT_RESERVATION_HANDOFF_URL",
    "SRT_RESERVATION_LIST_SOURCE",
    "SRT_RESERVE_RESULT_SOURCE",
    "SrtReadOnlyReservationListProbe",
    "SrtReservationListConfirmationAdapter",
    "SrtReservationListEvidence",
    "SrtReservationRecord",
    "normalize_srt_reservation_records",
    "normalize_srt_reserve_result",
)
