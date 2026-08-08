from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from rail_waitlist import official_page_confirmations as compatibility_application
from rail_waitlist import schemas as legacy_schemas
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.official_page_confirmation import application as canonical_application
from rail_waitlist.official_page_confirmation import schemas as canonical_schemas
from rail_waitlist.provider_schema_base import ProviderContractModel
from rail_waitlist.schema_base import ApiModel

API_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_NAMES = (
    "OfficialPageSeatConfirmationItem",
    "OfficialPageSeatConfirmationCreate",
    "OfficialPageSeatConfirmationItemRead",
    "OfficialPageSeatConfirmationRead",
)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "korail",
        "origin_node_id": " 0010 ",
        "destination_node_id": " 0001 ",
        "train_number": " 00026 ",
        "departure_at": datetime(
            2026,
            7,
            30,
            12,
            0,
            0,
            123456,
            tzinfo=timezone(timedelta(hours=9)),
        ),
        "passenger_count": 1,
        "seat_classes": [
            {"seat_class": "standard", "status": "sold_out"},
            {"seat_class": "first", "status": "available"},
        ],
    }
    payload.update(overrides)
    return payload


def test_official_page_schema_and_application_compatibility_exports_are_exact() -> None:
    assert (
        legacy_schemas.OFFICIAL_PAGE_CONFIRMATION_SOURCE
        == canonical_schemas.OFFICIAL_PAGE_CONFIRMATION_SOURCE
        == "official-page-user-confirmation"
    )
    assert legacy_schemas.OfficialPageSeatStatus is canonical_schemas.OfficialPageSeatStatus
    for name in SCHEMA_NAMES:
        canonical = getattr(canonical_schemas, name)
        assert getattr(legacy_schemas, name) is canonical
        assert canonical.__module__ == "rail_waitlist.official_page_confirmation.schemas"

    assert compatibility_application.CONFIRMATION_FRESHNESS == timedelta(minutes=5)
    assert compatibility_application.IDEMPOTENCY_SCOPE == "official-page-seat-confirmation.create"
    assert compatibility_application.upsert_official_page_confirmations is (
        canonical_application.upsert_official_page_confirmations
    )
    assert compatibility_application.overlay_official_page_confirmations is (
        canonical_application.overlay_official_page_confirmations
    )


def test_official_page_schema_shape_bases_and_nested_identity_are_preserved() -> None:
    assert canonical_schemas.OfficialPageSeatConfirmationItem.__bases__ == (ProviderContractModel,)
    assert canonical_schemas.OfficialPageSeatConfirmationCreate.__bases__ == (
        ProviderContractModel,
    )
    assert canonical_schemas.OfficialPageSeatConfirmationItemRead.__bases__ == (ApiModel,)
    assert canonical_schemas.OfficialPageSeatConfirmationRead.__bases__ == (ApiModel,)
    assert get_args(canonical_schemas.OfficialPageSeatStatus) == (
        "available",
        "sold_out",
        "waitlist_available",
        "not_offered",
    )
    assert list(canonical_schemas.OfficialPageSeatConfirmationItem.model_fields) == [
        "seat_class",
        "status",
    ]
    assert list(canonical_schemas.OfficialPageSeatConfirmationCreate.model_fields) == [
        "provider",
        "origin_node_id",
        "destination_node_id",
        "train_number",
        "departure_at",
        "passenger_count",
        "seat_classes",
    ]
    assert list(canonical_schemas.OfficialPageSeatConfirmationItemRead.model_fields) == [
        "id",
        "seat_class",
        "status",
    ]
    assert list(canonical_schemas.OfficialPageSeatConfirmationRead.model_fields) == [
        "provider",
        "origin_node_id",
        "destination_node_id",
        "train_number",
        "departure_at",
        "passenger_count",
        "seat_classes",
        "source",
        "provenance_kind",
        "observed_at",
        "fresh_until",
        "created_count",
        "replayed",
    ]
    create_nested = get_args(
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_fields["seat_classes"].annotation
    )
    read_nested = get_args(
        canonical_schemas.OfficialPageSeatConfirmationRead.model_fields["seat_classes"].annotation
    )
    assert create_nested == (canonical_schemas.OfficialPageSeatConfirmationItem,)
    assert read_nested == (canonical_schemas.OfficialPageSeatConfirmationItemRead,)
    assert canonical_schemas.OfficialPageSeatConfirmationCreate.model_config["extra"] == "forbid"
    assert canonical_schemas.OfficialPageSeatConfirmationRead.model_config.get("extra") is None
    assert canonical_schemas.OfficialPageSeatConfirmationRead.model_fields["source"].is_required()
    assert (
        canonical_schemas.OfficialPageSeatConfirmationRead.model_fields["provenance_kind"].default
        == "user_confirmed_official_page"
    )


def test_official_page_create_normalization_timezone_and_strict_boundary_are_preserved() -> None:
    created = canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(_payload())
    assert created.provider is Provider.KORAIL
    assert created.origin_node_id == "0010"
    assert created.destination_node_id == "0001"
    assert created.train_number == "26"
    assert created.departure_at == datetime(2026, 7, 30, 3, 0, tzinfo=UTC)
    assert created.departure_at.microsecond == 0
    assert [item.seat_class for item in created.seat_classes] == [
        SeatClass.STANDARD,
        SeatClass.FIRST,
    ]

    all_zero = canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
        _payload(train_number=" 00000 ")
    )
    alphanumeric = canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
        _payload(train_number=" ktx-001 ")
    )
    assert all_zero.train_number == "0"
    assert alphanumeric.train_number == "KTX-001"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
            _payload(raw_html="<html>must-not-cross-boundary</html>")
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
            _payload(
                seat_classes=[
                    {
                        "seat_class": "standard",
                        "status": "available",
                        "transport_error": "blocked",
                    }
                ]
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "mock"},
        {"origin_node_id": "0010", "destination_node_id": "0010"},
        {"passenger_count": 0},
        {"passenger_count": 10},
        {
            "departure_at": datetime(2026, 7, 30, 12, 0)  # noqa: DTZ001 - naive compatibility case
        },
        {"origin_node_id": "captcha"},
        {"destination_node_id": "NetFunnel blocked"},
        {"train_number": "CODE -8003"},
        {"seat_classes": []},
        {
            "seat_classes": [
                {"seat_class": "standard", "status": "available"},
                {"seat_class": "standard", "status": "sold_out"},
            ]
        },
        {"seat_classes": [{"seat_class": "any", "status": "available"}]},
    ],
)
def test_official_page_create_rejects_existing_invalid_identity_and_bounds(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(_payload(**overrides))


def test_official_page_create_preserves_model_validator_error_precedence() -> None:
    with pytest.raises(ValidationError, match="only accept KORAIL or SRT"):
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
            _payload(
                provider="mock",
                origin_node_id="0010",
                destination_node_id="0010",
                seat_classes=[
                    {"seat_class": "standard", "status": "available"},
                    {"seat_class": "standard", "status": "sold_out"},
                ],
            )
        )
    with pytest.raises(ValidationError, match="node IDs must differ"):
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
            _payload(
                origin_node_id="0010",
                destination_node_id="0010",
                seat_classes=[
                    {"seat_class": "standard", "status": "available"},
                    {"seat_class": "standard", "status": "sold_out"},
                ],
            )
        )
    with pytest.raises(ValidationError, match="unique seat classes"):
        canonical_schemas.OfficialPageSeatConfirmationCreate.model_validate(
            _payload(
                seat_classes=[
                    {"seat_class": "standard", "status": "available"},
                    {"seat_class": "standard", "status": "sold_out"},
                ]
            )
        )


def test_official_page_read_preserves_attribute_and_timestamp_contracts() -> None:
    seoul = timezone(timedelta(hours=9))
    read = canonical_schemas.OfficialPageSeatConfirmationRead.model_validate(
        SimpleNamespace(
            provider=Provider.KORAIL,
            origin_node_id="0010",
            destination_node_id="0001",
            train_number="26",
            departure_at=datetime(2026, 7, 30, 12, 0, 0, 111111),  # noqa: DTZ001 - naive compatibility case
            passenger_count=1,
            seat_classes=[
                SimpleNamespace(
                    id="confirmation-1",
                    seat_class=SeatClass.STANDARD,
                    status="available",
                    ignored_nested="ignored",
                )
            ],
            source="official-page-user-confirmation",
            observed_at=datetime(2026, 7, 30, 12, 1, 0, 222222, tzinfo=seoul),
            fresh_until=datetime(2026, 7, 30, 3, 6, 0, 333333),  # noqa: DTZ001 - naive compatibility case
            created_count=-1,
            replayed=False,
            ignored_outer="ignored",
        )
    )

    assert read.provenance_kind == "user_confirmed_official_page"
    assert read.departure_at == datetime(2026, 7, 30, 12, 0, 0, 111111, tzinfo=UTC)
    assert read.observed_at == datetime(2026, 7, 30, 3, 1, 0, 222222, tzinfo=UTC)
    assert read.fresh_until == datetime(2026, 7, 30, 3, 6, 0, 333333, tzinfo=UTC)
    assert read.created_count == -1
    assert read.seat_classes[0].id == "confirmation-1"
    assert "ignored_outer" not in read.model_dump()
    assert "ignored_nested" not in read.seat_classes[0].model_dump()

    with pytest.raises(ValidationError):
        canonical_schemas.OfficialPageSeatConfirmationRead.model_validate(
            {**read.model_dump(exclude={"source"})}
        )
    with pytest.raises(ValidationError):
        canonical_schemas.OfficialPageSeatConfirmationRead.model_validate(
            {**read.model_dump(), "source": "another-source"}
        )


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_official_page_schema_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys
from typing import get_args

if sys.argv[1] == "canonical-first":
    from rail_waitlist.official_page_confirmation import schemas as canonical
    from rail_waitlist import schemas as legacy
else:
    from rail_waitlist import schemas as legacy
    from rail_waitlist.official_page_confirmation import schemas as canonical

names = (
    "OfficialPageSeatConfirmationItem",
    "OfficialPageSeatConfirmationCreate",
    "OfficialPageSeatConfirmationItemRead",
    "OfficialPageSeatConfirmationRead",
)
result = {
    "identity": all(getattr(legacy, name) is getattr(canonical, name) for name in names),
    "status_identity": legacy.OfficialPageSeatStatus is canonical.OfficialPageSeatStatus,
    "create_nested": get_args(
        canonical.OfficialPageSeatConfirmationCreate.model_fields["seat_classes"].annotation
    )[0] is canonical.OfficialPageSeatConfirmationItem,
    "read_nested": get_args(
        canonical.OfficialPageSeatConfirmationRead.model_fields["seat_classes"].annotation
    )[0] is canonical.OfficialPageSeatConfirmationItemRead,
    "schemas": len([getattr(canonical, name).model_json_schema() for name in names]),
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
        "create_nested": True,
        "identity": True,
        "read_nested": True,
        "schemas": 4,
        "status_identity": True,
    }
