"""Compatibility facade for secret-free reservation confirmation contracts."""

from __future__ import annotations

import re as re
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from enum import StrEnum as StrEnum
from typing import Protocol as Protocol
from urllib.parse import urlsplit as urlsplit

from .domain import Provider as Provider
from .domain import SeatClass as SeatClass
from .provider_registry.official_url_policy import (
    OFFICIAL_HOST_ROOTS as OFFICIAL_HOST_ROOTS,
)
from .provider_registry.official_url_policy import (
    require_official_handoff_url as require_official_handoff_url,
)
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationAdapter as ReservationConfirmationAdapter,
)
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome as ReservationConfirmationOutcome,
)
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult as ReservationConfirmationResult,
)
from .reservations.provider_confirmation.contracts import (
    ReservationConfirmationTarget as ReservationConfirmationTarget,
)
