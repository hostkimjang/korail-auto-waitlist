"""Own the reusable authenticated KORAIL Pydoll session lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from .auth_contracts import (
    KorailCredentialInput as ContractKorailCredentialInput,
)
from .auth_contracts import (
    KorailLoginMethod as ContractKorailLoginMethod,
)
from .page_contracts import PydollPageSnapshot

__all__ = [
    "ActivePydollAuthenticationSession",
    "Awaitable",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSourceUnavailable",
    "Callable",
    "ContractKorailCredentialInput",
    "ContractKorailLoginMethod",
    "CredentialFingerprint",
    "KorailCredentialInput",
    "KorailLoginMethod",
    "KorailSessionActorSnapshot",
    "KorailSessionActorState",
    "OwnedCleanup",
    "Protocol",
    "PydollAuthenticationSession",
    "PydollAuthenticationSessionActor",
    "PydollAuthenticationSessionContext",
    "PydollAuthenticationSessionFactory",
    "PydollAuthenticationSessionLease",
    "PydollPageSnapshot",
    "ResponseSafetyGuard",
    "StrEnum",
    "annotations",
    "asyncio",
    "credential_fingerprint",
    "dataclass",
    "field",
    "hashlib",
    "sys",
]

KorailCredentialInput = ContractKorailCredentialInput
KorailLoginMethod = ContractKorailLoginMethod


class KorailSessionActorState(StrEnum):
    COLD = "cold"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    STALE = "stale"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class KorailSessionActorSnapshot:
    state: KorailSessionActorState
    credential_generation: str | None
    created_at_monotonic: float | None
    last_verified_at_monotonic: float | None
    last_used_at_monotonic: float | None
    local_reuse_until_monotonic: float | None
    locally_reusable: bool


def credential_fingerprint(credential: KorailCredentialInput) -> bytes:
    """Return a domain-separated digest without retaining credential text."""

    digest = hashlib.sha256(b"rail-waitlist:korail-pydoll-credential:v1\0")
    for value in (
        credential.login_method.value,
        credential.login_id,
        credential.password,
    ):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


class PydollAuthenticationSession(Protocol):
    async def open(self) -> PydollPageSnapshot: ...

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool: ...

    async def probe_authenticated_session(self) -> bool: ...


class PydollAuthenticationSessionContext[AuthSession_co: PydollAuthenticationSession](Protocol):
    async def __aenter__(self) -> AuthSession_co: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


type PydollAuthenticationSessionFactory[AuthSession: PydollAuthenticationSession] = Callable[
    [str, int, bool],
    PydollAuthenticationSessionContext[AuthSession],
]
ResponseSafetyGuard = Callable[[PydollPageSnapshot, str], None]
OwnedCleanup = Callable[[Awaitable[object]], Awaitable[None]]
CredentialFingerprint = Callable[[KorailCredentialInput], bytes]


@dataclass
class ActivePydollAuthenticationSession[AuthSession: PydollAuthenticationSession]:
    context: PydollAuthenticationSessionContext[AuthSession]
    session: AuthSession
    created_at: float
    last_used_at: float
    searches_started: int = 0
    credential_version: str | None = None
    authenticated_credential_version: str | None = None
    authenticated_credential_fingerprint: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PydollAuthenticationSessionLease[AuthSession: PydollAuthenticationSession]:
    context: PydollAuthenticationSessionContext[AuthSession]
    session: AuthSession
    created_at: float
    searches_started: int
    persistent: bool
    reused: bool
    authenticated: bool


class PydollAuthenticationSessionActor[AuthSession: PydollAuthenticationSession]:
    """Serialize and validate one credential-bound reusable browser generation."""

    def __init__(
        self,
        *,
        page_url: str,
        timeout_ms: int,
        headless: bool,
        session_factory: PydollAuthenticationSessionFactory[AuthSession],
        session_reuse_ttl_seconds: float,
        session_reuse_max_searches: int,
        monotonic: Callable[[], float],
        cleanup: OwnedCleanup,
        response_safety_guard: ResponseSafetyGuard,
        fingerprint: CredentialFingerprint = credential_fingerprint,
    ) -> None:
        self._page_url = page_url
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._session_factory = session_factory
        self._session_reuse_ttl_seconds = session_reuse_ttl_seconds
        self._session_reuse_max_searches = session_reuse_max_searches
        self._monotonic = monotonic
        self._cleanup = cleanup
        self._response_safety_guard = response_safety_guard
        self._fingerprint = fingerprint
        self._lock = asyncio.Lock()
        self._active_session: ActivePydollAuthenticationSession[AuthSession] | None = None
        self._state = KorailSessionActorState.COLD
        self._generation: str | None = None
        self._created_at: float | None = None
        self._last_verified_at: float | None = None
        self._last_used_at: float | None = None

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def active_session(self) -> ActivePydollAuthenticationSession[AuthSession] | None:
        return self._active_session

    @active_session.setter
    def active_session(
        self,
        value: ActivePydollAuthenticationSession[AuthSession] | None,
    ) -> None:
        self._active_session = value

    @property
    def state(self) -> KorailSessionActorState:
        return self._state

    @state.setter
    def state(self, value: KorailSessionActorState) -> None:
        self._state = value

    @property
    def generation(self) -> str | None:
        return self._generation

    @generation.setter
    def generation(self, value: str | None) -> None:
        self._generation = value

    @property
    def created_at(self) -> float | None:
        return self._created_at

    @created_at.setter
    def created_at(self, value: float | None) -> None:
        self._created_at = value

    @property
    def last_verified_at(self) -> float | None:
        return self._last_verified_at

    @last_verified_at.setter
    def last_verified_at(self, value: float | None) -> None:
        self._last_verified_at = value

    @property
    def last_used_at(self) -> float | None:
        return self._last_used_at

    @last_used_at.setter
    def last_used_at(self, value: float | None) -> None:
        self._last_used_at = value

    @property
    def reuse_enabled(self) -> bool:
        return self._session_reuse_ttl_seconds > 0 and self._session_reuse_max_searches > 1

    async def discard_if_credential_changed(self, credential: KorailCredentialInput) -> None:
        active = self._active_session
        fingerprint = self._fingerprint(credential)
        if active is not None and (
            (
                active.credential_version is not None
                and active.credential_version != credential.version
            )
            or (
                active.authenticated_credential_version is not None
                and active.authenticated_credential_fingerprint != fingerprint
            )
        ):
            self._state = KorailSessionActorState.STALE
            await self.discard_active_session()

    async def acquire_session(
        self,
        *,
        credential_version: str | None = None,
    ) -> PydollAuthenticationSessionLease[AuthSession]:
        if not self.reuse_enabled:
            created_at = self._monotonic()
            context = self._session_factory(self._page_url, self._timeout_ms, self._headless)
            session = await context.__aenter__()
            return PydollAuthenticationSessionLease(
                context=context,
                session=session,
                created_at=created_at,
                searches_started=1,
                persistent=False,
                reused=False,
                authenticated=False,
            )

        now = self._monotonic()
        active = self._active_session
        if active is not None and (
            now - active.last_used_at >= self._session_reuse_ttl_seconds
            or active.searches_started >= self._session_reuse_max_searches
        ):
            if active.authenticated_credential_version is not None:
                self._state = KorailSessionActorState.STALE
            await self.discard_active_session()
            active = None
        reused = active is not None
        if active is None:
            context = self._session_factory(self._page_url, self._timeout_ms, self._headless)
            session = await context.__aenter__()
            active = ActivePydollAuthenticationSession(
                context=context,
                session=session,
                created_at=now,
                last_used_at=now,
                credential_version=credential_version,
            )
            self._active_session = active
            if credential_version is not None:
                self._generation = credential_version
                self._created_at = now
                self._last_used_at = now
        elif credential_version is not None and active.credential_version is None:
            active.credential_version = credential_version
            self._generation = credential_version
            self._created_at = active.created_at
        active.searches_started += 1
        active.last_used_at = now
        if credential_version is not None:
            self._last_used_at = now
        return PydollAuthenticationSessionLease(
            context=active.context,
            session=active.session,
            created_at=active.created_at,
            searches_started=active.searches_started,
            persistent=True,
            reused=reused,
            authenticated=active.authenticated_credential_version is not None,
        )

    async def ensure_authenticated_session(
        self,
        session: AuthSession,
        credential: KorailCredentialInput,
    ) -> bool:
        """Authenticate once per exact credential generation and in-memory digest."""

        active = self._active_session
        if active is None or active.session is not session:
            now = self._monotonic()
            self._state = KorailSessionActorState.AUTHENTICATING
            self._generation = credential.version
            self._created_at = now
            self._last_used_at = now
            authenticated = await session.ensure_authenticated(credential)
            if authenticated:
                verified_at = self._monotonic()
                self._last_verified_at = verified_at
                self._last_used_at = verified_at
            self._state = (
                KorailSessionActorState.STALE
                if authenticated
                else KorailSessionActorState.AUTH_REQUIRED
            )
            return authenticated
        fingerprint = self._fingerprint(credential)
        if (
            active.authenticated_credential_version == credential.version
            and active.authenticated_credential_fingerprint == fingerprint
        ):
            active.last_used_at = self._monotonic()
            self._last_used_at = active.last_used_at
            self._state = KorailSessionActorState.READY
            return True
        self._state = KorailSessionActorState.AUTHENTICATING
        self._generation = credential.version
        authenticated = await session.ensure_authenticated(credential)
        if not authenticated:
            await self.discard_active_session()
            self._state = KorailSessionActorState.AUTH_REQUIRED
            return False
        active.authenticated_credential_version = credential.version
        active.authenticated_credential_fingerprint = fingerprint
        active.last_used_at = self._monotonic()
        self._last_verified_at = active.last_used_at
        self._last_used_at = active.last_used_at
        self._state = KorailSessionActorState.READY
        return True

    async def verify_credentials(self, credential: KorailCredentialInput) -> bool:
        async with self._lock:
            return await self._verify_credentials_locked(credential)

    async def _verify_credentials_locked(self, credential: KorailCredentialInput) -> bool:
        """Replace and authenticate one generation while the actor lock is held."""

        if self._active_session is not None:
            self._state = KorailSessionActorState.STALE
        await self.discard_active_session()

        lease: PydollAuthenticationSessionLease[AuthSession] | None = None
        stage = "browser_launch"
        try:
            lease = await self.acquire_session(credential_version=credential.version)
            session = lease.session
            stage = "load_page"
            self._response_safety_guard(await session.open(), stage)
            stage = "authenticate"
            return await self.ensure_authenticated_session(session, credential)
        except asyncio.CancelledError:
            await self.discard_active_session()
            self._state = KorailSessionActorState.STALE
            raise
        except (BrowserProtectionDetected, BrowserRateLimited):
            await self.discard_active_session()
            self._state = KorailSessionActorState.BLOCKED
            raise
        except BrowserSourceUnavailable as error:
            await self.discard_active_session()
            self._state = KorailSessionActorState.STALE
            if error.stage == "unspecified":
                raise BrowserSourceUnavailable(stage) from error
            raise
        except Exception as error:
            await self.discard_active_session()
            self._state = KorailSessionActorState.STALE
            raise BrowserSourceUnavailable(stage) from error
        finally:
            if lease is not None and not lease.persistent:
                await lease.context.__aexit__(*sys.exc_info())

    async def prewarm_credentials(self, credential: KorailCredentialInput) -> bool:
        async with self._lock:
            active = self._active_session
            fingerprint = self._fingerprint(credential)
            now = self._monotonic()
            if (
                active is not None
                and active.authenticated_credential_version == credential.version
                and active.authenticated_credential_fingerprint == fingerprint
                and now - active.last_used_at < self._session_reuse_ttl_seconds
                and active.searches_started < self._session_reuse_max_searches
                and self._state is KorailSessionActorState.READY
            ):
                try:
                    authenticated = await active.session.probe_authenticated_session()
                except asyncio.CancelledError:
                    await self.discard_active_session()
                    self._state = KorailSessionActorState.STALE
                    raise
                except (BrowserProtectionDetected, BrowserRateLimited):
                    await self.discard_active_session()
                    self._state = KorailSessionActorState.BLOCKED
                    raise
                except BrowserSourceUnavailable:
                    await self.discard_active_session()
                    self._state = KorailSessionActorState.STALE
                    raise
                except Exception as error:
                    await self.discard_active_session()
                    self._state = KorailSessionActorState.STALE
                    raise BrowserSourceUnavailable("session_keepalive") from error

                if authenticated:
                    verified_at = self._monotonic()
                    active.last_used_at = verified_at
                    self._last_verified_at = verified_at
                    self._last_used_at = verified_at
                    return True

                self._state = KorailSessionActorState.STALE
                await self.discard_active_session()

            return await self._verify_credentials_locked(credential)

    def snapshot(self) -> KorailSessionActorSnapshot:
        now = self._monotonic()
        active = self._active_session
        state = self._state
        local_reuse_until = None
        locally_reusable = False
        if active is not None and active.authenticated_credential_version is not None:
            local_reuse_until = active.last_used_at + self._session_reuse_ttl_seconds
            locally_reusable = (
                self.reuse_enabled
                and now < local_reuse_until
                and active.searches_started < self._session_reuse_max_searches
                and state is KorailSessionActorState.READY
            )
            if state is KorailSessionActorState.READY and not locally_reusable:
                state = KorailSessionActorState.STALE
        return KorailSessionActorSnapshot(
            state=state,
            credential_generation=self._generation,
            created_at_monotonic=self._created_at,
            last_verified_at_monotonic=self._last_verified_at,
            last_used_at_monotonic=self._last_used_at,
            local_reuse_until_monotonic=local_reuse_until,
            locally_reusable=locally_reusable,
        )

    async def discard_active_session(self) -> None:
        active = self._active_session
        self._active_session = None
        if active is not None:
            await self._cleanup(active.context.__aexit__(*sys.exc_info()))

    async def discard_with_state(self, state: KorailSessionActorState) -> None:
        """Retire the current generation before exposing its terminal actor state."""

        await self.discard_active_session()
        self._state = state

    async def close_locked(self) -> None:
        await self.discard_active_session()
        self._state = KorailSessionActorState.COLD
