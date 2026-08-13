from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast, overload

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from ..domain import Provider, SeatClass
from ..korail_sidecar.browser_contracts import (
    BrowserAdapterError,
    BrowserClient,
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
)
from ..provider_call_context import (
    REQUEST_ID_HEADER,
    REQUEST_TIMEOUT_MS_HEADER,
    bind_request_id,
    validated_log_id,
)
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationResult,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)
from ..reservations.provider_confirmation.korail import (
    KORAIL_CONFIRMATION_SOURCE,
    normalize_korail_same_session_detail,
)
from .browser_page_contracts import (
    FULLSTACK_E2E_PAGE_URL,
    OFFICIAL_KORAIL_SEARCH_URL,
)
from .browser_service_availability import BrowserProviderUnavailable
from .contracts import (
    KorailLoginVerificationOutcomeValue,
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
    KorailReservationConfirmationRequest,
    KorailReservationConfirmationResult,
    KorailReservationOutcomeValue,
    KorailReservedSeat,
    KorailReserveOnceRequest,
    KorailReserveOnceResult,
    KorailReserveProgressFrame,
    KorailReserveResultFrame,
    KorailSessionActorStateValue,
    KorailSessionStateResult,
)
from .runtime import KorailBrowserEngine
from .search_coordinator import KorailBrowserAutomation

NO_STORE_HEADERS = {"Cache-Control": "no-store"}
MAX_SEAT_SNAPSHOT_TIMEOUT_MS = 170_000


class _ReservationClient(Protocol):
    async def reserve_once(
        self,
        request: object,
        *,
        on_progress: Callable[[object], None] | None = None,
    ) -> object: ...

    async def prewarm_credentials(self, credential: object) -> bool: ...

    async def read_reservation_detail(self, target: object) -> object: ...

    def session_snapshot(self) -> object: ...


class ReadinessState(Protocol):
    ready: bool

    async def probe_if_due(self, *, force: bool = False) -> bool: ...


class ReadinessFactory(Protocol):
    def __call__(
        self,
        probe: Callable[[], Awaitable[None]],
        *,
        retry_interval_seconds: float,
        probe_timeout_seconds: float,
    ) -> ReadinessState: ...


class BrowserClientFactory(Protocol):
    def __call__(
        self,
        engine: KorailBrowserEngine,
        *,
        page_url: str,
        timeout_seconds: float,
        allow_fullstack_fixture: bool,
    ) -> BrowserClient: ...


class AutomationFactory(Protocol):
    def __call__(
        self,
        engine: KorailBrowserEngine | None = None,
        *,
        browser_client: BrowserClient | None = None,
    ) -> KorailBrowserAutomation: ...


class FloatSetting(Protocol):
    def __call__(
        self,
        name: str,
        default: float,
        *,
        minimum: float,
        maximum: float,
    ) -> float: ...


class EnvironmentReader(Protocol):
    @overload
    def __call__(self, key: str) -> str | None: ...

    @overload
    def __call__(self, key: str, default: str) -> str: ...


class SidecarLogger(Protocol):
    def error(self, message: object, *args: object) -> None: ...

    def info(self, message: object, *args: object) -> None: ...

    def warning(self, message: object, *args: object) -> None: ...


@dataclass(frozen=True)
class AdapterHttpDependencies:
    browser_engine_setting: Callable[[], KorailBrowserEngine]
    build_browser_client: BrowserClientFactory
    float_setting: FloatSetting
    build_automation: AutomationFactory
    readiness_factory: ReadinessFactory
    readiness_probe_for_engine: Callable[[KorailBrowserEngine], Callable[[], Awaitable[None]]]
    getenv: EnvironmentReader
    monotonic: Callable[[], float]
    logger: SidecarLogger


class _SessionState(Protocol):
    value: KorailSessionActorStateValue


class _SessionSnapshot(Protocol):
    state: _SessionState
    credential_generation: str | None
    created_at_monotonic: float | None
    last_verified_at_monotonic: float | None
    last_used_at_monotonic: float | None
    local_reuse_until_monotonic: float | None
    locally_reusable: bool


class _Outcome(Protocol):
    value: KorailReservationOutcomeValue


class _ReservedSeat(Protocol):
    car_number: str
    seat_number: str


class _ReserveOnceResult(Protocol):
    outcome: _Outcome
    reason: str
    seat_clicked: bool
    reservation_clicked: bool
    session_ready_at: datetime | None
    target_rechecked_at: datetime | None
    seat_selected_at: datetime | None
    reservation_requested_at: datetime | None
    reserved_seats: tuple[_ReservedSeat, ...]


class _ReservationProgress(Protocol):
    stage: str
    occurred_at: datetime


def create_adapter_app(
    automation: KorailBrowserAutomation | None = None,
    token: str | None = None,
    readiness_probe: Callable[[], Awaitable[None]] | None = None,
    reservation_client: _ReservationClient | None = None,
    *,
    readiness_retry_interval_seconds: float = 5,
    readiness_probe_timeout_seconds: float = 30,
    dependencies: AdapterHttpDependencies,
) -> FastAPI:
    def internal_reservation_request(request: KorailReserveOnceRequest) -> object:
        from .pydoll.auth_contracts import KorailCredentialInput, KorailLoginMethod
        from .pydoll.reservation_contracts import (
            KorailReservationRequest,
            KorailReservationSeatClass,
        )

        return KorailReservationRequest(
            origin=request.origin,
            destination=request.destination,
            travel_date=request.travel_date,
            train_number=request.train_number,
            train_type=request.train_type,
            departure_time=request.departure_time,
            arrival_time=request.arrival_time,
            seat_class=KorailReservationSeatClass(request.seat_class),
            credential=KorailCredentialInput(
                login_id=request.credential.login_id.get_secret_value(),
                password=request.credential.password.get_secret_value(),
                version=request.credential.version,
                login_method=KorailLoginMethod(request.credential.login_method),
            ),
        )

    def public_reservation_result(result: _ReserveOnceResult) -> KorailReserveOnceResult:
        return KorailReserveOnceResult(
            outcome=result.outcome.value,
            reason=result.reason,
            seat_clicked=result.seat_clicked,
            reservation_clicked=result.reservation_clicked,
            session_ready_at=result.session_ready_at,
            target_rechecked_at=result.target_rechecked_at,
            seat_selected_at=result.seat_selected_at,
            reservation_requested_at=result.reservation_requested_at,
            reserved_seats=[
                KorailReservedSeat(
                    car_number=seat.car_number,
                    seat_number=seat.seat_number,
                )
                for seat in result.reserved_seats
            ],
        )

    def failed_reservation_result() -> KorailReserveOnceResult:
        return KorailReserveOnceResult(
            outcome="failed",
            reason="reservation_backend_error",
            seat_clicked=False,
            reservation_clicked=False,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = dependencies.browser_engine_setting()
        app.state.browser_engine = engine.value
        app.state.pending_reservation_tasks = set()
        if automation is None:
            page_url = dependencies.getenv("KORAIL_BROWSER_PAGE_URL", OFFICIAL_KORAIL_SEARCH_URL)
            allow_fullstack_fixture = (
                dependencies.getenv("ENVIRONMENT", "").strip().lower() == "test"
                and page_url == FULLSTACK_E2E_PAGE_URL
            )
            client = dependencies.build_browser_client(
                engine,
                page_url=page_url,
                timeout_seconds=dependencies.float_setting(
                    "KORAIL_BROWSER_ACTION_TIMEOUT_SECONDS", 25, minimum=5, maximum=60
                ),
                allow_fullstack_fixture=allow_fullstack_fixture,
            )
            app.state.automation = dependencies.build_automation(engine, browser_client=client)
            app.state.reservation_client = reservation_client or (
                client if callable(getattr(client, "reserve_once", None)) else None
            )
        else:
            app.state.automation = automation
            inferred_client = getattr(automation, "_client", None)
            app.state.reservation_client = reservation_client or (
                inferred_client
                if callable(getattr(inferred_client, "reserve_once", None))
                else None
            )
        app.state.token = token or dependencies.getenv("KORAIL_BROWSER_ADAPTER_TOKEN")
        if app.state.token is None or len(app.state.token.encode("utf-8")) < 32:
            raise RuntimeError("KORAIL_BROWSER_ADAPTER_TOKEN must be at least 32 UTF-8 bytes")
        app.state.readiness = dependencies.readiness_factory(
            readiness_probe or dependencies.readiness_probe_for_engine(engine),
            retry_interval_seconds=readiness_retry_interval_seconds,
            probe_timeout_seconds=readiness_probe_timeout_seconds,
        )
        await app.state.readiness.probe_if_due(force=True)
        try:
            yield
        finally:
            app.state.readiness.ready = False
            pending = tuple(app.state.pending_reservation_tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await app.state.automation.close()

    app = FastAPI(
        title="KORAIL experimental browser adapter",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def redact_credential_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> Response:
        if request.url.path in {
            "/v1/seat-snapshot",
            "/v1/verify-login",
            "/v1/prewarm-login",
            "/v1/reserve-once",
            "/v1/reserve-once/stream",
            "/v1/confirm-reservation",
        }:
            return JSONResponse(
                status_code=422,
                content={"detail": "request_validation_failed"},
                headers=NO_STORE_HEADERS,
            )
        return await request_validation_exception_handler(request, error)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(response: Response) -> dict[str, str]:
        response.headers.update(NO_STORE_HEADERS)
        if not await app.state.readiness.probe_if_due():
            raise HTTPException(503, "not_ready", headers=NO_STORE_HEADERS)
        return {"status": "ready"}

    @app.get(
        "/v1/session-state",
        response_model=KorailSessionStateResult,
        include_in_schema=False,
    )
    async def session_state(
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> KorailSessionStateResult:
        """Expose only process-local, non-secret authentication actor telemetry."""

        response.headers.update(NO_STORE_HEADERS)
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        client = app.state.reservation_client
        snapshot_method = getattr(client, "session_snapshot", None)
        if client is None or not callable(snapshot_method):
            return KorailSessionStateResult(state="cold", locally_reusable=False)
        snapshot = cast(_SessionSnapshot, snapshot_method())
        now = dependencies.monotonic()

        def age(value: float | None) -> float | None:
            return None if value is None else max(0.0, now - value)

        return KorailSessionStateResult(
            state=snapshot.state.value,
            credential_generation=snapshot.credential_generation,
            created_age_seconds=age(snapshot.created_at_monotonic),
            last_verified_age_seconds=age(snapshot.last_verified_at_monotonic),
            last_used_age_seconds=age(snapshot.last_used_at_monotonic),
            local_reuse_remaining_seconds=(
                None
                if snapshot.local_reuse_until_monotonic is None
                else max(0.0, snapshot.local_reuse_until_monotonic - now)
            ),
            locally_reusable=snapshot.locally_reusable,
        )

    @app.post("/v1/seat-snapshot", response_model=BrowserSeatSearchResult)
    async def seat_snapshot(
        request: BrowserSeatSearchRequest,
        response: Response,
        authorization: str | None = Header(default=None),
        rail_request_id: str | None = Header(default=None, alias=REQUEST_ID_HEADER),
        rail_timeout_ms: str | None = Header(
            default=None,
            alias=REQUEST_TIMEOUT_MS_HEADER,
            max_length=6,
        ),
    ) -> BrowserSeatSearchResult:
        response.headers["Cache-Control"] = "no-store"
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        with bind_request_id(validated_log_id(rail_request_id)) as request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
            response_headers = {**NO_STORE_HEADERS, REQUEST_ID_HEADER: request_id}
            if not app.state.readiness.ready:
                raise HTTPException(503, "not_ready", headers=response_headers)
            timeout_ms = None
            if (
                rail_timeout_ms is not None
                and rail_timeout_ms.isascii()
                and rail_timeout_ms.isdigit()
            ):
                parsed_timeout_ms = int(rail_timeout_ms)
                if 1 <= parsed_timeout_ms <= MAX_SEAT_SNAPSHOT_TIMEOUT_MS:
                    timeout_ms = parsed_timeout_ms
            try:
                automation_owner = cast(KorailBrowserAutomation, app.state.automation)
                if timeout_ms is None:
                    return await automation_owner.search(request)
                return await automation_owner.search(request, timeout_seconds=timeout_ms / 1000)
            except BrowserRateLimited as error:
                raise HTTPException(
                    429,
                    {"reason": error.reason},
                    headers=response_headers,
                ) from None
            except BrowserProtectionDetected as error:
                raise HTTPException(
                    423,
                    {"reason": error.reason},
                    headers=response_headers,
                ) from None
            except BrowserProviderUnavailable as error:
                retry_after = error.retry_after_seconds
                headers = dict(response_headers)
                if retry_after is not None:
                    headers["Retry-After"] = str(retry_after)
                raise HTTPException(503, {"reason": error.reason}, headers=headers) from None
            except BrowserSourceUnavailable as error:
                raise HTTPException(
                    504 if error.stage in {"caller_deadline", "search_deadline"} else 503,
                    {"reason": error.reason},
                    headers=response_headers,
                ) from None
            except BrowserAdapterError as error:
                status = 422 if error.reason == "passenger_count_not_supported" else 503
                raise HTTPException(
                    status,
                    {"reason": error.reason},
                    headers=response_headers,
                ) from None

    @app.post(
        "/v1/reserve-once",
        response_model=KorailReserveOnceResult,
        response_model_exclude_none=True,
    )
    async def reserve_once(
        request: KorailReserveOnceRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> KorailReserveOnceResult:
        response.headers["Cache-Control"] = "no-store"
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        if not app.state.readiness.ready:
            raise HTTPException(503, "not_ready", headers=NO_STORE_HEADERS)
        client = app.state.reservation_client
        if client is None:
            raise HTTPException(503, "reservation_not_ready", headers=NO_STORE_HEADERS)

        internal_request = internal_reservation_request(request)
        try:
            result = cast(_ReserveOnceResult, await client.reserve_once(internal_request))
        except Exception:  # noqa: BLE001 -- never serialize backend exceptions containing secrets.
            dependencies.logger.error("KORAIL reserve-once failed with a redacted backend error")
            return failed_reservation_result()
        dependencies.logger.info(
            "KORAIL reserve-once completed outcome=%s reason=%s "
            "seat_clicked=%s reservation_clicked=%s",
            result.outcome.value,
            result.reason,
            str(result.seat_clicked).lower(),
            str(result.reservation_clicked).lower(),
        )
        return public_reservation_result(result)

    @app.post("/v1/reserve-once/stream", response_class=StreamingResponse)
    async def reserve_once_stream(
        request: KorailReserveOnceRequest,
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        if not app.state.readiness.ready:
            raise HTTPException(503, "not_ready", headers=NO_STORE_HEADERS)
        client = app.state.reservation_client
        if client is None:
            raise HTTPException(503, "reservation_not_ready", headers=NO_STORE_HEADERS)

        internal_request = internal_reservation_request(request)
        frames: asyncio.Queue[KorailReserveProgressFrame | KorailReserveResultFrame] = (
            asyncio.Queue()
        )

        def emit_progress(progress: object) -> None:
            typed = cast(_ReservationProgress, progress)
            frames.put_nowait(
                KorailReserveProgressFrame(
                    stage=cast(Any, typed.stage),
                    occurred_at=typed.occurred_at,
                )
            )

        async def run_once() -> None:
            try:
                result = cast(
                    _ReserveOnceResult,
                    await client.reserve_once(internal_request, on_progress=emit_progress),
                )
                dependencies.logger.info(
                    "KORAIL reserve-once stream completed outcome=%s reason=%s "
                    "seat_clicked=%s reservation_clicked=%s",
                    result.outcome.value,
                    result.reason,
                    str(result.seat_clicked).lower(),
                    str(result.reservation_clicked).lower(),
                )
                terminal = public_reservation_result(result)
            except Exception:  # noqa: BLE001 -- redact browser and credential details.
                dependencies.logger.error(
                    "KORAIL reserve-once stream failed with a redacted backend error"
                )
                terminal = failed_reservation_result()
            frames.put_nowait(KorailReserveResultFrame(result=terminal))

        task = asyncio.create_task(run_once())
        app.state.pending_reservation_tasks.add(task)

        def release_task(done: asyncio.Task[None]) -> None:
            app.state.pending_reservation_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(release_task)

        async def stream_frames() -> AsyncIterator[bytes]:
            while True:
                frame = await frames.get()
                yield (frame.model_dump_json(exclude_none=True) + "\n").encode()
                if isinstance(frame, KorailReserveResultFrame):
                    return

        return StreamingResponse(
            stream_frames(),
            media_type="application/x-ndjson",
            headers=NO_STORE_HEADERS,
        )

    @app.post(
        "/v1/confirm-reservation",
        response_model=KorailReservationConfirmationResult,
    )
    async def confirm_reservation(
        request: KorailReservationConfirmationRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> KorailReservationConfirmationResult:
        response.headers.update(NO_STORE_HEADERS)
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        if not app.state.readiness.ready:
            raise HTTPException(503, "not_ready", headers=NO_STORE_HEADERS)
        client = app.state.reservation_client
        read_detail = getattr(client, "read_reservation_detail", None)
        if client is None or not callable(read_detail):
            raise HTTPException(503, "confirmation_not_ready", headers=NO_STORE_HEADERS)

        target = ReservationConfirmationTarget(
            attempt_id=request.attempt_id,
            candidate_id=request.candidate_id,
            provider=Provider.KORAIL,
            train_number=request.train_number,
            origin=request.origin,
            destination=request.destination,
            departure_at=request.departure_at,
            arrival_at=request.arrival_at,
            seat_class=SeatClass(request.seat_class),
            passenger_count=request.passenger_count,
            credential_version=request.credential_version,
            purpose=ReservationConfirmationPurpose(request.purpose),
            reserved_seats=tuple(
                ReservationConfirmationSeat(
                    car_number=seat.car_number,
                    seat_number=seat.seat_number,
                )
                for seat in request.reserved_seats
            ),
        )
        try:
            confirmation = normalize_korail_same_session_detail(
                target,
                await read_detail(target),
            )
        except Exception:  # noqa: BLE001 -- provider exception text may contain secrets.
            dependencies.logger.error(
                "KORAIL reservation confirmation failed with a redacted error"
            )
            confirmation = ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.INCONCLUSIVE,
                source=KORAIL_CONFIRMATION_SOURCE,
                observed_at=datetime.now(UTC),
            )
        dependencies.logger.info(
            "KORAIL reservation confirmation completed purpose=%s outcome=%s source=%s",
            target.purpose.value,
            confirmation.outcome.value,
            confirmation.source,
        )
        return KorailReservationConfirmationResult(
            outcome=confirmation.outcome.value,
            source=cast(
                Literal[
                    "korail-same-session-detail",
                    "korail-reservation-list",
                    "korail-issued-ticket-list",
                ],
                confirmation.source,
            ),
            observed_at=confirmation.observed_at,
            payment_deadline=confirmation.payment_deadline,
            official_handoff_url=confirmation.official_handoff_url,
        )

    @app.post("/v1/verify-login", response_model=KorailLoginVerifyResult)
    async def verify_login(
        request: KorailLoginVerifyRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> KorailLoginVerifyResult:
        response.headers["Cache-Control"] = "no-store"
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        if not app.state.readiness.ready:
            raise HTTPException(503, "not_ready", headers=NO_STORE_HEADERS)
        client = app.state.reservation_client
        prewarm = getattr(client, "prewarm_credentials", None)
        verify = getattr(client, "verify_credentials", None)
        if client is None or (not callable(prewarm) and not callable(verify)):
            raise HTTPException(503, "login_verification_not_ready", headers=NO_STORE_HEADERS)

        from .pydoll.auth_contracts import KorailCredentialInput, KorailLoginMethod

        credential = KorailCredentialInput(
            login_id=request.credential.login_id.get_secret_value(),
            password=request.credential.password.get_secret_value(),
            version=request.credential.version,
            login_method=KorailLoginMethod(request.credential.login_method),
        )
        verify_credential = cast(Callable[[object], Awaitable[bool]], verify or prewarm)
        try:
            authenticated = await verify_credential(credential)
        except (BrowserRateLimited, BrowserProtectionDetected):
            return KorailLoginVerifyResult(outcome="provider_blocked")
        except BrowserSourceUnavailable as error:
            # ``stage`` is a closed, code-owned diagnostic label. Keep credentials and
            # third-party exception text out of logs while retaining an operable signal.
            dependencies.logger.warning(
                "KORAIL login verification unavailable at stage=%s",
                error.stage,
            )
            return KorailLoginVerifyResult(outcome="failed")
        except Exception:  # noqa: BLE001 -- external exception text may contain secrets.
            dependencies.logger.error(
                "KORAIL login verification failed with a redacted backend error"
            )
            return KorailLoginVerifyResult(outcome="failed")
        outcome: KorailLoginVerificationOutcomeValue = (
            "authenticated" if authenticated else "auth_required"
        )
        dependencies.logger.info("KORAIL login verification completed outcome=%s", outcome)
        return KorailLoginVerifyResult(outcome=outcome)

    @app.post("/v1/prewarm-login", response_model=KorailLoginVerifyResult)
    async def prewarm_login(
        request: KorailLoginVerifyRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> KorailLoginVerifyResult:
        response.headers.update(NO_STORE_HEADERS)
        expected = f"Bearer {app.state.token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "unauthorized", headers=NO_STORE_HEADERS)
        if not app.state.readiness.ready:
            raise HTTPException(503, "not_ready", headers=NO_STORE_HEADERS)
        client = app.state.reservation_client
        prewarm = getattr(client, "prewarm_credentials", None)
        if client is None or not callable(prewarm):
            raise HTTPException(503, "login_prewarm_not_ready", headers=NO_STORE_HEADERS)

        from .pydoll.auth_contracts import KorailCredentialInput, KorailLoginMethod

        credential = KorailCredentialInput(
            login_id=request.credential.login_id.get_secret_value(),
            password=request.credential.password.get_secret_value(),
            version=request.credential.version,
            login_method=KorailLoginMethod(request.credential.login_method),
        )
        try:
            authenticated = await prewarm(credential)
        except (BrowserRateLimited, BrowserProtectionDetected):
            return KorailLoginVerifyResult(outcome="provider_blocked")
        except BrowserSourceUnavailable as error:
            dependencies.logger.warning("KORAIL login prewarm unavailable at stage=%s", error.stage)
            return KorailLoginVerifyResult(outcome="failed")
        except Exception:  # noqa: BLE001 -- external exception text may contain secrets.
            dependencies.logger.error("KORAIL login prewarm failed with a redacted backend error")
            return KorailLoginVerifyResult(outcome="failed")
        outcome: KorailLoginVerificationOutcomeValue = (
            "authenticated" if authenticated else "auth_required"
        )
        dependencies.logger.info("KORAIL login prewarm completed outcome=%s", outcome)
        return KorailLoginVerifyResult(outcome=outcome)

    return app
