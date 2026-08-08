"""Compatibility facade for KORAIL browser sidecar reservation wire contracts."""

from .korail_sidecar import contracts as _contracts

BaseModel = _contracts.BaseModel
ConfigDict = _contracts.ConfigDict
Field = _contracts.Field
KorailCredentialRequest = _contracts.KorailCredentialRequest
KorailLoginMethodValue = _contracts.KorailLoginMethodValue
KorailLoginVerificationOutcomeValue = _contracts.KorailLoginVerificationOutcomeValue
KorailLoginVerifyRequest = _contracts.KorailLoginVerifyRequest
KorailLoginVerifyResult = _contracts.KorailLoginVerifyResult
KorailReservationConfirmationRequest = _contracts.KorailReservationConfirmationRequest
KorailReservationConfirmationResult = _contracts.KorailReservationConfirmationResult
KorailReservationOutcomeValue = _contracts.KorailReservationOutcomeValue
KorailReservationSeatClassValue = _contracts.KorailReservationSeatClassValue
KorailReserveOnceRequest = _contracts.KorailReserveOnceRequest
KorailReserveOnceResult = _contracts.KorailReserveOnceResult
KorailSessionActorStateValue = _contracts.KorailSessionActorStateValue
KorailSessionStateResult = _contracts.KorailSessionStateResult
Literal = _contracts.Literal
SecretStr = _contracts.SecretStr
_InternalModel = _contracts._InternalModel
clock_time = _contracts.clock_time
date = _contracts.date
datetime = _contracts.datetime
field_validator = _contracts.field_validator
model_validator = _contracts.model_validator
