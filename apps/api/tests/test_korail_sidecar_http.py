import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.routing import APIRoute

import rail_waitlist.korail_browser_adapter_service as compatibility_service
from rail_waitlist.korail_sidecar import http as canonical_http

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "rail_waitlist"


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
