from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import models  # noqa: F401 - registers metadata
from .auth import router as auth_router
from .browser_companion.http import (
    admin_router as browser_companion_admin_router,
)
from .browser_companion.http import (
    router as browser_bridge_router,
)
from .config import get_settings
from .database import SessionFactory, create_schema, get_session
from .domain import OutboxStatus
from .event_stream.http import router as event_stream_router
from .file_logging import configure_service_file_logging
from .health.schemas import HealthResponse
from .korail_browser_seat_source import KorailBrowserSeatSource
from .metrics import HTTP_DURATION, HTTP_REQUESTS, OUTBOX_PENDING
from .notification_management.http import router as notification_management_router
from .operation_summary.http import router as operation_summary_router
from .outbox_management.models import OutboxEvent
from .provider_account_management.http import router as provider_account_management_router
from .provider_account_management.login_verification import ProviderLoginVerifier
from .provider_account_management.runtime import (
    ProviderRuntimePrewarmRegistry,
    run_provider_session_manager,
)
from .provider_adapters.srt_fullstack_fixture import fullstack_srt_client_factory
from .provider_adapters.srt_seat_source import SrtLiveSeatSource
from .provider_adapters.tago import TagoClient
from .provider_registry.http import router as provider_registry_router
from .seat_status_cooldown import RedisCooldownStore
from .seat_status_operations.http import router as seat_status_operations_router
from .srt_sidecar.client import SrtProviderAdapterClient
from .timetable_management.catalog_application import StationCatalogService
from .timetable_management.catalog_http import router as station_catalog_router
from .timetable_management.http import router as timetable_management_router
from .timetable_management.official_evidence_http import (
    confirmation_router as official_confirmation_router,
)
from .timetable_management.official_evidence_http import (
    snapshot_router as official_snapshot_router,
)
from .timetable_management.station_visibility import KorailStationVisibility
from .timetable_snapshot_cache import TimetableSnapshotCache
from .ui_preferences.http import router as ui_preferences_router
from .watch_management.http import router as watch_management_router

configure_service_file_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_schema:
        await create_schema()
    preload_task = asyncio.create_task(app.state.station_catalog_service.preload())
    provider_session_manager_task = asyncio.create_task(
        run_provider_session_manager(
            app.state.provider_runtime_session_factory,
            app.state.provider_login_verifier,
            app.state.provider_runtime_prewarm_registry,
        ),
        name="provider-session-manager",
    )
    try:
        yield
    finally:
        for task in (preload_task, provider_session_manager_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            preload_task,
            provider_session_manager_task,
            return_exceptions=True,
        )
        await app.state.timetable_snapshot_cache.close()
        await app.state.station_catalog_service.close()
        await app.state.korail_browser_seat_source.close()
        await app.state.srt_seat_source.drain_pending_calls()
        close_srt_source = getattr(app.state.srt_seat_source, "close", None)
        if close_srt_source is not None:
            await close_srt_source()
        await app.state.seat_status_redis.aclose()


def create_app(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    tago_client: TagoClient | None = None,
) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def redact_provider_account_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> Response:
        if request.url.path.startswith("/api/v1/provider-accounts/"):
            return JSONResponse(
                status_code=422,
                content={"detail": "request_validation_failed"},
                headers={"Cache-Control": "no-store"},
            )
        return await request_validation_exception_handler(request, error)

    app.state.station_catalog_service = StationCatalogService(
        session_factory,
        tago_client=tago_client,
        station_visibility=KorailStationVisibility(url=settings.korail_station_data_url),
    )
    app.state.seat_status_redis = Redis.from_url(settings.redis_url, decode_responses=True)
    seat_status_cooldown_store = RedisCooldownStore(app.state.seat_status_redis)
    app.state.seat_status_cooldown_store = seat_status_cooldown_store
    app.state.timetable_snapshot_cache = TimetableSnapshotCache()
    app.state.timetable_snapshot_session_factory = session_factory
    if settings.srt_provider_adapter_enabled:
        app.state.srt_seat_source = SrtProviderAdapterClient(
            settings.srt_provider_adapter_url,
            settings.srt_provider_adapter_timeout_seconds,
            settings.srt_provider_adapter_token,
        )
    else:
        app.state.srt_seat_source = SrtLiveSeatSource(
            enabled=settings.srt_seat_status_enabled,
            cache_ttl_seconds=settings.srt_seat_status_cache_ttl_seconds,
            timeout_seconds=settings.srt_seat_status_timeout_seconds,
            rate_limit_cooldown_seconds=settings.seat_status_rate_limit_cooldown_seconds,
            protection_cooldown_seconds=settings.seat_status_protection_cooldown_seconds,
            cooldown_store=seat_status_cooldown_store,
            **(
                {
                    "client_factory": fullstack_srt_client_factory(
                        settings.srt_fullstack_fixture_url
                    ),
                    "source_name": "fullstack-srt-fixture",
                }
                if settings.srt_fullstack_fixture_url is not None
                else {}
            ),
        )
    app.state.korail_browser_seat_source = KorailBrowserSeatSource(
        enabled=(settings.experimental_rail_enabled and settings.korail_browser_adapter_enabled),
        adapter_url=settings.korail_browser_adapter_url,
        cache_ttl_seconds=settings.korail_browser_adapter_cache_ttl_seconds,
        timeout_seconds=settings.korail_browser_adapter_timeout_seconds,
        token=settings.korail_browser_adapter_token,
        rate_limit_cooldown_seconds=settings.seat_status_rate_limit_cooldown_seconds,
        protection_cooldown_seconds=settings.seat_status_protection_cooldown_seconds,
        cooldown_store=seat_status_cooldown_store,
        allow_fullstack_test_url=settings.environment == "test",
    )
    app.state.provider_login_verifier = ProviderLoginVerifier(
        app.state.korail_browser_seat_source,
        app.state.srt_seat_source if settings.srt_provider_adapter_enabled else None,
    )
    app.state.provider_runtime_session_factory = session_factory
    app.state.provider_runtime_prewarm_registry = ProviderRuntimePrewarmRegistry()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
            "X-CSRF-Token",
            "X-Rail-Bridge-Token",
            "X-Rail-Bridge-Client-Id",
            "X-Rail-Bridge-Challenge",
        ],
    )
    app.include_router(auth_router)
    app.include_router(browser_bridge_router)
    app.include_router(browser_companion_admin_router)
    app.include_router(notification_management_router)
    app.include_router(operation_summary_router)
    app.include_router(provider_account_management_router)
    app.include_router(timetable_management_router)
    app.include_router(ui_preferences_router)
    app.include_router(watch_management_router)
    app.include_router(official_snapshot_router)
    app.include_router(provider_registry_router)
    app.include_router(station_catalog_router)
    app.include_router(seat_status_operations_router)
    app.include_router(official_confirmation_router)
    app.include_router(event_stream_router)

    @app.middleware("http")
    async def instrument_requests(request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        route_label = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route_label, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route_label).observe(time.perf_counter() - started)
        return response

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok", experimental_rail_enabled=settings.experimental_rail_enabled
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics(session: AsyncSession = Depends(get_session)) -> Response:
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
        )
        OUTBOX_PENDING.set(int(pending or 0))
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
