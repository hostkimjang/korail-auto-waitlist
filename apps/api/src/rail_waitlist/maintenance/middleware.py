from __future__ import annotations

from contextlib import ExitStack

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .gate import MaintenanceClosed, MaintenanceGate

EXEMPT_PATHS = frozenset({"/healthz", "/readyz", "/api/v1/auth/login", "/api/v1/auth/status"})
# Management screens must keep working while the manager drains and restarts the app.
MANAGEMENT_PATHS = frozenset(
    {"/api/v1/system/update", "/api/v1/system/settings", "/api/v1/system/restart"}
)


class MaintenanceMiddleware:
    def __init__(self, app: ASGIApp, exempt_paths: frozenset[str] = EXEMPT_PATHS) -> None:
        self.app = app
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["path"] in self.exempt_paths
            or scope["path"] in MANAGEMENT_PATHS
            or scope["path"].startswith("/api/v1/system/update/jobs/")
        ):
            await self.app(scope, receive, send)
            return
        gate = MaintenanceGate.configured()
        with ExitStack() as stack:
            try:
                stack.enter_context(gate.operation())
            except MaintenanceClosed:
                await JSONResponse(
                    status_code=503,
                    content={"detail": "maintenance_in_progress"},
                    headers={"Cache-Control": "no-store", "Retry-After": "5"},
                )(scope, receive, send)
                return
            await self.app(scope, receive, send)
