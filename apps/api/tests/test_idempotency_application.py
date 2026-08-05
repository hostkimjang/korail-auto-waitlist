from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist import services
from rail_waitlist.idempotency import application as idempotency_application
from rail_waitlist.idempotency.application import (
    IdempotencyConflict,
    get_idempotent_resource,
    remember_idempotency,
    request_hash,
)
from rail_waitlist.models import IdempotencyRecord
from rail_waitlist.timetable_management import official_evidence_http

CONFLICT_DETAIL = "Idempotency-Key was already used with a different request"


class _HashPayload(BaseModel):
    service_date: date
    passengers: int


class _DynamicHashPayload:
    def __getattr__(self, name: str):
        if name == "model_dump":
            return self._model_dump
        raise AttributeError(name)

    @staticmethod
    def _model_dump(*, mode: str) -> object:
        assert mode == "json"
        return {"service_date": "2030-07-30", "passengers": 2}


def _watch_payload(*, day_offset: int, train_number: str = "KTX-001") -> dict[str, object]:
    travel_date = (date.today() + timedelta(days=day_offset)).isoformat()
    return {
        "provider": "mock",
        "origin": "서울",
        "origin_node_id": "N-SEOUL",
        "destination": "부산",
        "destination_node_id": "N-BUSAN",
        "travel_date": travel_date,
        "time_from": "08:00:00",
        "time_to": "12:00:00",
        "passenger_count": 1,
        "train_numbers": [train_number],
        "mode": "official",
        "candidates": [
            {
                "train_number": train_number,
                "departure_at": f"{travel_date}T08:30:00+09:00",
                "arrival_at": f"{travel_date}T11:00:00+09:00",
                "seat_class": "standard",
                "priority": 1,
            }
        ],
    }


def _confirmation_payload() -> dict[str, object]:
    return {
        "provider": "korail",
        "origin_node_id": "0010",
        "destination_node_id": "0001",
        "train_number": "00026",
        "departure_at": "2030-07-30T12:00:00+09:00",
        "passenger_count": 1,
        "seat_classes": [{"seat_class": "standard", "status": "sold_out"}],
    }


def test_request_hash_serializes_mapping_order_and_pydantic_json_consistently() -> None:
    model = _HashPayload(service_date=date(2030, 7, 30), passengers=2)

    assert request_hash({"passengers": 2, "service_date": "2030-07-30"}) == request_hash(
        {"service_date": "2030-07-30", "passengers": 2}
    )
    assert request_hash(model) == request_hash(model.model_dump(mode="json"))


def test_request_hash_preserves_dynamic_model_dump_duck_typing() -> None:
    assert request_hash(_DynamicHashPayload()) == request_hash(
        {"service_date": "2030-07-30", "passengers": 2}
    )


def test_services_reexports_canonical_idempotency_functions_by_identity() -> None:
    assert services.request_hash is idempotency_application.request_hash
    assert services.get_idempotent_resource is idempotency_application.get_idempotent_resource
    assert services.remember_idempotency is idempotency_application.remember_idempotency


async def test_idempotency_record_replay_conflict_and_unit_of_work(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    payload_hash = request_hash({"passengers": 1})

    async with factory() as session:
        await remember_idempotency(session, "test.scope", None, "ignored", payload_hash)
        assert await get_idempotent_resource(session, "test.scope", None, payload_hash) is None
        assert await session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0

        await remember_idempotency(session, "test.scope", "rollback", "resource-1", payload_hash)
        assert (
            await get_idempotent_resource(session, "test.scope", "rollback", payload_hash)
            == "resource-1"
        )
        await session.rollback()

    async with factory() as session:
        assert (
            await get_idempotent_resource(session, "test.scope", "rollback", payload_hash) is None
        )
        await remember_idempotency(session, "test.scope", "commit", "resource-2", payload_hash)
        await session.commit()

    async with factory() as session:
        assert (
            await get_idempotent_resource(session, "test.scope", "commit", payload_hash)
            == "resource-2"
        )
        with pytest.raises(IdempotencyConflict, match=CONFLICT_DETAIL):
            await get_idempotent_resource(
                session,
                "test.scope",
                "commit",
                request_hash({"passengers": 2}),
            )


async def test_watch_create_maps_only_payload_mismatch_to_existing_http_409(client) -> None:
    headers = {"Idempotency-Key": "create-conflict-boundary"}
    first = await client.post(
        "/api/v1/watches", json=_watch_payload(day_offset=21), headers=headers
    )
    conflict = await client.post(
        "/api/v1/watches", json=_watch_payload(day_offset=22), headers=headers
    )

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": CONFLICT_DETAIL}


async def test_watch_transition_http_boundaries_map_idempotency_conflicts(client) -> None:
    for index, action in enumerate(("start", "pause", "cancel"), start=1):
        first = await client.post("/api/v1/watches", json=_watch_payload(day_offset=30 + index * 2))
        second = await client.post(
            "/api/v1/watches", json=_watch_payload(day_offset=31 + index * 2)
        )
        assert first.status_code == second.status_code == 201
        first_id = first.json()["id"]
        second_id = second.json()["id"]
        if action == "pause":
            assert (await client.post(f"/api/v1/watches/{first_id}/start")).status_code == 200
            assert (await client.post(f"/api/v1/watches/{second_id}/start")).status_code == 200

        headers = {"Idempotency-Key": f"{action}-conflict-boundary"}
        accepted = await client.post(f"/api/v1/watches/{first_id}/{action}", headers=headers)
        conflict = await client.post(f"/api/v1/watches/{second_id}/{action}", headers=headers)

        assert accepted.status_code == 200, accepted.text
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": CONFLICT_DETAIL}


async def test_official_evidence_concurrent_replay_persists_one_batch(client, db_engine) -> None:
    endpoint = "/api/v1/seat-observations/official-page-confirmations"
    headers = {"Idempotency-Key": "official-concurrent-owner-boundary"}

    first, second = await asyncio.gather(
        client.post(endpoint, json=_confirmation_payload(), headers=headers),
        client.post(endpoint, json=_confirmation_payload(), headers=headers),
    )

    assert first.status_code == second.status_code == 201
    assert sorted((first.json()["created_count"], second.json()["created_count"])) == [0, 1]
    assert {first.json()["replayed"], second.json()["replayed"]} == {False, True}

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        records = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == "official-page-seat-confirmation.create",
                IdempotencyRecord.key == headers["Idempotency-Key"],
            )
        )
    assert records == 1


@pytest.mark.parametrize(
    "application_error",
    [
        IdempotencyConflict(CONFLICT_DETAIL),
        ValueError("idempotent confirmation batch is incomplete"),
    ],
)
async def test_official_evidence_preserves_application_conflict_http_contract(
    client, monkeypatch, application_error: Exception
) -> None:
    async def fail_upsert(*_args: object, **_kwargs: object) -> object:
        raise application_error

    monkeypatch.setattr(
        official_evidence_http,
        "upsert_official_page_confirmations",
        fail_upsert,
    )
    response = await client.post(
        "/api/v1/seat-observations/official-page-confirmations",
        json=_confirmation_payload(),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": str(application_error)}
