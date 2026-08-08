from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import Provider, SeatClass, SeatObservationStatus
from rail_waitlist.models import (
    OutboxEvent,
    ReservationAttempt,
    SeatObservation,
    WatchCandidate,
)
from rail_waitlist.providers import MockProviderAdapter, official_unknown_seat_classes
from rail_waitlist.schemas import (
    SeatAvailabilityAction,
    SeatAvailabilityProvenance,
    SeatClassAvailability,
    TimetableItem,
)
from rail_waitlist.timetable_evidence import persist_timetable_seat_evidence
from rail_waitlist.timetable_management import application as timetable_application
from rail_waitlist.timetable_management.models import TimetableSeatEvidence
from rail_waitlist.worker import _process_due_watches

DEPARTURE = datetime(2030, 8, 1, 8, 30, tzinfo=ZoneInfo("Asia/Seoul"))
OFFICIAL_URL = "https://www.korail.com/ticket/search"


def timetable_item(*, observed: bool = False) -> TimetableItem:
    now = datetime.now(UTC).replace(microsecond=0)
    seats = official_unknown_seat_classes(OFFICIAL_URL, reason="source_not_configured")
    if observed:
        seats = [
            SeatClassAvailability(
                seat_class=seat_class,
                status=status,
                provenance=SeatAvailabilityProvenance(
                    kind="official_provider",
                    source="authorized-provider",
                    observed_at=now,
                ),
                actions=[SeatAvailabilityAction(kind="add_to_watch")],
            )
            for seat_class, status in (
                (SeatClass.STANDARD, "sold_out"),
                (SeatClass.FIRST, "available"),
            )
        ]
    return TimetableItem(
        provider=Provider.KORAIL,
        train_number="00026",
        train_type="KTX",
        origin="대전",
        destination="서울",
        departure_at=DEPARTURE,
        arrival_at=DEPARTURE + timedelta(hours=1),
        timetable_source="TAGO",
        timetable_retrieved_at=now,
        seat_classes=seats,
        official_booking_url=OFFICIAL_URL,
    )


class StaticOfficialAdapter(MockProviderAdapter):
    def __init__(self, item: TimetableItem) -> None:
        self.item = item

    async def timetable(self, *args, **kwargs):
        return [self.item]


def enable_watch_registration(monkeypatch) -> None:
    monkeypatch.setattr(
        timetable_application,
        "get_execution_provider",
        lambda provider: MockProviderAdapter(),
    )


async def timetable_request(client) -> object:
    return await client.get(
        "/api/v1/timetables",
        params={
            "provider": "korail",
            "origin": "대전",
            "destination": "서울",
            "origin_node_id": "0010",
            "destination_node_id": "0001",
            "departure_from": DEPARTURE.isoformat(),
            "departure_to": (DEPARTURE + timedelta(hours=2)).isoformat(),
            "passenger_count": 1,
        },
    )


def watch_payload(
    evidence_id: str, *, seat_class: str = "standard", **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "korail",
        "origin": "대전",
        "origin_node_id": "0010",
        "destination": "서울",
        "destination_node_id": "0001",
        "travel_date": DEPARTURE.date().isoformat(),
        "time_from": "08:00:00",
        "time_to": "09:00:00",
        "seat_class": seat_class,
        "passenger_count": 1,
        "train_numbers": ["26"],
        "mode": "official",
        "candidates": [
            {
                "train_number": "26",
                "departure_at": DEPARTURE.isoformat(),
                "arrival_at": (DEPARTURE + timedelta(hours=1)).isoformat(),
                "seat_class": seat_class,
                "priority": 1,
                "registration_evidence_id": evidence_id,
            }
        ],
    }
    payload.update(overrides)
    return payload


async def test_not_observed_seat_does_not_issue_registration_evidence(
    client, db_engine, monkeypatch
):
    item = timetable_item().model_copy(
        update={"timetable_retrieved_at": datetime.now(UTC) - timedelta(days=1)}
    )
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(item),
    )

    first = await timetable_request(client)
    second = await timetable_request(client)
    assert first.status_code == second.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    first_seat = first.json()[0]["seat_classes"][0]
    second_seat = second.json()[0]["seat_classes"][0]
    assert first_seat["registration_evidence_id"] is None
    assert second_seat["registration_evidence_id"] is None

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = list((await session.scalars(select(TimetableSeatEvidence))).all())
    assert rows == []


@pytest.mark.parametrize(
    ("status", "provenance_kind", "source", "observed_at", "reason", "registration_allowed"),
    [
        (SeatObservationStatus.UNKNOWN, "not_observed", None, None, "source_not_configured", True),
        (
            SeatObservationStatus.NOT_OFFERED,
            "official_provider",
            "authorized-provider",
            "now",
            None,
            False,
        ),
    ],
)
async def test_watch_rejects_legacy_non_actionable_evidence(
    client, db_engine, status, provenance_kind, source, observed_at, reason, registration_allowed
):
    now = datetime.now(UTC).replace(microsecond=0)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        evidence = TimetableSeatEvidence(
            evidence_hash=f"legacy-{status.value}".ljust(64, "0"),
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            canonical_train_number="26",
            departure_at=DEPARTURE,
            passenger_count=1,
            seat_class=SeatClass.STANDARD,
            status=status,
            provenance_kind=provenance_kind,
            source=source,
            observed_at=now if observed_at == "now" else None,
            reason=reason,
            registration_allowed=registration_allowed,
            created_at=now,
            registration_valid_until=now + timedelta(minutes=5),
        )
        session.add(evidence)
        await session.commit()
        evidence_id = evidence.id

    created = await client.post("/api/v1/watches", json=watch_payload(evidence_id))
    assert created.status_code == 422
    assert "not eligible" in created.text

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(WatchCandidate)) == 0


async def test_observed_seats_without_execution_capability_cannot_create_watches(
    client, db_engine, monkeypatch
):
    item = timetable_item(observed=True)
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(item),
    )

    response = await timetable_request(client)

    assert response.status_code == 200
    seats = response.json()[0]["seat_classes"]
    assert [seat["status"] for seat in seats] == ["sold_out", "available"]
    assert all(seat["provenance"]["kind"] == "official_provider" for seat in seats)
    assert all(seat["registration_evidence_id"] is None for seat in seats)
    assert all(action["kind"] != "add_to_watch" for seat in seats for action in seat["actions"])

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(TimetableSeatEvidence)) == 0


async def test_observed_add_to_watch_seats_issue_evidence_and_create_watches(client, monkeypatch):
    item = timetable_item(observed=True)
    enable_watch_registration(monkeypatch)
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(item),
    )

    response = await timetable_request(client)
    assert response.status_code == 200
    seats = {seat["seat_class"]: seat for seat in response.json()[0]["seat_classes"]}
    assert seats["standard"]["status"] == "sold_out"
    assert seats["first"]["status"] == "available"

    for seat_class in ("standard", "first"):
        created = await client.post(
            "/api/v1/watches",
            json=watch_payload(
                seats[seat_class]["registration_evidence_id"], seat_class=seat_class
            ),
        )
        assert created.status_code == 201, created.text


async def test_provider_and_user_confirmed_provenance_remain_distinct(
    client, db_engine, monkeypatch
):
    observed_item = timetable_item(observed=True)
    enable_watch_registration(monkeypatch)
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(observed_item),
    )
    provider_response = await timetable_request(client)
    provider_seat = provider_response.json()[0]["seat_classes"][0]
    assert provider_seat["provenance"]["kind"] == "official_provider"

    unknown_item = timetable_item()
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(unknown_item),
    )
    confirmation = await client.post(
        "/api/v1/seat-observations/official-page-confirmations",
        json={
            "provider": "korail",
            "origin_node_id": "0010",
            "destination_node_id": "0001",
            "train_number": "26",
            "departure_at": DEPARTURE.isoformat(),
            "passenger_count": 1,
            "seat_classes": [{"seat_class": "standard", "status": "sold_out"}],
        },
    )
    assert confirmation.status_code == 201
    user_response = await timetable_request(client)
    user_seat = user_response.json()[0]["seat_classes"][0]
    assert user_seat["provenance"]["kind"] == "user_confirmed_official_page"
    assert user_seat["provenance"]["source"] == "official-page-user-confirmation"

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = list((await session.scalars(select(TimetableSeatEvidence))).all())
    assert {row.provenance_kind for row in rows} >= {
        "official_provider",
        "user_confirmed_official_page",
    }


@pytest.mark.parametrize(
    "mismatch",
    [
        "provider",
        "origin",
        "destination",
        "passengers",
        "train",
        "departure",
        "seat_class",
    ],
)
async def test_watch_rejects_exact_identity_mismatch(client, monkeypatch, mismatch):
    item = timetable_item(observed=True)
    enable_watch_registration(monkeypatch)
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(item),
    )
    response = await timetable_request(client)
    evidence_id = response.json()[0]["seat_classes"][0]["registration_evidence_id"]
    payload = watch_payload(evidence_id)
    if mismatch == "provider":
        payload["provider"] = "srt"
    elif mismatch == "origin":
        payload["origin_node_id"] = "9999"
    elif mismatch == "destination":
        payload["destination_node_id"] = "9999"
    elif mismatch == "passengers":
        payload["passenger_count"] = 2
    elif mismatch == "train":
        payload["train_numbers"] = ["27"]
        payload["candidates"][0]["train_number"] = "27"
    elif mismatch == "departure":
        payload["candidates"][0]["departure_at"] = datetime(
            2030, 8, 1, 8, 31, tzinfo=ZoneInfo("Asia/Seoul")
        ).isoformat()
    else:
        payload["seat_class"] = "first"
        payload["candidates"][0]["seat_class"] = "first"
    created = await client.post("/api/v1/watches", json=payload)
    assert created.status_code == 422
    assert "does not match" in created.text


async def test_observed_issuance_bucket_reuses_then_rolls_over(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    bucket = datetime(2026, 8, 1, 12, 0, 10, tzinfo=UTC)
    item = timetable_item(observed=True)
    seats = [
        seat.model_copy(
            update={"provenance": seat.provenance.model_copy(update={"observed_at": bucket})}
        )
        for seat in item.seat_classes
    ]
    item = item.model_copy(
        update={"seat_classes": seats, "timetable_retrieved_at": datetime(2020, 1, 1, tzinfo=UTC)}
    )
    async with factory() as session:
        first = await persist_timetable_seat_evidence(
            session,
            [item],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=bucket,
        )
        reused = await persist_timetable_seat_evidence(
            session,
            [item],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=bucket + timedelta(seconds=40),
        )
        rolled = await persist_timetable_seat_evidence(
            session,
            [item],
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            passenger_count=1,
            now=bucket + timedelta(minutes=1),
        )
        await session.commit()

    first_id = first[0].seat_classes[0].registration_evidence_id
    reused_id = reused[0].seat_classes[0].registration_evidence_id
    rolled_id = rolled[0].seat_classes[0].registration_evidence_id
    assert first_id == reused_id
    assert rolled_id != first_id


async def test_watch_rejects_expired_evidence(client, db_engine, monkeypatch):
    item = timetable_item(observed=True)
    enable_watch_registration(monkeypatch)
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(item),
    )
    response = await timetable_request(client)
    evidence_id = response.json()[0]["seat_classes"][0]["registration_evidence_id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        row = await session.get(TimetableSeatEvidence, evidence_id)
        row.created_at = datetime.now(UTC) - timedelta(minutes=10)
        row.registration_valid_until = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    created = await client.post("/api/v1/watches", json=watch_payload(evidence_id))
    assert created.status_code == 409
    assert created.json() == {
        "detail": {
            "code": "registration_evidence_conflict",
            "reason": "expired",
            "message": "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
        }
    }


async def test_official_candidate_requires_server_evidence(client):
    payload = watch_payload("missing")
    payload["candidates"][0].pop("registration_evidence_id")
    created = await client.post("/api/v1/watches", json=payload)
    assert created.status_code == 422
    assert "require registration evidence" in created.text


async def test_evidence_and_official_watch_do_not_trigger_worker_side_effects(
    client, app, db_engine, monkeypatch
):
    item = timetable_item(observed=True)
    enable_watch_registration(monkeypatch)
    monkeypatch.setattr(
        timetable_application,
        "get_timetable_provider",
        lambda provider: StaticOfficialAdapter(item),
    )
    response = await timetable_request(client)
    evidence_id = response.json()[0]["seat_classes"][0]["registration_evidence_id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0

    created = await client.post("/api/v1/watches", json=watch_payload(evidence_id))
    watch_id = created.json()["id"]
    await client.post(f"/api/v1/watches/{watch_id}/start")
    monkeypatch.setattr(worker_module, "SessionFactory", app.state.test_session_factory)
    assert await _process_due_watches() == 0
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(SeatObservation)) == 0
        assert await session.scalar(select(func.count()).select_from(ReservationAttempt)) == 0
        notification_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "notification.dispatch_requested")
        )
        assert notification_count == 0


async def test_database_rejects_invalid_not_observed_shape(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(microsecond=0)
    async with factory() as session:
        session.add(
            TimetableSeatEvidence(
                evidence_hash="a" * 64,
                provider=Provider.KORAIL,
                origin_node_id="0010",
                destination_node_id="0001",
                canonical_train_number="26",
                departure_at=DEPARTURE,
                passenger_count=1,
                seat_class=SeatClass.STANDARD,
                status=SeatObservationStatus.AVAILABLE,
                provenance_kind="not_observed",
                reason="source_not_configured",
                created_at=now,
                registration_valid_until=now + timedelta(minutes=5),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
