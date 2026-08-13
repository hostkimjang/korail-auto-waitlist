"""Compatibility facade for SRT sidecar wire contracts."""

from __future__ import annotations

from .srt_sidecar import contracts as _contracts

BaseModel = _contracts.BaseModel
ConfigDict = _contracts.ConfigDict
Field = _contracts.Field
KOREA = _contracts.KOREA
Literal = _contracts.Literal
Provider = _contracts.Provider
ProviderCredentials = _contracts.ProviderCredentials
ReservationConfirmationOutcome = _contracts.ReservationConfirmationOutcome
ReservationConfirmationResult = _contracts.ReservationConfirmationResult
ReservationConfirmationTarget = _contracts.ReservationConfirmationTarget
ReservationRequest = _contracts.ReservationRequest
ReservationResult = _contracts.ReservationResult
SeatClass = _contracts.SeatClass
SeatObservationRequest = _contracts.SeatObservationRequest
SeatObservationResult = _contracts.SeatObservationResult
SecretStr = _contracts.SecretStr
SrtConfirmReservationRequest = _contracts.SrtConfirmReservationRequest
SrtConfirmReservationResult = _contracts.SrtConfirmReservationResult
SrtCredentialRequest = _contracts.SrtCredentialRequest
SrtLoginRequest = _contracts.SrtLoginRequest
SrtLoginResult = _contracts.SrtLoginResult
SrtObserveRequest = _contracts.SrtObserveRequest
SrtObserveResult = _contracts.SrtObserveResult
SrtReadOnlyCallRegistrationRequest = _contracts.SrtReadOnlyCallRegistrationRequest
SrtReadOnlyCallRegistrationResult = _contracts.SrtReadOnlyCallRegistrationResult
SrtReadOnlyCallStatus = _contracts.SrtReadOnlyCallStatus
SrtOfficialSeatStatus = _contracts.SrtOfficialSeatStatus
SrtProviderAdapterModel = _contracts.SrtProviderAdapterModel
SrtReservationConfirmationResult = _contracts.SrtReservationConfirmationResult
SrtReservationConfirmationTarget = _contracts.SrtReservationConfirmationTarget
SrtReserveOnceRequest = _contracts.SrtReserveOnceRequest
SrtReserveOnceResult = _contracts.SrtReserveOnceResult
SrtSessionActorState = _contracts.SrtSessionActorState
SrtSessionStatus = _contracts.SrtSessionStatus
SrtTimetableOverlayRequest = _contracts.SrtTimetableOverlayRequest
SrtTimetableOverlayResult = _contracts.SrtTimetableOverlayResult
SrtTimetableSearchRequest = _contracts.SrtTimetableSearchRequest
SrtTimetableSearchResult = _contracts.SrtTimetableSearchResult
SrtTimetableTrain = _contracts.SrtTimetableTrain
TimetableItem = _contracts.TimetableItem
ZoneInfo = _contracts.ZoneInfo
datetime = _contracts.datetime
model_validator = _contracts.model_validator
