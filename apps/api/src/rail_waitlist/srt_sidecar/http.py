from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from ..provider_call_context import (
    REQUEST_ID_HEADER,
    bind_request_id,
    current_request_id,
    validated_log_id,
)
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationDiagnosticCode,
)
from ..reservations.provider_confirmation.srt import SRT_RESERVATION_LIST_SOURCE
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
    SrtReadOnlyCallRegistrationRequest,
    SrtReadOnlyCallRegistrationResult,
    SrtReadOnlyCallStatus,
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
from .read_only_lifecycle import READ_ONLY_CALL_ID_HEADER, SrtReadOnlyCallRegistry

_TRACKED_READ_ONLY_PATHS = frozenset(
    {"/v1/observe", "/v1/timetable-overlay", "/v1/timetable-search"}
)
_LOGGER = logging.getLogger(__name__)


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
        app.state.read_only_registry = SrtReadOnlyCallRegistry(app.state.source)
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
            supplied_request_id = validated_log_id(request.headers.get(REQUEST_ID_HEADER))
            with bind_request_id(supplied_request_id) as request_id:
                tracked_call_id = request.headers.get(READ_ONLY_CALL_ID_HEADER)
                registry = cast(SrtReadOnlyCallRegistry, request.app.state.read_only_registry)
                response: Response
                if request.url.path in _TRACKED_READ_ONLY_PATHS and tracked_call_id is not None:
                    if validated_log_id(tracked_call_id) is None:
                        response = JSONResponse(
                            status_code=422,
                            content={"detail": "request_validation_failed"},
                        )
                    elif not await registry.begin(tracked_call_id, request_id):
                        response = JSONResponse(
                            status_code=409,
                            content={"detail": "read_only_call_not_registered"},
                        )
                    else:
                        try:
                            response = await call_next(request)
                        finally:
                            await registry.finish(tracked_call_id)
                else:
                    response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        return await call_next(request)

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

    @app.post(
        "/v1/read-only-call-register",
        response_model=SrtReadOnlyCallRegistrationResult,
    )
    async def register_read_only_call(
        data: SrtReadOnlyCallRegistrationRequest,
        request: Request,
    ) -> SrtReadOnlyCallRegistrationResult:
        if validated_log_id(data.call_id) is None or validated_log_id(data.request_id) is None:
            raise HTTPException(422, "request_validation_failed")
        registry = cast(SrtReadOnlyCallRegistry, request.app.state.read_only_registry)
        accepted = await registry.register(data.call_id, data.request_id)
        return SrtReadOnlyCallRegistrationResult(
            accepted=accepted,
            instance_id=registry.instance_id,
        )

    @app.get("/v1/read-only-call-status", response_model=SrtReadOnlyCallStatus)
    async def read_only_call_status(
        request: Request,
        call_id: str = Query(min_length=32, max_length=32),
    ) -> SrtReadOnlyCallStatus:
        if validated_log_id(call_id) is None:
            raise HTTPException(422, "request_validation_failed")
        registry = cast(SrtReadOnlyCallRegistry, request.app.state.read_only_registry)
        return SrtReadOnlyCallStatus(
            state=await registry.status(call_id),
            instance_id=registry.instance_id,
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
        request_id = current_request_id() or "unbound"
        try:
            result = await executor_owner.confirm_reservation(
                data.target.to_domain(),
                data.credential.to_credentials(),
            )
        except Exception as error:
            _LOGGER.error(
                "SRT reservation confirmation failed "
                "event=provider_confirmation_failed provider=SRT operation=confirm_reservation "
                "request_id=%s outcome=inconclusive diagnostic_code=%s "
                "source=%s phase=official_read",
                request_id,
                ReservationConfirmationDiagnosticCode.OFFICIAL_READ_UNAVAILABLE.value,
                SRT_RESERVATION_LIST_SOURCE,
            )
            raise HTTPException(503, "adapter_unavailable") from error
        _LOGGER.info(
            "SRT reservation confirmation completed "
            "event=provider_confirmation_completed provider=SRT operation=confirm_reservation "
            "request_id=%s outcome=%s diagnostic_code=%s source=%s phase=completed",
            request_id,
            result.outcome.value,
            result.diagnostic_code.value if result.diagnostic_code is not None else "none",
            result.source,
        )
        return SrtConfirmReservationResult(
            result=SrtReservationConfirmationResult.from_domain(result)
        )

    return app
