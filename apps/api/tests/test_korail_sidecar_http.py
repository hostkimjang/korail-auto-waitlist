import ast
import inspect
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import rail_waitlist.korail_browser_adapter_service as compatibility_service
from rail_waitlist.korail_sidecar import http as canonical_http
from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
)
from rail_waitlist.provider_call_context import (
    REQUEST_ID_HEADER,
    REQUEST_TIMEOUT_MS_HEADER,
    current_request_id,
    validated_log_id,
)
from rail_waitlist.reservations.provider_confirmation.korail import (
    KorailSameSessionDetailEvidence,
)

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "rail_waitlist"
TOKEN = "k" * 32


class CapturingAutomation:
    def __init__(self, *, failure_stage: str | None = None) -> None:
        self.failure_stage = failure_stage
        self.calls: list[tuple[float | None, str | None]] = []

    async def search(
        self,
        request: BrowserSeatSearchRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> BrowserSeatSearchResult:
        self.calls.append((timeout_seconds, current_request_id()))
        if self.failure_stage is not None:
            raise BrowserSourceUnavailable(self.failure_stage)
        return BrowserSeatSearchResult(
            origin=request.origin,
            destination=request.destination,
            travel_date=request.travel_date,
            passenger_count=1,
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
            trains=[],
        )

    async def close(self) -> None:
        return


class CapturingReservationClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def reserve_once(self, _request: object, *, on_progress=None) -> object:
        operation = "reserve_once_stream" if on_progress is not None else "reserve_once"
        self.calls.append((operation, current_request_id()))
        return SimpleNamespace(
            outcome=SimpleNamespace(value="failed"),
            reason="fixture_terminal",
            seat_clicked=False,
            reservation_clicked=False,
            session_ready_at=None,
            target_rechecked_at=None,
            seat_selected_at=None,
            reservation_requested_at=None,
            reserved_seats=(),
            confirmation_correlation_seats=(),
        )

    async def read_reservation_detail(self, target: object) -> KorailSameSessionDetailEvidence:
        credential_version = cast(int, cast(SimpleNamespace, target).credential_version)
        self.calls.append(("confirm_reservation", current_request_id()))
        return KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 13, tzinfo=UTC),
            credential_version=credential_version,
            exact_identity_matched=False,
            payment_pending_markers_present=False,
        )


async def ready_probe() -> None:
    return


def seat_snapshot_payload() -> dict[str, object]:
    return BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 13),
        departure_from=time(12),
        departure_to=time(14),
        passenger_count=1,
    ).model_dump(mode="json")


def reserve_once_payload() -> dict[str, object]:
    return {
        "origin": "서울",
        "destination": "부산",
        "travel_date": "2026-08-13",
        "train_number": "43",
        "train_type": "KTX",
        "departure_time": "12:00:00",
        "arrival_time": "14:00:00",
        "seat_class": "general",
        "credential": {
            "login_method": "membership_number",
            "login_id": "fixture-login-secret",
            "password": "fixture-password-secret",
            "version": "credential:7",
        },
    }


def confirmation_payload() -> dict[str, object]:
    return {
        "attempt_id": "attempt-fixture",
        "candidate_id": "candidate-fixture",
        "train_number": "43",
        "origin": "서울",
        "destination": "부산",
        "departure_at": "2026-08-13T03:00:00Z",
        "arrival_at": "2026-08-13T05:00:00Z",
        "seat_class": "standard",
        "passenger_count": 1,
        "credential_version": 7,
        "purpose": "initial",
    }


def test_sidecar_facade_references_the_canonical_http_factory() -> None:
    assert compatibility_service._create_adapter_http_app is canonical_http.create_adapter_app


def test_sidecar_facade_preserves_the_create_adapter_app_signature() -> None:
    signature = inspect.signature(compatibility_service.create_adapter_app)

    assert tuple(signature.parameters) == (
        "automation",
        "token",
        "readiness_probe",
        "reservation_client",
        "readiness_retry_interval_seconds",
        "readiness_probe_timeout_seconds",
    )
    assert (
        signature.parameters["readiness_retry_interval_seconds"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert (
        signature.parameters["readiness_probe_timeout_seconds"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


def test_sidecar_facade_captures_compatibility_globals_at_call_time(monkeypatch) -> None:
    def browser_engine_setting():
        return compatibility_service.KorailBrowserEngine.PYDOLL

    def build_browser_client(*args, **kwargs):
        return None

    def float_setting(*args, **kwargs):
        return 25.0

    def build_automation(*args, **kwargs):
        return None

    def readiness_factory(*args, **kwargs):
        return None

    def readiness_probe_for_engine(engine):
        return None

    def getenv(key, default=None):
        return default

    def monotonic():
        return 17.0

    logger = SimpleNamespace()
    captured: dict[str, object] = {}
    expected_app = FastAPI()

    def capture_factory(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return expected_app

    monkeypatch.setattr(compatibility_service, "_browser_engine_setting", browser_engine_setting)
    monkeypatch.setattr(compatibility_service, "_build_browser_client", build_browser_client)
    monkeypatch.setattr(compatibility_service, "_float_setting", float_setting)
    monkeypatch.setattr(compatibility_service, "build_automation", build_automation)
    monkeypatch.setattr(compatibility_service, "_ReadinessGate", readiness_factory)
    monkeypatch.setattr(
        compatibility_service,
        "_readiness_probe_for_engine",
        readiness_probe_for_engine,
    )
    monkeypatch.setattr(compatibility_service, "os", SimpleNamespace(getenv=getenv))
    monkeypatch.setattr(compatibility_service, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(compatibility_service, "logger", logger)
    monkeypatch.setattr(compatibility_service, "_create_adapter_http_app", capture_factory)

    result = compatibility_service.create_adapter_app(
        token="t" * 32,
        readiness_retry_interval_seconds=7,
        readiness_probe_timeout_seconds=11,
    )

    assert result is expected_app
    assert captured["args"] == (None, "t" * 32, None, None)
    kwargs = cast(dict[str, object], captured["kwargs"])
    assert kwargs["readiness_retry_interval_seconds"] == 7
    assert kwargs["readiness_probe_timeout_seconds"] == 11
    dependencies = cast(canonical_http.AdapterHttpDependencies, kwargs["dependencies"])
    assert dependencies.browser_engine_setting is browser_engine_setting
    assert dependencies.build_browser_client is build_browser_client
    assert dependencies.float_setting is float_setting
    assert dependencies.build_automation is build_automation
    assert dependencies.readiness_factory is readiness_factory
    assert dependencies.readiness_probe_for_engine is readiness_probe_for_engine
    assert dependencies.getenv is getenv
    assert dependencies.monotonic is monotonic
    assert dependencies.logger is logger


def test_sidecar_routes_are_owned_by_the_canonical_http_module() -> None:
    routes = {
        (route.path, frozenset(route.methods or set())): route
        for route in compatibility_service.app.routes
        if isinstance(route, APIRoute)
    }

    assert set(routes) == {
        ("/healthz", frozenset({"GET"})),
        ("/readyz", frozenset({"GET"})),
        ("/v1/session-state", frozenset({"GET"})),
        ("/v1/seat-snapshot", frozenset({"POST"})),
        ("/v1/reserve-once", frozenset({"POST"})),
        ("/v1/reserve-once/stream", frozenset({"POST"})),
        ("/v1/confirm-reservation", frozenset({"POST"})),
        ("/v1/verify-login", frozenset({"POST"})),
        ("/v1/prewarm-login", frozenset({"POST"})),
    }
    assert all(
        route.endpoint.__module__ == "rail_waitlist.korail_sidecar.http"
        for route in routes.values()
    )
    assert compatibility_service.app.openapi_url is None
    assert compatibility_service.app.docs_url is None
    assert compatibility_service.app.redoc_url is None


def test_seat_snapshot_validates_request_and_timeout_headers_and_resets_context(
    caplog,
) -> None:
    automation = CapturingAutomation()
    app = compatibility_service.create_adapter_app(
        automation=cast(canonical_http.KorailBrowserAutomation, automation),
        token=TOKEN,
        readiness_probe=ready_probe,
    )
    request_id = "42b41ae2322242b18e98ec989d09a994"
    malicious = "bad request_id=ffffffffffffffffffffffffffffffff"

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        accepted = client.post(
            "/v1/seat-snapshot",
            json=seat_snapshot_payload(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: request_id,
                REQUEST_TIMEOUT_MS_HEADER: "1234",
            },
        )
        replaced = client.post(
            "/v1/seat-snapshot",
            json=seat_snapshot_payload(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: malicious,
                REQUEST_TIMEOUT_MS_HEADER: "999999",
            },
        )
        unauthorized = client.post(
            "/v1/seat-snapshot",
            json=seat_snapshot_payload(),
            headers={REQUEST_ID_HEADER: request_id},
        )
        invalid_timeout = client.post(
            "/v1/seat-snapshot",
            json=seat_snapshot_payload(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_TIMEOUT_MS_HEADER: "1000000",
            },
        )

    generated_request_id = replaced.headers[REQUEST_ID_HEADER]
    assert accepted.status_code == 200
    assert accepted.headers[REQUEST_ID_HEADER] == request_id
    assert automation.calls[0] == (1.234, request_id)
    assert replaced.status_code == 200
    assert validated_log_id(generated_request_id) == generated_request_id
    assert generated_request_id != malicious
    assert automation.calls[1] == (None, generated_request_id)
    assert unauthorized.status_code == 401
    assert unauthorized.headers[REQUEST_ID_HEADER] == request_id
    assert invalid_timeout.status_code == 422
    assert invalid_timeout.json() == {"detail": "request_validation_failed"}
    assert validated_log_id(invalid_timeout.headers[REQUEST_ID_HEADER]) is not None
    assert "1000000" not in invalid_timeout.text
    assert len(automation.calls) == 2
    assert malicious not in caplog.text
    rejected = [
        record.getMessage()
        for record in caplog.records
        if "KORAIL adapter request rejected" in record.getMessage()
    ]
    assert any(
        f"failure=unauthorized operation=seat_snapshot request_id={request_id}" in message
        for message in rejected
    )
    assert any(
        "failure=request_validation operation=seat_snapshot" in message for message in rejected
    )
    assert current_request_id() is None


def test_seat_snapshot_projects_internal_deadlines_as_bounded_504() -> None:
    automation = CapturingAutomation(failure_stage="caller_deadline")
    app = compatibility_service.create_adapter_app(
        automation=cast(canonical_http.KorailBrowserAutomation, automation),
        token=TOKEN,
        readiness_probe=ready_probe,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/seat-snapshot",
            json=seat_snapshot_payload(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert response.status_code == 504
    assert response.json() == {"detail": {"reason": "source_unavailable"}}
    assert validated_log_id(response.headers[REQUEST_ID_HEADER]) is not None
    assert automation.calls[0][0] is None
    assert current_request_id() is None


def test_reservation_routes_bind_echo_and_log_secret_free_request_ids(caplog) -> None:
    automation = CapturingAutomation()
    reservation_client = CapturingReservationClient()
    app = compatibility_service.create_adapter_app(
        automation=cast(canonical_http.KorailBrowserAutomation, automation),
        token=TOKEN,
        readiness_probe=ready_probe,
        reservation_client=cast(canonical_http._ReservationClient, reservation_client),
    )
    supplied_request_id = "42b41ae2322242b18e98ec989d09a994"
    malformed_request_id = "bad request_id=ffffffffffffffffffffffffffffffff"

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        legacy = client.post(
            "/v1/reserve-once",
            json=reserve_once_payload(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: supplied_request_id,
            },
        )
        streamed = client.post(
            "/v1/reserve-once/stream",
            json=reserve_once_payload(),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: malformed_request_id,
            },
        )
        confirmed = client.post(
            "/v1/confirm-reservation",
            json=confirmation_payload(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    stream_request_id = streamed.headers[REQUEST_ID_HEADER]
    confirmation_request_id = confirmed.headers[REQUEST_ID_HEADER]
    assert legacy.status_code == 200
    assert streamed.status_code == 200
    assert confirmed.status_code == 200
    assert legacy.headers[REQUEST_ID_HEADER] == supplied_request_id
    assert validated_log_id(stream_request_id) == stream_request_id
    assert stream_request_id != malformed_request_id
    assert validated_log_id(confirmation_request_id) == confirmation_request_id
    assert reservation_client.calls == [
        ("reserve_once", supplied_request_id),
        ("reserve_once_stream", stream_request_id),
        ("confirm_reservation", confirmation_request_id),
    ]
    messages = [record.getMessage() for record in caplog.records]
    for operation, request_id in reservation_client.calls:
        correlated = [
            message
            for message in messages
            if f"operation={operation} request_id={request_id}" in message
        ]
        assert len(correlated) == 2
        assert any("started" in message for message in correlated)
        assert any("completed" in message for message in correlated)
    assert malformed_request_id not in caplog.text
    assert "attempt-fixture" not in caplog.text
    assert "candidate-fixture" not in caplog.text
    assert "fixture-login-secret" not in caplog.text
    assert "fixture-password-secret" not in caplog.text
    assert current_request_id() is None


def test_reservation_rejections_keep_request_correlation_without_logging_secrets(caplog) -> None:
    automation = CapturingAutomation()
    reservation_client = CapturingReservationClient()
    app = compatibility_service.create_adapter_app(
        automation=cast(canonical_http.KorailBrowserAutomation, automation),
        token=TOKEN,
        readiness_probe=ready_probe,
        reservation_client=cast(canonical_http._ReservationClient, reservation_client),
    )
    unauthorized_request_id = "42b41ae2322242b18e98ec989d09a994"
    invalid_request_id = "52b41ae2322242b18e98ec989d09a994"
    combined_request_id = "62b41ae2322242b18e98ec989d09a994"
    authorization_secret = "authorization-secret-must-not-appear"
    body_secret = "body-secret-must-not-appear"

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        unauthorized = client.post(
            "/v1/confirm-reservation",
            json=confirmation_payload(),
            headers={
                "Authorization": f"Bearer {authorization_secret}",
                REQUEST_ID_HEADER: unauthorized_request_id,
            },
        )
        invalid = client.post(
            "/v1/reserve-once",
            json={"credential": {"password": body_secret}},
            headers={
                "Authorization": f"Bearer {TOKEN}",
                REQUEST_ID_HEADER: invalid_request_id,
            },
        )
        combined = client.post(
            "/v1/reserve-once",
            json={"credential": {"password": body_secret}},
            headers={
                "Authorization": f"Bearer {authorization_secret}",
                REQUEST_ID_HEADER: combined_request_id,
            },
        )

    assert unauthorized.status_code == 401
    assert unauthorized.headers[REQUEST_ID_HEADER] == unauthorized_request_id
    assert invalid.status_code == 422
    assert invalid.headers[REQUEST_ID_HEADER] == invalid_request_id
    assert combined.status_code == 401
    assert combined.headers[REQUEST_ID_HEADER] == combined_request_id
    rejected = [
        record.getMessage()
        for record in caplog.records
        if "KORAIL adapter request rejected" in record.getMessage()
    ]
    assert rejected == [
        "KORAIL adapter request rejected failure=unauthorized "
        f"operation=confirm_reservation request_id={unauthorized_request_id}",
        "KORAIL adapter request rejected failure=request_validation "
        f"operation=reserve_once request_id={invalid_request_id}",
        "KORAIL adapter request rejected failure=unauthorized "
        f"operation=reserve_once request_id={combined_request_id}",
    ]
    assert authorization_secret not in caplog.text
    assert body_secret not in caplog.text
    assert reservation_client.calls == []
    assert current_request_id() is None


def test_sidecar_dependency_direction_and_lazy_pydoll_imports_are_fixed() -> None:
    runtime_path = SOURCE_ROOT / "korail_sidecar" / "runtime.py"
    http_path = SOURCE_ROOT / "korail_sidecar" / "http.py"
    facade_path = SOURCE_ROOT / "korail_browser_adapter_service.py"
    runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8"), filename=str(runtime_path))
    http_tree = ast.parse(http_path.read_text(encoding="utf-8"), filename=str(http_path))
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))

    def imported_roots(tree: ast.AST) -> set[str]:
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                roots.add(node.module.split(".")[0])
        return roots

    assert "fastapi" not in imported_roots(runtime_tree)
    assert "http" not in imported_roots(runtime_tree)
    assert "korail_browser_adapter_service" not in imported_roots(runtime_tree)
    assert "korail_browser_adapter_service" not in imported_roots(http_tree)

    facade_functions = {
        node.name
        for node in facade_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert facade_functions == {"create_adapter_app"}
    assert not any(
        isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef)) for node in facade_tree.body
    )

    top_level_imports = {
        node.module
        for node in http_tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "korail_pydoll_browser" not in top_level_imports


def test_adapter_service_remains_the_exact_deployment_composition_root() -> None:
    facade_path = SOURCE_ROOT / "korail_browser_adapter_service.py"
    tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    local_definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    app_assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "app" for target in node.targets)
    ]

    assert imported_modules == {
        "__future__",
        "collections.abc",
        "fastapi",
        "file_logging",
        "korail_sidecar.http",
        "korail_sidecar.playwright.client",
        "korail_sidecar.runtime",
        "korail_sidecar.search_coordinator",
        "logging",
        "os",
        "time",
        "typing",
    }
    assert local_definitions == {"create_adapter_app"}
    assert len(app_assignments) == 1
    app_factory = app_assignments[0].value
    assert isinstance(app_factory, ast.Call)
    assert isinstance(app_factory.func, ast.Name)
    assert app_factory.func.id == "create_adapter_app"
    assert app_factory.args == []
    assert app_factory.keywords == []

    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile.browser"
    docker_lines = [
        line.strip()
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    runtime_starts = [
        index
        for index, line in enumerate(docker_lines)
        if line.casefold() == "from browser-base as runtime"
    ]
    assert len(runtime_starts) == 1
    runtime_start = runtime_starts[0]
    runtime_end = next(
        (
            index
            for index in range(runtime_start + 1, len(docker_lines))
            if docker_lines[index].casefold().startswith("from ")
        ),
        len(docker_lines),
    )
    runtime_stage = docker_lines[runtime_start:runtime_end]
    expected_cmd = (
        'CMD ["uvicorn", "rail_waitlist.korail_browser_adapter_service:app", "--host", '
        '"0.0.0.0", "--port", "8001", "--no-access-log"]'
    )
    assert [line for line in runtime_stage if line.casefold().startswith("cmd ")] == [expected_cmd]
    assert runtime_stage[-1] == expected_cmd
