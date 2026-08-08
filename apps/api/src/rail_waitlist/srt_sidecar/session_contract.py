from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum as StrEnum


class SrtSessionActorState(StrEnum):
    COLD = "cold"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    STALE = "stale"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SrtSessionActorSnapshot:
    state: SrtSessionActorState
    credential_generation: int | None
    created_at_monotonic: float | None
    last_verified_at_monotonic: float | None
    last_used_at_monotonic: float | None
    local_reuse_until_monotonic: float | None
    locally_reusable: bool
