from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

from rail_waitlist.event_stream.http import event_wire
from rail_waitlist.event_stream.schemas import EventRead as CanonicalEventRead
from rail_waitlist.models import OutboxEvent
from rail_waitlist.schemas import EventRead


def test_legacy_event_read_export_is_the_exact_canonical_object() -> None:
    assert EventRead is CanonicalEventRead


def test_event_read_preserves_fields_from_attributes_and_datetime_semantics() -> None:
    aware_created_at = datetime(2026, 8, 6, 10, 2, 3, tzinfo=timezone(timedelta(hours=9)))
    event = CanonicalEventRead.model_validate(
        SimpleNamespace(
            id="event-1",
            event_type="watch.updated",
            aggregate_id="watch-1",
            payload={"watch_id": "watch-1"},
            created_at=aware_created_at,
        )
    )

    assert list(CanonicalEventRead.model_fields) == [
        "id",
        "event_type",
        "aggregate_id",
        "payload",
        "created_at",
    ]
    assert event.created_at == aware_created_at
    assert event.model_dump_json() == (
        '{"id":"event-1","event_type":"watch.updated","aggregate_id":"watch-1",'
        '"payload":{"watch_id":"watch-1"},"created_at":"2026-08-06T10:02:03+09:00"}'
    )

    naive_created_at = datetime(2026, 8, 6, 1, 2, 3)  # noqa: DTZ001 - compatibility case
    naive_event = CanonicalEventRead(
        id="event-2",
        event_type="watch.created",
        aggregate_id="watch-2",
        payload={},
        created_at=naive_created_at,
    )
    assert naive_event.created_at.tzinfo is None
    assert naive_event.model_dump_json().endswith('"created_at":"2026-08-06T01:02:03"}')

    utc_event = CanonicalEventRead(
        id="event-3",
        event_type="watch.expired",
        aggregate_id="watch-3",
        payload={},
        created_at=datetime(2026, 8, 6, 1, 2, 3, tzinfo=UTC),
    )
    assert utc_event.model_dump_json().endswith('"created_at":"2026-08-06T01:02:03Z"}')


def test_event_wire_preserves_the_sse_envelope_and_event_json_contract() -> None:
    event = OutboxEvent(
        id="event-1",
        aggregate_type="watch",
        aggregate_id="watch-1",
        event_type="watch.updated",
        payload={"watch_id": "watch-1"},
        dedupe_key="event-wire-contract",
        created_at=datetime(2026, 8, 6, 1, 2, 3, tzinfo=UTC),
    )

    lines = event_wire(event).splitlines()
    assert lines[:2] == ["id: event-1", "event: watch.updated"]
    assert lines[3:] == [""]
    assert json.loads(lines[2].removeprefix("data: ")) == {
        "id": "event-1",
        "event_type": "watch.updated",
        "aggregate_id": "watch-1",
        "payload": {"watch_id": "watch-1"},
        "created_at": "2026-08-06T01:02:03Z",
    }
