from __future__ import annotations

import hmac
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from requests import RequestException
from SRT import SRTError, SRTLoginError, SRTNotLoggedInError, SRTResponseError
from SRT.errors import SRTNetFunnelError

from .file_logging import configure_service_file_logging
from .schemas import ReservationRequest, ReservationResult, SeatObservationRequest, TimetableItem
from .seat_status_cooldown import RedisCooldownStore
from .srt_provider_adapter_contract import (
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
from .srt_reservation import default_srt_reservation_executor
from .srt_seat_source import SrtLiveSeatSource

configure_service_file_logging()


class SrtProviderSource(Protocol):
    async def observation_deferred_until(self): ...

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ): ...

    async def overlay(
        self,
        items: list[TimetableItem],
        *,
        origin: str,
        destination: str,
        departure_from,
        departure_to,
        passenger_count: int,
    ): ...

    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from,
        departure_to,
        passenger_count: int,
    ): ...

    async def drain_pending_calls(self) -> None: ...


class SrtProviderExecutor(Protocol):
    async def verify_credentials(self, credentials) -> bool: ...

    async def prewarm_credentials(self, credentials) -> bool: ...

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials,
    ) -> ReservationResult: ...

    async def confirm_reservation(self, target, credentials): ...

    def session_snapshot(self): ...


def _bounded_number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be numeric") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _build_default_source() -> tuple[SrtLiveSeatSource, Redis]:
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    source = SrtLiveSeatSource(
        enabled=True,
        cache_ttl_seconds=int(_bounded_number("SRT_SEAT_STATUS_CACHE_TTL_SECONDS", 1, 1, 300)),
        timeout_seconds=_bounded_number("SRT_SEAT_STATUS_TIMEOUT_SECONDS", 8, 3, 30),
        rate_limit_cooldown_seconds=int(
            _bounded_number("SEAT_STATUS_RATE_LIMIT_COOLDOWN_SECONDS", 1800, 60, 86400)
        ),
        protection_cooldown_seconds=int(
            _bounded_number("SEAT_STATUS_PROTECTION_COOLDOWN_SECONDS", 300, 300, 86400)
        ),
        cooldown_store=RedisCooldownStore(redis),
    )
    return source, redis


def create_srt_provider_adapter_app(
    *,
    source: SrtProviderSource | None = None,
    executor: SrtProviderExecutor | None = None,
    token: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_token = token or os.getenv("SRT_PROVIDER_ADAPTER_TOKEN")
        if resolved_token is None or len(resolved_token.encode("utf-8")) < 32:
            raise RuntimeError("SRT_PROVIDER_ADAPTER_TOKEN must be at least 32 UTF-8 bytes")
        app.state.token = resolved_token
        app.state.redis = None
        if source is None:
            app.state.source, app.state.redis = _build_default_source()
        else:
            app.state.source = source
        app.state.executor = executor or default_srt_reservation_executor()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            await app.state.source.drain_pending_calls()
            if app.state.redis is not None:
                await app.state.redis.aclose()

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
    async def authenticate_internal_request(request: Request, call_next):
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
        snapshot = request.app.state.executor.session_snapshot()
        deferred_until = await request.app.state.source.observation_deferred_until()
        now = monotonic()

        def age(value: float | None) -> float | None:
            return None if value is None else max(0.0, now - value)

        return SrtSessionStatus(
            state=snapshot.state,
            credential_generation=snapshot.credential_generation,
            locally_reusable=snapshot.locally_reusable,
            created_age_seconds=age(snapshot.created_at_monotonic),
            last_verified_age_seconds=age(snapshot.last_verified_at_monotonic),
            last_used_age_seconds=age(snapshot.last_used_at_monotonic),
            local_reuse_remaining_seconds=(
                None
                if snapshot.local_reuse_until_monotonic is None
                else max(0.0, snapshot.local_reuse_until_monotonic - now)
            ),
            observation_deferred_until=deferred_until,
        )

    @app.post("/v1/prewarm-or-verify-login", response_model=SrtLoginResult)
    async def prewarm_or_verify_login(
        data: SrtLoginRequest,
        request: Request,
    ) -> SrtLoginResult:
        credentials = data.credential.to_credentials()
        method = (
            request.app.state.executor.prewarm_credentials
            if data.operation == "prewarm"
            else request.app.state.executor.verify_credentials
        )
        try:
            authenticated = await method(credentials)
        except ValueError:
            outcome = "invalid_identifier"
        except (SRTLoginError, SRTNotLoggedInError):
            outcome = "auth_required"
        except SRTNetFunnelError:
            outcome = "provider_blocked"
        except (RequestException, SRTResponseError, SRTError):
            outcome = "failed"
        except Exception:  # noqa: BLE001 - response-shape failures stay sanitized.
            outcome = "failed"
        else:
            outcome = "authenticated" if authenticated else "auth_required"
        return SrtLoginResult(outcome=outcome)

    @app.post("/v1/observe", response_model=SrtObserveResult)
    async def observe(data: SrtObserveRequest, request: Request) -> SrtObserveResult:
        try:
            observations = await request.app.state.source.observe(
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
        try:
            items = await request.app.state.source.overlay(
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
        try:
            trains = await request.app.state.source.search_timetable(
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
        try:
            result = await request.app.state.executor.reserve_once(
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
        try:
            result = await request.app.state.executor.confirm_reservation(
                data.target.to_domain(),
                data.credential.to_credentials(),
            )
        except Exception as error:
            raise HTTPException(503, "adapter_unavailable") from error
        return SrtConfirmReservationResult(
            result=SrtReservationConfirmationResult.from_domain(result)
        )

    return app


app = create_srt_provider_adapter_app()
