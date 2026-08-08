"""Compatibility facade for the canonical Pydoll authentication session actor."""

from __future__ import annotations

from .korail_sidecar.pydoll import auth_actor as _owner
from .korail_sidecar.pydoll.auth_actor import Callable as Callable

ActivePydollAuthenticationSession = _owner.ActivePydollAuthenticationSession
Awaitable = _owner.Awaitable
BrowserProtectionDetected = _owner.BrowserProtectionDetected
BrowserRateLimited = _owner.BrowserRateLimited
BrowserSourceUnavailable = _owner.BrowserSourceUnavailable
ContractKorailCredentialInput = _owner.ContractKorailCredentialInput
ContractKorailLoginMethod = _owner.ContractKorailLoginMethod
CredentialFingerprint = _owner.CredentialFingerprint
KorailCredentialInput = _owner.KorailCredentialInput
KorailLoginMethod = _owner.KorailLoginMethod
KorailSessionActorSnapshot = _owner.KorailSessionActorSnapshot
KorailSessionActorState = _owner.KorailSessionActorState
OwnedCleanup = _owner.OwnedCleanup
Protocol = _owner.Protocol
PydollAuthenticationSession = _owner.PydollAuthenticationSession
PydollAuthenticationSessionActor = _owner.PydollAuthenticationSessionActor
PydollAuthenticationSessionContext = _owner.PydollAuthenticationSessionContext
PydollAuthenticationSessionFactory = _owner.PydollAuthenticationSessionFactory  # type: ignore[type-arg]
PydollAuthenticationSessionLease = _owner.PydollAuthenticationSessionLease
PydollPageSnapshot = _owner.PydollPageSnapshot
ResponseSafetyGuard = _owner.ResponseSafetyGuard
StrEnum = _owner.StrEnum
annotations = _owner.annotations
asyncio = _owner.asyncio
credential_fingerprint = _owner.credential_fingerprint
dataclass = _owner.dataclass
field = _owner.field
hashlib = _owner.hashlib
sys = _owner.sys

del _owner
