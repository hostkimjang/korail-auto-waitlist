from __future__ import annotations

import hmac as _hmac
import logging as _logging
import os as _os
import time as _time
from collections.abc import Callable as _Callable
from contextlib import asynccontextmanager as _asynccontextmanager
from typing import Protocol as _Protocol

from . import file_logging as _file_logging
from .srt_sidecar import application as _application
from .srt_sidecar import contracts as _contracts
from .srt_sidecar import http as _http
from .srt_sidecar import runtime as _runtime
from .srt_sidecar.reservation import (
    default_srt_reservation_executor as _default_srt_reservation_executor,
)

hmac = _hmac
os = _os
time = _time
Callable = _Callable
asynccontextmanager = _asynccontextmanager
Protocol = _Protocol

FastAPI = _http.FastAPI
HTTPException = _http.HTTPException
Request = _http.Request
request_validation_exception_handler = _http.request_validation_exception_handler
RequestValidationError = _http.RequestValidationError
JSONResponse = _http.JSONResponse
Response = _http.Response

Redis = _runtime.Redis
RedisCooldownStore = _runtime.RedisCooldownStore
SrtLiveSeatSource = _runtime.SrtLiveSeatSource

RequestException = _application.RequestException
SRTError = _application.SRTError
SRTLoginError = _application.SRTLoginError
SRTNotLoggedInError = _application.SRTNotLoggedInError
SRTResponseError = _application.SRTResponseError
SRTNetFunnelError = _application.SRTNetFunnelError

configure_service_file_logging = _file_logging.configure_service_file_logging
SeatObservationRequest = _application.SeatObservationRequest
ReservationRequest = _application.ReservationRequest
ReservationResult = _application.ReservationResult
default_srt_reservation_executor = _default_srt_reservation_executor
TimetableItem = _application.TimetableItem

SrtProviderSource = _application.SrtProviderSource
SrtProviderExecutor = _application.SrtProviderExecutor

SrtConfirmReservationRequest = _contracts.SrtConfirmReservationRequest
SrtConfirmReservationResult = _contracts.SrtConfirmReservationResult
SrtLoginRequest = _contracts.SrtLoginRequest
SrtLoginResult = _contracts.SrtLoginResult
SrtObserveRequest = _contracts.SrtObserveRequest
SrtObserveResult = _contracts.SrtObserveResult
SrtReservationConfirmationResult = _contracts.SrtReservationConfirmationResult
SrtReserveOnceRequest = _contracts.SrtReserveOnceRequest
SrtReserveOnceResult = _contracts.SrtReserveOnceResult
SrtSessionStatus = _contracts.SrtSessionStatus
SrtTimetableOverlayRequest = _contracts.SrtTimetableOverlayRequest
SrtTimetableOverlayResult = _contracts.SrtTimetableOverlayResult
SrtTimetableSearchRequest = _contracts.SrtTimetableSearchRequest
SrtTimetableSearchResult = _contracts.SrtTimetableSearchResult
SrtTimetableTrain = _contracts.SrtTimetableTrain

configure_service_file_logging()
_file_logging.configure_service_console_logging(_logging.getLogger("rail_waitlist"))


def _bounded_number(name: str, default: float, minimum: float, maximum: float) -> float:
    return _runtime.bounded_number(
        name,
        default,
        minimum,
        maximum,
        getenv=lambda key, fallback=None: os.getenv(key, fallback),
    )


def _build_default_source() -> tuple[SrtProviderSource, _runtime.RedisResource]:
    dependencies = _runtime.default_runtime_dependencies(
        getenv=lambda key, fallback=None: os.getenv(key, fallback),
        redis_from_url=lambda url, *, decode_responses: Redis.from_url(
            url,
            decode_responses=decode_responses,
        ),
        cooldown_store_factory=lambda redis: RedisCooldownStore(redis),
        source_factory=lambda **kwargs: SrtLiveSeatSource(**kwargs),
    )
    return _runtime.build_default_source(
        dependencies=dependencies,
        number_reader=lambda name, default, minimum, maximum: _bounded_number(
            name,
            default,
            minimum,
            maximum,
        ),
    )


def create_srt_provider_adapter_app(
    *,
    source: SrtProviderSource | None = None,
    executor: SrtProviderExecutor | None = None,
    token: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    return _http.create_srt_provider_adapter_app(
        source=source,
        executor=executor,
        token=token,
        monotonic=monotonic,
        dependencies=_http.SrtHttpDependencies(
            getenv=lambda key, fallback=None: os.getenv(key, fallback),
            build_default_source=lambda: _build_default_source(),
            default_executor=lambda: default_srt_reservation_executor(),
            login_exception_types=lambda: _application.default_srt_login_exception_types(
                auth_required=(SRTLoginError, SRTNotLoggedInError),
                provider_blocked=(SRTNetFunnelError,),
                failed=(RequestException, SRTResponseError, SRTError),
            ),
        ),
    )


app = create_srt_provider_adapter_app()
