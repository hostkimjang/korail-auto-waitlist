from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from rail_waitlist.config import get_settings
from rail_waitlist.maintenance.schemas import SettingsSubmission

SETTINGS_PATH = "/api/v1/system/settings"
RESTART_PATH = "/api/v1/system/restart"
TOKEN = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
IDEMPOTENCY = "e" * 32
RESTART_BODY = {"scope": "app", "idempotency_key": IDEMPOTENCY}
STARTED_AT = "2026-11-19T01:02:03.123456789Z"
RAW = (
    "COMPOSE_PROJECT_NAME=railwait\n"
    "POSTGRES_PASSWORD=\u2022\u2022\u2022\u2022\u2022\u2022\n"
    "TAGO_SERVICE_KEY=\u2022\u2022\u2022\u2022\u2022\u2022\n"
    "KORAIL_RESERVATION_ONCE_ENABLED=true\n"
)
REVISION = hashlib.sha256(b"install env bytes").hexdigest()
TAGO_SECRET = "tago-private-key-7Q2"
CONTRACT_FIELDS = [
    ("KORAIL_RESERVATION_ONCE_ENABLED", "boolean", "app"),
    ("SRT_RESERVATION_ONCE_ENABLED", "boolean", "app"),
    ("TAGO_SERVICE_KEY", "secret", "app"),
    ("WEBPUSH_VAPID_SUBJECT", "contact", "app"),
    ("VITE_KORAIL_BOOKING_DEEPLINK_ENABLED", "boolean", "web"),
    ("VITE_KORAIL_BOOKING_VALIDATED_VERSION", "version", "web"),
    ("VITE_KORAIL_TICKET_DEEPLINK_ENABLED", "boolean", "web"),
    ("VITE_KORAIL_TICKET_VALIDATED_VERSION", "version", "web"),
    ("VITE_SRT_MAIN_DEEPLINK_ENABLED", "boolean", "web"),
    ("VITE_SRT_MAIN_VALIDATED_VERSION", "version", "web"),
    ("VITE_SRT_TICKET_DEEPLINK_ENABLED", "boolean", "web"),
    ("VITE_SRT_TICKET_VALIDATED_VERSION", "version", "web"),
]
SNAPSHOT_VALUES = {
    "KORAIL_RESERVATION_ONCE_ENABLED": "true",
    "SRT_RESERVATION_ONCE_ENABLED": "false",
    "WEBPUSH_VAPID_SUBJECT": "mailto:admin@example.com",
    "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED": "true",
    "VITE_KORAIL_BOOKING_VALIDATED_VERSION": "4.2.1",
    "VITE_KORAIL_TICKET_DEEPLINK_ENABLED": "false",
    "VITE_KORAIL_TICKET_VALIDATED_VERSION": "",
    "VITE_SRT_MAIN_DEEPLINK_ENABLED": "true",
    "VITE_SRT_MAIN_VALIDATED_VERSION": "2.0.0+b17",
    "VITE_SRT_TICKET_DEEPLINK_ENABLED": "false",
    "VITE_SRT_TICKET_VALIDATED_VERSION": "1.9_rc-2",
}
UNAVAILABLE_PROJECTION = {
    "revision": None,
    "fields": [],
    "raw": None,
    "secret_keys": [],
    "locked_keys": [],
    "restart_available": False,
    "install_location": None,
}


def snapshot(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        key: {"value": value, "configured": True} for key, value in SNAPSHOT_VALUES.items()
    }
    fields["TAGO_SERVICE_KEY"] = {"configured": True}
    fields["POSTGRES_PASSWORD"] = {"value": "unlisted-private-value", "configured": True}
    data: dict[str, Any] = {
        "schema": 1,
        "manager_started_at": STARTED_AT,
        "generated_at": "2026-11-19T01:02:04.5Z",
        "revision": REVISION,
        "fields": fields,
        "raw": RAW,
        "secret_keys": ["POSTGRES_PASSWORD", "TAGO_SERVICE_KEY"],
        "locked_keys": ["COMPOSE_PROJECT_NAME"],
    }
    data.update(overrides)
    return data


def write_snapshot(directory: Path, data: dict[str, Any]) -> None:
    (directory / "status/settings.json").write_text(json.dumps(data))


def write_heartbeat(directory: Path, **overrides: Any) -> None:
    heartbeat = {
        "schema": 1,
        "state": "ready",
        "pid": 1,
        "started_at": STARTED_AT,
        "heartbeat_at": datetime.now(UTC).isoformat(),
        **overrides,
    }
    (directory / "status/manager.json").write_text(json.dumps(heartbeat))


def write_capabilities(directory: Path, **overrides: Any) -> None:
    capabilities = {
        "schema": 1,
        "manager_started_at": STARTED_AT,
        "version": "1.5.0",
        "capabilities": ["settings", "restart"],
        "install_path": "/Users/me/Library/Application Support/Railwait",
        "host_os": "darwin",
        **overrides,
    }
    (directory / "status/capabilities.json").write_text(json.dumps(capabilities))


def submission(**form: Any) -> dict[str, Any]:
    return {"base_revision": REVISION, "idempotency_key": IDEMPOTENCY, **form}


def request_files(directory: Path) -> list[str]:
    return sorted(path.name for path in (directory / "requests").iterdir())


@pytest.fixture
def management_ipc(tmp_path, monkeypatch):
    directory = tmp_path / "management"
    for folder in ("control", "gate", "requests", "status/jobs"):
        (directory / folder).mkdir(parents=True)
    (directory / "gate/operation.lock").touch()
    # A closed gate proves the settings screen stays usable while the app drains.
    (directory / "control/maintenance.json").write_text(
        json.dumps({"schema": 1, "closed": True, "generation": "settings-1"})
    )
    write_heartbeat(directory)
    write_snapshot(directory, snapshot())
    token = tmp_path / "request-token"
    token.write_text(TOKEN)
    monkeypatch.setattr(get_settings(), "railwait_management_dir", str(directory))
    monkeypatch.setattr(get_settings(), "railwait_update_token_file", str(token))
    return directory


async def test_unmanaged_install_reports_reason_and_refuses_submission(client):
    response = await client.get(SETTINGS_PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema": 1,
        "managed": False,
        "manager_available": False,
        "available": False,
        "reason": "managed_install_required",
        **UNAVAILABLE_PROJECTION,
    }
    response = await client.post(
        SETTINGS_PATH, json=submission(fields={"SRT_RESERVATION_ONCE_ENABLED": "true"})
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "settings_manager_unavailable"}


async def test_ready_status_projects_allowlisted_fields_in_contract_order(client, management_ipc):
    assert (await client.get("/api/v1/providers")).json() == {"detail": "maintenance_in_progress"}
    response = await client.get(SETTINGS_PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert list(body) == [
        "schema",
        "managed",
        "manager_available",
        "available",
        "reason",
        "revision",
        "fields",
        "raw",
        "secret_keys",
        "locked_keys",
        "restart_available",
        "install_location",
    ]
    assert body["schema"] == 1
    assert body["managed"] is True
    assert body["manager_available"] is True
    assert body["available"] is True
    assert body["reason"] == "ready"
    assert body["revision"] == REVISION
    assert body["raw"] == RAW
    assert body["secret_keys"] == ["POSTGRES_PASSWORD", "TAGO_SERVICE_KEY"]
    assert body["locked_keys"] == ["COMPOSE_PROJECT_NAME"]
    # A manager without status/capabilities.json predates restarts.
    assert body["restart_available"] is False
    assert body["install_location"] is None
    assert [(f["key"], f["kind"], f["scope"]) for f in body["fields"]] == CONTRACT_FIELDS
    for field in body["fields"]:
        assert list(field) == ["key", "kind", "scope", "value", "configured"]
        assert field["configured"] is True
        expected = None if field["kind"] == "secret" else SNAPSHOT_VALUES[field["key"]]
        assert field["value"] == expected
    assert "unlisted-private-value" not in response.text
    assert TOKEN not in response.text


async def test_missing_allowlisted_keys_are_reported_as_unconfigured(client, management_ipc):
    data = snapshot()
    del data["fields"]["VITE_SRT_MAIN_VALIDATED_VERSION"]
    del data["fields"]["TAGO_SERVICE_KEY"]
    write_snapshot(management_ipc, data)
    fields = {f["key"]: f for f in (await client.get(SETTINGS_PATH)).json()["fields"]}
    assert len(fields) == 12
    assert fields["VITE_SRT_MAIN_VALIDATED_VERSION"]["value"] == ""
    assert fields["VITE_SRT_MAIN_VALIDATED_VERSION"]["configured"] is False
    assert fields["TAGO_SERVICE_KEY"]["value"] is None
    assert fields["TAGO_SERVICE_KEY"]["configured"] is False


async def test_snapshot_from_same_manager_start_matches_by_instant(client, management_ipc):
    write_snapshot(management_ipc, snapshot(manager_started_at="2026-11-19T10:02:03.123456+09:00"))
    assert (await client.get(SETTINGS_PATH)).json()["reason"] == "ready"


@pytest.mark.parametrize(
    "heartbeat",
    [
        {"heartbeat_at": (datetime.now(UTC) - timedelta(seconds=11)).isoformat()},
        {"state": "stopped"},
        {"state": "failed"},
        None,
    ],
    ids=["stale", "stopped", "failed", "missing"],
)
async def test_unavailable_manager_hides_snapshot(client, management_ipc, heartbeat):
    if heartbeat is None:
        (management_ipc / "status/manager.json").unlink()
    else:
        write_heartbeat(management_ipc, **heartbeat)
    response = await client.get(SETTINGS_PATH)
    assert response.json() == {
        "schema": 1,
        "managed": True,
        "manager_available": False,
        "available": False,
        "reason": "manager_unavailable",
        **UNAVAILABLE_PROJECTION,
    }


def _replace_snapshot(**overrides: Any) -> Callable[[Path], None]:
    return lambda path: path.write_text(json.dumps(snapshot(**overrides)))


def _snapshot_with_field(key: str, entry: Any) -> Callable[[Path], None]:
    def write(path: Path) -> None:
        data = snapshot()
        data["fields"][key] = entry
        path.write_text(json.dumps(data))

    return write


def _without(key: str) -> Callable[[Path], None]:
    def write(path: Path) -> None:
        data = snapshot()
        del data[key]
        path.write_text(json.dumps(data))

    return write


def _symlink(path: Path) -> None:
    target = path.with_name("elsewhere.json")
    target.write_text(json.dumps(snapshot()))
    path.unlink()
    path.symlink_to(target)


UPGRADE_CASES: dict[str, Callable[[Path], None]] = {
    "missing": lambda path: path.unlink(),
    "malformed": lambda path: path.write_bytes(b"{"),
    "not_object": lambda path: path.write_text("[]"),
    "symlink": _symlink,
    "file_too_large": _replace_snapshot(raw="\uac00" * 100_000),
    "stale_process": _replace_snapshot(manager_started_at="2026-11-19T01:02:02.123456789Z"),
    "naive_started_at": _replace_snapshot(manager_started_at="2026-11-19T01:02:03.123456"),
    "naive_generated_at": _replace_snapshot(generated_at="2026-11-19T01:02:04"),
    "schema_2": _replace_snapshot(schema=2),
    "schema_bool": _replace_snapshot(schema=True),
    "unknown_top_level": _replace_snapshot(env_path="/srv/railwait/.env"),
    "missing_raw": _without("raw"),
    "revision_uppercase": _replace_snapshot(revision=REVISION.upper()),
    "revision_short": _replace_snapshot(revision=REVISION[:-1]),
    "fields_not_object": _replace_snapshot(fields=[]),
    "raw_too_long": _replace_snapshot(raw="x" * 196_609),
    "raw_not_string": _replace_snapshot(raw=None),
    "secret_keys_bad_name": _replace_snapshot(secret_keys=["1PASSWORD"]),
    "locked_keys_too_many": _replace_snapshot(locked_keys=[f"KEY_{i}" for i in range(257)]),
    "tago_value_present": _snapshot_with_field(
        "TAGO_SERVICE_KEY", {"value": "snapshot-leaked-tago", "configured": True}
    ),
    "value_not_string": _snapshot_with_field(
        "KORAIL_RESERVATION_ONCE_ENABLED", {"value": True, "configured": True}
    ),
    "entry_null": _snapshot_with_field("VITE_SRT_MAIN_VALIDATED_VERSION", None),
    "entry_missing_configured": _snapshot_with_field(
        "VITE_SRT_MAIN_VALIDATED_VERSION", {"value": "1.0"}
    ),
}


@pytest.mark.parametrize("case", list(UPGRADE_CASES))
async def test_unusable_snapshot_requires_manager_upgrade(client, management_ipc, case):
    UPGRADE_CASES[case](management_ipc / "status/settings.json")
    response = await client.get(SETTINGS_PATH)
    assert response.status_code == 200
    assert response.json() == {
        "schema": 1,
        "managed": True,
        "manager_available": True,
        "available": False,
        "reason": "manager_upgrade_required",
        **UNAVAILABLE_PROJECTION,
    }
    assert "snapshot-leaked-tago" not in response.text
    response = await client.post(SETTINGS_PATH, json=submission(raw="A=1\n"))
    assert response.status_code == 409
    assert response.json() == {"detail": "settings_manager_upgrade_required"}
    assert request_files(management_ipc) == []


ALL_FIELDS = {**SNAPSHOT_VALUES, "TAGO_SERVICE_KEY": TAGO_SECRET}


@pytest.mark.parametrize(
    "form",
    [
        {"fields": {"VITE_SRT_MAIN_DEEPLINK_ENABLED": "false"}},
        {"fields": dict(reversed(list(ALL_FIELDS.items())))},
        {"raw": RAW + "TAGO_SERVICE_KEY=" + TAGO_SECRET + "\n"},
    ],
    ids=["one_field", "all_fields", "raw"],
)
async def test_submission_writes_documented_request_file(client, management_ipc, caplog, form):
    caplog.set_level(logging.DEBUG)
    response = await client.post(SETTINGS_PATH, json=submission(**form))
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert list(response.json()) == ["schema", "id", "state"]
    assert response.json()["schema"] == 1
    assert response.json()["state"] == "queued"
    identifier = response.json()["id"]
    assert len(identifier) == 32 and set(identifier) <= set("0123456789abcdef")
    assert identifier != IDEMPOTENCY
    assert TOKEN not in response.text and TAGO_SECRET not in response.text
    assert TAGO_SECRET not in caplog.text
    assert request_files(management_ipc) == [f"{identifier}.settings.json"]
    path = management_ipc / "requests" / f"{identifier}.settings.json"
    assert path.stat().st_mode & 0o777 == 0o644
    written = json.loads(path.read_text(encoding="utf-8"))
    expected: dict[str, Any] = {
        "schema": 1,
        "id": identifier,
        "idempotency_key": IDEMPOTENCY,
        "auth_token": TOKEN,
        "base_revision": REVISION,
    }
    if "raw" in form:
        expected["raw"] = form["raw"]
    else:
        allowlist = [key for key, _, _ in CONTRACT_FIELDS]
        expected["fields"] = {
            key: form["fields"][key] for key in allowlist if key in form["fields"]
        }
    assert written == expected
    assert list(written) == list(expected)
    assert list(written.get("fields", {})) == list(expected.get("fields", {}))


@pytest.mark.parametrize(
    "fields",
    [
        {"VITE_KORAIL_BOOKING_VALIDATED_VERSION": ""},
        {"VITE_SRT_TICKET_VALIDATED_VERSION": "A" * 64},
        {"WEBPUSH_VAPID_SUBJECT": ""},
        {"WEBPUSH_VAPID_SUBJECT": "mailto:ops.team+push@mail.example.co.kr"},
        {"WEBPUSH_VAPID_SUBJECT": "https://push.example.com"},
        {"TAGO_SERVICE_KEY": ""},
        {"TAGO_SERVICE_KEY": "Ab+/=%~!#&()*,-.:;<>?@[]^_`{|}\\".ljust(512, "x")},
    ],
    ids=["version_empty", "version_64", "contact_default", "mailto", "https", "clear", "tago_512"],
)
async def test_boundary_values_are_accepted(client, management_ipc, fields):
    response = await client.post(SETTINGS_PATH, json=submission(fields=fields))
    assert response.status_code == 202
    written = json.loads(
        (management_ipc / "requests" / f"{response.json()['id']}.settings.json").read_text()
    )
    assert written["fields"] == fields


async def test_raw_at_byte_limit_is_accepted(client, management_ipc):
    raw = "\uac00" * 21_845 + "X"
    assert len(raw.encode()) == 65_536
    assert (await client.post(SETTINGS_PATH, json=submission(raw=raw))).status_code == 202


async def test_changed_revision_is_rejected_without_request(client, management_ipc):
    response = await client.post(
        SETTINGS_PATH,
        json={
            **submission(fields={"SRT_RESERVATION_ONCE_ENABLED": "true"}),
            "base_revision": "f" * 64,
        },
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "settings_changed"}
    assert response.headers["cache-control"] == "no-store"
    assert request_files(management_ipc) == []


async def test_upgrade_check_precedes_revision_check(client, management_ipc):
    (management_ipc / "status/settings.json").unlink()
    response = await client.post(
        SETTINGS_PATH, json={**submission(raw="A=1\n"), "base_revision": "f" * 64}
    )
    assert response.json() == {"detail": "settings_manager_upgrade_required"}


def _stale_manager(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_heartbeat(directory, heartbeat_at=(datetime.now(UTC) - timedelta(seconds=30)).isoformat())


def _stopped_manager(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_heartbeat(directory, state="stopped")


def _missing_token(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Path(get_settings().railwait_update_token_file or "").unlink()


def _invalid_token(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Path(get_settings().railwait_update_token_file or "").write_text("short-token")


def _unset_token(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "railwait_update_token_file", None)


def _publish_refused(directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("requests folder is read-only")

    # Fails after the temporary file exists, so the test also proves it is removed.
    monkeypatch.setattr(os, "link", refuse)


UNAVAILABLE_CASES = {
    "stale_manager": _stale_manager,
    "stopped_manager": _stopped_manager,
    "missing_token": _missing_token,
    "invalid_token": _invalid_token,
    "unset_token": _unset_token,
    "publish_refused": _publish_refused,
}


@pytest.mark.parametrize("case", list(UNAVAILABLE_CASES))
async def test_unavailable_manager_or_write_failure_returns_503(
    client, management_ipc, monkeypatch, case
):
    UNAVAILABLE_CASES[case](management_ipc, monkeypatch)
    # The wrong base revision proves availability is checked before the revision.
    response = await client.post(
        SETTINGS_PATH,
        json={**submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET}), "base_revision": "f" * 64}
        if case.endswith("manager")
        else submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET}),
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "settings_manager_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert TAGO_SECRET not in response.text
    assert request_files(management_ipc) == []


async def test_missing_requests_folder_returns_503(client, management_ipc):
    (management_ipc / "requests").rmdir()
    response = await client.post(SETTINGS_PATH, json=submission(raw="A=1\n"))
    assert response.status_code == 503
    assert response.json() == {"detail": "settings_manager_unavailable"}


INVALID_BODIES: dict[str, dict[str, Any]] = {
    "fields_and_raw": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET}, raw=TAGO_SECRET),
    "no_form": submission(),
    "fields_null": submission(fields=None),
    "raw_null_with_fields": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET}, raw=None),
    "unknown_top_level": {
        **submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET}),
        "auth_token": TAGO_SECRET,
    },
    "unknown_field": submission(fields={"POSTGRES_PASSWORD": TAGO_SECRET}),
    "locked_field_mixed": submission(
        fields={"TAGO_SERVICE_KEY": TAGO_SECRET, "COMPOSE_PROJECT_NAME": "other"}
    ),
    "lowercase_key": submission(fields={"tago_service_key": TAGO_SECRET}),
    "empty_fields": submission(fields={}),
    "fields_list": submission(fields=["TAGO_SERVICE_KEY", TAGO_SECRET]),
    "value_null": submission(fields={"TAGO_SERVICE_KEY": None}),
    "boolean_title_case": submission(fields={"SRT_RESERVATION_ONCE_ENABLED": "True"}),
    "boolean_digit": submission(fields={"SRT_RESERVATION_ONCE_ENABLED": "1"}),
    "boolean_json": submission(fields={"SRT_RESERVATION_ONCE_ENABLED": True}),
    "boolean_empty": submission(fields={"VITE_SRT_MAIN_DEEPLINK_ENABLED": ""}),
    "version_space": submission(fields={"VITE_SRT_MAIN_VALIDATED_VERSION": "1.0 beta"}),
    "version_newline": submission(fields={"VITE_SRT_MAIN_VALIDATED_VERSION": "1.0\n"}),
    "version_too_long": submission(fields={"VITE_SRT_MAIN_VALIDATED_VERSION": "1" * 65}),
    "version_number": submission(fields={"VITE_SRT_MAIN_VALIDATED_VERSION": 1}),
    "tago_space": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET + " x"}),
    "tago_quote": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET + "'"}),
    "tago_double_quote": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET + '"'}),
    "tago_non_ascii": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET + "\uc778"}),
    "tago_too_long": submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET + "x" * 512}),
    "contact_bare_email": submission(fields={"WEBPUSH_VAPID_SUBJECT": "admin@example.com"}),
    "contact_two_addresses": submission(
        fields={"WEBPUSH_VAPID_SUBJECT": "mailto:a@example.com,b@example.com"}
    ),
    "contact_mailto_query": submission(
        fields={"WEBPUSH_VAPID_SUBJECT": "mailto:a@example.com?subject=x"}
    ),
    "contact_quote": submission(fields={"WEBPUSH_VAPID_SUBJECT": "mailto:o'neil@example.com"}),
    "contact_http": submission(fields={"WEBPUSH_VAPID_SUBJECT": "http://push.example.com"}),
    "contact_port": submission(fields={"WEBPUSH_VAPID_SUBJECT": "https://push.example.com:8443"}),
    "contact_path": submission(fields={"WEBPUSH_VAPID_SUBJECT": "https://push.example.com/a"}),
    "contact_query": submission(fields={"WEBPUSH_VAPID_SUBJECT": "https://push.example.com?a"}),
    "contact_fragment": submission(fields={"WEBPUSH_VAPID_SUBJECT": "https://push.example.com#a"}),
    "contact_too_long": submission(
        fields={"WEBPUSH_VAPID_SUBJECT": "mailto:" + "a" * 236 + "@example.com"}
    ),
    "raw_nul": submission(raw=TAGO_SECRET + "\x00"),
    "raw_too_large": submission(raw=TAGO_SECRET + "\n" + "\uac00" * 21_846),
    "raw_lone_surrogate": submission(raw=TAGO_SECRET + "\ud800"),
    "raw_number": submission(raw=1),
    "revision_uppercase": {**submission(raw=TAGO_SECRET), "base_revision": REVISION.upper()},
    "revision_missing": {"idempotency_key": IDEMPOTENCY, "raw": TAGO_SECRET},
    "idempotency_short": {**submission(raw=TAGO_SECRET), "idempotency_key": "e" * 31},
}


@pytest.mark.parametrize("case", list(INVALID_BODIES))
async def test_invalid_submission_is_redacted_and_writes_nothing(
    client, management_ipc, caplog, case
):
    caplog.set_level(logging.DEBUG)
    # ASCII-escaped JSON lets a lone surrogate reach the server as "\\ud800".
    response = await client.post(
        SETTINGS_PATH,
        content=json.dumps(INVALID_BODIES[case]).encode("ascii"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "request_validation_failed"}
    assert response.headers["cache-control"] == "no-store"
    assert TAGO_SECRET not in response.text
    assert TAGO_SECRET not in caplog.text
    assert request_files(management_ipc) == []


async def test_body_validation_precedes_manager_availability(client, management_ipc):
    (management_ipc / "status/manager.json").unlink()
    response = await client.post(
        SETTINGS_PATH, json=submission(fields={"POSTGRES_PASSWORD": TAGO_SECRET})
    )
    assert response.status_code == 422
    response = await client.post(
        SETTINGS_PATH,
        content=b'{"base_revision":',
        headers={"Content-Type": "application/json"},
    )
    assert response.json() == {"detail": "request_validation_failed"}


def test_submitted_secret_and_raw_never_appear_in_repr_or_errors():
    fields = SettingsSubmission.model_validate(submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET}))
    raw = SettingsSubmission.model_validate(submission(raw="TAGO_SERVICE_KEY=" + TAGO_SECRET))
    assert TAGO_SECRET not in repr(fields) and TAGO_SECRET not in str(fields)
    assert TAGO_SECRET not in repr(raw) and TAGO_SECRET not in str(raw)
    with pytest.raises(ValidationError) as error:
        SettingsSubmission.model_validate(
            submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET + " "}, extra=TAGO_SECRET)
        )
    assert TAGO_SECRET not in str(error.value)


async def test_settings_endpoints_require_admin_csrf_and_trusted_origin(
    public_client, client, management_ipc
):
    body = submission(fields={"TAGO_SERVICE_KEY": TAGO_SECRET})
    assert (await public_client.get(SETTINGS_PATH)).status_code == 401
    assert (await public_client.post(SETTINGS_PATH, json=body)).status_code == 401
    assert (
        await client.post(SETTINGS_PATH, json=body, headers={"X-CSRF-Token": "wrong"})
    ).status_code == 403
    assert (
        await client.post(SETTINGS_PATH, json=body, headers={"Origin": "https://untrusted.example"})
    ).status_code == 403
    assert request_files(management_ipc) == []


@pytest.mark.parametrize(
    "error_code",
    [
        "E_SETTINGS_CHANGED",
        "E_SETTINGS_INVALID",
        "E_SETTINGS_FORBIDDEN",
        "E_SETTINGS_UNCHANGED",
        "E_RESTART_ENV_INVALID",
        "E_RESTART_UNAVAILABLE",
    ],
)
async def test_update_job_reader_accepts_settings_error_codes(client, management_ipc, error_code):
    identifier = os.urandom(16).hex()
    now = datetime.now(UTC).isoformat()
    (management_ipc / f"status/jobs/{identifier}.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": identifier,
                "target_release_id": "1.4.7",
                "plan_id": "0" * 64,
                "idempotency_key": IDEMPOTENCY,
                "state": "rejected",
                "stage": "rejected",
                "created_at": now,
                "updated_at": now,
                "error_code": error_code,
            }
        )
    )
    response = await client.get(f"/api/v1/system/update/jobs/{identifier}")
    assert response.status_code == 200
    assert response.json()["error_code"] == error_code
    assert response.json()["state"] == "rejected"


async def test_capabilities_enable_restart_and_locate_install_env(client, management_ipc):
    write_capabilities(management_ipc)
    body = (await client.get(SETTINGS_PATH)).json()
    assert body["restart_available"] is True
    assert body["install_location"] == {
        "install_path": "/Users/me/Library/Application Support/Railwait",
        "env_path": "/Users/me/Library/Application Support/Railwait/.env",
        "host_os": "darwin",
    }


async def test_capabilities_ignore_unknown_fields_from_newer_managers(client, management_ipc):
    write_capabilities(
        management_ipc, capabilities=["settings", "restart", "later"], extra={"x": 1}
    )
    assert (await client.get(SETTINGS_PATH)).json()["restart_available"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"manager_started_at": "2026-11-19T01:02:04Z"},
        {"capabilities": ["settings"]},
        {"schema": 2},
        {"host_os": "plan9"},
    ],
    ids=["earlier_process", "no_restart", "schema", "host_os"],
)
async def test_restart_needs_current_capability_file(client, management_ipc, overrides):
    write_capabilities(management_ipc, **overrides)
    body = (await client.get(SETTINGS_PATH)).json()
    assert body["available"] is True
    assert body["restart_available"] is False
    response = await client.post(RESTART_PATH, json=RESTART_BODY)
    assert response.status_code == 409
    assert response.json() == {"detail": "restart_manager_upgrade_required"}
    assert request_files(management_ipc) == []


@pytest.mark.parametrize(
    ("line", "location"),
    [
        (
            "RAILWAIT_INSTALL_DIR='/home/me/railwait'",
            {
                "install_path": "/home/me/railwait",
                "env_path": "/home/me/railwait/.env",
                "host_os": "linux",
            },
        ),
        (
            "RAILWAIT_INSTALL_DIR='C:\\Users\\me\\Railwait'",
            {
                "install_path": "C:\\Users\\me\\Railwait",
                "env_path": "C:\\Users\\me\\Railwait\\.env",
                "host_os": "windows",
            },
        ),
        (
            "RAILWAIT_INSTALL_DIR=/srv/railwait/",
            {"install_path": "/srv/railwait/", "env_path": "/srv/railwait/.env", "host_os": None},
        ),
        ("RAILWAIT_INSTALL_DIR='railwait'", None),
    ],
    ids=["linux", "windows", "unknown_os", "relative"],
)
async def test_install_location_falls_back_to_the_snapshot_of_older_managers(
    client, management_ipc, line, location
):
    write_snapshot(management_ipc, snapshot(raw=RAW + line + "\n"))
    body = (await client.get(SETTINGS_PATH)).json()
    assert body["restart_available"] is False
    assert body["install_location"] == location


async def test_restart_submission_writes_documented_request_file(client, management_ipc, caplog):
    caplog.set_level(logging.DEBUG)
    write_capabilities(management_ipc)
    # The fixture closes the maintenance gate: a restart can be requested while the app drains.
    response = await client.post(RESTART_PATH, json=RESTART_BODY)
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert list(response.json()) == ["schema", "id", "state"]
    assert response.json()["state"] == "queued"
    identifier = response.json()["id"]
    assert len(identifier) == 32 and set(identifier) <= set("0123456789abcdef")
    assert TOKEN not in response.text and TOKEN not in caplog.text
    assert request_files(management_ipc) == [f"{identifier}.restart.json"]
    path = management_ipc / "requests" / f"{identifier}.restart.json"
    assert path.stat().st_mode & 0o777 == 0o644
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {
        "schema": 1,
        "id": identifier,
        "idempotency_key": IDEMPOTENCY,
        "auth_token": TOKEN,
        "scope": "app",
    }
    assert list(written) == ["schema", "id", "idempotency_key", "auth_token", "scope"]


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "all", "idempotency_key": IDEMPOTENCY},
        {"scope": "app"},
        {"scope": "app", "idempotency_key": "E" * 32},
        {"scope": "app", "idempotency_key": IDEMPOTENCY, "services": ["postgres"]},
    ],
    ids=["scope", "missing_key", "key_format", "extra_field"],
)
async def test_invalid_restart_submission_is_redacted_and_writes_nothing(
    client, management_ipc, body
):
    write_capabilities(management_ipc)
    response = await client.post(RESTART_PATH, json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "request_validation_failed"}
    assert request_files(management_ipc) == []


async def test_restart_with_unavailable_manager_returns_503(client, management_ipc):
    write_capabilities(management_ipc)
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    write_heartbeat(management_ipc, heartbeat_at=stale)
    response = await client.post(RESTART_PATH, json=RESTART_BODY)
    assert response.status_code == 503
    assert response.json() == {"detail": "restart_manager_unavailable"}
    assert request_files(management_ipc) == []


async def test_restart_requires_admin_csrf_and_trusted_origin(
    public_client, client, management_ipc
):
    write_capabilities(management_ipc)
    body = RESTART_BODY
    assert (await public_client.post(RESTART_PATH, json=body)).status_code == 401
    assert (
        await client.post(RESTART_PATH, json=body, headers={"X-CSRF-Token": "wrong"})
    ).status_code == 403
    assert (
        await client.post(RESTART_PATH, json=body, headers={"Origin": "https://untrusted.example"})
    ).status_code == 403
    assert request_files(management_ipc) == []
