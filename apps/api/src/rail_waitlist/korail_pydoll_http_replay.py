"""Compatibility facade for the KORAIL Pydoll HTTP replay manager."""

from __future__ import annotations

from .korail_sidecar.pydoll import http_replay as _owner

annotations = _owner.annotations
asyncio = _owner.asyncio
logging = _owner.logging
OrderedDict = _owner.OrderedDict
Awaitable = _owner.Awaitable
Callable = _owner.Callable
Mapping = _owner.Mapping
dataclass = _owner.dataclass
date = _owner.date
MappingProxyType = _owner.MappingProxyType
Protocol = _owner.Protocol
HttpReplayInvalidCapture = _owner.HttpReplayInvalidCapture
HttpReplayInvalidResponse = _owner.HttpReplayInvalidResponse
HttpReplayLeaseInvalid = _owner.HttpReplayLeaseInvalid
HttpReplayProtectionDetected = _owner.HttpReplayProtectionDetected
HttpReplayRateLimited = _owner.HttpReplayRateLimited
HttpReplaySessionInvalid = _owner.HttpReplaySessionInvalid
HttpReplaySourceUnavailable = _owner.HttpReplaySourceUnavailable
KorailHttpReplayPlan = _owner.KorailHttpReplayPlan
BrowserProtectionDetected = _owner.BrowserProtectionDetected
BrowserRateLimited = _owner.BrowserRateLimited
BrowserSeatSearchRequest = _owner.BrowserSeatSearchRequest
BrowserSeatSearchResult = _owner.BrowserSeatSearchResult
BrowserSourceUnavailable = _owner.BrowserSourceUnavailable
normalize_replay_protection_trigger = _owner.normalize_replay_protection_trigger
logger = _owner.logger
DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE = _owner.DEFAULT_HTTP_REPLAY_ROUTE_CACHE_SIZE
_RouteKey = _owner._RouteKey
Cleanup = _owner.Cleanup
KorailHttpReplayCaptureSession = _owner.KorailHttpReplayCaptureSession
KorailHttpReplaySearchClient = _owner.KorailHttpReplaySearchClient
KorailHttpReplayClientFactory = _owner.KorailHttpReplayClientFactory
_ActiveHttpReplayLease = _owner._ActiveHttpReplayLease
PydollHttpReplayManager = _owner.PydollHttpReplayManager
