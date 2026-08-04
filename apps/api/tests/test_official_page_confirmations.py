from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.models import OfficialPageSeatConfirmation
from rail_waitlist.official_page_confirmations import (
    overlay_official_page_confirmations,
    upsert_official_page_confirmations,
)
from rail_waitlist.providers import MockProviderAdapter, official_unknown_seat_classes
from rail_waitlist.schemas import (
    OfficialPageSeatConfirmationCreate,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)

SOURCE = "official-page-user-confirmation"


def confirmation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "korail",
        "origin_node_id": "0010",
        "destination_node_id": "0001",
        "train_number": "00026",
        "departure_at": "2026-07-30T12:00:00+09:00",
        "passenger_count": 1,
        "seat_classes": [
            {"seat_class": "standard", "status": "sold_out"},
            {"seat_class": "first", "status": "available"},
        ],
    }
    payload.update(overrides)
    return payload


def timetable_item(
    *,
    train_number: str = "26",
    departure_at: datetime | None = None,
    standard: SeatClassAvailability | None = None,
) -> TimetableItem:
    departure = departure_at or datetime.fromisoformat("2026-07-30T12:00:00+09:00")
    seats = official_unknown_seat_classes("https://www.korail.com")
    if standard is not None:
        seats = [standard, seats[1]]
    return TimetableItem(
        provider=Provider.KORAIL,
        train_number=train_number,
        train_type="KTX",
        origin="대전",
        destination="서울",
        departure_at=departure,
        arrival_at=departure + timedelta(hours=1, minutes=4),
        adult_fare=23_700,
        timetable_source="TAGO",
        timetable_retrieved_at=datetime.now(UTC),
        seat_classes=seats,
        official_booking_url="https://www.korail.com",
    )


async def test_endpoint_is_admin_only_and_rejects_raw_or_unmapped_input(public_client, client):
    endpoint = "/api/v1/seat-observations/official-page-confirmations"
    unauthenticated = await public_client.post(endpoint, json=confirmation_payload())
    missing_csrf = await client.post(
        endpoint,
        json=confirmation_payload(),
        headers={"X-CSRF-Token": ""},
    )
    raw_page = await client.post(
        endpoint, json=confirmation_payload(raw_html="<html>secret</html>")
    )
    client_evidence = await client.post(
        endpoint,
        json=confirmation_payload(
            source=SOURCE,
            observed_at=datetime.now(UTC).isoformat(),
        ),
    )
    blocked = await client.post(
        endpoint,
        json=confirmation_payload(
            seat_classes=[{"seat_class": "standard", "status": "provider_blocked"}]
        ),
    )
    unknown = await client.post(
        endpoint,
        json=confirmation_payload(seat_classes=[{"seat_class": "standard", "status": "unknown"}]),
    )
    formerly_broad_statuses = [
        await client.post(
            endpoint,
            json=confirmation_payload(seat_classes=[{"seat_class": "standard", "status": status}]),
        )
        for status in ("unavailable", "limited", "standing_plus_seat", "not_enough_seats")
    ]

    assert unauthenticated.status_code == 401
    assert missing_csrf.status_code == 403
    assert raw_page.status_code == 422
    assert client_evidence.status_code == 422
    assert blocked.status_code == 422
    assert unknown.status_code == 422
    assert all(response.status_code == 422 for response in formerly_broad_statuses)


async def test_endpoint_rejects_ambiguous_identity_or_non_atomic_seats(client):
    endpoint = "/api/v1/seat-observations/official-page-confirmations"
    invalid_payloads = [
        confirmation_payload(provider="mock"),
        confirmation_payload(passenger_count=0),
        confirmation_payload(departure_at="2026-07-30T12:00:00"),
        confirmation_payload(train_number="  "),
        confirmation_payload(train_number="CODE -8003"),
        confirmation_payload(seat_classes=[]),
        confirmation_payload(
            seat_classes=[
                {"seat_class": "standard", "status": "available"},
                {"seat_class": "standard", "status": "sold_out"},
            ]
        ),
        confirmation_payload(seat_classes=[{"seat_class": "any", "status": "available"}]),
        confirmation_payload(
            seat_classes=[
                {
                    "seat_class": "standard",
                    "status": "available",
                    "error_message": "CODE -8003",
                }
            ]
        ),
    ]

    responses = [await client.post(endpoint, json=payload) for payload in invalid_payloads]
    assert all(response.status_code == 422 for response in responses)


async def test_atomic_batch_uses_server_time_and_idempotency_key(client, db_engine):
    endpoint = "/api/v1/seat-observations/official-page-confirmations"
    before = datetime.now(UTC).replace(microsecond=0)
    headers = {"Idempotency-Key": "official-page-confirmation-26"}
    first = await client.post(endpoint, json=confirmation_payload(), headers=headers)
    second = await client.post(endpoint, json=confirmation_payload(), headers=headers)
    after = datetime.now(UTC) + timedelta(seconds=1)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["train_number"] == "26"
    assert first_body["passenger_count"] == 1
    assert first_body["created_count"] == 2
    assert first_body["replayed"] is False
    assert second_body["created_count"] == 0
    assert second_body["replayed"] is True
    assert second_body["seat_classes"] == first_body["seat_classes"]
    assert second_body["observed_at"] == first_body["observed_at"]
    assert first_body["source"] == SOURCE
    assert first.headers["Cache-Control"] == "no-store"
    assert first_body["provenance_kind"] == "user_confirmed_official_page"
    observed_at = datetime.fromisoformat(first_body["observed_at"])
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    assert before <= observed_at <= after

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = list((await session.scalars(select(OfficialPageSeatConfirmation))).all())
        assert len(rows) == 2
        assert {row.seat_class for row in rows} == {SeatClass.STANDARD, SeatClass.FIRST}
        assert len({row.observed_at for row in rows}) == 1


async def test_idempotency_key_reuse_with_different_batch_is_rejected(client):
    endpoint = "/api/v1/seat-observations/official-page-confirmations"
    headers = {"Idempotency-Key": "official-page-confirmation-conflict"}
    first = await client.post(endpoint, json=confirmation_payload(), headers=headers)
    conflict = await client.post(
        endpoint,
        json=confirmation_payload(passenger_count=2),
        headers=headers,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409


async def test_idempotency_replay_returns_immutable_original_batch_after_new_revision(
    client, db_engine
):
    endpoint = "/api/v1/seat-observations/official-page-confirmations"
    original_payload = confirmation_payload()
    revised_payload = confirmation_payload(
        seat_classes=[
            {"seat_class": "standard", "status": "available"},
            {"seat_class": "first", "status": "not_offered"},
        ]
    )
    original = await client.post(
        endpoint,
        json=original_payload,
        headers={"Idempotency-Key": "immutable-original"},
    )
    revised = await client.post(
        endpoint,
        json=revised_payload,
        headers={"Idempotency-Key": "immutable-revision"},
    )
    replay = await client.post(
        endpoint,
        json=original_payload,
        headers={"Idempotency-Key": "immutable-original"},
    )

    assert original.status_code == revised.status_code == replay.status_code == 201
    assert original.json()["replayed"] is False
    assert revised.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    for field in ("seat_classes", "observed_at", "fresh_until"):
        assert replay.json()[field] == original.json()[field]
    assert replay.json()["seat_classes"] != revised.json()["seat_classes"]

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = list((await session.scalars(select(OfficialPageSeatConfirmation))).all())
    assert len(rows) == 4
    assert len({row.batch_id for row in rows}) == 2


async def test_timetable_endpoint_overlays_exact_confirmation_batch(client, monkeypatch):
    from rail_waitlist import api

    class LocalKorailTimetable(MockProviderAdapter):
        async def timetable(self, *args, **kwargs):
            return [timetable_item()]

    monkeypatch.setattr(api, "get_timetable_provider", lambda provider: LocalKorailTimetable())
    stored = await client.post(
        "/api/v1/seat-observations/official-page-confirmations",
        json=confirmation_payload(),
    )
    response = await client.get(
        "/api/v1/timetables",
        params={
            "provider": "korail",
            "origin": "대전",
            "destination": "서울",
            "origin_node_id": "0010",
            "destination_node_id": "0001",
            "departure_from": "2026-07-30T12:00:00+09:00",
            "departure_to": "2026-07-30T13:00:00+09:00",
            "passenger_count": 1,
        },
    )

    assert stored.status_code == 201, stored.text
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    seats = response.json()[0]["seat_classes"]
    assert [(seat["seat_class"], seat["status"]) for seat in seats] == [
        ("standard", "sold_out"),
        ("first", "available"),
    ]
    assert all(seat["provenance"]["kind"] == "user_confirmed_official_page" for seat in seats)
    assert all(seat["provenance"]["fresh_until"] for seat in seats)


async def test_fresh_exact_batch_overlays_unknown_by_passenger_count(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    data = OfficialPageSeatConfirmationCreate.model_validate(confirmation_payload())
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
        await upsert_official_page_confirmations(session, data, now=now)
        await session.commit()
        exact = await overlay_official_page_confirmations(
            session,
            [timetable_item()],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=now,
        )
        wrong_passengers = await overlay_official_page_confirmations(
            session,
            [timetable_item()],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=2,
            now=now,
        )
        already_observed = await overlay_official_page_confirmations(
            session,
            [timetable_item(standard=observed_standard)],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=now,
        )
        stale = await overlay_official_page_confirmations(
            session,
            [timetable_item()],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=now + timedelta(minutes=6),
        )

    standard, first = exact[0].seat_classes
    assert standard.status == "sold_out"
    assert first.status == "available"
    assert standard.provenance.kind == "user_confirmed_official_page"
    assert standard.provenance.source == SOURCE
    assert standard.provenance.observed_at == now
    assert standard.provenance.fresh_until == now + timedelta(minutes=5)
    assert wrong_passengers[0].seat_classes[0].status == "unknown"
    assert already_observed[0].seat_classes[0].status == "available"
    assert already_observed[0].seat_classes[0].provenance.kind == "official_provider"
    assert stale[0].seat_classes[0].status == "unknown"


async def test_overlay_selects_latest_append_only_revision(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    original = OfficialPageSeatConfirmationCreate.model_validate(confirmation_payload())
    revised = OfficialPageSeatConfirmationCreate.model_validate(
        confirmation_payload(
            seat_classes=[
                {"seat_class": "standard", "status": "available"},
                {"seat_class": "first", "status": "not_offered"},
            ]
        )
    )

    async with factory() as session:
        await upsert_official_page_confirmations(session, original, now=now)
        await upsert_official_page_confirmations(session, revised, now=now + timedelta(seconds=1))
        await session.commit()
        result = await overlay_official_page_confirmations(
            session,
            [timetable_item()],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=now + timedelta(seconds=2),
        )
        rows = list((await session.scalars(select(OfficialPageSeatConfirmation))).all())

    assert [seat.status for seat in result[0].seat_classes] == ["available", "not_offered"]
    assert len(rows) == 4
    assert len({row.batch_id for row in rows}) == 2


async def test_overlay_recalculates_actions_for_each_confirmation_status(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    statuses = ("available", "sold_out", "waitlist_available", "not_offered")
    items = [timetable_item(train_number=str(index + 100)) for index in range(len(statuses))]

    async with factory() as session:
        for index, status in enumerate(statuses):
            data = OfficialPageSeatConfirmationCreate.model_validate(
                confirmation_payload(
                    train_number=str(index + 100),
                    seat_classes=[{"seat_class": "standard", "status": status}],
                )
            )
            await upsert_official_page_confirmations(
                session,
                data,
                now=now + timedelta(microseconds=index),
            )
        await session.commit()
        overlaid = await overlay_official_page_confirmations(
            session,
            items,
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=now,
        )

    expected = {
        "available": ["official_check", "add_to_watch"],
        "sold_out": ["add_to_watch"],
        "waitlist_available": ["official_waitlist", "add_to_watch"],
        "not_offered": ["official_check"],
    }
    for item, status in zip(overlaid, statuses, strict=True):
        standard = item.seat_classes[0]
        assert standard.status == status
        assert [action.kind for action in standard.actions] == expected[status]
        for action in standard.actions:
            if action.url is not None:
                assert action.url.host == "www.korail.com"


async def test_same_train_and_seat_are_distinct_for_each_passenger_count(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    one = OfficialPageSeatConfirmationCreate.model_validate(
        confirmation_payload(
            passenger_count=1,
            seat_classes=[{"seat_class": "standard", "status": "available"}],
        )
    )
    two = OfficialPageSeatConfirmationCreate.model_validate(
        confirmation_payload(
            passenger_count=2,
            seat_classes=[{"seat_class": "standard", "status": "sold_out"}],
        )
    )

    async with factory() as session:
        await upsert_official_page_confirmations(session, one)
        await upsert_official_page_confirmations(session, two)
        await session.commit()
        rows = list(
            (
                await session.scalars(
                    select(OfficialPageSeatConfirmation).order_by(
                        OfficialPageSeatConfirmation.passenger_count
                    )
                )
            ).all()
        )

    assert [(row.passenger_count, row.status.value) for row in rows] == [
        (1, "available"),
        (2, "sold_out"),
    ]


def test_model_stores_no_raw_page_or_transport_fields():
    assert set(OfficialPageSeatConfirmation.__table__.columns.keys()) == {
        "id",
        "batch_id",
        "provider",
        "origin_node_id",
        "destination_node_id",
        "train_number",
        "departure_at",
        "passenger_count",
        "seat_class",
        "status",
        "source",
        "observed_at",
        "fresh_until",
        "created_at",
    }
    for forbidden in ("cookie", "token", "payload", "raw_html", "error_message"):
        assert forbidden not in OfficialPageSeatConfirmation.__table__.columns


def test_user_confirmation_provenance_requires_valid_freshness_window():
    now = datetime.now(UTC)
    missing = {
        "kind": "user_confirmed_official_page",
        "source": SOURCE,
        "observed_at": now,
    }
    invalid = {**missing, "fresh_until": now - timedelta(seconds=1)}
    equal = {**missing, "fresh_until": now}
    wrong_source = {
        **missing,
        "source": "official-provider-feed",
        "fresh_until": now + timedelta(minutes=1),
    }

    for payload in (missing, invalid, equal, wrong_source):
        try:
            SeatAvailabilityProvenance.model_validate(payload)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid user confirmation freshness was accepted")
