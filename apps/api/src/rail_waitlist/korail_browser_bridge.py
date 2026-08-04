from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import AnyHttpUrl, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import auth_rate_limiter, keyed_hash, require_admin
from .config import get_settings
from .database import get_session
from .domain import SeatClass, SeatObservationStatus
from .models import (
    BrowserCompanionChallenge,
    BrowserCompanionCredential,
    BrowserCompanionPairing,
    KorailBrowserSeatSnapshot,
    KorailBrowserSnapshotBatch,
)
from .schemas import (
    KORAIL_BROWSER_COMPANION_SOURCE,
    BrowserCompanionChallengeCreate,
    BrowserCompanionChallengeRead,
    BrowserCompanionCredentialRead,
    BrowserCompanionPairingCreate,
    BrowserCompanionPairingExchange,
    BrowserCompanionPairingRead,
    BrowserCompanionPairingResult,
    BrowserCompanionStatus,
    KorailBrowserSnapshotCreate,
    KorailBrowserSnapshotRead,
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
    normalize_official_train_number,
)

SOURCE = KORAIL_BROWSER_COMPANION_SOURCE
FRESHNESS = timedelta(minutes=2)
PAIRING_FRESHNESS = timedelta(minutes=5)
CHALLENGE_FRESHNESS = timedelta(seconds=30)
SNAPSHOT_BUDGET_WINDOW = timedelta(minutes=1)
SNAPSHOT_BUDGET = 6
MAX_OUTSTANDING_CHALLENGES = 10
SNAPSHOT_PATH = "/api/v1/browser-bridge/korail-snapshots"
KOREA = ZoneInfo("Asia/Seoul")
EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")

router = APIRouter(prefix="/api/v1/browser-bridge")
admin_router = APIRouter(
    prefix="/api/v1/browser-companion",
    dependencies=[Depends(require_admin)],
)
Session = Annotated[AsyncSession, Depends(get_session)]
BridgeToken = Annotated[str | None, Header(alias="X-Rail-Bridge-Token", max_length=512)]
BridgeClientId = Annotated[str | None, Header(alias="X-Rail-Bridge-Client-Id", max_length=64)]
BridgeChallenge = Annotated[str | None, Header(alias="X-Rail-Bridge-Challenge", max_length=512)]


@dataclass(frozen=True)
class BridgePrincipal:
    credential_id: str
    client_id: str
    extension_origin: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_bridge_enabled() -> None:
    settings = get_settings()
    if not settings.korail_browser_bridge_enabled:
        raise HTTPException(404, "browser bridge is disabled")


def _extension_origin(request: Request) -> str:
    origin = request.headers.get("origin", "").rstrip("/")
    if not EXTENSION_ORIGIN.fullmatch(origin):
        raise HTTPException(403, "browser companion origin is required")
    return origin


async def require_bridge_credential(
    request: Request,
    session: Session,
    token: BridgeToken = None,
    client_id: BridgeClientId = None,
) -> BridgePrincipal:
    _require_bridge_enabled()
    if not token or not client_id:
        raise HTTPException(401, "invalid browser bridge token")
    origin = _extension_origin(request)
    credential = await session.scalar(
        select(BrowserCompanionCredential)
        .where(
            BrowserCompanionCredential.token_hash == keyed_hash("browser-bridge-credential", token),
            BrowserCompanionCredential.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if (
        credential is None
        or not secrets.compare_digest(credential.client_id, client_id)
        or not secrets.compare_digest(credential.extension_origin, origin)
    ):
        raise HTTPException(401, "invalid browser bridge token")
    return BridgePrincipal(
        credential_id=credential.id,
        client_id=credential.client_id,
        extension_origin=credential.extension_origin,
    )


@admin_router.get("/status", response_model=BrowserCompanionStatus)
async def browser_companion_status(response: Response, session: Session) -> BrowserCompanionStatus:
    response.headers["Cache-Control"] = "no-store"
    credentials = list(
        (
            await session.scalars(
                select(BrowserCompanionCredential)
                .where(BrowserCompanionCredential.revoked_at.is_(None))
                .order_by(BrowserCompanionCredential.created_at.desc())
            )
        ).all()
    )
    return BrowserCompanionStatus(
        enabled=get_settings().korail_browser_bridge_enabled,
        credentials=[BrowserCompanionCredentialRead.model_validate(item) for item in credentials],
    )


@admin_router.post("/pairings", response_model=BrowserCompanionPairingRead, status_code=201)
async def create_browser_companion_pairing(
    data: BrowserCompanionPairingCreate,
    response: Response,
    session: Session,
) -> BrowserCompanionPairingRead:
    _require_bridge_enabled()
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    await session.execute(
        delete(BrowserCompanionPairing).where(
            BrowserCompanionPairing.expires_at < now - timedelta(days=1)
        )
    )
    raw_code = secrets.token_urlsafe(32)
    pairing = BrowserCompanionPairing(
        code_hash=keyed_hash("browser-companion-pairing", raw_code),
        label=data.label,
        expires_at=now + PAIRING_FRESHNESS,
        created_at=now,
    )
    session.add(pairing)
    await session.commit()
    return BrowserCompanionPairingRead(
        pairing_code=raw_code,
        expires_at=pairing.expires_at,
    )


@admin_router.delete("/credentials/{credential_id}", status_code=204)
async def revoke_browser_companion_credential(
    credential_id: str,
    response: Response,
    session: Session,
) -> Response:
    credential = await session.scalar(
        select(BrowserCompanionCredential)
        .where(
            BrowserCompanionCredential.id == credential_id,
            BrowserCompanionCredential.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if credential is None:
        raise HTTPException(404, "browser companion credential not found")
    credential.revoked_at = datetime.now(UTC)
    await session.commit()
    response.status_code = 204
    return response


@router.post("/pair", response_model=BrowserCompanionPairingResult)
async def exchange_browser_companion_pairing(
    data: BrowserCompanionPairingExchange,
    request: Request,
    response: Response,
    session: Session,
) -> BrowserCompanionPairingResult:
    _require_bridge_enabled()
    await auth_rate_limiter.check("browser-companion-pair", request, 10)
    response.headers["Cache-Control"] = "no-store"
    origin = _extension_origin(request)
    now = datetime.now(UTC)
    pairing = await session.scalar(
        select(BrowserCompanionPairing)
        .where(
            BrowserCompanionPairing.code_hash
            == keyed_hash("browser-companion-pairing", data.pairing_code)
        )
        .with_for_update()
    )
    if pairing is None:
        raise HTTPException(401, "invalid or expired pairing code")
    if pairing.consumed_at is not None or _as_utc(pairing.expires_at) <= now:
        raise HTTPException(410, "pairing code is expired or already used")

    raw_token = secrets.token_urlsafe(48)
    credential = BrowserCompanionCredential(
        token_hash=keyed_hash("browser-bridge-credential", raw_token),
        extension_origin=origin,
        client_id=data.client_id,
        label=pairing.label,
        created_at=now,
    )
    pairing.consumed_at = now
    session.add(credential)
    await session.commit()
    return BrowserCompanionPairingResult(
        credential_id=credential.id,
        bridge_token=raw_token,
        label=credential.label,
    )


@router.post("/challenges", response_model=BrowserCompanionChallengeRead, status_code=201)
async def create_browser_companion_challenge(
    data: BrowserCompanionChallengeCreate,
    response: Response,
    session: Session,
    principal: Annotated[BridgePrincipal, Depends(require_bridge_credential)],
) -> BrowserCompanionChallengeRead:
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    await session.execute(
        delete(BrowserCompanionChallenge).where(
            BrowserCompanionChallenge.credential_id == principal.credential_id,
            BrowserCompanionChallenge.expires_at < now - timedelta(hours=1),
        )
    )
    outstanding = int(
        await session.scalar(
            select(func.count())
            .select_from(BrowserCompanionChallenge)
            .where(
                BrowserCompanionChallenge.credential_id == principal.credential_id,
                BrowserCompanionChallenge.consumed_at.is_(None),
                BrowserCompanionChallenge.expires_at > now,
            )
        )
        or 0
    )
    if outstanding >= MAX_OUTSTANDING_CHALLENGES:
        raise HTTPException(429, "too many outstanding challenges", headers={"Retry-After": "30"})
    raw_challenge = secrets.token_urlsafe(32)
    challenge = BrowserCompanionChallenge(
        credential_id=principal.credential_id,
        challenge_hash=keyed_hash("browser-companion-challenge", raw_challenge),
        method="POST",
        path=SNAPSHOT_PATH,
        body_sha256=data.body_sha256,
        expires_at=now + CHALLENGE_FRESHNESS,
        created_at=now,
    )
    session.add(challenge)
    await session.commit()
    return BrowserCompanionChallengeRead(
        challenge=raw_challenge,
        expires_at=challenge.expires_at,
    )


async def _consume_snapshot_challenge(
    *,
    request: Request,
    session: AsyncSession,
    principal: BridgePrincipal,
    raw_challenge: str | None,
) -> str:
    if not raw_challenge:
        raise HTTPException(401, "browser companion challenge is required")
    now = datetime.now(UTC)
    challenge = await session.scalar(
        select(BrowserCompanionChallenge).where(
            BrowserCompanionChallenge.credential_id == principal.credential_id,
            BrowserCompanionChallenge.challenge_hash
            == keyed_hash("browser-companion-challenge", raw_challenge),
        )
    )
    if challenge is None:
        raise HTTPException(403, "invalid browser companion challenge")
    if challenge.consumed_at is not None:
        raise HTTPException(409, "browser companion challenge was already used")
    if _as_utc(challenge.expires_at) <= now:
        raise HTTPException(410, "browser companion challenge expired")
    body_sha256 = hashlib.sha256(await request.body()).hexdigest()
    if (
        challenge.method != request.method
        or challenge.path != request.url.path
        or not secrets.compare_digest(challenge.body_sha256, body_sha256)
    ):
        await session.execute(
            update(BrowserCompanionChallenge)
            .where(
                BrowserCompanionChallenge.id == challenge.id,
                BrowserCompanionChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        await session.commit()
        raise HTTPException(403, "browser companion challenge does not match request")
    consumed = await session.execute(
        update(BrowserCompanionChallenge)
        .where(
            BrowserCompanionChallenge.id == challenge.id,
            BrowserCompanionChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "browser companion challenge was already used")
    return challenge.id


async def _consume_snapshot_budget(
    session: AsyncSession, principal: BridgePrincipal, now: datetime
) -> BrowserCompanionCredential:
    credential = await session.scalar(
        select(BrowserCompanionCredential)
        .where(
            BrowserCompanionCredential.id == principal.credential_id,
            BrowserCompanionCredential.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if credential is None:
        raise HTTPException(401, "browser companion credential was revoked")
    window_started = (
        _as_utc(credential.window_started_at) if credential.window_started_at is not None else None
    )
    if window_started is None or window_started + SNAPSHOT_BUDGET_WINDOW <= now:
        credential.window_started_at = now
        credential.accepted_in_window = 0
        window_started = now
    if credential.accepted_in_window >= SNAPSHOT_BUDGET:
        retry_after = max(
            1,
            int((window_started + SNAPSHOT_BUDGET_WINDOW - now).total_seconds()),
        )
        raise HTTPException(
            429,
            "browser companion request budget exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    credential.accepted_in_window += 1
    credential.last_used_at = now
    return credential


@router.post(
    "/korail-snapshots",
    response_model=KorailBrowserSnapshotRead,
    status_code=201,
)
async def create_korail_snapshot(
    request: Request,
    response: Response,
    session: Session,
    principal: Annotated[BridgePrincipal, Depends(require_bridge_credential)],
    challenge: BridgeChallenge = None,
) -> KorailBrowserSnapshotRead:
    response.headers["Cache-Control"] = "no-store"
    observed_at = datetime.now(UTC)
    challenge_id = await _consume_snapshot_challenge(
        request=request,
        session=session,
        principal=principal,
        raw_challenge=challenge,
    )
    # Consume the one-time challenge even when the normalized payload is invalid.
    # This keeps malformed requests from accumulating reusable live challenges.
    await session.commit()
    try:
        data = KorailBrowserSnapshotCreate.model_validate_json(await request.body())
    except ValidationError as error:
        raise HTTPException(
            422,
            detail=error.errors(include_url=False, include_context=False),
        ) from None
    await _consume_snapshot_budget(session, principal, observed_at)
    fresh_until = observed_at + FRESHNESS
    batch = KorailBrowserSnapshotBatch(
        id=str(uuid.uuid4()),
        credential_id=principal.credential_id,
        challenge_id=challenge_id,
        origin=data.origin,
        destination=data.destination,
        travel_date=data.travel_date,
        passenger_count=data.passenger_count,
        source=SOURCE,
        observed_at=observed_at,
        fresh_until=fresh_until,
        created_at=observed_at,
    )
    session.add(batch)
    for train in data.trains:
        for seat_class, status in (
            (SeatClass.STANDARD, train.standard),
            (SeatClass.FIRST, train.first),
        ):
            session.add(
                KorailBrowserSeatSnapshot(
                    batch_id=batch.id,
                    train_number=train.train_number,
                    departure_at=train.departure_at,
                    seat_class=seat_class,
                    status=SeatObservationStatus(status),
                    created_at=observed_at,
                )
            )
    await session.commit()
    return KorailBrowserSnapshotRead(
        batch_id=batch.id,
        accepted_trains=len(data.trains),
        accepted_seats=len(data.trains) * 2,
        source=SOURCE,
        observed_at=observed_at,
        fresh_until=fresh_until,
    )


def _snapshot_key(
    train_number: str, departure_at: datetime, seat_class: object
) -> tuple[str, datetime, object]:
    return (
        normalize_official_train_number(train_number),
        _as_utc(departure_at).replace(microsecond=0),
        seat_class,
    )


async def overlay_korail_browser_snapshots(
    session: AsyncSession,
    items: list[TimetableItem],
    *,
    origin: str,
    destination: str,
    passenger_count: int,
    now: datetime | None = None,
) -> list[TimetableItem]:
    if not items:
        return items
    current = _as_utc(now or datetime.now(UTC))
    departures = [_as_utc(item.departure_at).replace(microsecond=0) for item in items]
    travel_dates = {item.departure_at.astimezone(KOREA).date() for item in items}
    rows = list(
        (
            await session.execute(
                select(KorailBrowserSeatSnapshot, KorailBrowserSnapshotBatch)
                .join(
                    KorailBrowserSnapshotBatch,
                    KorailBrowserSeatSnapshot.batch_id == KorailBrowserSnapshotBatch.id,
                )
                .where(
                    KorailBrowserSnapshotBatch.origin == origin.strip(),
                    KorailBrowserSnapshotBatch.destination == destination.strip(),
                    KorailBrowserSnapshotBatch.travel_date.in_(travel_dates),
                    KorailBrowserSnapshotBatch.passenger_count == passenger_count,
                    KorailBrowserSnapshotBatch.fresh_until > current,
                    KorailBrowserSeatSnapshot.departure_at >= min(departures),
                    KorailBrowserSeatSnapshot.departure_at <= max(departures),
                )
                .order_by(
                    KorailBrowserSnapshotBatch.observed_at.desc(),
                    KorailBrowserSeatSnapshot.created_at.desc(),
                    KorailBrowserSeatSnapshot.id.desc(),
                )
            )
        ).all()
    )
    snapshots: dict[
        tuple[str, datetime, object],
        tuple[KorailBrowserSeatSnapshot, KorailBrowserSnapshotBatch],
    ] = {}
    for snapshot, batch in rows:
        snapshots.setdefault(
            _snapshot_key(snapshot.train_number, snapshot.departure_at, snapshot.seat_class),
            (snapshot, batch),
        )

    result: list[TimetableItem] = []
    for item in items:
        if item.origin.strip() != origin.strip() or item.destination.strip() != destination.strip():
            result.append(item)
            continue
        seat_classes: list[SeatClassAvailability] = []
        for seat in item.seat_classes:
            matched = snapshots.get(
                _snapshot_key(item.train_number, item.departure_at, seat.seat_class)
            )
            if matched is None or seat.status != "unknown":
                seat_classes.append(seat)
                continue
            snapshot, batch = matched
            seat_classes.append(
                SeatClassAvailability.model_validate(
                    {
                        **seat.model_dump(),
                        "status": snapshot.status.value,
                        "provenance": SeatAvailabilityProvenance(
                            kind="official_page_browser_companion",
                            source=SOURCE,
                            observed_at=_as_utc(batch.observed_at),
                            fresh_until=_as_utc(batch.fresh_until),
                        ),
                        "actions": _seat_actions(snapshot.status.value, item.official_booking_url),
                    }
                )
            )
        result.append(item.model_copy(update={"seat_classes": seat_classes}))
    return result


def _seat_actions(status: str, official_booking_url: AnyHttpUrl) -> list[SeatAvailabilityAction]:
    if status in {"available", "limited", "standing_plus_seat"}:
        return [SeatAvailabilityAction(kind="official_check", url=official_booking_url)]
    if status == "sold_out":
        return [SeatAvailabilityAction(kind="add_to_watch")]
    if status == "waitlist_available":
        return [
            SeatAvailabilityAction(kind="official_waitlist", url=official_booking_url),
            SeatAvailabilityAction(kind="add_to_watch"),
        ]
    return []
