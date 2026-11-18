from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from httpx2 import ASGITransport, AsyncClient

from rail_waitlist.config import get_settings
from rail_waitlist.maintenance.admission import admitted_sync, create_admitted_task
from rail_waitlist.maintenance.gate import MaintenanceClosed, MaintenanceGate
from rail_waitlist.worker_task_runtime import run_task_isolated

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Container gate uses POSIX flock")


def write_control(directory: Path, *, closed: bool, generation: str = "generation-1") -> None:
    temporary = directory / "control/next.json"
    temporary.write_text(json.dumps({"schema": 1, "closed": closed, "generation": generation}))
    temporary.replace(directory / "control/maintenance.json")


@pytest.fixture
def management_directory(tmp_path, monkeypatch):
    directory = tmp_path / "management"
    (directory / "control").mkdir(parents=True)
    (directory / "gate").mkdir()
    (directory / "gate/operation.lock").touch()
    write_control(directory, closed=False)
    monkeypatch.setattr(get_settings(), "railwait_management_dir", str(directory))
    return directory


def test_unconfigured_gate_preserves_existing_behavior():
    gate = MaintenanceGate(None)
    with gate.operation():
        assert gate.is_closed() is False
        assert gate.status()["drained"] is True


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"invalid",
        b"{}",
        b"[]",
        b"x" * 4097,
        b'{"schema":true,"closed":false,"generation":"ok"}',
        b'{"schema":1,"closed":0,"generation":"ok"}',
        b'{"schema":1,"closed":false,"generation":""}',
        b'{"schema":1,"closed":false,"generation":"ok","extra":1}',
    ],
)
def test_configured_missing_or_malformed_gate_is_closed(management_directory, payload):
    path = management_directory / "control/maintenance.json"
    if payload is None:
        path.unlink()
    else:
        path.write_bytes(payload)
    gate = MaintenanceGate(management_directory)
    assert gate.is_closed() is True
    with pytest.raises(MaintenanceClosed, match="maintenance_in_progress"):
        with gate.operation():
            raise AssertionError("closed work was admitted")


def test_close_refuses_new_work_and_drain_waits_for_active_operation(management_directory):
    gate = MaintenanceGate(management_directory)
    with gate.operation():
        write_control(management_directory, closed=True, generation="generation-2")
        assert gate.status() == {
            "schema": 1,
            "closed": True,
            "generation": "generation-2",
            "drained": False,
        }
        with pytest.raises(MaintenanceClosed):
            with gate.operation():
                raise AssertionError("closed work was admitted")
    assert gate.status()["drained"] is True
    write_control(management_directory, closed=False, generation="generation-3")
    with gate.operation():
        assert gate.status()["drained"] is False


def test_nonregular_control_and_missing_lock_fail_closed_without_blocking(management_directory):
    control = management_directory / "control/maintenance.json"
    control.unlink()
    os.mkfifo(control)
    gate = MaintenanceGate(management_directory)
    assert gate.is_closed() is True
    control.unlink()
    write_control(management_directory, closed=False)
    (management_directory / "gate/operation.lock").unlink()
    with pytest.raises(MaintenanceClosed):
        with gate.operation():
            raise AssertionError("missing lock must refuse work")


async def test_worker_skips_unstarted_work_and_releases_no_secrets(management_directory):
    write_control(management_directory, closed=True)
    events = []

    async def operation():
        events.append("operation")
        return 1

    async def dispose():
        events.append("dispose")

    assert await run_task_isolated(operation(), dispose_engine=dispose) == 0
    assert events == ["dispose"]


async def test_worker_operation_and_cleanup_finish_before_drain(management_directory):
    gate = MaintenanceGate(management_directory)
    started, release = asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        return 7

    async def dispose():
        assert gate.status()["drained"] is False

    task = asyncio.create_task(run_task_isolated(operation(), dispose_engine=dispose))
    await asyncio.wait_for(started.wait(), 2)
    write_control(management_directory, closed=True)
    assert gate.status()["drained"] is False
    release.set()
    assert await asyncio.wait_for(task, 2) == 7
    assert gate.status()["drained"] is True


async def test_detached_task_keeps_admission_after_caller_returns(management_directory):
    gate = MaintenanceGate(management_directory)
    release = asyncio.Event()

    async def operation():
        await release.wait()

    task = create_admitted_task(operation())
    write_control(management_directory, closed=True)
    assert gate.status()["drained"] is False
    release.set()
    await task
    await asyncio.sleep(0)
    assert gate.status()["drained"] is True


async def test_cancel_before_background_task_starts_releases_lock(management_directory):
    gate = MaintenanceGate(management_directory)

    async def operation():
        raise AssertionError("task should be cancelled before starting")

    task = create_admitted_task(operation())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert gate.status()["drained"] is True


async def test_real_thread_keeps_lock_after_async_waiter_is_cancelled(management_directory):
    gate = MaintenanceGate(management_directory)
    started, release = threading.Event(), threading.Event()

    @admitted_sync
    def operation():
        started.set()
        release.wait(timeout=10)

    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        write_control(management_directory, closed=True)
        assert gate.status()["drained"] is False
    finally:
        release.set()
    for _ in range(100):
        if gate.status()["drained"]:
            break
        await asyncio.sleep(0.01)
    assert gate.status()["drained"] is True


async def test_sse_stream_finishes_when_maintenance_closes(app, management_directory, monkeypatch):
    from rail_waitlist.event_stream import http as stream_module

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(stream_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(get_settings(), "sse_poll_seconds", 0)
    stream = stream_module._stream_events(ConnectedRequest(), None)
    assert await stream.__anext__() == ": keepalive\n\n"
    write_control(management_directory, closed=True)
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


async def test_provider_manager_skips_closed_start_and_resumes_after_reopening(
    management_directory,
    monkeypatch,
):
    from rail_waitlist.provider_account_management import runtime

    write_control(management_directory, closed=True)
    calls = []
    warmed = asyncio.Event()
    registry = runtime.ProviderRuntimePrewarmRegistry()

    async def prewarm(factory, verifier, state):
        calls.append("prewarm")
        assert MaintenanceGate(management_directory).status()["drained"] is False
        state.completed = True
        warmed.set()

    async def recover(factory, verifier, state):
        calls.append("recover")
        return 0

    monkeypatch.setattr(runtime, "prewarm_provider_sessions", prewarm)
    monkeypatch.setattr(runtime, "recover_provider_sessions_once", recover)
    task = asyncio.create_task(
        runtime.run_provider_session_manager(
            None,
            None,
            registry,
            interval_seconds=0.001,
        )
    )
    try:
        await asyncio.sleep(0.01)
        assert calls == []
        assert MaintenanceGate(management_directory).status()["drained"] is True
        write_control(management_directory, closed=False, generation="resumed-2")
        await asyncio.wait_for(warmed.wait(), 2)
        assert calls[0] == "prewarm"
        assert registry.completed is True
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_lifespan_defers_station_preload_while_closed(app, management_directory, monkeypatch):
    from rail_waitlist.main import lifespan

    write_control(management_directory, closed=True)
    called = False

    async def preload():
        nonlocal called
        called = True

    monkeypatch.setattr(app.state.station_catalog_service, "preload", preload)
    monkeypatch.setattr(get_settings(), "auto_create_schema", False)
    async with lifespan(app):
        await asyncio.sleep(0)
        assert called is False
        assert MaintenanceGate(management_directory).status()["drained"] is True


async def test_http_closes_new_requests_but_allows_health_and_login(app, management_directory):
    write_control(management_directory, closed=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        response = await client.get("/api/v1/providers")
        assert response.status_code == 503
        assert response.json() == {"detail": "maintenance_in_progress"}
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 200
        assert (await client.get("/api/v1/auth/status")).status_code == 200
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={
                    "username": "unknown",
                    "password": "a valid test password",
                },
            )
        ).status_code == 401


async def test_http_retains_lock_until_response_finishes(app, management_directory):
    started, release = asyncio.Event(), asyncio.Event()

    @app.get("/maintenance-test-operation")
    async def operation():
        started.set()
        await release.wait()
        return {"finished": True}

    gate = MaintenanceGate(management_directory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        task = asyncio.create_task(client.get("/maintenance-test-operation"))
        await asyncio.wait_for(started.wait(), 2)
        write_control(management_directory, closed=True)
        assert gate.status()["drained"] is False
        assert (await client.get("/maintenance-test-operation")).status_code == 503
        release.set()
        assert (await asyncio.wait_for(task, 2)).status_code == 200
        assert gate.status()["drained"] is True


async def test_process_exit_releases_os_lock_and_status_module_observes_drain(management_directory):
    source = Path(__file__).resolve().parents[1] / "src"
    environment = {
        **os.environ,
        "PYTHONPATH": str(source),
        "RAILWAIT_MANAGEMENT_DIR": str(management_directory),
    }
    script = (
        "import time; from rail_waitlist.maintenance.gate import MaintenanceGate; "
        "guard=MaintenanceGate.configured().operation(); guard.__enter__(); "
        "print('ready',flush=True); time.sleep(30)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        env=environment,
        cwd=management_directory,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 3) == b"ready\n"
        write_control(management_directory, closed=True)
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "rail_waitlist.maintenance", "--status"],
            env=environment,
            cwd=management_directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        assert json.loads(result.stdout)["drained"] is False
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.communicate(), 5)
    assert MaintenanceGate(management_directory).status()["drained"] is True


@pytest.mark.parametrize("revisions", [[], ["0042_protection_halt"], ["branch_one", "branch_two"]])
def test_database_revision_command_is_exact_or_fails_closed(tmp_path, revisions):
    database = tmp_path / "revision.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.executemany(
            "INSERT INTO alembic_version VALUES (?)", [(item,) for item in revisions]
        )
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "rail_waitlist.maintenance", "--database-revision"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if len(revisions) == 1:
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"schema": 1, "database_revision": revisions[0]}
        assert result.stderr == ""
    else:
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "Maintenance status unavailable.\n"
