from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RailLoginMethod = Literal["membership_number", "email", "phone"]


@dataclass(frozen=True, repr=False)
class ProviderCredentials:
    login_id: str = field(repr=False)
    password: str = field(repr=False)
    credential_version: int
    login_method: RailLoginMethod = "membership_number"
