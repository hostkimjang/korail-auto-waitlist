from __future__ import annotations

import time
import typing as _typing
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from requests import RequestException
from SRT import SRTError, SRTLoginError, SRTResponseError  # type: ignore[import-untyped]
from SRT.errors import SRTNetFunnelError  # type: ignore[import-untyped]

from ..domain import Provider
from ..korail_sidecar.contracts import KorailSessionStateResult
from ..srt_sidecar.contracts import SrtSessionStatus as _SrtSessionStatus
from ..srt_sidecar.reservation import default_srt_reservation_executor
from ..srt_sidecar.session_contract import (
    SrtSessionActorSnapshot as _SrtSessionActorSnapshot,
)
from .contracts import ProviderCredentials


class ProviderLoginVerificationOutcome(StrEnum):
    AUTHENTICATED = "authenticated"
    INVALID_IDENTIFIER = "invalid_identifier"
    AUTH_REQUIRED = "auth_required"
    PROVIDER_BLOCKED = "provider_blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class ProviderLoginVerification:
    outcome: ProviderLoginVerificationOutcome

    @property
    def authenticated(self) -> bool:
        return self.outcome is ProviderLoginVerificationOutcome.AUTHENTICATED


class ProviderSessionRuntimeState(StrEnum):
    COLD = "cold"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    STALE = "stale"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ProviderSessionRuntimeSnapshot:
    provider: Provider
    state: ProviderSessionRuntimeState
    credential_generation: str | None
    created_age_seconds: float | None
    last_verified_age_seconds: float | None
    last_used_age_seconds: float | None
    local_reuse_remaining_seconds: float | None
    locally_reusable: bool


class KorailLoginVerifier(Protocol):
    async def verify_login(self, credentials: ProviderCredentials) -> ProviderLoginVerification: ...

    async def prewarm_login(
        self, credentials: ProviderCredentials
    ) -> ProviderLoginVerification: ...

    async def session_state(self) -> KorailSessionStateResult: ...


class SrtLoginVerifier(Protocol):
    async def verify_credentials(self, credentials: ProviderCredentials) -> bool: ...

    async def prewarm_credentials(self, credentials: ProviderCredentials) -> bool: ...


class ProviderLoginVerifier:
    """Verify one provider login without timetable search or reservation side effects."""

    def __init__(
        self,
        korail: KorailLoginVerifier,
        srt: SrtLoginVerifier | None = None,
    ) -> None:
        self._korail = korail
        self._srt = srt or _typing.cast(SrtLoginVerifier, default_srt_reservation_executor())

    async def verify(
        self,
        provider: Provider,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        if provider is Provider.KORAIL:
            return await self._korail.verify_login(credentials)
        if provider is Provider.SRT:
            return await self._verify_srt(credentials, prewarm=False)
        return ProviderLoginVerification(ProviderLoginVerificationOutcome.FAILED)

    async def prewarm(
        self,
        provider: Provider,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        """Prepare a provider session without timetable or reservation side effects."""

        if provider is Provider.KORAIL:
            return await self._korail.prewarm_login(credentials)
        if provider is Provider.SRT:
            return await self._verify_srt(credentials, prewarm=True)
        return ProviderLoginVerification(ProviderLoginVerificationOutcome.FAILED)

    async def _verify_srt(
        self,
        credentials: ProviderCredentials,
        *,
        prewarm: bool,
    ) -> ProviderLoginVerification:
        try:
            method = self._srt.prewarm_credentials if prewarm else self._srt.verify_credentials
            authenticated = await method(credentials)
        except ValueError:
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.INVALID_IDENTIFIER)
        except SRTLoginError:
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.AUTH_REQUIRED)
        except SRTNetFunnelError:
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.PROVIDER_BLOCKED)
        except (RequestException, SRTResponseError, SRTError):
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.FAILED)
        except Exception:  # noqa: BLE001
            # SRTrain response-shape changes can surface as ordinary Python exceptions.
            # Keep the credential boundary fail-closed and return only a sanitized outcome.
            return ProviderLoginVerification(ProviderLoginVerificationOutcome.FAILED)
        return ProviderLoginVerification(
            ProviderLoginVerificationOutcome.AUTHENTICATED
            if authenticated
            else ProviderLoginVerificationOutcome.AUTH_REQUIRED
        )

    async def session_snapshot(
        self,
        provider: Provider,
    ) -> ProviderSessionRuntimeSnapshot:
        """Return provider-process telemetry without credential-derived material."""

        if provider is Provider.KORAIL:
            snapshot = await self._korail.session_state()
            return ProviderSessionRuntimeSnapshot(
                provider=provider,
                state=ProviderSessionRuntimeState(snapshot.state),
                credential_generation=snapshot.credential_generation,
                created_age_seconds=snapshot.created_age_seconds,
                last_verified_age_seconds=snapshot.last_verified_age_seconds,
                last_used_age_seconds=snapshot.last_used_age_seconds,
                local_reuse_remaining_seconds=snapshot.local_reuse_remaining_seconds,
                locally_reusable=snapshot.locally_reusable,
            )
        if provider is Provider.SRT:
            remote_status = getattr(self._srt, "session_status", None)
            if callable(remote_status):
                status_reader = _typing.cast(
                    _typing.Callable[[], _typing.Awaitable[_SrtSessionStatus]],
                    remote_status,
                )
                raw = await status_reader()
                return ProviderSessionRuntimeSnapshot(
                    provider=provider,
                    state=ProviderSessionRuntimeState(raw.state.value),
                    credential_generation=(
                        None
                        if raw.credential_generation is None
                        else str(raw.credential_generation)
                    ),
                    created_age_seconds=_typing.cast(
                        float | None,
                        getattr(raw, "created_age_seconds", None),
                    ),
                    last_verified_age_seconds=_typing.cast(
                        float | None,
                        getattr(raw, "last_verified_age_seconds", None),
                    ),
                    last_used_age_seconds=_typing.cast(
                        float | None,
                        getattr(raw, "last_used_age_seconds", None),
                    ),
                    local_reuse_remaining_seconds=_typing.cast(
                        float | None,
                        getattr(raw, "local_reuse_remaining_seconds", None),
                    ),
                    locally_reusable=raw.locally_reusable,
                )
            snapshot_reader = _typing.cast(
                _typing.Callable[[], _SrtSessionActorSnapshot],
                getattr(self._srt, "session_snapshot"),
            )
            raw_snapshot = snapshot_reader()
            now = time.monotonic()

            def age(value: float | None) -> float | None:
                return None if value is None else max(0.0, now - value)

            return ProviderSessionRuntimeSnapshot(
                provider=provider,
                state=ProviderSessionRuntimeState(raw_snapshot.state.value),
                credential_generation=(
                    None
                    if raw_snapshot.credential_generation is None
                    else str(raw_snapshot.credential_generation)
                ),
                created_age_seconds=age(raw_snapshot.created_at_monotonic),
                last_verified_age_seconds=age(raw_snapshot.last_verified_at_monotonic),
                last_used_age_seconds=age(raw_snapshot.last_used_at_monotonic),
                local_reuse_remaining_seconds=(
                    None
                    if raw_snapshot.local_reuse_until_monotonic is None
                    else max(0.0, raw_snapshot.local_reuse_until_monotonic - now)
                ),
                locally_reusable=raw_snapshot.locally_reusable,
            )
        return ProviderSessionRuntimeSnapshot(
            provider=provider,
            state=ProviderSessionRuntimeState.COLD,
            credential_generation=None,
            created_age_seconds=None,
            last_verified_age_seconds=None,
            last_used_age_seconds=None,
            local_reuse_remaining_seconds=None,
            locally_reusable=False,
        )
