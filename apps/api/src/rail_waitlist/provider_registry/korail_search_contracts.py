from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KorailStationIdentity:
    code: str
    name: str
