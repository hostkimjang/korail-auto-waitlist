from __future__ import annotations

import asyncio
import logging
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
from ..provider_call_context import (
    REQUEST_ID_HEADER,
    current_request_id,
    new_log_id,
    validated_log_id,
)
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
    SrtReadOnlyCallRegistrationRequest,
    SrtReadOnlyCallRegistrationResult,
    SrtReadOnlyCallStatus,
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
from .read_only_lifecycle import READ_ONLY_CALL_ID_HEADER as _READ_ONLY_CALL_ID_HEADER

SRT_PROVIDER_ADAPTER_ORIGIN = "http://srt-provider-adapter:8002"

_ResponseModelT = _TypeVar("_ResponseModelT", bound=_BaseModel)
_LoginOperation = _Literal["verify", "prewarm"]
_HttpMethod = _Literal["GET", "POST"]
_LOGGER = logging.getLogger(__name__)
_OPERATION_BY_PATH = {
    "/v1/session-status": "session_status",
    "/v1/observe": "observe",
    "/v1/timetable-overlay": "timetable_overlay",
    "/v1/timetable-search": "timetable_search",
    "/v1/prewarm-or-verify-login": "login",
    "/v1/reserve-once": "reserve_once",
    "/v1/confirm-reservation": "confirm_reservation",
}
_TRACKED_READ_ONLY_PATHS = frozenset(
    {"/v1/observe", "/v1/timetable-overlay", "/v1/timetable-search"}
)
_READ_ONLY_STATUS_POLL_SECONDS = 0.05


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
        self._pending_read_only_calls: dict[str, str | None] = {}
        self._read_only_cleanup_task: asyncio.Task[None] | None = None

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
        cleanup_task = self._ensure_read_only_cleanup_task()
        pending_cancellation: asyncio.CancelledError | None = None
        while cleanup_task is not None and not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as error:
                # group_runtime releases its lease as soon as this method exits. Preserve
                # cancellation until every sidecar-owned provider call is terminal.
                if pending_cancellation is None:
                    pending_cancellation = error
        if cleanup_task is not None:
            cleanup_task.result()
        if pending_cancellation is not None:
            raise pending_cancellation

    async def close(self) -> None:
        try:
            await self.drain_pending_calls()
        finally:
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
        request_id = current_request_id() or new_log_id()
        operation = _OPERATION_BY_PATH[path]
        request_headers = {REQUEST_ID_HEADER: request_id}
        if path in _TRACKED_READ_ONLY_PATHS:
            call_id = new_log_id()
            self._pending_read_only_calls[call_id] = None
            try:
                instance_id = await self._register_read_only_call(call_id, request_id)
            except asyncio.CancelledError:
                self._pending_read_only_calls.pop(call_id, None)
                raise
            except Exception:
                self._pending_read_only_calls.pop(call_id, None)
                raise
            self._pending_read_only_calls[call_id] = instance_id
            self._ensure_read_only_cleanup_task()
            request_headers[_READ_ONLY_CALL_ID_HEADER] = call_id
        _LOGGER.info(
            "Provider sidecar request started event=provider_sidecar_request_started "
            "provider=SRT operation=%s request_id=%s",
            operation,
            request_id,
        )
        try:
            response = await self._client.request(
                method,
                path,
                json=payload,
                headers=request_headers,
            )
        except httpx.TimeoutException as error:
            _LOGGER.warning(
                "Provider sidecar request failed event=provider_sidecar_request_failed "
                "provider=SRT operation=%s "
                "request_id=%s outcome=timeout",
                operation,
                request_id,
            )
            raise SrtProviderAdapterUnavailable("SRT provider adapter is unavailable") from error
        except httpx.TransportError as error:
            _LOGGER.warning(
                "Provider sidecar request failed event=provider_sidecar_request_failed "
                "provider=SRT operation=%s "
                "request_id=%s outcome=transport_error",
                operation,
                request_id,
            )
            raise SrtProviderAdapterUnavailable("SRT provider adapter is unavailable") from error
        if response.status_code != 200:
            _LOGGER.warning(
                "Provider sidecar request failed event=provider_sidecar_request_failed "
                "provider=SRT operation=%s "
                "request_id=%s outcome=http_status status_code=%s",
                operation,
                request_id,
                response.status_code,
            )
            raise SrtProviderAdapterUnavailable(
                f"SRT provider adapter returned HTTP {response.status_code}"
            )
        try:
            result = response_model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            _LOGGER.warning(
                "Provider sidecar request failed event=provider_sidecar_request_failed "
                "provider=SRT operation=%s "
                "request_id=%s outcome=validation_error",
                operation,
                request_id,
            )
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter returned an invalid response"
            ) from error
        if isinstance(result, SrtConfirmReservationResult):
            confirmation = result.result
            _LOGGER.info(
                "Provider sidecar request completed event=provider_sidecar_request_completed "
                "provider=SRT operation=%s request_id=%s outcome=success "
                "terminal_outcome=%s diagnostic_code=%s source=%s phase=completed",
                operation,
                request_id,
                confirmation.outcome.value,
                (
                    confirmation.diagnostic_code.value
                    if confirmation.diagnostic_code is not None
                    else "none"
                ),
                confirmation.source,
            )
        else:
            _LOGGER.info(
                "Provider sidecar request completed event=provider_sidecar_request_completed "
                "provider=SRT operation=%s "
                "request_id=%s outcome=success",
                operation,
                request_id,
            )
        return result

    async def _register_read_only_call(self, call_id: str, request_id: str) -> str:
        data = SrtReadOnlyCallRegistrationRequest(call_id=call_id, request_id=request_id)
        result = await self._read_only_lifecycle_request(
            "POST",
            "/v1/read-only-call-register",
            payload=data.model_dump(mode="json"),
            params=None,
            response_model=SrtReadOnlyCallRegistrationResult,
        )
        if not result.accepted or validated_log_id(result.instance_id) is None:
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter rejected read-only call registration"
            )
        return result.instance_id

    async def _read_only_call_status(self, call_id: str) -> SrtReadOnlyCallStatus:
        result = await self._read_only_lifecycle_request(
            "GET",
            "/v1/read-only-call-status",
            payload=None,
            params={"call_id": call_id},
            response_model=SrtReadOnlyCallStatus,
        )
        if validated_log_id(result.instance_id) is None:
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter returned an invalid lifecycle response"
            )
        return result

    async def _read_only_lifecycle_request(
        self,
        method: _HttpMethod,
        path: str,
        *,
        payload: object | None,
        params: dict[str, str] | None,
        response_model: type[_ResponseModelT],
    ) -> _ResponseModelT:
        try:
            response = await self._client.request(
                method,
                path,
                json=payload,
                params=params,
                headers={REQUEST_ID_HEADER: new_log_id()},
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter lifecycle status is unavailable"
            ) from error
        if response.status_code != 200:
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter lifecycle status is unavailable"
            )
        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise SrtProviderAdapterUnavailable(
                "SRT provider adapter returned an invalid lifecycle response"
            ) from error

    def _ensure_read_only_cleanup_task(self) -> asyncio.Task[None] | None:
        if not self._pending_read_only_calls:
            return None
        task = self._read_only_cleanup_task
        if task is None or task.done():
            task = asyncio.create_task(self._poll_read_only_calls_until_terminal())
            self._read_only_cleanup_task = task
        return task

    async def _poll_read_only_calls_until_terminal(self) -> None:
        reported_failures: set[str] = set()
        while self._pending_read_only_calls:
            for call_id, registered_instance_id in tuple(self._pending_read_only_calls.items()):
                if registered_instance_id is None:
                    continue
                try:
                    status = await self._read_only_call_status(call_id)
                except Exception:  # noqa: BLE001 - every unverified state must fail closed.
                    if call_id not in reported_failures:
                        reported_failures.add(call_id)
                        _LOGGER.warning(
                            "Provider sidecar drain status unavailable "
                            "event=provider_sidecar_drain_status_unavailable provider=SRT "
                            "read_only_call_id=%s",
                            call_id,
                        )
                    continue
                reported_failures.discard(call_id)
                if status.instance_id != registered_instance_id or status.state == "terminal":
                    self._pending_read_only_calls.pop(call_id, None)
            if self._pending_read_only_calls:
                await asyncio.sleep(_READ_ONLY_STATUS_POLL_SECONDS)
