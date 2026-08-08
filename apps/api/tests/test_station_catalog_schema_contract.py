from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from rail_waitlist import providers
from rail_waitlist import schemas as legacy_schemas
from rail_waitlist.domain import Provider
from rail_waitlist.main import app
from rail_waitlist.schema_base import ApiModel
from rail_waitlist.timetable_management import schemas

API_ROOT = Path(__file__).resolve().parents[1]


def station_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "node_id": "N-SEOUL",
        "name": "서울",
        "city_code": "11",
        "city_name": "서울특별시",
    }
    payload.update(overrides)
    return payload


def catalog_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "korail",
        "source": "TAGO",
        "retrieved_at": datetime(
            2026,
            8,
            6,
            12,
            30,
            tzinfo=timezone(timedelta(hours=9)),
        ),
        "catalog_scope": "intercity_station_guide_intersection",
        "provider_membership": "not_verified_by_source",
        "note": "공개 역 식별자와 표시 허용 목록의 교집합입니다.",
        "stations": [station_payload()],
    }
    payload.update(overrides)
    return payload


def test_station_schema_compatibility_exports_are_exact_canonical_objects() -> None:
    assert legacy_schemas.StationItem is schemas.StationItem
    assert legacy_schemas.StationCatalog is schemas.StationCatalog
    assert providers.StationCatalog is schemas.StationCatalog
    assert schemas.StationItem.__module__ == "rail_waitlist.timetable_management.schemas"
    assert schemas.StationCatalog.__module__ == "rail_waitlist.timetable_management.schemas"


def test_station_schema_shape_base_and_nested_identity_are_preserved() -> None:
    assert schemas.StationItem.__bases__ == (ApiModel,)
    assert schemas.StationCatalog.__bases__ == (ApiModel,)
    assert list(schemas.StationItem.model_fields) == [
        "node_id",
        "name",
        "city_code",
        "city_name",
    ]
    assert list(schemas.StationCatalog.model_fields) == [
        "provider",
        "source",
        "retrieved_at",
        "catalog_scope",
        "provider_membership",
        "note",
        "stations",
    ]
    assert get_args(schemas.StationCatalog.model_fields["stations"].annotation) == (
        schemas.StationItem,
    )
    assert schemas.StationItem.model_config["from_attributes"] is True
    assert schemas.StationCatalog.model_config["from_attributes"] is True
    assert all(field.is_required() for field in schemas.StationItem.model_fields.values())
    assert all(field.is_required() for field in schemas.StationCatalog.model_fields.values())


def test_station_schema_json_fingerprint_is_preserved() -> None:
    item_schema = schemas.StationItem.model_json_schema()
    catalog_schema = schemas.StationCatalog.model_json_schema()

    assert item_schema == {
        "properties": {
            "node_id": {
                "maxLength": 80,
                "minLength": 1,
                "title": "Node Id",
                "type": "string",
            },
            "name": {
                "maxLength": 80,
                "minLength": 1,
                "title": "Name",
                "type": "string",
            },
            "city_code": {
                "maxLength": 20,
                "minLength": 1,
                "title": "City Code",
                "type": "string",
            },
            "city_name": {
                "maxLength": 80,
                "minLength": 1,
                "title": "City Name",
                "type": "string",
            },
        },
        "required": ["node_id", "name", "city_code", "city_name"],
        "title": "StationItem",
        "type": "object",
    }
    assert catalog_schema["properties"]["source"]["enum"] == ["TAGO", "mock"]
    assert catalog_schema["properties"]["catalog_scope"]["enum"] == [
        "all_tago_train_stations",
        "intercity_station_guide_intersection",
        "mock",
    ]
    assert catalog_schema["properties"]["provider_membership"]["enum"] == [
        "not_verified_by_source",
        "mock",
    ]
    assert catalog_schema["properties"]["note"]["minLength"] == 1
    assert catalog_schema["properties"]["note"]["maxLength"] == 240
    assert catalog_schema["properties"]["stations"]["items"] == {"$ref": "#/$defs/StationItem"}
    assert catalog_schema["required"] == [
        "provider",
        "source",
        "retrieved_at",
        "catalog_scope",
        "provider_membership",
        "note",
        "stations",
    ]


def test_station_catalog_wire_timezone_and_attribute_contracts_are_preserved() -> None:
    catalog = schemas.StationCatalog.model_validate(catalog_payload())
    assert catalog.provider is Provider.KORAIL
    assert isinstance(catalog.stations[0], schemas.StationItem)
    assert catalog.retrieved_at.utcoffset() == timedelta(hours=9)
    assert catalog.model_dump(mode="json") == {
        "provider": "korail",
        "source": "TAGO",
        "retrieved_at": "2026-08-06T12:30:00+09:00",
        "catalog_scope": "intercity_station_guide_intersection",
        "provider_membership": "not_verified_by_source",
        "note": "공개 역 식별자와 표시 허용 목록의 교집합입니다.",
        "stations": [station_payload()],
    }

    from_attributes = schemas.StationItem.model_validate(
        SimpleNamespace(**station_payload(), ignored="ignored")
    )
    assert from_attributes == catalog.stations[0]
    assert "ignored" not in from_attributes.model_dump()

    with pytest.raises(ValidationError, match="retrieved_at must include a timezone"):
        schemas.StationCatalog.model_validate(
            catalog_payload(retrieved_at=datetime(2026, 8, 6, 12, 30))  # noqa: DTZ001
        )


@pytest.mark.parametrize(
    "payload",
    [
        station_payload(node_id=""),
        station_payload(name="x" * 81),
        station_payload(city_code="x" * 21),
        station_payload(city_name="x" * 81),
    ],
)
def test_station_item_rejects_existing_length_violations(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schemas.StationItem.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "official_provider"},
        {"catalog_scope": "provider_membership"},
        {"provider_membership": "verified"},
        {"note": ""},
        {"note": "x" * 241},
    ],
)
def test_station_catalog_rejects_existing_literal_and_note_violations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schemas.StationCatalog.model_validate(catalog_payload(**overrides))


def test_station_openapi_component_fingerprint_is_preserved() -> None:
    components = app.openapi()["components"]["schemas"]
    assert components["StationItem"] == schemas.StationItem.model_json_schema()
    catalog_component = components["StationCatalog"]
    assert catalog_component["title"] == "StationCatalog"
    assert catalog_component["properties"]["stations"]["items"] == {
        "$ref": "#/components/schemas/StationItem"
    }
    assert catalog_component["properties"]["provider"]["$ref"] == ("#/components/schemas/Provider")
    assert catalog_component["required"] == [
        "provider",
        "source",
        "retrieved_at",
        "catalog_scope",
        "provider_membership",
        "note",
        "stations",
    ]


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_station_schema_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys
from typing import get_args

if sys.argv[1] == "canonical-first":
    from rail_waitlist.timetable_management import schemas as canonical
    from rail_waitlist import providers, schemas as legacy
else:
    from rail_waitlist import schemas as legacy
    from rail_waitlist.timetable_management import schemas as canonical
    from rail_waitlist import providers

result = {
    "catalog": legacy.StationCatalog is canonical.StationCatalog,
    "item": legacy.StationItem is canonical.StationItem,
    "provider_facade": providers.StationCatalog is canonical.StationCatalog,
    "nested": get_args(canonical.StationCatalog.model_fields["stations"].annotation)[0]
        is canonical.StationItem,
    "schemas": len([
        canonical.StationItem.model_json_schema(),
        canonical.StationCatalog.model_json_schema(),
    ]),
}
print(json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "catalog": True,
        "item": True,
        "nested": True,
        "provider_facade": True,
        "schemas": 2,
    }
