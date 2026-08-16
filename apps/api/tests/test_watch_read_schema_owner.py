from __future__ import annotations

import ast
import base64
import hashlib
import json
import pickle
import subprocess
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from rail_waitlist import schemas as legacy
from rail_waitlist.domain import (
    BookingWindowStatus,
    OperationalStatus,
    ReservationOutcome,
    ReservationResultReasonCode,
    SeatClass,
    SeatObservationStatus,
)
from rail_waitlist.observations.contracts import ObservationErrorCategory
from rail_waitlist.timetable_management.schemas import TimetableSeatEvidenceRead
from rail_waitlist.watch_management import schemas as canonical

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
WATCH_READ_SYMBOLS = {
    "WatchCandidateLatestReservationAttemptRead",
    "WatchCandidateRead",
    "WatchCandidateLatestObservationRead",
    "WatchRead",
}
LEGACY_WATCH_READ_PICKLE_ZLIB = (
    "eNqFVktvHEUQdsLa+3DsOI6dF0gIJUgJkm0MFwQIKTgWKE52g60op9DqnendajzTPfT07HoRkfAh"
    "L9QSkdIgDhw4IOCAhDhx4sQhPP4ABw6c+RVU98x6Z+2Q2JI9U/1Vd9VX9VXPJ5XPdxsT/scsKsoj"
    "0qdcRzzVy2kALKapNfUbVAewyWhoH9oLu/aWPW9qhIQ80IT4t8M8tOZI38GWItalwcCaWqJkj4dM"
    "WbMwvnEoY8oFIq4NEQ+tqcQy2La37aY1U1LxrgNUL1H2AZP4NJubiJAhI/6wq621jaVLF9cvr7ea"
    "1kyHLNVcUM0denKLySyy5njJOnJteNet9db1K+ioFe2xiIRUM4zI/dM8xseKtzy0a5Xqv7VaHljd"
    "LZGOkrGF06bigYiYmvL85Ziqx2hp4YW1qVppoZEyqkkQ0TS10DD1LXxd82+YfS3VVIRUhTn2aIJ2"
    "JrpMkUBmQtuNQ2YGI3UMZHGbqdTeRCefyOrqa5aaRoD+3MXslmDCzPuirQ2tY9WDSfenaub2nPaq"
    "dqR8ioVXzJGQJVTpTDFCtUt7xBGm3nDkTJ31Wbo1Z/9ICreGr56vkEWa4vvGhPu9i+m5FO+4NE+4"
    "FguziIVk/JS3xjd+J0e7YsZ4+D500xyngc5otN88g0fTAYm5yBwtTTMvE6Z8NyAYGdeZL8Wx1si8"
    "lVuxJPW94PKaLLal3OaiS/pchLJf8j/+dr5ywy+MdqjgcSJ3Hj9ZZirAFlvISV+SfcHUUofvuNCt"
    "OVnGynbKVA9THtFSzeU6pGUM3lEsBYIdw6MRfLIMb1CleA+hJZ6rqyUEXIBlJ16OktMD13mTLlWn"
    "jmEw1pxJsyTBs1KMrD0ge33kBNbEQcK6KPQ8KsKcxgUmjAWIXIPqIqtcrdirZ8d79YoHtUaYg81r"
    "pkb0LzolldClEtIeDh7ajlgxWArm4X0z/XRip59O5ixTSqJGMeCuVAPbzMw8IckgpOgUELaDHOCI"
    "bJqTJWuHsyhMScrc9PwME9qFW/Ax9OD2A7NQwiWuTkgp+mdtc6agDmMaJoqhaxYn2lH40uMo3Bxh"
    "L+bQxzBZlZkOJMoZqZwvebQKM/I4I6Qm+7hcCKTocBXnkQz3MHUH7eDMcoMWa6T0kynmgqewD3Ko"
    "DHk2kZh1iHFHXDBMH88NeLTP5fCYIhI6iJnQBGQUEiZC9KKpuxjOF0Gj9xhGSBJJP25dU6MZE1FM"
    "q4HP955ZjKlwAwYnQrCNu32YcYUyuG+OehQOaeQ9v3tOCdYfcsUjVBBhCU/x7rEZ3GnCXVfwb+Eb"
    "+Bq+hO/gAXwBX8H3D+A+1ngEuAzvQRMIXIM3IIJPIYEbKMsqtCCALrwJN2E7d6F4ntS8w4O8DgFQ"
    "IfA646G/IerF+9IqXhF4w4ZexR2Ec4r343y5mRIZcbwB3DwsdcG13IpNMO0PGhApokExEf2NVtIy"
    "8Se4ibhPkled3V1zbRpRHAXFSD3XkUHmRkh5Dy60e8Yx6UqNeWycRm00zLTv8JG4a/5zA8duvteM"
    "QLUVBfrfZpoNJJYb522h6qaZG/bBsMPy8XVAYxjyPTObCUdoFyNWmcDIJszCkE0yvB4yhcweG6p4"
    "WTDdl2obP6MaF8XgXa2T6wgoJGgqOfy5PdEHUrFlMvZqzTO5izkH6J6+vrLCdmicRGyZCySKhyvu"
    "22mpCADp2LVpG78IsOufqL5GloQHEGN8HcXvk4LUJ2w06tsT8Bf8hp36MvwIz8Pv8CIswB/wA/yD"
    "vduDV6EOj+BXmIO/4RSswM/wC8zDn/BT3svL/wFMAxai"
)


def _attempt_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "outcome": ReservationOutcome.NOT_AVAILABLE,
        "confirmation_outcome": "not_found",
        "started_at": "2026-08-07T00:00:00Z",
        "finished_at": "2026-08-07T00:01:00Z",
        "progress_stages": [],
        "reserved_seats": [],
        "post_deadline_reconciled_at": "2026-08-07T00:02:00Z",
        "payment_hold_end_reason": "confirmed_payment_hold_no_longer_present",
        "retryable": True,
        "manual_check_required": False,
        "retry_condition": "new_availability_episode",
    }
    payload.update(overrides)
    return payload


def _candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "candidate-1",
        "train_number": "MOCK-118",
        "train_type": None,
        "departure_at": "2026-08-08T06:35:00Z",
        "scheduled_departure_at": "2026-08-08T06:35:00Z",
        "estimated_departure_at": None,
        "actual_departure_at": None,
        "delay_minutes": None,
        "operational_status": OperationalStatus.SCHEDULED,
        "booking_window_status": BookingWindowStatus.OPEN,
        "operational_source": "watch-read-owner-test",
        "operational_observed_at": "2026-08-07T00:00:00Z",
        "operational_fresh_until": "2026-08-07T00:05:00Z",
        "arrival_at": "2026-08-08T07:49:00Z",
        "seat_class": SeatClass.STANDARD,
        "priority": 1,
        "state": "observed",
        "latest_observation": {
            "status": SeatObservationStatus.AVAILABLE,
            "source": "watch-read-owner-test",
            "observed_at": "2026-08-07T00:00:00Z",
            "fresh_until": "2026-08-07T00:05:00Z",
            "error_category": None,
        },
        "latest_reservation_attempt": _attempt_payload(),
    }
    payload.update(overrides)
    return payload


def test_central_schema_hub_preserves_exact_watch_read_aliases() -> None:
    for symbol in WATCH_READ_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)
    assert not hasattr(canonical, "AutomaticReservationRetryFenceReason")


def test_watch_read_contracts_have_canonical_owners_and_nested_identities() -> None:
    models = (
        canonical.WatchCandidateLatestReservationAttemptRead,
        canonical.WatchCandidateRead,
        canonical.WatchCandidateLatestObservationRead,
        canonical.WatchRead,
    )
    assert {model.__module__ for model in models} == {"rail_waitlist.watch_management.schemas"}
    assert canonical.WatchCandidateRead in get_args(
        canonical.WatchRead.model_fields["candidates"].annotation
    )
    latest_observation_annotation = canonical.WatchCandidateRead.model_fields[
        "latest_observation"
    ].annotation
    assert getattr(latest_observation_annotation, "__forward_arg__", None) == (
        "'WatchCandidateLatestObservationRead | None'"
    )
    assert canonical.WatchCandidateLatestReservationAttemptRead in get_args(
        canonical.WatchCandidateRead.model_fields["latest_reservation_attempt"].annotation
    )
    assert TimetableSeatEvidenceRead in get_args(
        canonical.WatchCandidateRead.model_fields["registration_evidence"].annotation
    )
    assert ObservationErrorCategory in get_args(
        canonical.WatchCandidateLatestObservationRead.model_fields["error_category"].annotation
    )


def test_watch_read_contract_fields_and_defaults_are_unchanged() -> None:
    assert tuple(canonical.WatchCandidateLatestReservationAttemptRead.model_fields) == (
        "outcome",
        "result_reason_code",
        "confirmation_outcome",
        "confirmation_diagnostic_code",
        "confirmation_observed_at",
        "reconciliation_attempt_count",
        "reconciliation_resolution",
        "next_reconcile_at",
        "started_at",
        "finished_at",
        "progress_stages",
        "reserved_seats",
        "post_deadline_reconciled_at",
        "payment_hold_end_reason",
        "automatic_reservation_retry_fence_reason",
        "retryable",
        "manual_check_required",
        "manual_rearm_available",
        "manual_rearm_reason",
        "retry_condition",
    )
    assert tuple(canonical.WatchCandidateRead.model_fields) == (
        "id",
        "train_number",
        "train_type",
        "departure_at",
        "scheduled_departure_at",
        "estimated_departure_at",
        "actual_departure_at",
        "delay_minutes",
        "operational_status",
        "booking_window_status",
        "operational_source",
        "operational_observed_at",
        "operational_fresh_until",
        "arrival_at",
        "seat_class",
        "priority",
        "state",
        "suppressed_by_candidate_id",
        "registration_evidence",
        "latest_observation",
        "latest_reservation_attempt",
    )
    assert tuple(canonical.WatchCandidateLatestObservationRead.model_fields) == (
        "status",
        "source",
        "observed_at",
        "fresh_until",
        "error_category",
    )
    assert tuple(canonical.WatchRead.model_fields) == (
        "id",
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
        "status",
        "next_check_at",
        "observation_execution_state",
        "cooldown_until",
        "payment_deadline",
        "reservation_attempted",
        "unchanged_runs",
        "official_booking_url",
        "created_at",
        "updated_at",
        "last_checked_at",
    )
    assert canonical.WatchCandidateRead.model_fields["delay_minutes"].default is None
    assert canonical.WatchCandidateRead.model_fields["latest_observation"].default is None
    assert canonical.WatchCandidateRead.model_fields["latest_reservation_attempt"].default is None
    assert canonical.WatchRead.model_fields["last_checked_at"].default is None
    assert canonical.WatchRead.model_fields["observation_execution_state"].default == "idle"
    assert (
        canonical.WatchCandidateLatestReservationAttemptRead.model_fields[
            "result_reason_code"
        ].default
        is ReservationResultReasonCode.RESERVATION_PENDING
    )
    assert (
        canonical.WatchCandidateLatestReservationAttemptRead.model_fields[
            "reconciliation_attempt_count"
        ].default
        == 0
    )
    confirmation_schema = canonical.WatchCandidateLatestReservationAttemptRead.model_json_schema()[
        "properties"
    ]["confirmation_outcome"]["anyOf"][0]
    assert confirmation_schema["enum"] == [
        "confirmed_payment_required",
        "confirmed_paid",
        "not_found",
        "auth_required",
        "provider_blocked",
        "inconclusive",
    ]
    diagnostic_schema = canonical.WatchCandidateLatestReservationAttemptRead.model_json_schema()[
        "$defs"
    ]["ReservationConfirmationDiagnosticCode"]
    assert diagnostic_schema["enum"] == [
        "official_read_unavailable",
        "credential_context_mismatch",
        "official_record_ambiguous",
        "official_evidence_insufficient",
        "unspecified",
    ]
    retry_fence_schema = canonical.WatchCandidateLatestReservationAttemptRead.model_json_schema()[
        "$defs"
    ]["AutomaticReservationRetryFenceReason"]
    assert retry_fence_schema["enum"] == ["confirmed_absent_recovery_consumed"]
    execution_state_annotation = canonical.WatchRead.model_fields[
        "observation_execution_state"
    ].annotation
    assert set(get_args(execution_state_annotation)) == {
        "idle",
        "in_progress",
    }
    assert all(
        model.model_config == {"from_attributes": True}
        for model in (
            canonical.WatchCandidateLatestReservationAttemptRead,
            canonical.WatchCandidateRead,
            canonical.WatchCandidateLatestObservationRead,
            canonical.WatchRead,
        )
    )


def test_watch_read_normalizes_only_legacy_inconclusive_diagnostics() -> None:
    legacy = canonical.WatchCandidateLatestReservationAttemptRead.model_validate(
        _attempt_payload(
            outcome=ReservationOutcome.UNKNOWN,
            confirmation_outcome="inconclusive",
        )
    )
    assert legacy.confirmation_diagnostic_code is not None
    assert legacy.confirmation_diagnostic_code.value == "unspecified"

    with pytest.raises(ValueError, match="requires an inconclusive"):
        canonical.WatchCandidateLatestReservationAttemptRead.model_validate(
            _attempt_payload(
                confirmation_outcome="not_found",
                confirmation_diagnostic_code="official_read_unavailable",
            )
        )


@pytest.mark.parametrize(
    ("model", "expected_sha256"),
    [
        (
            canonical.WatchCandidateLatestReservationAttemptRead,
            "cb596262e1941ad44ba22b24c43cabd420a1d63f23adb23127256e4a8594094d",
        ),
        (
            canonical.WatchCandidateRead,
            "7f355d778fdd0ba7ec41ad88bf4c24635ec1ed3913b0513bc52eea646321ca87",
        ),
        (
            canonical.WatchCandidateLatestObservationRead,
            "7bec2c9b52d4ddc527fc2f3794333e3a274f298c16828af9066391c8f25d9d91",
        ),
        (
            canonical.WatchRead,
            "7228b3ca7bf8a7c822e1e0e2646fcff1803a1e17505d9db1e6ebd0e601674a48",
        ),
    ],
)
def test_watch_read_json_schema_is_stable(model: type[BaseModel], expected_sha256: str) -> None:
    encoded = json.dumps(
        model.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected_sha256


def test_watch_read_timezone_and_fail_closed_validation_are_preserved() -> None:
    attempt = canonical.WatchCandidateLatestReservationAttemptRead(
        **_attempt_payload(
            started_at=datetime(2026, 8, 7, 0, 0),  # noqa: DTZ001 - DB compatibility.
            finished_at=datetime(2026, 8, 7, 0, 1),  # noqa: DTZ001 - DB compatibility.
        )
    )
    assert attempt.started_at.tzinfo is UTC
    assert attempt.finished_at is not None and attempt.finished_at.tzinfo is UTC

    candidate = canonical.WatchCandidateRead(
        **_candidate_payload(
            departure_at=datetime(2026, 8, 8, 6, 35)  # noqa: DTZ001 - DB compatibility.
        )
    )
    assert candidate.departure_at.tzinfo is UTC
    assert isinstance(candidate.latest_observation, canonical.WatchCandidateLatestObservationRead)
    assert isinstance(
        candidate.latest_reservation_attempt,
        canonical.WatchCandidateLatestReservationAttemptRead,
    )

    invalid_attempts = (
        {"finished_at": "2026-08-06T23:59:00Z"},
        {"post_deadline_reconciled_at": "2026-08-07T00:02:00"},
        {"retry_condition": "retry_immediately"},
        {"automatic_reservation_retry_fence_reason": "not_a_closed_reason"},
        {
            "automatic_reservation_retry_fence_reason": ("confirmed_absent_recovery_consumed"),
        },
        {
            "progress_stages": [
                {"stage": "seat_selected", "occurred_at": "2026-08-07T00:00:10Z"},
                {"stage": "target_rechecked", "occurred_at": "2026-08-07T00:00:20Z"},
            ]
        },
    )
    for updates in invalid_attempts:
        with pytest.raises(ValidationError):
            canonical.WatchCandidateLatestReservationAttemptRead(**_attempt_payload(**updates))

    invalid_candidates = (
        {"operational_fresh_until": None},
        {"operational_fresh_until": "2026-08-06T23:59:00Z"},
        {"operational_source": "not allowed"},
        {"delay_minutes": -1},
    )
    for updates in invalid_candidates:
        with pytest.raises(ValidationError):
            canonical.WatchCandidateRead(**_candidate_payload(**updates))

    with pytest.raises(ValidationError):
        canonical.WatchRead.model_validate(
            {
                "id": "watch-1",
                "provider": "mock",
                "origin": "Daejeon",
                "origin_node_id": None,
                "destination": "Seoul",
                "destination_node_id": None,
                "travel_date": "2026-08-08",
                "time_from": "06:00:00",
                "time_to": "08:00:00",
                "seat_class": "standard",
                "passenger_count": 1,
                "train_numbers": [],
                "candidates": [],
                "notification_channel_ids": [],
                "mode": "official",
                "reservation_policy": "notify_only",
                "seat_observation_mode": "balanced",
                "focused_observation_interval_seconds": 25,
                "status": "watching",
                "next_check_at": None,
                "cooldown_until": None,
                "payment_deadline": None,
                "reservation_attempted": False,
                "unchanged_runs": 0,
                "official_booking_url": "http://example.invalid/mock-booking",
                "created_at": "2026-08-07T00:00:00Z",
                "updated_at": "2026-08-07T00:00:00Z",
            }
        )


def test_pre_move_legacy_watch_pickle_restores_the_exact_nested_contracts() -> None:
    compressed = base64.b64decode(LEGACY_WATCH_READ_PICKLE_ZLIB)
    watch = pickle.loads(zlib.decompress(compressed))

    assert isinstance(watch, canonical.WatchRead)
    assert isinstance(watch.candidates[0], canonical.WatchCandidateRead)
    assert isinstance(
        watch.candidates[0].latest_observation,
        canonical.WatchCandidateLatestObservationRead,
    )
    assert isinstance(
        watch.candidates[0].latest_reservation_attempt,
        canonical.WatchCandidateLatestReservationAttemptRead,
    )
    assert pickle.loads(pickle.dumps(watch)) == watch


@pytest.mark.parametrize(
    "imports",
    [
        "from rail_waitlist.watch_management import schemas as owner",
        (
            "from rail_waitlist import schemas; "
            "from rail_waitlist.watch_management import schemas as owner"
        ),
        (
            "import rail_waitlist.services; "
            "from rail_waitlist.watch_management import schemas as owner"
        ),
        (
            "import rail_waitlist.watch_management.http; "
            "from rail_waitlist.watch_management import schemas as owner"
        ),
        (
            "import rail_waitlist.watch_management.read_model; "
            "from rail_waitlist.watch_management import schemas as owner"
        ),
    ],
)
def test_watch_read_contract_identity_is_import_order_independent(imports: str) -> None:
    script = f"""
import sys
from typing import get_args
{imports}
canonical_first = {imports!r}.startswith('from rail_waitlist.watch_management')
if canonical_first:
    assert 'rail_waitlist.schemas' not in sys.modules
from rail_waitlist import schemas
assert (
    schemas.WatchCandidateLatestReservationAttemptRead
    is owner.WatchCandidateLatestReservationAttemptRead
)
assert schemas.WatchCandidateRead is owner.WatchCandidateRead
assert schemas.WatchCandidateLatestObservationRead is owner.WatchCandidateLatestObservationRead
assert schemas.WatchRead is owner.WatchRead
assert owner.WatchCandidateRead in get_args(owner.WatchRead.model_fields['candidates'].annotation)
assert (
    owner.WatchCandidateRead.model_fields[
        'latest_observation'
    ].annotation.__forward_arg__
    == "'WatchCandidateLatestObservationRead | None'"
)
assert owner.WatchCandidateLatestReservationAttemptRead in get_args(
    owner.WatchCandidateRead.model_fields['latest_reservation_attempt'].annotation
)
assert owner.WatchRead.__module__ == 'rail_waitlist.watch_management.schemas'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_watch_read_owner_and_central_facade_have_exact_definition_boundaries() -> None:
    owner_path = SOURCE_ROOT / "rail_waitlist" / "watch_management" / "schemas.py"
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    assert {
        node.name for node in owner_tree.body if isinstance(node, ast.ClassDef)
    } & WATCH_READ_SYMBOLS == WATCH_READ_SYMBOLS
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "schemas" and node.level == 2
        for node in ast.walk(owner_tree)
    )

    facade_path = SOURCE_ROOT / "rail_waitlist" / "schemas.py"
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    assert not (
        {node.name for node in facade_tree.body if isinstance(node, ast.ClassDef)}
        & WATCH_READ_SYMBOLS
    )
    aliases: dict[str, tuple[str, str]] = {}
    for node in facade_tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id in WATCH_READ_SYMBOLS
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
        ):
            aliases[target.id] = (node.value.value.id, node.value.attr)
    assert aliases == {
        symbol: ("watch_management_schemas", symbol) for symbol in WATCH_READ_SYMBOLS
    }


def _attribute_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))


def _central_watch_read_references(source: str, relative_path: Path) -> list[str]:
    central_owner = ("rail_waitlist", "schemas")
    module_parts = list(relative_path.with_suffix("").parts)
    package_parts = module_parts[:-1]
    tree = ast.parse(source, filename=str(relative_path))
    violations: list[str] = []
    package_aliases: set[str] = set()
    schema_aliases: set[str] = set()
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = tuple(alias.name.split("."))
                if imported == ("rail_waitlist",):
                    package_aliases.add(alias.asname or "rail_waitlist")
                if imported == central_owner:
                    if alias.asname is None:
                        package_aliases.add("rail_waitlist")
                    else:
                        schema_aliases.add(alias.asname)
                if imported == ("importlib",):
                    importlib_aliases.add(alias.asname or "importlib")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            resolved = tuple((node.module or "").split("."))
        else:
            keep = len(package_parts) - (node.level - 1)
            imported_parts = tuple(part for part in (node.module or "").split(".") if part)
            resolved = (*package_parts[:keep], *imported_parts)
        imported_names = {alias.name for alias in node.names}
        if resolved == central_owner and (
            imported_names & WATCH_READ_SYMBOLS or "*" in imported_names
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> symbols")
        if resolved == ("rail_waitlist",):
            schema_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "schemas"
            )
        if resolved == ("importlib",):
            import_module_aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "import_module"
            )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if value is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(value, ast.Name) and value.id in schema_aliases:
                    before = len(schema_aliases)
                    schema_aliases.add(target.id)
                    changed = changed or len(schema_aliases) != before
                if isinstance(value, ast.Name) and value.id in package_aliases:
                    before = len(package_aliases)
                    package_aliases.add(target.id)
                    changed = changed or len(package_aliases) != before
                parts = _attribute_chain(value)
                if len(parts) == 2 and parts[0] in package_aliases and parts[1] == "schemas":
                    before = len(schema_aliases)
                    schema_aliases.add(target.id)
                    changed = changed or len(schema_aliases) != before

    def is_schema_reference(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in schema_aliases
        parts = _attribute_chain(node)
        return len(parts) == 2 and parts[0] in package_aliases and parts[1] == "schemas"

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = _attribute_chain(node)
            if len(parts) >= 2 and parts[0] in schema_aliases and parts[-1] in WATCH_READ_SYMBOLS:
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> schema-attribute")
            if (
                len(parts) >= 3
                and parts[0] in package_aliases
                and parts[1] == "schemas"
                and parts[-1] in WATCH_READ_SYMBOLS
            ):
                violations.append(f"{relative_path.as_posix()}:{node.lineno} -> package-attribute")
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and is_schema_reference(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in WATCH_READ_SYMBOLS
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> getattr")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.schemas"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> __import__")
        dynamic_import = (
            isinstance(node.func, ast.Name) and node.func.id in import_module_aliases
        ) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_aliases
            and node.func.attr == "import_module"
        )
        if (
            dynamic_import
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "rail_waitlist.schemas"
        ):
            violations.append(f"{relative_path.as_posix()}:{node.lineno} -> importlib")

    return violations


@pytest.mark.parametrize(
    "source",
    [
        "from .schemas import WatchRead",
        "from rail_waitlist.schemas import WatchCandidateRead",
        "from .schemas import *",
        "import rail_waitlist.schemas as schemas; schemas.WatchRead",
        "from rail_waitlist import schemas as s; s.WatchCandidateLatestObservationRead",
        "import rail_waitlist as rw; rw.schemas.WatchCandidateRead",
        "import importlib; importlib.import_module('rail_waitlist.schemas')",
        "from importlib import import_module; import_module('rail_waitlist.schemas')",
        "import rail_waitlist.schemas as s; alias = s; alias.WatchRead",
        "import rail_waitlist.schemas as s; getattr(s, 'WatchCandidateRead')",
        "__import__('rail_waitlist.schemas').schemas.WatchRead",
        "import rail_waitlist as rw; s = rw.schemas; s.WatchRead",
    ],
)
def test_central_watch_read_detector_rejects_all_access_forms(source: str) -> None:
    assert _central_watch_read_references(source, Path("rail_waitlist/probe.py"))


def test_production_watch_read_consumers_are_exact_and_do_not_reenter_central_hub() -> None:
    direct_consumers: dict[str, set[str]] = {}
    violations: list[str] = []
    central_path = Path("rail_waitlist/schemas.py")

    for module_path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        relative_path = module_path.relative_to(SOURCE_ROOT)
        source = module_path.read_text(encoding="utf-8")
        if relative_path != central_path:
            violations.extend(_central_watch_read_references(source, relative_path))
        if relative_path == central_path or relative_path.as_posix().endswith(
            "watch_management/schemas.py"
        ):
            continue
        tree = ast.parse(source, filename=str(relative_path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "schemas"
            and node.level == 1
            and relative_path.parent.as_posix() == "rail_waitlist/watch_management"
            for alias in node.names
            if alias.name in WATCH_READ_SYMBOLS
        }
        if imports:
            direct_consumers[relative_path.as_posix()] = imports

    assert violations == []
    assert direct_consumers == {
        "rail_waitlist/watch_management/http.py": {"WatchRead"},
        "rail_waitlist/watch_management/read_model.py": {"WatchRead"},
    }
