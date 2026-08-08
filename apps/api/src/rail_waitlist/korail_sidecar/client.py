from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from ..timetable_management.schemas import SeatAvailabilityNotObservedReason
from .browser_contracts import BrowserSeatSearchRequest, BrowserSeatSearchResult
from .contracts import (
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
    KorailReservationConfirmationRequest,
    KorailReservationConfirmationResult,
    KorailReserveOnceRequest,
    KorailReserveOnceResult,
    KorailSessionStateResult,
)


class BrowserAdapterTransport(Protocol):
    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult: ...

    async def reserve(self, request: KorailReserveOnceRequest) -> KorailReserveOnceResult: ...

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
    ) -> None:
        self.reason = reason
        self.rate_limited = rate_limited
        self.protection = protection
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
