from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.auth import keyed_hash
from rail_waitlist.config import get_settings
from rail_waitlist.domain import Provider, SeatClass, SeatObservationStatus
from rail_waitlist.korail_browser_bridge import SOURCE, overlay_korail_browser_snapshots
from rail_waitlist.models import (
    BrowserCompanionCredential,
    KorailBrowserSeatSnapshot,
    KorailBrowserSnapshotBatch,
    OutboxEvent,
    ReservationAttempt,
    SeatObservation,
    WatchTransitionHistory,
)
from rail_waitlist.providers import MockProviderAdapter, official_unknown_seat_classes
from rail_waitlist.schemas import (
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)

ENDPOINT = "/api/v1/browser-bridge/korail-snapshots"
BRIDGE_TOKEN = "browser-bridge-test-token-value-32-chars"
BRIDGE_ORIGIN = f"chrome-extension://{'a' * 32}"
BRIDGE_CLIENT_ID = "11111111-1111-4111-8111-111111111111"


def snapshot_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "origin": "대전",
        "destination": "서울",
        "travel_date": "2030-07-30",
        "passenger_count": 1,
        "trains": [
            {
                "train_number": "00026",
                "departure_at": "2030-07-30T12:00:00+09:00",
                "standard": "sold_out",
                "first": "available",
            }
        ],
    }
    payload.update(overrides)
    return payload


def timetable_item(
    *,
    train_number: str = "26",
    origin: str = "대전",
    destination: str = "서울",
    departure_at: datetime | None = None,
    standard: SeatClassAvailability | None = None,
) -> TimetableItem:
    departure = departure_at or datetime.fromisoformat("2030-07-30T12:00:00+09:00")
    seats = official_unknown_seat_classes("https://www.korail.com")
    if standard is not None:
        seats = [standard, seats[1]]
    return TimetableItem(
        provider=Provider.KORAIL,
        train_number=train_number,
        train_type="KTX",
        origin=origin,
        destination=destination,
        departure_at=departure,
        arrival_at=departure + timedelta(hours=1),
        adult_fare=23_700,
        timetable_source="TAGO",
        timetable_retrieved_at=datetime.now(UTC),
        seat_classes=seats,
        official_booking_url="https://www.korail.com",
    )


def enable_bridge() -> bool:
    settings = get_settings()
    previous = settings.korail_browser_bridge_enabled
    settings.korail_browser_bridge_enabled = True
    return previous


def restore_bridge(previous: bool) -> None:
    get_settings().korail_browser_bridge_enabled = previous


async def seed_bridge_credential(app) -> None:
    async with app.state.test_session_factory() as session:
        session.add(
            BrowserCompanionCredential(
                token_hash=keyed_hash("browser-bridge-credential", BRIDGE_TOKEN),
                extension_origin=BRIDGE_ORIGIN,
                client_id=BRIDGE_CLIENT_ID,
                label="테스트 브라우저",
            )
        )
        await session.commit()


def credential_headers(**extra: str) -> dict[str, str]:
    return {
        "Origin": BRIDGE_ORIGIN,
        "X-Rail-Bridge-Token": BRIDGE_TOKEN,
        "X-Rail-Bridge-Client-Id": BRIDGE_CLIENT_ID,
        **extra,
    }


async def post_snapshot(public_client, payload: dict[str, object]):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    challenge = await public_client.post(
        "/api/v1/browser-bridge/challenges",
        json={"body_sha256": hashlib.sha256(body).hexdigest()},
        headers=credential_headers(),
    )
    assert challenge.status_code == 201, challenge.text
    return await public_client.post(
        ENDPOINT,
        content=body,
        headers=credential_headers(
            **{
                "Content-Type": "application/json",
                "X-Rail-Bridge-Challenge": challenge.json()["challenge"],
            }
        ),
    )


async def test_endpoint_is_disabled_by_default_and_requires_only_bridge_token(
    app, public_client, client
):
    settings = get_settings()
    configured = settings.korail_browser_bridge_enabled
    settings.korail_browser_bridge_enabled = False
    try:
        disabled = await public_client.post(
            ENDPOINT,
            json=snapshot_payload(),
            headers=credential_headers(),
        )
        settings.korail_browser_bridge_enabled = True
        await seed_bridge_credential(app)
        invalid_body_without_token = await public_client.post(
            ENDPOINT, json={"raw_html": "must not reach body validation"}
        )
        missing = await public_client.post(ENDPOINT, json=snapshot_payload())
        wrong = await public_client.post(
            ENDPOINT,
            json=snapshot_payload(),
            headers={
                **credential_headers(),
                "X-Rail-Bridge-Token": "wrong-token-value-that-is-long-enough",
            },
        )
        admin_without_bridge_token = await client.post(ENDPOINT, json=snapshot_payload())
        accepted = await post_snapshot(public_client, snapshot_payload())
    finally:
        settings.korail_browser_bridge_enabled = configured

    assert disabled.status_code == 404
    assert invalid_body_without_token.status_code == 401
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert admin_without_bridge_token.status_code == 401
    assert accepted.status_code == 201, accepted.text
    assert accepted.headers["Cache-Control"] == "no-store"


async def test_endpoint_accepts_only_normalized_snapshot_contract(app, public_client, db_engine):
    previous = enable_bridge()
    try:
        await seed_bridge_credential(app)
        response = await post_snapshot(public_client, snapshot_payload())
    finally:
        restore_bridge(previous)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["accepted_trains"] == 1
    assert body["accepted_seats"] == 2
    assert body["source"] == SOURCE
    observed_at = datetime.fromisoformat(body["observed_at"])
    fresh_until = datetime.fromisoformat(body["fresh_until"])
    assert fresh_until - observed_at == timedelta(minutes=2)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        batches = list((await session.scalars(select(KorailBrowserSnapshotBatch))).all())
        snapshots = list((await session.scalars(select(KorailBrowserSeatSnapshot))).all())
        snapshot_count = await session.scalar(
            select(func.count()).select_from(KorailBrowserSeatSnapshot)
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

    assert len(batches) == 1
    assert snapshot_count == 2
    assert side_effect_counts == [0, 0, 0, 0]
    assert {snapshot.seat_class for snapshot in snapshots} == {
        SeatClass.STANDARD,
        SeatClass.FIRST,
    }
    assert {snapshot.status.value for snapshot in snapshots} == {"sold_out", "available"}
    assert batches[0].source == SOURCE


async def test_endpoint_accepts_and_persists_standing_plus_seat(app, public_client, db_engine):
    previous = enable_bridge()
    try:
        await seed_bridge_credential(app)
        response = await post_snapshot(
            public_client,
            snapshot_payload(
                trains=[
                    {
                        "train_number": "26",
                        "departure_at": "2030-07-30T12:00:00+09:00",
                        "standard": "standing_plus_seat",
                        "first": "not_offered",
                    }
                ]
            ),
        )
    finally:
        restore_bridge(previous)

    assert response.status_code == 201, response.text
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        statuses = {
            snapshot.status.value
            for snapshot in (await session.scalars(select(KorailBrowserSeatSnapshot))).all()
        }
    assert statuses == {"standing_plus_seat", "not_offered"}


async def test_endpoint_rejects_raw_secrets_protection_markers_and_ambiguous_identity(
    app,
    public_client,
):
    invalid_payloads = [
        snapshot_payload(raw_html="<html>secret</html>"),
        snapshot_payload(cookie="session=secret"),
        snapshot_payload(token="provider-secret"),
        snapshot_payload(origin="CODE -8003"),
        snapshot_payload(destination="macro_err1"),
        snapshot_payload(
            trains=[
                {
                    "train_number": "CAPTCHA",
                    "departure_at": "2030-07-30T12:00:00+09:00",
                    "standard": "available",
                    "first": "sold_out",
                }
            ]
        ),
        snapshot_payload(
            trains=[
                {
                    "train_number": "26",
                    "departure_at": "2030-07-30T12:00:00",
                    "standard": "available",
                    "first": "sold_out",
                }
            ]
        ),
        snapshot_payload(
            trains=[
                {
                    "train_number": "26",
                    "departure_at": "2030-07-31T12:00:00+09:00",
                    "standard": "available",
                    "first": "sold_out",
                }
            ]
        ),
        snapshot_payload(
            trains=[
                {
                    "train_number": "26",
                    "departure_at": "2030-07-30T12:00:00+09:00",
                    "standard": "unknown",
                    "first": "sold_out",
                }
            ]
        ),
        snapshot_payload(
            trains=[
                {
                    "train_number": "26",
                    "departure_at": "2030-07-30T12:00:00+09:00",
                    "standard": "available",
                    "first": "sold_out",
                    "response": {"raw": "secret"},
                }
            ]
        ),
        snapshot_payload(
            trains=[
                {
                    "train_number": str(index),
                    "departure_at": "2030-07-30T12:00:00+09:00",
                    "standard": "available",
                    "first": "sold_out",
                }
                for index in range(101)
            ]
        ),
    ]
    previous = enable_bridge()
    try:
        await seed_bridge_credential(app)
        responses = [await post_snapshot(public_client, payload) for payload in invalid_payloads]
    finally:
        restore_bridge(previous)

    assert all(response.status_code == 422 for response in responses)


async def test_overlay_uses_latest_exact_fresh_batch_and_preserves_observed_status(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    departure = datetime.fromisoformat("2030-07-30T12:00:00+09:00")
    departure_utc = departure.astimezone(UTC)
    observed_standard = SeatClassAvailability(
        seat_class=SeatClass.STANDARD,
        status="available",
        provenance=SeatAvailabilityProvenance(
            kind="official_provider",
            source="authorized-provider",
            observed_at=now,
        ),
    )

    async with factory() as session:
        older = KorailBrowserSnapshotBatch(
            origin="대전",
            destination="서울",
            travel_date=departure.date(),
            passenger_count=1,
            source=SOURCE,
            observed_at=now - timedelta(seconds=10),
            fresh_until=now + timedelta(minutes=1),
            created_at=now - timedelta(seconds=10),
        )
        newer = KorailBrowserSnapshotBatch(
            origin="대전",
            destination="서울",
            travel_date=departure.date(),
            passenger_count=1,
            source=SOURCE,
            observed_at=now,
            fresh_until=now + timedelta(minutes=2),
            created_at=now,
        )
        session.add_all([older, newer])
        await session.flush()
        for batch, status in ((older, "sold_out"), (newer, "limited")):
            for seat_class in (SeatClass.STANDARD, SeatClass.FIRST):
                session.add(
                    KorailBrowserSeatSnapshot(
                        batch_id=batch.id,
                        train_number="26",
                        departure_at=departure_utc,
                        seat_class=seat_class,
                        status=SeatObservationStatus(status),
                        created_at=batch.created_at,
                    )
                )
        await session.commit()

        exact = await overlay_korail_browser_snapshots(
            session,
            [timetable_item(standard=observed_standard)],
            origin="대전",
            destination="서울",
            passenger_count=1,
            now=now,
        )
        wrong_route = await overlay_korail_browser_snapshots(
            session,
            [timetable_item()],
            origin="대전",
            destination="부산",
            passenger_count=1,
            now=now,
        )
        wrong_passengers = await overlay_korail_browser_snapshots(
            session,
            [timetable_item()],
            origin="대전",
            destination="서울",
            passenger_count=2,
            now=now,
        )
        stale = await overlay_korail_browser_snapshots(
            session,
            [timetable_item()],
            origin="대전",
            destination="서울",
            passenger_count=1,
            now=now + timedelta(minutes=3),
        )
        expires_now = await overlay_korail_browser_snapshots(
            session,
            [timetable_item()],
            origin="대전",
            destination="서울",
            passenger_count=1,
            now=now + timedelta(minutes=2),
        )
        mixed_routes = await overlay_korail_browser_snapshots(
            session,
            [timetable_item(), timetable_item(origin="부산")],
            origin="대전",
            destination="서울",
            passenger_count=1,
            now=now,
        )

    standard, first = exact[0].seat_classes
    assert standard.status == "available"
    assert standard.provenance.source == "authorized-provider"
    assert first.status == "limited"
    assert first.provenance.kind == "official_page_browser_companion"
    assert first.provenance.source == SOURCE
    assert first.provenance.observed_at == now
    assert first.provenance.fresh_until == now + timedelta(minutes=2)
    assert wrong_route[0].seat_classes[0].status == "unknown"
    assert wrong_passengers[0].seat_classes[0].status == "unknown"
    assert stale[0].seat_classes[0].status == "unknown"
    assert expires_now[0].seat_classes[0].status == "unknown"
    assert mixed_routes[0].seat_classes[0].status == "limited"
    assert mixed_routes[1].seat_classes[0].status == "unknown"


async def test_overlay_recalculates_actions_for_every_bridge_status(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    departure = datetime.fromisoformat("2030-07-30T12:00:00+09:00")
    statuses = (
        "available",
        "limited",
        "standing_plus_seat",
        "sold_out",
        "waitlist_available",
        "not_offered",
    )
    batch = KorailBrowserSnapshotBatch(
        origin="대전",
        destination="서울",
        travel_date=departure.date(),
        passenger_count=1,
        source=SOURCE,
        observed_at=now,
        fresh_until=now + timedelta(minutes=2),
        created_at=now,
    )
    items = [timetable_item(train_number=str(index + 100)) for index in range(len(statuses))]

    async with factory() as session:
        session.add(batch)
        await session.flush()
        for index, status in enumerate(statuses):
            session.add(
                KorailBrowserSeatSnapshot(
                    batch_id=batch.id,
                    train_number=str(index + 100),
                    departure_at=departure.astimezone(UTC),
                    seat_class=SeatClass.STANDARD,
                    status=SeatObservationStatus(status),
                    created_at=now,
                )
            )
        await session.commit()
        overlaid = await overlay_korail_browser_snapshots(
            session,
            items,
            origin="대전",
            destination="서울",
            passenger_count=1,
            now=now,
        )

    expected = {
        "available": ["official_check"],
        "limited": ["official_check"],
        "standing_plus_seat": ["official_check"],
        "sold_out": ["add_to_watch"],
        "waitlist_available": ["official_waitlist", "add_to_watch"],
        "not_offered": [],
    }
    for item, status in zip(overlaid, statuses, strict=True):
        standard = item.seat_classes[0]
        assert standard.status == status
        assert [action.kind for action in standard.actions] == expected[status]
        for action in standard.actions:
            if action.url is not None:
                assert action.url.host == "www.korail.com"


async def test_korail_timetable_endpoint_overlays_browser_snapshot(
    app, public_client, client, monkeypatch
):
    from rail_waitlist.timetable_management import application as timetable_application

    class LocalKorailTimetable(MockProviderAdapter):
        async def timetable(self, *args, **kwargs):
            return [timetable_item()]

    monkeypatch.setattr(
        timetable_application, "get_timetable_provider", lambda provider: LocalKorailTimetable()
    )
    previous = enable_bridge()
    try:
        await seed_bridge_credential(app)
        stored = await post_snapshot(public_client, snapshot_payload())
    finally:
        restore_bridge(previous)
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": "korail",
            "origin": "대전",
            "destination": "서울",
            "origin_node_id": "0010",
            "destination_node_id": "0001",
            "departure_from": "2030-07-30T12:00:00+09:00",
            "departure_to": "2030-07-30T13:00:00+09:00",
            "passenger_count": 1,
        },
    )

    assert stored.status_code == 201, stored.text
    assert response.status_code == 200, response.text
    seats = response.json()[0]["seat_classes"]
    assert [(seat["seat_class"], seat["status"]) for seat in seats] == [
        ("standard", "sold_out"),
        ("first", "available"),
    ]
    assert all(seat["provenance"]["kind"] == "official_page_browser_companion" for seat in seats)
    assert all(seat["provenance"]["source"] == SOURCE for seat in seats)
