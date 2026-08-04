from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from rail_waitlist.config import get_settings
from rail_waitlist.models import (
    BrowserCompanionChallenge,
    BrowserCompanionCredential,
    BrowserCompanionPairing,
    KorailBrowserSnapshotBatch,
    OutboxEvent,
    ReservationAttempt,
    SeatObservation,
    WatchTransitionHistory,
)

EXTENSION_ORIGIN = f"chrome-extension://{'b' * 32}"
CLIENT_ID = "33333333-3333-4333-8333-333333333333"
SNAPSHOT_ENDPOINT = "/api/v1/browser-bridge/korail-snapshots"


def payload() -> dict[str, object]:
    return {
        "origin": "대전",
        "destination": "서울",
        "travel_date": "2030-07-30",
        "passenger_count": 1,
        "trains": [
            {
                "train_number": "26",
                "departure_at": "2030-07-30T12:00:00+09:00",
                "standard": "sold_out",
                "first": "available",
            }
        ],
    }


async def pair(client, public_client) -> tuple[dict[str, object], str]:
    issued = await client.post(
        "/api/v1/browser-companion/pairings",
        json={"label": "Chrome 데스크톱"},
    )
    assert issued.status_code == 201, issued.text
    code = issued.json()["pairing_code"]
    exchanged = await public_client.post(
        "/api/v1/browser-bridge/pair",
        json={"pairing_code": code, "client_id": CLIENT_ID},
        headers={"Origin": EXTENSION_ORIGIN},
    )
    assert exchanged.status_code == 200, exchanged.text
    return exchanged.json(), code


def credential_headers(token: str, **extra: str) -> dict[str, str]:
    return {
        "Origin": EXTENSION_ORIGIN,
        "X-Rail-Bridge-Token": token,
        "X-Rail-Bridge-Client-Id": CLIENT_ID,
        **extra,
    }


async def challenged_snapshot(
    public_client,
    token: str,
    data: dict[str, object],
    extra_headers: dict[str, str] | None = None,
):
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    challenge = await public_client.post(
        "/api/v1/browser-bridge/challenges",
        json={"body_sha256": hashlib.sha256(body).hexdigest()},
        headers=credential_headers(token, **(extra_headers or {})),
    )
    assert challenge.status_code == 201, challenge.text
    raw_challenge = challenge.json()["challenge"]
    response = await public_client.post(
        SNAPSHOT_ENDPOINT,
        content=body,
        headers=credential_headers(
            token,
            **{
                **(extra_headers or {}),
                "Content-Type": "application/json",
                "X-Rail-Bridge-Challenge": raw_challenge,
            },
        ),
    )
    return response, raw_challenge


async def test_admin_pairing_is_one_time_and_stores_only_hashes(app, client, public_client):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        result, pairing_code = await pair(client, public_client)
        replay = await public_client.post(
            "/api/v1/browser-bridge/pair",
            json={"pairing_code": pairing_code, "client_id": CLIENT_ID},
            headers={"Origin": EXTENSION_ORIGIN},
        )
        status = await client.get("/api/v1/browser-companion/status")
        async with app.state.test_session_factory() as session:
            pairing = await session.scalar(select(BrowserCompanionPairing))
            credential = await session.scalar(select(BrowserCompanionCredential))
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert replay.status_code == 410
    assert status.status_code == 200
    assert status.headers["Cache-Control"] == "no-store"
    assert status.json()["credentials"][0]["label"] == "Chrome 데스크톱"
    assert pairing is not None and credential is not None
    assert pairing.consumed_at is not None
    assert pairing_code not in pairing.code_hash
    assert result["bridge_token"] not in credential.token_hash
    assert credential.extension_origin == EXTENSION_ORIGIN


async def test_pairing_requires_admin_csrf_and_extension_origin(client, public_client):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        unauthenticated = await public_client.post(
            "/api/v1/browser-companion/pairings", json={"label": "실패"}
        )
        issued = await client.post(
            "/api/v1/browser-companion/pairings", json={"label": "Origin 검사"}
        )
        invalid_origin = await public_client.post(
            "/api/v1/browser-bridge/pair",
            json={
                "pairing_code": issued.json()["pairing_code"],
                "client_id": CLIENT_ID,
            },
        )
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert unauthenticated.status_code == 401
    assert issued.status_code == 201
    assert invalid_origin.status_code == 403


async def test_challenge_is_body_bound_one_time_and_revoke_stops_access(app, client, public_client):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        result, _ = await pair(client, public_client)
        token = str(result["bridge_token"])
        accepted, raw_challenge = await challenged_snapshot(public_client, token, payload())
        replay = await public_client.post(
            SNAPSHOT_ENDPOINT,
            json=payload(),
            headers=credential_headers(token, **{"X-Rail-Bridge-Challenge": raw_challenge}),
        )
        revoked = await client.delete(
            f"/api/v1/browser-companion/credentials/{result['credential_id']}"
        )
        after_revoke = await public_client.post(
            "/api/v1/browser-bridge/challenges",
            json={"body_sha256": "0" * 64},
            headers=credential_headers(token),
        )
        async with app.state.test_session_factory() as session:
            batch_count = await session.scalar(
                select(func.count()).select_from(KorailBrowserSnapshotBatch)
            )
            challenge_count = await session.scalar(
                select(func.count()).select_from(BrowserCompanionChallenge)
            )
            side_effect_counts = [
                await session.scalar(select(func.count()).select_from(model))
                for model in (
                    SeatObservation,
                    ReservationAttempt,
                    WatchTransitionHistory,
                    OutboxEvent,
                )
            ]
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert accepted.status_code == 201, accepted.text
    assert replay.status_code == 409
    assert revoked.status_code == 204
    assert after_revoke.status_code == 401
    assert batch_count == 1
    assert challenge_count == 1
    assert side_effect_counts == [0, 0, 0, 0]


async def test_expired_or_body_mismatched_challenge_is_rejected(app, client, public_client):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        result, _ = await pair(client, public_client)
        token = str(result["bridge_token"])
        body = json.dumps(payload(), ensure_ascii=False, separators=(",", ":")).encode()
        challenge = await public_client.post(
            "/api/v1/browser-bridge/challenges",
            json={"body_sha256": hashlib.sha256(body).hexdigest()},
            headers=credential_headers(token),
        )
        raw = challenge.json()["challenge"]
        mismatched = await public_client.post(
            SNAPSHOT_ENDPOINT,
            json={**payload(), "passenger_count": 2},
            headers=credential_headers(token, **{"X-Rail-Bridge-Challenge": raw}),
        )

        second = await public_client.post(
            "/api/v1/browser-bridge/challenges",
            json={"body_sha256": hashlib.sha256(body).hexdigest()},
            headers=credential_headers(token),
        )
        async with app.state.test_session_factory() as session:
            row = await session.scalar(
                select(BrowserCompanionChallenge)
                .where(
                    BrowserCompanionChallenge.challenge_hash.is_not(None),
                    BrowserCompanionChallenge.consumed_at.is_(None),
                )
                .order_by(BrowserCompanionChallenge.created_at.desc())
            )
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        expired = await public_client.post(
            SNAPSHOT_ENDPOINT,
            content=body,
            headers=credential_headers(
                token,
                **{
                    "Content-Type": "application/json",
                    "X-Rail-Bridge-Challenge": second.json()["challenge"],
                },
            ),
        )
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert mismatched.status_code == 403
    assert expired.status_code == 410


async def test_credential_snapshot_budget_is_enforced(client, public_client):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        result, _ = await pair(client, public_client)
        token = str(result["bridge_token"])
        responses = [
            (await challenged_snapshot(public_client, token, payload()))[0] for _ in range(7)
        ]
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert [response.status_code for response in responses[:6]] == [201] * 6
    assert responses[6].status_code == 429
    assert int(responses[6].headers["Retry-After"]) >= 1


async def test_credential_snapshot_budget_ignores_rotating_forwarded_for(client, public_client):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        result, _ = await pair(client, public_client)
        token = str(result["bridge_token"])
        responses = [
            (
                await challenged_snapshot(
                    public_client,
                    token,
                    payload(),
                    {"X-Forwarded-For": f"198.51.100.{index}"},
                )
            )[0]
            for index in range(1, 8)
        ]
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert [response.status_code for response in responses[:6]] == [201] * 6
    assert responses[6].status_code == 429
    assert int(responses[6].headers["Retry-After"]) >= 1


async def test_body_bound_challenge_is_atomically_consumed_under_concurrent_submission(
    client, public_client
):
    previous = get_settings().korail_browser_bridge_enabled
    get_settings().korail_browser_bridge_enabled = True
    try:
        result, _ = await pair(client, public_client)
        token = str(result["bridge_token"])
        body = json.dumps(payload(), ensure_ascii=False, separators=(",", ":")).encode()
        challenge = await public_client.post(
            "/api/v1/browser-bridge/challenges",
            json={"body_sha256": hashlib.sha256(body).hexdigest()},
            headers=credential_headers(token),
        )
        assert challenge.status_code == 201, challenge.text
        headers = credential_headers(
            token,
            **{
                "Content-Type": "application/json",
                "X-Rail-Bridge-Challenge": challenge.json()["challenge"],
            },
        )
        first, second = await asyncio.gather(
            public_client.post(SNAPSHOT_ENDPOINT, content=body, headers=headers),
            public_client.post(SNAPSHOT_ENDPOINT, content=body, headers=headers),
        )
    finally:
        get_settings().korail_browser_bridge_enabled = previous

    assert sorted((first.status_code, second.status_code)) == [201, 409]
