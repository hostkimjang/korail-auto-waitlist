from __future__ import annotations

from datetime import datetime
from typing import Literal as _Literal
from typing import TypeVar as _TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel as _BaseModel
from pydantic import ValidationError
from SRT import SRTError  # type: ignore[import-untyped]
from SRT.errors import SRTNetFunnelError  # type: ignore[import-untyped]

from ..observations.contracts import SeatObservationRequest, SeatObservationResult
from ..provider_account_management.contracts import ProviderCredentials
from ..reservations.contracts import ReservationRequest, ReservationResult
from ..reservations.provider_confirmation.contracts import (
    ReservationConfirmationResult,
    ReservationConfirmationTarget,
)
from ..timetable_management.schemas import TimetableItem
from .contracts import (
    SrtConfirmReservationRequest,
    SrtConfirmReservationResult,
    SrtCredentialRequest,
    SrtLoginRequest,
    SrtLoginResult,
    SrtObserveRequest,
    SrtObserveResult,
    SrtReservationConfirmationTarget,
    SrtReserveOnceRequest,
    SrtReserveOnceResult,
    SrtSessionStatus,
    SrtTimetableOverlayRequest,
    SrtTimetableOverlayResult,
    SrtTimetableSearchRequest,
    SrtTimetableSearchResult,
    SrtTimetableTrain,
)

SRT_PROVIDER_ADAPTER_ORIGIN = "http://srt-provider-adapter:8002"

_ResponseModelT = _TypeVar("_ResponseModelT", bound=_BaseModel)
_LoginOperation = _Literal["verify", "prewarm"]
_HttpMethod = _Literal["GET", "POST"]


class SrtProviderAdapterUnavailable(RuntimeError):
    pass


def validate_srt_provider_adapter_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if not (
        parsed.scheme == "http"
        and parsed.hostname == "srt-provider-adapter"
        and parsed.port == 8002
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    ):
        raise ValueError("SRT provider adapter URL must be the exact internal sidecar origin")
    return SRT_PROVIDER_ADAPTER_ORIGIN


class SrtProviderAdapterClient:
    """Strict process-local transport for the long-lived SRT provider actor."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        token: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = validate_srt_provider_adapter_url(base_url)
        if token is None or len(token.encode("utf-8")) < 32:
            raise ValueError("SRT provider adapter token must be at least 32 UTF-8 bytes")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            transport=transport,
        )

    async def session_status(self) -> SrtSessionStatus:
        return await self._request("GET", "/v1/session-status", None, SrtSessionStatus)

    async def observation_deferred_until(self) -> datetime | None:
        return (await self.session_status()).observation_deferred_until

    async def observe(
        self,
        request: SeatObservationRequest,
        *,
        origin: str,
        destination: str,
    ) -> list[SeatObservationResult]:
        payload = SrtObserveRequest(
            request=request,
            origin=origin,
            destination=destination,
        )
        result = await self._request(
            "POST",
            "/v1/observe",
            payload.model_dump(mode="json"),
            SrtObserveResult,
        )
        return result.observations

    async def overlay(
        self,
        items: list[TimetableItem],
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[TimetableItem]:
        payload = SrtTimetableOverlayRequest(
            items=items,
            origin=origin,
            destination=destination,
            departure_from=departure_from,
            departure_to=departure_to,
            passenger_count=passenger_count,
        )
        result = await self._request(
            "POST",
            "/v1/timetable-overlay",
            payload.model_dump(mode="json"),
            SrtTimetableOverlayResult,
        )
        return result.items

    async def search_timetable(
        self,
        *,
        origin: str,
        destination: str,
        departure_from: datetime,
        departure_to: datetime,
        passenger_count: int,
    ) -> list[SrtTimetableTrain]:
        payload = SrtTimetableSearchRequest.model_validate(
            {
                "origin": origin,
                "destination": destination,
                "departure_from": departure_from,
                "departure_to": departure_to,
                "passenger_count": passenger_count,
            }
        )
        result = await self._request(
            "POST",
            "/v1/timetable-search",
            payload.model_dump(mode="json"),
            SrtTimetableSearchResult,
        )
        return result.trains

    async def verify_credentials(self, credentials: ProviderCredentials) -> bool:
        return await self._login(credentials, operation="verify")

    async def prewarm_credentials(self, credentials: ProviderCredentials) -> bool:
        return await self._login(credentials, operation="prewarm")

    async def _login(
        self,
        credentials: ProviderCredentials,
        *,
        operation: _LoginOperation,
    ) -> bool:
        credential = SrtCredentialRequest.from_credentials(credentials)
        request = SrtLoginRequest.model_validate({"operation": operation, "credential": credential})
        payload = {
            "operation": request.operation,
            "credential": credential.wire_payload(),
        }
        result = await self._request(
            "POST",
            "/v1/prewarm-or-verify-login",
            payload,
            SrtLoginResult,
        )
        if result.outcome == "authenticated":
            return True
        if result.outcome == "auth_required":
            return False
        if result.outcome == "invalid_identifier":
            raise ValueError("invalid SRT login identifier")
        if result.outcome == "provider_blocked":
            raise SRTNetFunnelError("SRT provider access is restricted")
        raise SRTError("SRT provider login verification failed")

    async def reserve_once(
        self,
        request: ReservationRequest,
        credentials: ProviderCredentials,
    ) -> ReservationResult:
        credential = SrtCredentialRequest.from_credentials(credentials)
        validated = SrtReserveOnceRequest(
            request=request,
            credential=credential,
        )
        payload = {
            "request": validated.request.model_dump(mode="json"),
            "credential": credential.wire_payload(),
        }
        result = await self._request(
            "POST",
            "/v1/reserve-once",
            payload,
            SrtReserveOnceResult,
        )
        return result.result

    async def confirm_reservation(
        self,
        target: ReservationConfirmationTarget,
        credentials: ProviderCredentials,
    ) -> ReservationConfirmationResult:
        credential = SrtCredentialRequest.from_credentials(credentials)
        validated = SrtConfirmReservationRequest(
            target=SrtReservationConfirmationTarget.from_domain(target),
            credential=credential,
        )
        payload = {
            "target": validated.target.model_dump(mode="json"),
            "credential": credential.wire_payload(),
        }
        result = await self._request(
            "POST",
            "/v1/confirm-reservation",
            payload,
            SrtConfirmReservationResult,
        )
        return result.result.to_domain()

    async def drain_pending_calls(self) -> None:
        # The sidecar owns and drains synchronous SRTrain calls. Closing a task-local
        # HTTP client must not imply that the provider actor was cancelled.
        return

    async def close(self) -> None:
        await self._client.aclose()

    async def aclose(self) -> None:
        await self.close()

    async def _request(
        self,
        method: _HttpMethod,
        path: str,
        payload: object | None,
        response_model: type[_ResponseModelT],
    ) -> _ResponseModelT:
        try:
            response = await self._client.request(method, path, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise SrtProviderAdapterUnavailable("SRT provider adapter is unavailable") from error
        if response.status_code != 200:
            raise SrtProviderAdapterUnavailable(
                f"SRT provider adapter returned HTTP {response.status_code}"
            )
        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter returned an invalid response"
            ) from error
