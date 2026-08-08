"""Compatibility facade for the accountless KORAIL seat source."""

from __future__ import annotations

from .provider_adapters import korail_seat_source as _source

asyncio = _source.asyncio
time = _source.time
Callable = _source.Callable
dataclass = _source.dataclass
UTC = _source.UTC
datetime = _source.datetime
Protocol = _source.Protocol
ZoneInfo = _source.ZoneInfo
AdultPassenger = _source.AdultPassenger
Korail = _source.Korail
KorailError = _source.KorailError
NoResultsError = _source.NoResultsError
RequestException = _source.RequestException
HTTPAdapter = _source.HTTPAdapter
CooldownStore = _source.CooldownStore
MemoryCooldownStore = _source.MemoryCooldownStore
SeatAvailabilityAction = _source.SeatAvailabilityAction
SeatAvailabilityNotObservedReason = _source.SeatAvailabilityNotObservedReason
SeatAvailabilityProvenance = _source.SeatAvailabilityProvenance
SeatAvailabilityStatus = _source.SeatAvailabilityStatus
SeatClassAvailability = _source.SeatClassAvailability
TimetableItem = _source.TimetableItem
SOURCE_NAME = _source.SOURCE_NAME
KOREA = _source.KOREA
PROTECTION_MARKERS = _source.PROTECTION_MARKERS
KorailClientFactory = _source.KorailClientFactory
PassengerFactory = _source.PassengerFactory
KorailSeatSnapshot = _source.KorailSeatSnapshot
normalize_train_number = _source.normalize_train_number
normalize_date = _source.normalize_date
normalize_time = _source.normalize_time
map_korail_seat_state = _source.map_korail_seat_state
KorailLiveSeatSource = _source.KorailLiveSeatSource

_KorailTrain = _source._KorailTrain
_KorailClient = _source._KorailClient
_DefaultTimeoutAdapter = _source._DefaultTimeoutAdapter
_CacheEntry = _source._CacheEntry
_ProviderCooldown = _source._ProviderCooldown
_default_client_factory = _source._default_client_factory
