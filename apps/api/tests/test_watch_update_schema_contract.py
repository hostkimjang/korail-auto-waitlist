from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy_schemas
from rail_waitlist import services
from rail_waitlist.domain import ReservationPolicy, SeatClass, SeatObservationMode
from rail_waitlist.main import create_app
from rail_waitlist.schema_base import ApiModel
from rail_waitlist.watch_management import http, schemas, update_application

API_ROOT = Path(__file__).resolve().parents[1]
FIELD_NAMES = [
    "time_from",
    "time_to",
    "seat_class",
    "passenger_count",
    "train_numbers",
    "notification_channel_ids",
    "payment_deadline",
    "reservation_policy",
    "seat_observation_mode",
    "focused_observation_interval_seconds",
]


def test_watch_update_has_one_canonical_schema_object() -> None:
    canonical = schemas.WatchUpdate

    assert legacy_schemas.WatchUpdate is canonical
    assert services.WatchUpdate is canonical
    assert http.WatchUpdate is canonical
    assert update_application.WatchUpdate is canonical
    assert canonical.__module__ == "rail_waitlist.watch_management.schemas"
    assert canonical.__bases__ == (ApiModel,)
    assert list(canonical.model_fields) == FIELD_NAMES
    assert all(not field.is_required() for field in canonical.model_fields.values())
    assert all(field.default is None for field in canonical.model_fields.values())


def test_watch_update_json_schema_contract_is_preserved() -> None:
    schema = schemas.WatchUpdate.model_json_schema()
    properties = schema["properties"]

    assert schema["title"] == "WatchUpdate"
    assert schema["type"] == "object"
    assert "required" not in schema
    assert list(properties) == FIELD_NAMES
    assert all(property_schema["default"] is None for property_schema in properties.values())
    assert schema["$defs"] == {
        "ReservationPolicy": {
            "enum": ["notify_only", "reserve_once_before_payment"],
            "title": "ReservationPolicy",
            "type": "string",
        },
        "SeatClass": {
            "enum": ["standard", "first", "infant", "free", "waitlist", "any"],
            "title": "SeatClass",
            "type": "string",
        },
        "SeatObservationMode": {
            "enum": ["balanced", "focused"],
            "title": "SeatObservationMode",
            "type": "string",
        },
    }
    assert properties["passenger_count"]["anyOf"][0] == {
        "maximum": 9,
        "minimum": 1,
        "type": "integer",
    }
    assert properties["focused_observation_interval_seconds"]["anyOf"][0] == {
        "maximum": 30,
        "minimum": 20,
        "type": "integer",
    }
    assert properties["train_numbers"]["anyOf"][0]["maxItems"] == 20
    assert properties["notification_channel_ids"]["anyOf"][0]["maxItems"] == 20
    assert properties["payment_deadline"]["anyOf"][0]["format"] == "date-time"
    assert properties["time_from"]["anyOf"][0]["format"] == "time"
    assert properties["time_to"]["anyOf"][0]["format"] == "time"


def test_watch_update_preserves_partial_update_and_normalization_contract() -> None:
    empty = schemas.WatchUpdate()
    assert empty.model_fields_set == set()
    assert empty.model_dump(exclude_unset=True) == {}

    deadline_offset = timezone(timedelta(hours=9))
    update = schemas.WatchUpdate.model_validate(
        {
            "time_from": "23:30:00",
            "time_to": "08:00:00",
            "seat_class": "any",
            "passenger_count": "2",
            "train_numbers": ["", "KTX-001", "KTX-001"],
            "notification_channel_ids": [],
            "payment_deadline": datetime(2030, 8, 1, 12, 0, tzinfo=deadline_offset),
            "reservation_policy": "notify_only",
            "seat_observation_mode": "balanced",
            "focused_observation_interval_seconds": 20.0,
            "future_field": "ignored",
        }
    )

    assert update.model_fields_set == set(FIELD_NAMES)
    assert update.passenger_count == 2
    assert update.seat_class is SeatClass.ANY
    assert update.reservation_policy is ReservationPolicy.NOTIFY_ONLY
    assert update.seat_observation_mode is SeatObservationMode.BALANCED
    assert update.focused_observation_interval_seconds == 20
    assert update.train_numbers == ["", "KTX-001", "KTX-001"]
    assert update.notification_channel_ids == []
    assert update.payment_deadline == datetime(2030, 8, 1, 3, 0, tzinfo=UTC)
    assert update.time_to is not None and update.time_from > update.time_to
    assert not hasattr(update, "future_field")

    offset_time = schemas.WatchUpdate.model_validate({"time_from": "23:30:00+09:00"})
    assert offset_time.time_from is not None
    assert offset_time.time_from.utcoffset() == deadline_offset.utcoffset(None)

    @dataclass
    class UpdateAttributes:
        passenger_count: int | None = None

    from_attributes = schemas.WatchUpdate.model_validate(UpdateAttributes())
    assert from_attributes.passenger_count is None
    assert from_attributes.model_fields_set == {"passenger_count"}


@pytest.mark.parametrize("field_name", FIELD_NAMES + ["future_field"])
def test_watch_update_rejects_every_explicit_dict_null(field_name: str) -> None:
    with pytest.raises(ValidationError, match=f"explicit null is not allowed: {field_name}"):
        schemas.WatchUpdate.model_validate({field_name: None})


def test_watch_update_sorts_multiple_explicit_null_field_names() -> None:
    with pytest.raises(
        ValidationError,
        match="explicit null is not allowed: passenger_count, time_from, time_to",
    ):
        schemas.WatchUpdate.model_validate(
            {"time_to": None, "passenger_count": None, "time_from": None}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"passenger_count": 0},
        {"passenger_count": 10},
        {"passenger_count": 1.5},
        {"train_numbers": ["1"] * 21},
        {"notification_channel_ids": ["1"] * 21},
        {"payment_deadline": "2030-08-01T12:00:00"},
        {"reservation_policy": "reserve_repeatedly"},
        {"seat_class": "premium_unknown"},
        {"seat_observation_mode": "aggressive"},
        {"focused_observation_interval_seconds": 19},
        {"focused_observation_interval_seconds": 31},
    ],
)
def test_watch_update_preserves_existing_rejections(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schemas.WatchUpdate.model_validate(payload)


def test_watch_update_openapi_component_and_patch_reference_are_preserved() -> None:
    openapi = create_app().openapi()
    component = openapi["components"]["schemas"]["WatchUpdate"]
    patch = openapi["paths"]["/api/v1/watches/{watch_id}"]["patch"]

    assert component["title"] == "WatchUpdate"
    assert component["type"] == "object"
    assert "required" not in component
    assert list(component["properties"]) == FIELD_NAMES
    assert all(
        {"type": "null"} in property_schema["anyOf"]
        for property_schema in component["properties"].values()
    )
    assert patch["requestBody"] == {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WatchUpdate"}}},
        "required": True,
    }
    assert set(patch["responses"]) == {"200", "422"}


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "legacy-first", "services-first", "http-first", "application-first"],
)
def test_watch_update_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.watch_management import schemas as canonical
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import schemas as legacy
elif sys.argv[1] == "services-first":
    from rail_waitlist import services
elif sys.argv[1] == "http-first":
    from rail_waitlist.watch_management import http
else:
    from rail_waitlist.watch_management import update_application

from rail_waitlist import schemas as legacy
from rail_waitlist import services
from rail_waitlist.watch_management import http, schemas as canonical, update_application

print(json.dumps({
    "legacy": legacy.WatchUpdate is canonical.WatchUpdate,
    "services": services.WatchUpdate is canonical.WatchUpdate,
    "http": http.WatchUpdate is canonical.WatchUpdate,
    "application": update_application.WatchUpdate is canonical.WatchUpdate,
    "module": canonical.WatchUpdate.__module__,
    "title": canonical.WatchUpdate.model_json_schema()["title"],
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
        "application": True,
        "http": True,
        "legacy": True,
        "module": "rail_waitlist.watch_management.schemas",
        "services": True,
        "title": "WatchUpdate",
    }
