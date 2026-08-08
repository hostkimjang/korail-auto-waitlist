"""Compatibility facade for the canonical Pydoll reservation DOM driver owner."""

from __future__ import annotations

from .korail_sidecar.pydoll import reservation_driver as _owner
from .korail_sidecar.pydoll.reservation_driver import Callable as Callable

Any = _owner.Any
Awaitable = _owner.Awaitable
BrowserSourceUnavailable = _owner.BrowserSourceUnavailable
CurrentSchedule = _owner.CurrentSchedule
KORAIL_ROUTE_HEADING = _owner.KORAIL_ROUTE_HEADING
KorailCredentialInput = _owner.KorailCredentialInput
KorailReservationOutcome = _owner.KorailReservationOutcome
KorailReservationRequest = _owner.KorailReservationRequest
KorailReservationResult = _owner.KorailReservationResult
Mapping = _owner.Mapping
Protocol = _owner.Protocol
PydollPageSnapshot = _owner.PydollPageSnapshot
PydollReservationDomDriver = _owner.PydollReservationDomDriver
ReadControlState = _owner.ReadControlState
ReservationAttemptState = _owner.ReservationAttemptState
ReservationControlState = _owner.ReservationControlState
ReservationDomCompatibilityPort = _owner.ReservationDomCompatibilityPort
ReservationExecuteScript = _owner.ReservationExecuteScript
VisibleElements = _owner.VisibleElements
annotations = _owner.annotations
asyncio = _owner.asyncio
booking_seat_control_key = _owner.booking_seat_control_key
dataclass = _owner.dataclass
date = _owner.date
datetime = _owner.datetime
is_rate_limit_response = _owner.is_rate_limit_response
logging = _owner.logging
normalize_korail_station = _owner.normalize_korail_station
normalize_korail_train_number = _owner.normalize_korail_train_number
protection_trigger_from_http_response = _owner.protection_trigger_from_http_response
protection_trigger_from_text = _owner.protection_trigger_from_text
re = _owner.re
urlsplit = _owner.urlsplit
_has_exact_train_number_marker = _owner._has_exact_train_number_marker
_normalized_train_number = _owner._normalized_train_number
_reservation_date_markers = _owner._reservation_date_markers
_sanitized_class_tokens = _owner._sanitized_class_tokens

del _owner
