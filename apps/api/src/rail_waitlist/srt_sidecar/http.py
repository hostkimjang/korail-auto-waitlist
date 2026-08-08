from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from .application import (
    SrtLoginExceptionTypes,
    SrtProviderExecutor,
    SrtProviderSource,
    build_session_status,
)
from .application import (
    prewarm_or_verify_login as _prewarm_or_verify_login,
)
from .contracts import (
    SrtConfirmReservationRequest,
    SrtConfirmReservationResult,
    SrtLoginRequest,
    SrtLoginResult,
    SrtObserveRequest,
    SrtObserveResult,
    SrtReservationConfirmationResult,
    SrtReserveOnceRequest,
    SrtReserveOnceResult,
    SrtSessionStatus,
    SrtTimetableOverlayRequest,
    SrtTimetableOverlayResult,
    SrtTimetableSearchRequest,
    SrtTimetableSearchResult,
    SrtTimetableTrain,
)
from .ports import EnvironmentReader, RedisResource


@dataclass(frozen=True)
class SrtHttpDependencies:
    getenv: EnvironmentReader
    build_default_source: Callable[[], tuple[SrtProviderSource, RedisResource]]
    default_executor: Callable[[], SrtProviderExecutor]
    login_exception_types: Callable[[], SrtLoginExceptionTypes]


def create_srt_provider_adapter_app(
    *,
    source: SrtProviderSource | None = None,
    executor: SrtProviderExecutor | None = None,
    token: str | None = None,
    monotonic: Callable[[], float],
    dependencies: SrtHttpDependencies,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_token = token or dependencies.getenv("SRT_PROVIDER_ADAPTER_TOKEN")
        if resolved_token is None or len(resolved_token.encode("utf-8")) < 32:
            raise RuntimeError("SRT_PROVIDER_ADAPTER_TOKEN must be at least 32 UTF-8 bytes")
        app.state.token = resolved_token
        app.state.redis = None
        if source is None:
            app.state.source, app.state.redis = dependencies.build_default_source()
        else:
            app.state.source = source
        app.state.executor = executor or dependencies.default_executor()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            source_owner = cast(SrtProviderSource, app.state.source)
            await source_owner.drain_pending_calls()
            redis = cast(RedisResource | None, app.state.redis)
            if redis is not None:
                await redis.aclose()

    app = FastAPI(title="SRT Provider Adapter", version="0.1.0", lifespan=lifespan)
    app.state.ready = False
    app.state.token = None

    @app.exception_handler(RequestValidationError)
    async def redact_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> Response:
        if request.url.path.startswith("/v1/"):
            return JSONResponse(
                status_code=422,
                content={"detail": "request_validation_failed"},
                headers={"Cache-Control": "no-store"},
            )
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def authenticate_internal_request(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path.startswith("/v1/"):
            authorization = request.headers.get("Authorization", "")
            expected = f"Bearer {request.app.state.token}"
            if not hmac.compare_digest(authorization, expected):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "unauthorized"},
                    headers={"Cache-Control": "no-store"},
                )
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> dict[str, str]:
        if not request.app.state.ready:
            raise HTTPException(503, "not_ready")
        return {"status": "ready"}

    @app.get("/v1/session-status", response_model=SrtSessionStatus)
    async def session_status(request: Request) -> SrtSessionStatus:
        return await build_session_status(
            cast(SrtProviderSource, request.app.state.source),
            cast(SrtProviderExecutor, request.app.state.executor),
            monotonic=monotonic,
        )

    @app.post("/v1/prewarm-or-verify-login", response_model=SrtLoginResult)
    async def prewarm_or_verify_login(
        data: SrtLoginRequest,
        request: Request,
    ) -> SrtLoginResult:
        return await _prewarm_or_verify_login(
            data,
            cast(SrtProviderExecutor, request.app.state.executor),
            exception_types=dependencies.login_exception_types(),
        )

    @app.post("/v1/observe", response_model=SrtObserveResult)
    async def observe(data: SrtObserveRequest, request: Request) -> SrtObserveResult:
        source_owner = cast(SrtProviderSource, request.app.state.source)
        try:
            observations = await source_owner.observe(
                data.request,
                origin=data.origin.strip(),
                destination=data.destination.strip(),
            )
        except Exception as error:
            raise HTTPException(503, "adapter_unavailable") from error
        return SrtObserveResult(observations=observations)

    @app.post("/v1/timetable-overlay", response_model=SrtTimetableOverlayResult)
    async def timetable_overlay(
        data: SrtTimetableOverlayRequest,
        request: Request,
    ) -> SrtTimetableOverlayResult:
        source_owner = cast(SrtProviderSource, request.app.state.source)
        try:
            items = await source_owner.overlay(
                data.items,
                origin=data.origin.strip(),
                destination=data.destination.strip(),
                departure_from=data.departure_from,
                departure_to=data.departure_to,
                passenger_count=data.passenger_count,
            )
        except Exception as error:
            raise HTTPException(503, "adapter_unavailable") from error
        return SrtTimetableOverlayResult(items=items)

    @app.post("/v1/timetable-search", response_model=SrtTimetableSearchResult)
    async def timetable_search(
        data: SrtTimetableSearchRequest,
        request: Request,
    ) -> SrtTimetableSearchResult:
        source_owner = cast(SrtProviderSource, request.app.state.source)
        try:
            trains = await source_owner.search_timetable(
                origin=data.origin,
                destination=data.destination,
                departure_from=data.departure_from,
                departure_to=data.departure_to,
                passenger_count=data.passenger_count,
            )
        except Exception as error:
            raise HTTPException(503, "adapter_unavailable") from error
        return SrtTimetableSearchResult(
            trains=[
                SrtTimetableTrain.model_validate(train, from_attributes=True) for train in trains
            ]
        )

    @app.post("/v1/reserve-once", response_model=SrtReserveOnceResult)
    async def reserve_once(
        data: SrtReserveOnceRequest,
        request: Request,
    ) -> SrtReserveOnceResult:
        executor_owner = cast(SrtProviderExecutor, request.app.state.executor)
        try:
            result = await executor_owner.reserve_once(
                data.request,
                data.credential.to_credentials(),
            )
        except Exception as error:
            raise HTTPException(503, "adapter_unavailable") from error
        return SrtReserveOnceResult(result=result)

    @app.post("/v1/confirm-reservation", response_model=SrtConfirmReservationResult)
    async def confirm_reservation(
        data: SrtConfirmReservationRequest,
        request: Request,
    ) -> SrtConfirmReservationResult:
        executor_owner = cast(SrtProviderExecutor, request.app.state.executor)
        try:
            result = await executor_owner.confirm_reservation(
                data.target.to_domain(),
                data.credential.to_credentials(),
            )
        except Exception as error:
            raise HTTPException(503, "adapter_unavailable") from error
        return SrtConfirmReservationResult(
            result=SrtReservationConfirmationResult.from_domain(result)
        )

    return app
