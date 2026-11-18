from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from rail_waitlist.config import get_settings

PLAN = "a" * 64
IDEMPOTENCY = "b" * 32
TOKEN = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
SUBMISSION = {"target_release_id": "1.4.0", "plan_id": PLAN, "idempotency_key": IDEMPOTENCY}


@pytest.fixture
def management_ipc(tmp_path, monkeypatch):
    directory = tmp_path / "management"
    for folder in ("control", "gate", "requests", "status/jobs"):
        (directory / folder).mkdir(parents=True)
    (directory / "gate/operation.lock").touch()
    (directory / "control/maintenance.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "closed": True,
                "generation": "update-1",
            }
        )
    )
    now = datetime.now(UTC).isoformat()
    (directory / "status/manager.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "state": "ready",
                "pid": 1,
                "started_at": now,
                "heartbeat_at": now,
            }
        )
    )
    (directory / "status/catalog.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "checked_at": now,
                "current_version": "1.3.3",
                "latest_version": "1.4.0",
                "plan_id": PLAN,
                "available": True,
                "reason": "update_available",
            }
        )
    )
    token = tmp_path / "request-token"
    token.write_text(TOKEN)
    monkeypatch.setattr(get_settings(), "railwait_management_dir", str(directory))
    monkeypatch.setattr(get_settings(), "railwait_update_token_file", str(token))
    return directory


async def test_unmanaged_install_displays_safe_reason(client):
    response = await client.get("/api/v1/system/update")
    assert response.status_code == 200
    assert response.json()["reason"] == "managed_install_required"
    assert response.json()["available"] is False
    assert response.json()["managed"] is False


async def test_management_status_stays_available_during_maintenance(client, management_ipc):
    response = await client.get("/api/v1/system/update")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["available"] is True
    assert response.json()["plan_id"] == PLAN
    assert response.json()["current_version"] == "1.3.3"
    assert TOKEN not in response.text


async def test_public_requests_are_rejected_even_when_gate_is_closed(public_client, management_ipc):
    assert (await public_client.get("/api/v1/system/update")).status_code == 401
    assert (await public_client.post("/api/v1/system/update", json=SUBMISSION)).status_code == 401
    assert not list((management_ipc / "requests").iterdir())


async def test_submit_requires_csrf_and_trusted_origin(client, management_ipc):
    assert (
        await client.post(
            "/api/v1/system/update", json=SUBMISSION, headers={"X-CSRF-Token": "wrong"}
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/system/update",
            json=SUBMISSION,
            headers={"Origin": "https://untrusted.example"},
        )
    ).status_code == 403
    assert not list((management_ipc / "requests").iterdir())


async def test_submit_writes_authenticated_atomic_request_and_never_returns_token(
    client, management_ipc
):
    response = await client.post("/api/v1/system/update", json=SUBMISSION)
    assert response.status_code == 202
    assert response.json()["schema"] == 1
    assert response.json()["state"] == "queued"
    assert TOKEN not in response.text
    identifier = response.json()["id"]
    files = list((management_ipc / "requests").iterdir())
    assert len(files) == 1
    assert files[0].name == identifier + ".json"
    assert files[0].stat().st_mode & 0o777 == 0o644
    assert json.loads(files[0].read_text()) == {
        "schema": 1,
        "id": identifier,
        **SUBMISSION,
        "auth_token": TOKEN,
    }
    assert json.loads((management_ipc / "control/maintenance.json").read_text())["closed"] is True


async def test_changed_plan_is_rejected_without_a_request(client, management_ipc):
    response = await client.post("/api/v1/system/update", json={**SUBMISSION, "plan_id": "c" * 64})
    assert response.status_code == 409
    assert response.json() == {"detail": "update_plan_changed"}
    assert not list((management_ipc / "requests").iterdir())


@pytest.mark.parametrize(
    "filename,seconds,reason",
    [
        ("manager.json", 11, "manager_unavailable"),
        ("catalog.json", 1801, "checking"),
    ],
)
async def test_stale_status_disables_updates(client, management_ipc, filename, seconds, reason):
    path = management_ipc / "status" / filename
    data = json.loads(path.read_text())
    data["heartbeat_at" if filename == "manager.json" else "checked_at"] = (
        datetime.now(UTC) - timedelta(seconds=seconds)
    ).isoformat()
    path.write_text(json.dumps(data))
    response = await client.get("/api/v1/system/update")
    assert response.json()["available"] is False
    assert response.json()["plan_id"] is None
    assert response.json()["reason"] == reason


async def test_malformed_catalog_and_raw_reason_are_not_echoed(client, management_ipc):
    path = management_ipc / "status/catalog.json"
    data = json.loads(path.read_text())
    data["reason"] = "credentials:private-data"
    path.write_text(json.dumps(data))
    response = await client.get("/api/v1/system/update")
    assert response.json()["available"] is False
    assert "credentials" not in response.text


async def test_go_empty_optional_catalog_fields_are_normalized(client, management_ipc):
    path = management_ipc / "status/catalog.json"
    catalog = json.loads(path.read_text())
    catalog.update(
        {"available": False, "latest_version": "1.3.3", "plan_id": "", "reason": "up_to_date"}
    )
    path.write_text(json.dumps(catalog))
    response = await client.get("/api/v1/system/update")
    assert response.json()["reason"] == "up_to_date"
    assert response.json()["plan_id"] is None
    catalog.update({"current_version": "", "latest_version": "", "reason": "update_blocked"})
    path.write_text(json.dumps(catalog))
    response = await client.get("/api/v1/system/update")
    assert response.json()["reason"] == "update_blocked"
    assert response.json()["current_version"] is None
    assert response.json()["latest_version"] is None


async def test_validation_does_not_echo_supplied_private_fields(client, management_ipc):
    response = await client.post("/api/v1/system/update", json={**SUBMISSION, "auth_token": TOKEN})
    assert response.status_code == 422
    assert response.json() == {"detail": "request_validation_failed"}
    assert TOKEN not in response.text


async def test_job_status_is_strict_and_never_projects_unknown_fields(client, management_ipc):
    now = datetime.now(UTC).isoformat()
    path = management_ipc / f"status/jobs/{IDEMPOTENCY}.json"
    job = {
        "schema": 1,
        "id": IDEMPOTENCY,
        **SUBMISSION,
        "state": "running",
        "stage": "draining",
        "created_at": now,
        "updated_at": now,
    }
    path.write_text(json.dumps(job))
    response = await client.get(f"/api/v1/system/update/jobs/{IDEMPOTENCY}")
    assert response.status_code == 200
    assert response.json()["stage"] == "draining"
    job["auth_token"] = TOKEN
    path.write_text(json.dumps(job))
    response = await client.get(f"/api/v1/system/update/jobs/{IDEMPOTENCY}")
    assert response.status_code == 503
    assert TOKEN not in response.text
    assert (await client.get("/api/v1/system/update/jobs/not-an-id")).status_code == 404
