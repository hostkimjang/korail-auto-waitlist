"""Compatibility facade for the canonical Pydoll login DOM driver owner."""

from __future__ import annotations

from .korail_sidecar.pydoll import login_driver as _owner
from .korail_sidecar.pydoll.login_driver import Callable as Callable

Any = _owner.Any
Awaitable = _owner.Awaitable
BrowserProtectionDetected = _owner.BrowserProtectionDetected
BrowserRateLimited = _owner.BrowserRateLimited
BrowserSourceUnavailable = _owner.BrowserSourceUnavailable
ExactTextWaiter = _owner.ExactTextWaiter
ExactVisibleReader = _owner.ExactVisibleReader
KorailCredentialInput = _owner.KorailCredentialInput
KorailLoginMethod = _owner.KorailLoginMethod
LoginAttemptState = _owner.LoginAttemptState
LoginExecuteScript = _owner.LoginExecuteScript
LoginGoTo = _owner.LoginGoTo
LoginWorkflowCompatibilityPort = _owner.LoginWorkflowCompatibilityPort
Mapping = _owner.Mapping
Protocol = _owner.Protocol
PydollLoginDomDriver = _owner.PydollLoginDomDriver
PydollPageSnapshot = _owner.PydollPageSnapshot
ResponseSafetyGuard = _owner.ResponseSafetyGuard
SnapshotReader = _owner.SnapshotReader
VisibleElements = _owner.VisibleElements
annotations = _owner.annotations
dataclass = _owner.dataclass
logging = _owner.logging
login_step = _owner.login_step
_LocalLoginAttemptState = _owner._LocalLoginAttemptState

del _owner
