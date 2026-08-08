"""Compatibility facade for the KORAIL Pydoll reservation contracts."""

from __future__ import annotations

from .korail_sidecar.pydoll import reservation_contracts as _owner

annotations = _owner.annotations
dataclass = _owner.dataclass
field = _owner.field
date = _owner.date
datetime = _owner.datetime
clock_time = _owner.clock_time
StrEnum = _owner.StrEnum
KorailCredentialInput = _owner.KorailCredentialInput
KorailReservationSeatClass = _owner.KorailReservationSeatClass
KorailReservationOutcome = _owner.KorailReservationOutcome
KorailReservationRequest = _owner.KorailReservationRequest
KorailReservationResult = _owner.KorailReservationResult
