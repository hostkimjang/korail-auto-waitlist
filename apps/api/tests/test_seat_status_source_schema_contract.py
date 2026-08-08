from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy_schemas
from rail_waitlist.main import app
from rail_waitlist.schema_base import ApiModel
from rail_waitlist.seat_status_operations import http, schemas

API_ROOT = Path(__file__).resolve().parents[1]


def test_seat_status_source_has_one_canonical_schema_object() -> None:
    assert legacy_schemas.SeatStatusCooldownCause is schemas.SeatStatusCooldownCause
    assert legacy_schemas.SeatStatusSourceStatus is schemas.SeatStatusSourceStatus
    assert http.SeatStatusSourceStatus is schemas.SeatStatusSourceStatus
    assert schemas.SeatStatusSourceStatus.__module__ == (
        "rail_waitlist.seat_status_operations.schemas"
    )
    assert schemas.SeatStatusSourceStatus.__bases__ == (ApiModel,)
    assert list(schemas.SeatStatusSourceStatus.model_fields) == [
        "provider",
        "source",
        "state",
        "cause",
        "retry_after_seconds",
    ]


def test_seat_status_source_json_and_openapi_contract_are_preserved() -> None:
    schema = schemas.SeatStatusSourceStatus.model_json_schema()
    properties = schema["properties"]

    assert schema["description"] == (
        "Current in-memory/Redis hold only; it is distinct from worker provider circuits."
    )
    assert schema["required"] == ["provider", "source", "state"]
    assert properties["provider"]["enum"] == ["korail", "srt"]
    assert properties["source"]["enum"] == ["korail_browser", "srt_live"]
    assert properties["state"]["enum"] == ["ready", "cooldown"]
    assert properties["cause"] == {
        "anyOf": [
            {
                "enum": ["provider_access_restricted", "source_unavailable"],
                "type": "string",
            },
            {"type": "null"},
        ],
        "default": None,
        "title": "Cause",
    }
    assert properties["retry_after_seconds"]["anyOf"] == [
        {"minimum": 1, "type": "integer"},
        {"type": "null"},
    ]
    assert properties["retry_after_seconds"]["default"] is None

    openapi = app.openapi()
    component = openapi["components"]["schemas"]["SeatStatusSourceStatus"]
    response_schema = openapi["paths"]["/api/v1/seat-status/status"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert component["description"] == schema["description"]
    assert component["required"] == schema["required"]
    assert component["properties"]["cause"]["anyOf"][0]["enum"] == [
        "provider_access_restricted",
        "source_unavailable",
    ]
    assert component["properties"]["retry_after_seconds"]["anyOf"][0]["minimum"] == 1.0
    assert response_schema["type"] == "array"
    assert response_schema["items"] == {"$ref": "#/components/schemas/SeatStatusSourceStatus"}


@pytest.mark.parametrize(
    "cause",
    ["provider_access_restricted", "source_unavailable"],
)
def test_seat_status_source_preserves_ready_and_cooldown_wire_values(cause: str) -> None:
    ready = schemas.SeatStatusSourceStatus(
        provider="srt",
        source="srt_live",
        state="ready",
    )
    assert ready.model_dump(mode="json") == {
        "provider": "srt",
        "source": "srt_live",
        "state": "ready",
        "cause": None,
        "retry_after_seconds": None,
    }

    cooldown = schemas.SeatStatusSourceStatus(
        provider="korail",
        source="korail_browser",
        state="cooldown",
        cause=cause,
        retry_after_seconds=2,
    )
    assert cooldown.cause == cause
    assert cooldown.retry_after_seconds == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "mock", "source": "srt_live", "state": "ready"},
        {"provider": "srt", "source": "unknown", "state": "ready"},
        {"provider": "srt", "source": "srt_live", "state": "unknown"},
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "unknown",
            "retry_after_seconds": 1,
        },
        {"provider": "srt", "source": "srt_live", "state": "cooldown"},
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "source_unavailable",
        },
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "retry_after_seconds": 1,
        },
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "ready",
            "cause": "source_unavailable",
        },
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "ready",
            "retry_after_seconds": 1,
        },
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "source_unavailable",
            "retry_after_seconds": 0,
        },
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "source_unavailable",
            "retry_after_seconds": 1.5,
        },
    ],
)
def test_seat_status_source_preserves_existing_rejections(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schemas.SeatStatusSourceStatus.model_validate(payload)


def test_seat_status_source_preserves_existing_permissive_coercions() -> None:
    permissive = schemas.SeatStatusSourceStatus.model_validate(
        {
            "provider": "korail",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "source_unavailable",
            "retry_after_seconds": "2",
            "ignored": "legacy-extra",
        }
    )
    assert permissive.source == "srt_live"
    assert permissive.retry_after_seconds == 2
    assert not hasattr(permissive, "ignored")

    integral_float = schemas.SeatStatusSourceStatus.model_validate(
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "source_unavailable",
            "retry_after_seconds": 2.0,
        }
    )
    assert integral_float.retry_after_seconds == 2

    boolean = schemas.SeatStatusSourceStatus.model_validate(
        {
            "provider": "srt",
            "source": "srt_live",
            "state": "cooldown",
            "cause": "source_unavailable",
            "retry_after_seconds": True,
        }
    )
    assert boolean.retry_after_seconds == 1

    @dataclass
    class SourceAttributes:
        provider: str = "srt"
        source: str = "srt_live"
        state: str = "ready"

    assert schemas.SeatStatusSourceStatus.model_validate(SourceAttributes()).state == "ready"


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first", "http-first"])
def test_seat_status_source_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    from rail_waitlist.seat_status_operations import schemas as canonical
    from rail_waitlist import schemas as legacy
    from rail_waitlist.seat_status_operations import http
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import schemas as legacy
    from rail_waitlist.seat_status_operations import schemas as canonical
    from rail_waitlist.seat_status_operations import http
else:
    from rail_waitlist.seat_status_operations import http
    from rail_waitlist.seat_status_operations import schemas as canonical
    from rail_waitlist import schemas as legacy

print(json.dumps({
    "cause": legacy.SeatStatusCooldownCause is canonical.SeatStatusCooldownCause,
    "legacy": legacy.SeatStatusSourceStatus is canonical.SeatStatusSourceStatus,
    "http": http.SeatStatusSourceStatus is canonical.SeatStatusSourceStatus,
    "module": canonical.SeatStatusSourceStatus.__module__,
    "title": canonical.SeatStatusSourceStatus.model_json_schema()["title"],
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
        "cause": True,
        "http": True,
        "legacy": True,
        "module": "rail_waitlist.seat_status_operations.schemas",
        "title": "SeatStatusSourceStatus",
    }
