from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy_schemas
from rail_waitlist.domain import Provider
from rail_waitlist.main import app
from rail_waitlist.schema_base import ApiModel
from rail_waitlist.timetable_management import http, schemas

API_ROOT = Path(__file__).resolve().parents[1]


def request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "korail",
        "origin": "대전",
        "destination": "서울",
        "departure_from": "2026-08-07T12:00:00+09:00",
        "departure_to": "2026-08-07T18:00:00+09:00",
        "passenger_count": 1,
        "origin_node_id": "N-DAEJEON",
        "destination_node_id": "N-SEOUL",
    }
    payload.update(overrides)
    return payload


def test_refresh_request_has_one_canonical_schema_object() -> None:
    assert legacy_schemas.SeatStatusRefreshRequest is schemas.SeatStatusRefreshRequest
    assert http.SeatStatusRefreshRequest is schemas.SeatStatusRefreshRequest
    assert schemas.SeatStatusRefreshRequest.__module__ == (
        "rail_waitlist.timetable_management.schemas"
    )
    assert schemas.SeatStatusRefreshRequest.__bases__ == (ApiModel,)
    assert list(schemas.SeatStatusRefreshRequest.model_fields) == [
        "provider",
        "origin",
        "destination",
        "departure_from",
        "departure_to",
        "passenger_count",
        "origin_node_id",
        "destination_node_id",
    ]


def test_refresh_request_json_and_openapi_contract_are_preserved() -> None:
    schema = schemas.SeatStatusRefreshRequest.model_json_schema()
    properties = schema["properties"]

    assert properties["provider"] == {
        "enum": ["korail", "srt"],
        "title": "Provider",
        "type": "string",
    }
    assert properties["origin"]["minLength"] == 1
    assert properties["origin"]["maxLength"] == 40
    assert properties["destination"]["minLength"] == 1
    assert properties["destination"]["maxLength"] == 40
    assert properties["passenger_count"]["default"] == 1
    assert properties["passenger_count"]["minimum"] == 1
    assert properties["passenger_count"]["maximum"] == 9
    assert properties["origin_node_id"]["minLength"] == 1
    assert properties["origin_node_id"]["maxLength"] == 80
    assert properties["destination_node_id"]["minLength"] == 1
    assert properties["destination_node_id"]["maxLength"] == 80
    assert schema["required"] == [
        "provider",
        "origin",
        "destination",
        "departure_from",
        "departure_to",
        "origin_node_id",
        "destination_node_id",
    ]

    openapi = app.openapi()
    component = openapi["components"]["schemas"]["SeatStatusRefreshRequest"]
    request_schema = openapi["paths"]["/api/v1/seat-status/refresh"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"]
    assert component == schema
    assert request_schema == {"$ref": "#/components/schemas/SeatStatusRefreshRequest"}


def test_refresh_request_preserves_valid_wire_and_existing_permissive_values() -> None:
    request = schemas.SeatStatusRefreshRequest.model_validate(request_payload())
    assert request.provider is Provider.KORAIL
    assert request.model_dump(mode="json") == request_payload()

    permissive = schemas.SeatStatusRefreshRequest.model_validate(
        request_payload(
            origin=" 대전 ",
            destination=" 서울 ",
            departure_from=datetime(2026, 8, 7, 12),
            departure_to=datetime(2026, 8, 7, 18),
            origin_node_id=" ",
        )
    )
    assert permissive.origin == " 대전 "
    assert permissive.departure_from.tzinfo is None
    assert permissive.origin_node_id == " "

    srt_same_node = schemas.SeatStatusRefreshRequest.model_validate(
        request_payload(
            provider="srt",
            origin_node_id="N-SAME",
            destination_node_id="N-SAME",
        )
    )
    assert srt_same_node.provider is Provider.SRT
    assert srt_same_node.origin_node_id == srt_same_node.destination_node_id == "N-SAME"


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "mock"},
        {"origin": " 서울 ", "destination": "서울"},
        {"departure_to": "2026-08-07T12:00:00+09:00"},
        {"departure_to": "2026-08-07T11:59:59+09:00"},
        {"passenger_count": 0},
        {"passenger_count": 10},
        {"origin_node_id": ""},
        {"destination_node_id": "x" * 81},
    ],
)
def test_refresh_request_preserves_existing_rejections(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schemas.SeatStatusRefreshRequest.model_validate(request_payload(**overrides))


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first", "http-first"])
def test_refresh_request_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management import schemas as canonical
    from rail_waitlist import schemas as legacy
    from rail_waitlist.timetable_management import http
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import schemas as legacy
    from rail_waitlist.timetable_management import schemas as canonical
    from rail_waitlist.timetable_management import http
else:
    from rail_waitlist.timetable_management import http
    from rail_waitlist.timetable_management import schemas as canonical
    from rail_waitlist import schemas as legacy

print(json.dumps({
    "legacy": legacy.SeatStatusRefreshRequest is canonical.SeatStatusRefreshRequest,
    "http": http.SeatStatusRefreshRequest is canonical.SeatStatusRefreshRequest,
    "module": canonical.SeatStatusRefreshRequest.__module__,
    "schema": canonical.SeatStatusRefreshRequest.model_json_schema()["title"],
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "legacy": True,
        "http": True,
        "module": "rail_waitlist.timetable_management.schemas",
        "schema": "SeatStatusRefreshRequest",
    }
