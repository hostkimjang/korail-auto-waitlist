"""Compatibility facade for the canonical Pydoll read-only search actor owner."""

from __future__ import annotations

from .korail_sidecar.pydoll import search_actor as _owner
from .korail_sidecar.pydoll.search_actor import Callable as Callable

Awaitable = _owner.Awaitable
BrowserProtectionDetected = _owner.BrowserProtectionDetected
BrowserRateLimited = _owner.BrowserRateLimited
BrowserSeatSearchRequest = _owner.BrowserSeatSearchRequest
BrowserSeatSearchResult = _owner.BrowserSeatSearchResult
BrowserSourceUnavailable = _owner.BrowserSourceUnavailable
BrowserTrainSnapshot = _owner.BrowserTrainSnapshot
Cleanup = _owner.Cleanup
KORAIL_ROUTE_HEADING = _owner.KORAIL_ROUTE_HEADING
KorailHttpReplayClientFactory = _owner.KorailHttpReplayClientFactory
KorailHttpReplayPlan = _owner.KorailHttpReplayPlan
KorailPydollReadOnlySearchSession = _owner.KorailPydollReadOnlySearchSession
KorailPydollReadOnlySearchSessionContext = _owner.KorailPydollReadOnlySearchSessionContext
KorailPydollReadOnlySearchSessionFactory = _owner.KorailPydollReadOnlySearchSessionFactory
KorailStationIdentityResolver = _owner.KorailStationIdentityResolver
KorailStationIdentityUnavailable = _owner.KorailStationIdentityUnavailable
Mapping = _owner.Mapping
Protocol = _owner.Protocol
PydollHttpReplayManager = _owner.PydollHttpReplayManager
PydollPageSnapshot = _owner.PydollPageSnapshot
PydollReadOnlySearchActor = _owner.PydollReadOnlySearchActor
ResponseSafetyGuard = _owner.ResponseSafetyGuard
UTC = _owner.UTC
annotations = _owner.annotations
asyncio = _owner.asyncio
build_korail_general_search_url = _owner.build_korail_general_search_url
clock_time = _owner.clock_time
dataclass = _owner.dataclass
date = _owner.date
datetime = _owner.datetime
logging = _owner.logging
normalize_korail_station = _owner.normalize_korail_station
normalize_korail_train_number = _owner.normalize_korail_train_number
parse_expected_delay_minutes = _owner.parse_expected_delay_minutes
parse_official_train_type = _owner.parse_official_train_type
parse_unambiguous_adult_fare = _owner.parse_unambiguous_adult_fare
service_datetimes = _owner.service_datetimes
status_from_seat_box = _owner.status_from_seat_box
sys = _owner.sys
_ActiveReadOnlySearchSession = _owner._ActiveReadOnlySearchSession
_MAX_MORE_RESULT_ACTIONS = _owner._MAX_MORE_RESULT_ACTIONS
_ReadOnlySearchSessionLease = _owner._ReadOnlySearchSessionLease

del _owner
