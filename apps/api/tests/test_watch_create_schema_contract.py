from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from rail_waitlist import schemas as legacy_schemas
from rail_waitlist import services
from rail_waitlist.domain import Provider, ReservationPolicy, SeatClass, SeatObservationMode
from rail_waitlist.main import create_app
from rail_waitlist.schema_base import ApiModel
from rail_waitlist.watch_management import create_application, http, schemas

API_ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
FUTURE_DATE = datetime.now(KST).date() + timedelta(days=7)
CANDIDATE_FIELDS = [
    "train_number",
    "departure_at",
    "arrival_at",
    "seat_class",
    "priority",
    "registration_evidence_id",
]
WATCH_FIELDS = [
    "provider",
    "origin",
    "origin_node_id",
    "destination",
    "destination_node_id",
    "travel_date",
    "time_from",
    "time_to",
    "seat_class",
    "passenger_count",
    "train_numbers",
    "candidates",
    "notification_channel_ids",
    "mode",
    "reservation_policy",
    "seat_observation_mode",
    "focused_observation_interval_seconds",
]


def _future_date() -> date:
    return FUTURE_DATE


def _candidate_payload(**overrides: object) -> dict[str, object]:
    travel_date = _future_date()
    payload: dict[str, object] = {
        "train_number": "001",
        "departure_at": datetime.combine(travel_date, time(9), tzinfo=KST),
        "arrival_at": datetime.combine(travel_date, time(10), tzinfo=KST),
        "seat_class": "standard",
        "priority": 1,
    }
    payload.update(overrides)
    return payload


def _watch_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "mock",
        "origin": "서울",
        "destination": "부산",
        "travel_date": _future_date(),
        "time_from": "08:00:00",
        "time_to": "18:00:00",
    }
    payload.update(overrides)
    return payload


def test_watch_create_models_have_one_canonical_identity_and_nesting() -> None:
    candidate = schemas.WatchCandidateCreate
    watch = schemas.WatchCreate

    assert legacy_schemas.WatchCandidateCreate is candidate
    assert legacy_schemas.WatchCreate is watch
    assert services.WatchCreate is watch
    assert http.WatchCreate is watch
    assert create_application.WatchCreate is watch
    assert candidate.__module__ == "rail_waitlist.watch_management.schemas"
    assert watch.__module__ == "rail_waitlist.watch_management.schemas"
    assert candidate.__bases__ == (ApiModel,)
    assert watch.__bases__ == (ApiModel,)
    assert list(candidate.model_fields) == CANDIDATE_FIELDS
    assert list(watch.model_fields) == WATCH_FIELDS
    assert get_args(watch.model_fields["candidates"].annotation)[0] is candidate


def test_watch_create_default_lists_are_fresh_and_defaults_are_preserved() -> None:
    first = schemas.WatchCreate.model_validate(_watch_payload())
    second = schemas.WatchCreate.model_validate(_watch_payload())

    assert first.train_numbers == []
    assert first.candidates == []
    assert first.notification_channel_ids == []
    assert first.train_numbers is not second.train_numbers
    assert first.candidates is not second.candidates
    assert first.notification_channel_ids is not second.notification_channel_ids
    assert first.seat_class is SeatClass.STANDARD
    assert first.passenger_count == 1
    assert first.mode == "official"
    assert first.reservation_policy is ReservationPolicy.NOTIFY_ONLY
    assert first.seat_observation_mode is SeatObservationMode.BALANCED
    assert first.focused_observation_interval_seconds == 25


def test_watch_create_json_schema_fingerprints_are_preserved() -> None:
    candidate = schemas.WatchCandidateCreate.model_json_schema()
    watch = schemas.WatchCreate.model_json_schema()

    assert candidate["title"] == "WatchCandidateCreate"
    assert candidate["required"] == ["train_number", "departure_at", "seat_class", "priority"]
    assert list(candidate["properties"]) == CANDIDATE_FIELDS
    assert candidate["properties"]["train_number"]["minLength"] == 1
    assert candidate["properties"]["train_number"]["maxLength"] == 40
    assert candidate["properties"]["priority"]["minimum"] == 1
    assert candidate["properties"]["priority"]["maximum"] == 20
    assert candidate["properties"]["registration_evidence_id"]["anyOf"][0] == {
        "maxLength": 36,
        "minLength": 1,
        "type": "string",
    }

    assert watch["title"] == "WatchCreate"
    assert watch["required"] == [
        "provider",
        "origin",
        "destination",
        "travel_date",
        "time_from",
        "time_to",
    ]
    assert list(watch["properties"]) == WATCH_FIELDS
    assert watch["properties"]["candidates"]["items"] == {"$ref": "#/$defs/WatchCandidateCreate"}
    assert watch["properties"]["candidates"]["maxItems"] == 20
    assert watch["properties"]["train_numbers"]["maxItems"] == 20
    assert watch["properties"]["notification_channel_ids"]["maxItems"] == 20
    assert "default" not in watch["properties"]["candidates"]
    assert "default" not in watch["properties"]["train_numbers"]
    assert "default" not in watch["properties"]["notification_channel_ids"]
    assert watch["properties"]["mode"]["pattern"] == "^(official|experimental)$"


def test_watch_candidate_normalizes_only_train_number_and_preserves_offset() -> None:
    departure = datetime.combine(_future_date(), time(9), tzinfo=KST)
    candidate = schemas.WatchCandidateCreate.model_validate(
        _candidate_payload(
            train_number=" 001 ",
            departure_at=departure,
            arrival_at=None,
            registration_evidence_id=" ",
            priority="1",
            future_field="ignored",
        )
    )

    assert candidate.train_number == "001"
    assert candidate.departure_at == departure
    assert candidate.departure_at.utcoffset() == timedelta(hours=9)
    assert candidate.arrival_at is None
    assert candidate.registration_evidence_id == " "
    assert candidate.priority == 1
    assert not hasattr(candidate, "future_field")


def test_watch_create_preserves_existing_permissive_schema_values() -> None:
    travel_date = _future_date()
    candidates = [
        _candidate_payload(
            train_number="002",
            departure_at=datetime.combine(travel_date, time(10), tzinfo=KST),
            arrival_at=None,
            seat_class="any",
            priority=2,
        ),
        _candidate_payload(
            train_number="001",
            departure_at=datetime.combine(travel_date, time(9), tzinfo=KST),
            arrival_at=None,
            seat_class="any",
            priority=1,
        ),
    ]
    watch = schemas.WatchCreate.model_validate(
        _watch_payload(
            origin=" 서울 ",
            origin_node_id=" SAME ",
            destination_node_id="SAME",
            seat_class="any",
            passenger_count="2",
            train_numbers=["001", "001", "002"],
            candidates=candidates,
            mode="experimental",
            focused_observation_interval_seconds="20",
            future_field="ignored",
        )
    )

    assert watch.origin == " 서울 "
    assert watch.origin_node_id == "SAME"
    assert watch.destination_node_id == "SAME"
    assert watch.provider is Provider.MOCK
    assert watch.seat_class is SeatClass.ANY
    assert watch.passenger_count == 2
    assert watch.train_numbers == ["001", "001", "002"]
    assert [candidate.priority for candidate in watch.candidates] == [2, 1]
    assert [candidate.train_number for candidate in watch.candidates] == ["002", "001"]
    assert watch.mode == "experimental"
    assert watch.focused_observation_interval_seconds == 20
    assert not hasattr(watch, "future_field")

    official_without_evidence = schemas.WatchCreate.model_validate(
        _watch_payload(
            provider="korail",
            origin_node_id=" N-SEOUL ",
            destination_node_id="N-BUSAN",
            train_numbers=["001"],
            candidates=[_candidate_payload(registration_evidence_id=None)],
        )
    )
    assert official_without_evidence.candidates[0].registration_evidence_id is None

    candidate_free = schemas.WatchCreate.model_validate(
        _watch_payload(
            train_numbers=["", "001", "001"],
            candidates=[],
            notification_channel_ids=["", "channel-1", "channel-1"],
        )
    )
    assert candidate_free.train_numbers == ["", "001", "001"]
    assert candidate_free.notification_channel_ids == ["", "channel-1", "channel-1"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"train_number": " "},
        {"train_number": "x" * 41},
        {"departure_at": datetime.combine(_future_date(), time(9))},
        {"arrival_at": datetime.combine(_future_date(), time(10))},
        {
            "arrival_at": datetime.combine(_future_date(), time(9), tzinfo=KST),
        },
        {"priority": 0},
        {"priority": 21},
        {"seat_class": "premium_unknown"},
        {"registration_evidence_id": ""},
        {"registration_evidence_id": "x" * 37},
    ],
)
def test_watch_candidate_preserves_existing_rejections(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schemas.WatchCandidateCreate.model_validate(_candidate_payload(**overrides))


def test_watch_create_preserves_route_time_and_provider_rejections() -> None:
    past_date = datetime.now(KST).date() - timedelta(days=1)
    invalid_payloads = [
        _watch_payload(destination=" 서울 "),
        _watch_payload(time_to="08:00:00"),
        _watch_payload(time_from="19:00:00", time_to="18:00:00"),
        _watch_payload(travel_date=past_date),
        _watch_payload(origin_node_id="ONLY"),
        _watch_payload(provider="korail"),
        _watch_payload(provider="srt", origin_node_id="SAME", destination_node_id="SAME"),
        _watch_payload(passenger_count=0),
        _watch_payload(passenger_count=10),
        _watch_payload(mode="private"),
        _watch_payload(seat_class="premium_unknown"),
        _watch_payload(seat_observation_mode="aggressive"),
        _watch_payload(focused_observation_interval_seconds=19),
        _watch_payload(focused_observation_interval_seconds=31),
        _watch_payload(train_numbers=["1"] * 21),
        _watch_payload(notification_channel_ids=["1"] * 21),
    ]

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            schemas.WatchCreate.model_validate(payload)


def test_watch_create_preserves_candidate_set_priority_and_window_rejections() -> None:
    travel_date = _future_date()
    first = _candidate_payload()
    second = _candidate_payload(
        train_number="002",
        departure_at=datetime.combine(travel_date, time(10), tzinfo=KST),
        arrival_at=None,
        priority=2,
    )
    invalid_payloads = [
        _watch_payload(train_numbers=["001"], candidates=[first, first]),
        _watch_payload(
            train_numbers=["001", "002"],
            candidates=[first, {**second, "priority": 3}],
        ),
        _watch_payload(
            train_numbers=["001", "002"],
            candidates=[first, {**second, "priority": 1}],
        ),
        _watch_payload(
            seat_class="first",
            train_numbers=["001"],
            candidates=[first],
        ),
        _watch_payload(train_numbers=["999"], candidates=[first]),
        _watch_payload(
            train_numbers=["001"],
            candidates=[
                {
                    **first,
                    "departure_at": datetime.combine(
                        travel_date + timedelta(days=1), time(9), tzinfo=KST
                    ),
                }
            ],
        ),
        _watch_payload(
            train_numbers=["001"],
            candidates=[
                {
                    **first,
                    "departure_at": datetime.combine(travel_date, time(7, 59), tzinfo=KST),
                }
            ],
        ),
        _watch_payload(
            candidates=[_candidate_payload(**{"priority": index}) for index in range(1, 22)]
        ),
    ]

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            schemas.WatchCreate.model_validate(payload)


def test_watch_create_accepts_overnight_arrival_with_service_date_end_window() -> None:
    travel_date = _future_date()
    watch = schemas.WatchCreate.model_validate(
        _watch_payload(
            provider="korail",
            origin="대전",
            origin_node_id="N-DAEJEON",
            destination="서울",
            destination_node_id="N-SEOUL",
            travel_date=travel_date,
            time_from="23:00:00",
            time_to="23:59:59",
            train_numbers=["222"],
            candidates=[
                _candidate_payload(
                    train_number="222",
                    departure_at=datetime.combine(travel_date, time(23), tzinfo=KST),
                    arrival_at=datetime.combine(
                        travel_date + timedelta(days=1), time(0, 7), tzinfo=KST
                    ),
                )
            ],
        )
    )

    assert watch.time_from == time(23)
    assert watch.time_to == time(23, 59, 59)
    assert watch.candidates[0].arrival_at.date() == travel_date + timedelta(days=1)


def test_watch_create_openapi_components_and_post_reference_are_preserved() -> None:
    openapi = create_app().openapi()
    candidate = openapi["components"]["schemas"]["WatchCandidateCreate"]
    watch = openapi["components"]["schemas"]["WatchCreate"]
    post = openapi["paths"]["/api/v1/watches"]["post"]

    assert candidate["required"] == ["train_number", "departure_at", "seat_class", "priority"]
    assert watch["required"] == [
        "provider",
        "origin",
        "destination",
        "travel_date",
        "time_from",
        "time_to",
    ]
    assert watch["properties"]["candidates"]["items"] == {
        "$ref": "#/components/schemas/WatchCandidateCreate"
    }
    assert post["requestBody"] == {
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WatchCreate"}}},
        "required": True,
    }
    assert set(post["responses"]) == {"201", "422"}


@pytest.mark.parametrize(
    "import_order",
    ["canonical-first", "legacy-first", "services-first", "http-first", "application-first"],
)
def test_watch_create_import_orders_keep_exact_identity(import_order: str) -> None:
    script = r"""
import json
import sys
from typing import get_args

if sys.argv[1] == "canonical-first":
    from rail_waitlist.watch_management import schemas as canonical
elif sys.argv[1] == "legacy-first":
    from rail_waitlist import schemas as legacy
elif sys.argv[1] == "services-first":
    from rail_waitlist import services
elif sys.argv[1] == "http-first":
    from rail_waitlist.watch_management import http
else:
    from rail_waitlist.watch_management import create_application

from rail_waitlist import schemas as legacy
from rail_waitlist import services
from rail_waitlist.watch_management import create_application, http, schemas as canonical

print(json.dumps({
    "candidate": legacy.WatchCandidateCreate is canonical.WatchCandidateCreate,
    "create": legacy.WatchCreate is canonical.WatchCreate,
    "services": services.WatchCreate is canonical.WatchCreate,
    "http": http.WatchCreate is canonical.WatchCreate,
    "application": create_application.WatchCreate is canonical.WatchCreate,
    "nested": get_args(canonical.WatchCreate.model_fields["candidates"].annotation)[0]
        is canonical.WatchCandidateCreate,
    "module": canonical.WatchCreate.__module__,
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
        "candidate": True,
        "create": True,
        "http": True,
        "module": "rail_waitlist.watch_management.schemas",
        "nested": True,
        "services": True,
    }
