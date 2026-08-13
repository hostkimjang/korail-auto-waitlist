from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from ..reservations.contracts import ReservationProgressStage
from ..timetable_management.schemas import SeatAvailabilityNotObservedReason
from .browser_contracts import BrowserSeatSearchRequest, BrowserSeatSearchResult
from .contracts import (
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
    KorailReservationConfirmationRequest,
    KorailReservationConfirmationResult,
    KorailReserveOnceRequest,
    KorailReserveOnceResult,
    KorailReserveProgressFrame,
    KorailReserveResultFrame,
    KorailSessionStateResult,
)

ReservationProgressCallback = Callable[[ReservationProgressStage], Awaitable[None]]
FailureCooldownScope = Literal["query", "provider"]


class BrowserAdapterTransport(Protocol):
    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult: ...

    async def reserve(self, request: KorailReserveOnceRequest) -> KorailReserveOnceResult: ...

    async def reserve_with_progress(
        self,
        request: KorailReserveOnceRequest,
        on_progress: ReservationProgressCallback,
    ) -> KorailReserveOnceResult: ...

    async def verify_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult: ...

    async def prewarm_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult: ...

    async def session_state(self) -> KorailSessionStateResult: ...

    async def confirm_reservation(
        self,
        request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult: ...

    async def close(self) -> None: ...


class _AdapterFailure(RuntimeError):
    def __init__(
        self,
        reason: SeatAvailabilityNotObservedReason,
        *,
        rate_limited: bool = False,
        protection: bool = False,
        cooldown_scope: FailureCooldownScope = "query",
        retry_after_seconds: int | None = None,
        reservation_command_uncertain: bool = False,
        progress_stages: tuple[ReservationProgressStage, ...] = (),
    ) -> None:
        self.reason = reason
        self.rate_limited = rate_limited
        self.protection = protection
        self.cooldown_scope = cooldown_scope
        self.retry_after_seconds = retry_after_seconds
        self.reservation_command_uncertain = reservation_command_uncertain
        self.progress_stages = progress_stages
        super().__init__(reason)


class HttpBrowserAdapterTransport:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        token: str | None,
        *,
        allow_fullstack_test_url: bool = False,
    ) -> None:
        parsed = urlsplit(base_url)
        is_production_sidecar = (
            parsed.scheme == "http"
            and parsed.hostname == "korail-browser-adapter"
            and parsed.port == 8001
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        is_fullstack_test_fixture = (
            allow_fullstack_test_url
            and parsed.scheme == "http"
            and parsed.hostname == "e2e-fake-upstream"
            and parsed.port == 8001
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
        if not (is_production_sidecar or is_fullstack_test_fixture):
            raise ValueError("browser adapter URL must be the exact internal sidecar origin")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        )

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        try:
            response = await self._client.post(
                "/v1/seat-snapshot",
                json=request.model_dump(mode="json"),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        provider_retry_after: int | None = None
        raw_retry_after = (
            response.headers.get("retry-after") if response.status_code == 503 else None
        )
        if raw_retry_after is not None and raw_retry_after.isascii() and raw_retry_after.isdigit():
            candidate = int(raw_retry_after)
            if 1 <= candidate <= 86400:
                try:
                    provider_payload = response.json()
                except ValueError:
                    provider_payload = None
                if provider_payload == {"detail": {"reason": "source_unavailable"}}:
                    provider_retry_after = candidate
        if provider_retry_after is not None:
            raise _AdapterFailure(
                "source_unavailable",
                cooldown_scope="provider",
                retry_after_seconds=provider_retry_after,
            )
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return BrowserSeatSearchResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def reserve(self, request: KorailReserveOnceRequest) -> KorailReserveOnceResult:
        payload = request.model_dump(mode="json", exclude={"credential"})
        payload["credential"] = {
            "login_method": request.credential.login_method,
            "login_id": request.credential.login_id.get_secret_value(),
            "password": request.credential.password.get_secret_value(),
            "version": request.credential.version,
        }
        try:
            response = await self._client.post("/v1/reserve-once", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailReserveOnceResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def reserve_with_progress(
        self,
        request: KorailReserveOnceRequest,
        on_progress: ReservationProgressCallback,
    ) -> KorailReserveOnceResult:
        payload = request.model_dump(mode="json", exclude={"credential"})
        payload["credential"] = {
            "login_method": request.credential.login_method,
            "login_id": request.credential.login_id.get_secret_value(),
            "password": request.credential.password.get_secret_value(),
            "version": request.credential.version,
        }
        expected_stages = (
            "authenticated_session_ready",
            "target_rechecked",
            "seat_selected",
            "reservation_requested",
        )
        progress: list[ReservationProgressStage] = []
        terminal: KorailReserveOnceResult | None = None
        try:
            async with self._client.stream(
                "POST",
                "/v1/reserve-once/stream",
                json=payload,
            ) as response:
                if response.status_code == 429:
                    raise _AdapterFailure("provider_access_restricted", rate_limited=True)
                if response.status_code in {403, 423}:
                    raise _AdapterFailure("provider_access_restricted", protection=True)
                if response.status_code != 200:
                    raise _AdapterFailure("source_unavailable")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if terminal is not None:
                        raise ValueError("frame received after terminal result")
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise ValueError("stream frame must be an object")
                    if raw.get("type") == "progress":
                        frame = KorailReserveProgressFrame.model_validate(raw)
                        if len(progress) >= len(expected_stages):
                            raise ValueError("too many reservation progress frames")
                        if frame.stage != expected_stages[len(progress)]:
                            raise ValueError("reservation progress frames are out of order")
                        stage = ReservationProgressStage(
                            stage=frame.stage,
                            occurred_at=frame.occurred_at,
                        )
                        if progress and stage.occurred_at < progress[-1].occurred_at:
                            raise ValueError("reservation progress times are out of order")
                        progress.append(stage)
                        await on_progress(stage)
                    elif raw.get("type") == "result":
                        terminal = KorailReserveResultFrame.model_validate(raw).result
                    else:
                        raise ValueError("unknown reservation stream frame")
        except _AdapterFailure:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure(
                "source_unavailable",
                reservation_command_uncertain=True,
                progress_stages=tuple(progress),
            ) from error
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as error:
            raise _AdapterFailure(
                "source_unavailable",
                reservation_command_uncertain=True,
                progress_stages=tuple(progress),
            ) from error
        if terminal is None:
            raise _AdapterFailure(
                "source_unavailable",
                reservation_command_uncertain=True,
                progress_stages=tuple(progress),
            )
        evidence = (
            terminal.session_ready_at,
            terminal.target_rechecked_at,
            terminal.seat_selected_at,
            terminal.reservation_requested_at,
        )
        expected_progress = [
            (stage, occurred_at)
            for stage, occurred_at in zip(expected_stages, evidence, strict=True)
            if occurred_at is not None
        ]
        actual_progress = [(item.stage, item.occurred_at) for item in progress]
        if actual_progress != expected_progress:
            raise _AdapterFailure(
                "source_unavailable",
                reservation_command_uncertain=True,
                progress_stages=tuple(progress),
            )
        return terminal

    async def verify_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        return await self._login_request("/v1/verify-login", request)

    async def prewarm_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        return await self._login_request("/v1/prewarm-login", request)

    async def _login_request(
        self,
        path: str,
        request: KorailLoginVerifyRequest,
    ) -> KorailLoginVerifyResult:
        payload = {
            "credential": {
                "login_method": request.credential.login_method,
                "login_id": request.credential.login_id.get_secret_value(),
                "password": request.credential.password.get_secret_value(),
                "version": request.credential.version,
            }
        }
        try:
            response = await self._client.post(path, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailLoginVerifyResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def session_state(self) -> KorailSessionStateResult:
        try:
            response = await self._client.get("/v1/session-state")
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailSessionStateResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def confirm_reservation(
        self,
        request: KorailReservationConfirmationRequest,
    ) -> KorailReservationConfirmationResult:
        try:
            response = await self._client.post(
                "/v1/confirm-reservation",
                json=request.model_dump(mode="json"),
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise _AdapterFailure("source_unavailable") from error
        if response.status_code == 429:
            raise _AdapterFailure("provider_access_restricted", rate_limited=True)
        if response.status_code in {403, 423}:
            raise _AdapterFailure("provider_access_restricted", protection=True)
        if response.status_code != 200:
            raise _AdapterFailure("source_unavailable")
        try:
            return KorailReservationConfirmationResult.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise _AdapterFailure("source_unavailable") from error

    async def close(self) -> None:
        await self._client.aclose()
