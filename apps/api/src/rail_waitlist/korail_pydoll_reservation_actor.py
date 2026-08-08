"""Compatibility facade for the canonical Pydoll reservation actor owner."""

from __future__ import annotations

import asyncio as asyncio
import re as re
import sys as sys
from collections.abc import Awaitable as Awaitable
from collections.abc import Callable as Callable
from dataclasses import replace as replace
from datetime import date as date
from datetime import datetime as datetime
from datetime import time as clock_time  # noqa: F401 -- compatibility export.
from typing import Protocol as Protocol

from .korail_sidecar.browser_contracts import (
    BrowserProtectionDetected as BrowserProtectionDetected,
)
from .korail_sidecar.browser_contracts import (
    BrowserRateLimited as BrowserRateLimited,
)
from .korail_sidecar.browser_contracts import (
    BrowserSourceUnavailable as BrowserSourceUnavailable,
)
from .korail_sidecar.pydoll.auth_actor import (
    KorailSessionActorState as KorailSessionActorState,
)
from .korail_sidecar.pydoll.auth_actor import (
    PydollAuthenticationSessionLease as PydollAuthenticationSessionLease,
)
from .korail_sidecar.pydoll.auth_contracts import (
    KorailCredentialInput as KorailCredentialInput,
)
from .korail_sidecar.pydoll.page_contracts import (
    KORAIL_ROUTE_HEADING as KORAIL_ROUTE_HEADING,
)
from .korail_sidecar.pydoll.page_contracts import (
    PydollPageSnapshot as PydollPageSnapshot,
)
from .korail_sidecar.pydoll.page_contracts import (
    normalize_korail_station as normalize_korail_station,
)
from .korail_sidecar.pydoll.page_contracts import (
    normalize_korail_train_number as normalize_korail_train_number,
)
from .korail_sidecar.pydoll.reservation_actor import (
    AcquireReservationSession as AcquireReservationSession,
)
from .korail_sidecar.pydoll.reservation_actor import (
    DirectSearchUrl as DirectSearchUrl,
)
from .korail_sidecar.pydoll.reservation_actor import (
    DiscardIfCredentialChanged as DiscardIfCredentialChanged,
)
from .korail_sidecar.pydoll.reservation_actor import (
    DiscardWithState as DiscardWithState,
)
from .korail_sidecar.pydoll.reservation_actor import (
    EnsureAuthenticatedSession as EnsureAuthenticatedSession,
)
from .korail_sidecar.pydoll.reservation_actor import (
    PydollReservationActor as PydollReservationActor,
)
from .korail_sidecar.pydoll.reservation_actor import (
    PydollReservationSession as PydollReservationSession,
)
from .korail_sidecar.pydoll.reservation_actor import (
    ReservationIdentityGuard as ReservationIdentityGuard,
)
from .korail_sidecar.pydoll.reservation_actor import (
    ResponseSafetyGuard as ResponseSafetyGuard,
)
from .korail_sidecar.pydoll.reservation_actor import (
    UniqueReservationTarget as UniqueReservationTarget,
)
from .korail_sidecar.pydoll.reservation_actor import (
    __all__ as __all__,
)
from .korail_sidecar.pydoll.reservation_actor import (
    assert_reservation_identity as assert_reservation_identity,
)
from .korail_sidecar.pydoll.reservation_actor import (
    has_unique_reservation_target as has_unique_reservation_target,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationOutcome as KorailReservationOutcome,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationRequest as KorailReservationRequest,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationResult as KorailReservationResult,
)
from .korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationSeatClass as KorailReservationSeatClass,
)
