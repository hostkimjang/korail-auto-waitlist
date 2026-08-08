"""Compatibility facade for the SRT sidecar HTTP client."""

from __future__ import annotations

from .srt_sidecar import client as _client

ProviderCredentials = _client.ProviderCredentials
ReservationConfirmationResult = _client.ReservationConfirmationResult
ReservationConfirmationTarget = _client.ReservationConfirmationTarget
ReservationRequest = _client.ReservationRequest
ReservationResult = _client.ReservationResult
SRTError = _client.SRTError
SRTNetFunnelError = _client.SRTNetFunnelError
SRT_PROVIDER_ADAPTER_ORIGIN = _client.SRT_PROVIDER_ADAPTER_ORIGIN
SeatObservationRequest = _client.SeatObservationRequest
SeatObservationResult = _client.SeatObservationResult
SrtConfirmReservationRequest = _client.SrtConfirmReservationRequest
SrtConfirmReservationResult = _client.SrtConfirmReservationResult
SrtCredentialRequest = _client.SrtCredentialRequest
SrtLoginRequest = _client.SrtLoginRequest
SrtLoginResult = _client.SrtLoginResult
SrtObserveRequest = _client.SrtObserveRequest
SrtObserveResult = _client.SrtObserveResult
SrtProviderAdapterClient = _client.SrtProviderAdapterClient
SrtProviderAdapterUnavailable = _client.SrtProviderAdapterUnavailable
SrtReservationConfirmationTarget = _client.SrtReservationConfirmationTarget
SrtReserveOnceRequest = _client.SrtReserveOnceRequest
SrtReserveOnceResult = _client.SrtReserveOnceResult
SrtSessionStatus = _client.SrtSessionStatus
SrtTimetableOverlayRequest = _client.SrtTimetableOverlayRequest
SrtTimetableOverlayResult = _client.SrtTimetableOverlayResult
SrtTimetableSearchRequest = _client.SrtTimetableSearchRequest
SrtTimetableSearchResult = _client.SrtTimetableSearchResult
SrtTimetableTrain = _client.SrtTimetableTrain
TimetableItem = _client.TimetableItem
ValidationError = _client.ValidationError
datetime = _client.datetime
httpx = _client.httpx
urlsplit = _client.urlsplit
validate_srt_provider_adapter_url = _client.validate_srt_provider_adapter_url
