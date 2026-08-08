"""Compatibility exports for the canonical browser-companion owners."""

from __future__ import annotations

import hashlib as hashlib
import re as re
import secrets as secrets
import uuid as uuid
from dataclasses import dataclass as dataclass
from datetime import UTC as UTC
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from typing import Annotated as Annotated
from zoneinfo import ZoneInfo as ZoneInfo

from fastapi import APIRouter as APIRouter
from fastapi import Depends as Depends
from fastapi import Header as Header
from fastapi import HTTPException as HTTPException
from fastapi import Request as Request
from fastapi import Response as Response
from pydantic import AnyHttpUrl as AnyHttpUrl
from pydantic import ValidationError as ValidationError
from sqlalchemy import delete as delete
from sqlalchemy import func as func
from sqlalchemy import select as select
from sqlalchemy import update as update
from sqlalchemy.ext.asyncio import AsyncSession as AsyncSession

from .auth import auth_rate_limiter as auth_rate_limiter
from .auth import keyed_hash as keyed_hash
from .auth import require_admin as require_admin
from .browser_companion.http import CHALLENGE_FRESHNESS as CHALLENGE_FRESHNESS
from .browser_companion.http import EXTENSION_ORIGIN as EXTENSION_ORIGIN
from .browser_companion.http import FRESHNESS as FRESHNESS
from .browser_companion.http import MAX_OUTSTANDING_CHALLENGES as MAX_OUTSTANDING_CHALLENGES
from .browser_companion.http import PAIRING_FRESHNESS as PAIRING_FRESHNESS
from .browser_companion.http import SNAPSHOT_BUDGET as SNAPSHOT_BUDGET
from .browser_companion.http import SNAPSHOT_BUDGET_WINDOW as SNAPSHOT_BUDGET_WINDOW
from .browser_companion.http import SNAPSHOT_PATH as SNAPSHOT_PATH
from .browser_companion.http import BridgeChallenge as BridgeChallenge
from .browser_companion.http import BridgeClientId as BridgeClientId
from .browser_companion.http import BridgePrincipal as BridgePrincipal
from .browser_companion.http import BridgeToken as BridgeToken
from .browser_companion.http import Session as Session
from .browser_companion.http import _consume_snapshot_budget as _consume_snapshot_budget
from .browser_companion.http import _consume_snapshot_challenge as _consume_snapshot_challenge
from .browser_companion.http import _extension_origin as _extension_origin
from .browser_companion.http import _require_bridge_enabled as _require_bridge_enabled
from .browser_companion.http import admin_router as admin_router
from .browser_companion.http import browser_companion_status as browser_companion_status
from .browser_companion.http import (
    create_browser_companion_challenge as create_browser_companion_challenge,
)
from .browser_companion.http import (
    create_browser_companion_pairing as create_browser_companion_pairing,
)
from .browser_companion.http import create_korail_snapshot as create_korail_snapshot
from .browser_companion.http import (
    exchange_browser_companion_pairing as exchange_browser_companion_pairing,
)
from .browser_companion.http import require_bridge_credential as require_bridge_credential
from .browser_companion.http import (
    revoke_browser_companion_credential as revoke_browser_companion_credential,
)
from .browser_companion.http import router as router
from .browser_companion.models import BrowserCompanionChallenge as BrowserCompanionChallenge
from .browser_companion.models import BrowserCompanionCredential as BrowserCompanionCredential
from .browser_companion.models import BrowserCompanionPairing as BrowserCompanionPairing
from .browser_companion.models import KorailBrowserSeatSnapshot as KorailBrowserSeatSnapshot
from .browser_companion.models import KorailBrowserSnapshotBatch as KorailBrowserSnapshotBatch
from .browser_companion.schemas import (
    KORAIL_BROWSER_COMPANION_SOURCE as KORAIL_BROWSER_COMPANION_SOURCE,
)
from .browser_companion.schemas import (
    BrowserCompanionChallengeCreate as BrowserCompanionChallengeCreate,
)
from .browser_companion.schemas import (
    BrowserCompanionChallengeRead as BrowserCompanionChallengeRead,
)
from .browser_companion.schemas import (
    BrowserCompanionCredentialRead as BrowserCompanionCredentialRead,
)
from .browser_companion.schemas import (
    BrowserCompanionPairingCreate as BrowserCompanionPairingCreate,
)
from .browser_companion.schemas import (
    BrowserCompanionPairingExchange as BrowserCompanionPairingExchange,
)
from .browser_companion.schemas import (
    BrowserCompanionPairingRead as BrowserCompanionPairingRead,
)
from .browser_companion.schemas import (
    BrowserCompanionPairingResult as BrowserCompanionPairingResult,
)
from .browser_companion.schemas import BrowserCompanionStatus as BrowserCompanionStatus
from .browser_companion.schemas import KorailBrowserSnapshotCreate as KorailBrowserSnapshotCreate
from .browser_companion.schemas import KorailBrowserSnapshotRead as KorailBrowserSnapshotRead
from .browser_companion.snapshot_overlay import KOREA as KOREA
from .browser_companion.snapshot_overlay import SOURCE as SOURCE
from .browser_companion.snapshot_overlay import _as_utc as _as_utc
from .browser_companion.snapshot_overlay import _seat_actions as _seat_actions
from .browser_companion.snapshot_overlay import _snapshot_key as _snapshot_key
from .browser_companion.snapshot_overlay import (
    overlay_korail_browser_snapshots as overlay_korail_browser_snapshots,
)
from .config import get_settings as get_settings
from .database import get_session as get_session
from .domain import SeatClass as SeatClass
from .domain import SeatObservationStatus as SeatObservationStatus
from .official_rail_identity import (
    normalize_official_train_number as normalize_official_train_number,
)
from .timetable_management.schemas import SeatAvailabilityAction as SeatAvailabilityAction
from .timetable_management.schemas import (
    SeatAvailabilityProvenance as SeatAvailabilityProvenance,
)
from .timetable_management.schemas import SeatClassAvailability as SeatClassAvailability
from .timetable_management.schemas import TimetableItem as TimetableItem
