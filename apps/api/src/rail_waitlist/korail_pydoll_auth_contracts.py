"""Compatibility facade for the KORAIL Pydoll authentication contracts."""

from __future__ import annotations

from .korail_sidecar.pydoll import auth_contracts as _owner

annotations = _owner.annotations
dataclass = _owner.dataclass
field = _owner.field
StrEnum = _owner.StrEnum
KorailLoginMethod = _owner.KorailLoginMethod
KorailCredentialInput = _owner.KorailCredentialInput
