from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain import Provider
from .application import (
    SUPPORTED_ACCOUNT_PROVIDERS,
    get_enabled_provider_credentials,
    update_provider_auth_status,
)
from .contracts import ProviderCredentials
from .login_verification import (
    ProviderLoginVerificationOutcome,
    ProviderLoginVerifier,
    ProviderSessionRuntimeState,
)
from .models import RailProviderAccount
from .schemas import RailProviderAuthStatus

LOGGER = logging.getLogger(__name__)
PROVIDER_AUTH_RECOVERY_INTERVAL_SECONDS = 30.0
RECOVERABLE_PROVIDER_AUTH_STATUSES: frozenset[RailProviderAuthStatus] = frozenset(
    {"auth_required", "provider_blocked"}
)


@dataclass(frozen=True)
class _EnabledAccountRuntime:
    provider: Provider
    credentials: ProviderCredentials
    auth_status: RailProviderAuthStatus
    updated_at: datetime

    @property
    def recovery_revision(self) -> tuple[Provider, int, int]:
        updated_at = self.updated_at
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return (
            self.provider,
            self.credentials.credential_version,
            int(updated_at.timestamp() * 1_000_000),
        )


@dataclass
class ProviderRuntimePrewarmRegistry:
    """Process-local startup results with no credential or provider payload material."""

    SESSION_REFRESH_WINDOW_SECONDS = 120.0
    PREWARM_INITIAL_BACKOFF_SECONDS = 60.0
    PREWARM_MAX_BACKOFF_SECONDS = 900.0

    outcomes: dict[Provider, RailProviderAuthStatus] = field(default_factory=dict)
    attempted_auth_revisions: set[tuple[Provider, int, int]] = field(default_factory=set)
    prewarm_in_flight: set[Provider] = field(default_factory=set)
    prewarm_retry_state: dict[Provider, tuple[int, int, float]] = field(default_factory=dict)
    completed: bool = False

    def outcome_for(self, provider: Provider) -> RailProviderAuthStatus | None:
        return self.outcomes.get(provider)

    def has_attempted_auth_revision(self, revision: tuple[Provider, int, int]) -> bool:
        return revision in self.attempted_auth_revisions

    def mark_auth_revision_attempted(self, revision: tuple[Provider, int, int]) -> None:
        provider = revision[0]
        self.attempted_auth_revisions = {
            attempted for attempted in self.attempted_auth_revisions if attempted[0] != provider
        }
        self.attempted_auth_revisions.add(revision)

    def begin_prewarm(
        self,
        provider: Provider,
        credential_version: int,
        *,
        now: float,
        bypass_backoff: bool = False,
    ) -> bool:
        """Fence one provider login attempt without retaining credential material."""

        if provider in self.prewarm_in_flight:
            return False
        retry = self.prewarm_retry_state.get(provider)
        if (
            not bypass_backoff
            and retry is not None
            and retry[0] == credential_version
            and now < retry[2]
        ):
            return False
        self.prewarm_in_flight.add(provider)
        return True

    def finish_prewarm(
        self,
        provider: Provider,
        credential_version: int,
        *,
        outcome: RailProviderAuthStatus | None,
        now: float,
    ) -> None:
        self.prewarm_in_flight.discard(provider)
        if outcome == "authenticated":
            self.prewarm_retry_state.pop(provider, None)
            return
        if outcome is not None:
            previous = self.prewarm_retry_state.get(provider)
            failure_count = (
                previous[1] + 1
                if previous is not None and previous[0] == credential_version
                else 1
            )
            backoff_seconds = min(
                self.PREWARM_INITIAL_BACKOFF_SECONDS
                * (2 ** (failure_count - 1)),
                self.PREWARM_MAX_BACKOFF_SECONDS,
            )
            if outcome == "provider_blocked":
                # Protection responses use the safest interval from the first failure.
                backoff_seconds = self.PREWARM_MAX_BACKOFF_SECONDS
            self.prewarm_retry_state[provider] = (
                credential_version,
                failure_count,
                now + backoff_seconds,
            )

    def forget_provider(self, provider: Provider) -> None:
        self.prewarm_in_flight.discard(provider)
        self.prewarm_retry_state.pop(provider, None)


async def _load_enabled_account_runtime(
    session: AsyncSession,
    provider: Provider,
) -> _EnabledAccountRuntime | None:
    row = (
        await session.execute(
            select(
                RailProviderAccount.credential_version,
                RailProviderAccount.last_auth_status,
                RailProviderAccount.updated_at,
            )
            .where(
                RailProviderAccount.provider == provider,
                RailProviderAccount.enabled.is_(True),
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return None
    credentials = await get_enabled_provider_credentials(session, provider)
    if credentials is None or credentials.credential_version != row.credential_version:
        return None
    return _EnabledAccountRuntime(
        provider=provider,
        credentials=credentials,
        auth_status=row.last_auth_status,
        updated_at=row.updated_at,
    )


def _account_status(outcome: ProviderLoginVerificationOutcome) -> RailProviderAuthStatus:
    if outcome is ProviderLoginVerificationOutcome.AUTHENTICATED:
        return "authenticated"
    if outcome is ProviderLoginVerificationOutcome.AUTH_REQUIRED:
        return "auth_required"
    if outcome is ProviderLoginVerificationOutcome.PROVIDER_BLOCKED:
        return "provider_blocked"
    return "failed"


async def _prewarm_account(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
    account_runtime: _EnabledAccountRuntime,
) -> RailProviderAuthStatus:
    provider = account_runtime.provider
    credentials = account_runtime.credentials
    try:
        verification = await verifier.prewarm(provider, credentials)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- provider exception text may contain secrets.
        registry.outcomes[provider] = "failed"
        LOGGER.warning("Provider runtime prewarm failed provider=%s", provider.value)
        return "failed"

    outcome = _account_status(verification.outcome)
    if verification.outcome is ProviderLoginVerificationOutcome.AUTHENTICATED:
        try:
            async with session_factory() as session:
                account = await update_provider_auth_status(
                    session,
                    provider,
                    "authenticated",
                    expected_credential_version=credentials.credential_version,
                    commit=False,
                )
                if (
                    account is None
                    or account.credential_version != credentials.credential_version
                    or account.last_authenticated_at is None
                ):
                    await session.rollback()
                    outcome = "not_checked"
                else:
                    # Keep the provider registry path lazy to avoid an import cycle.
                    from .auth_recovery_runtime import (
                        resume_watches_after_verified_provider_login,
                    )

                    await resume_watches_after_verified_provider_login(
                        session,
                        provider,
                        account.last_authenticated_at,
                    )
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- database/provider details stay redacted.
            outcome = "failed"
            LOGGER.warning(
                "Provider runtime prewarm persistence failed provider=%s",
                provider.value,
            )
    registry.outcomes[provider] = outcome
    LOGGER.info(
        "Provider runtime prewarm completed provider=%s outcome=%s",
        provider.value,
        verification.outcome.value,
    )
    return outcome


async def _restore_authenticated_account(
    session_factory: async_sessionmaker[AsyncSession],
    registry: ProviderRuntimePrewarmRegistry,
    account_runtime: _EnabledAccountRuntime,
) -> RailProviderAuthStatus:
    """Persist a generation-current authenticated session and resume stalled watches."""

    provider = account_runtime.provider
    credentials = account_runtime.credentials
    outcome: RailProviderAuthStatus = "not_checked"
    try:
        async with session_factory() as session:
            account = await update_provider_auth_status(
                session,
                provider,
                "authenticated",
                expected_credential_version=credentials.credential_version,
                commit=False,
            )
            if (
                account is None
                or account.credential_version != credentials.credential_version
                or account.last_authenticated_at is None
            ):
                await session.rollback()
            else:
                from .auth_recovery_runtime import (
                    resume_watches_after_verified_provider_login,
                )

                await resume_watches_after_verified_provider_login(
                    session,
                    provider,
                    account.last_authenticated_at,
                )
                await session.commit()
                outcome = "authenticated"
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- database/provider details stay redacted.
        outcome = "failed"
        LOGGER.warning(
            "Provider runtime authenticated-session persistence failed provider=%s",
            provider.value,
        )
    registry.outcomes[provider] = outcome
    return outcome


async def _restore_locally_reusable_session(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
    account_runtime: _EnabledAccountRuntime,
) -> RailProviderAuthStatus | None:
    """Reconcile a ready same-generation actor without another external login."""

    try:
        snapshot = await verifier.session_snapshot(account_runtime.provider)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- runtime telemetry is best-effort and redacted.
        return None
    if (
        snapshot.state is not ProviderSessionRuntimeState.READY
        or not snapshot.locally_reusable
        or snapshot.credential_generation != str(account_runtime.credentials.credential_version)
    ):
        return None
    outcome = await _restore_authenticated_account(
        session_factory,
        registry,
        account_runtime,
    )
    LOGGER.info(
        "Provider runtime reusable session reconciled provider=%s outcome=%s",
        account_runtime.provider.value,
        outcome,
    )
    return outcome


async def prewarm_provider_sessions(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
) -> None:
    """Reconcile every enabled account and persist only generation-current success."""

    try:
        await recover_provider_sessions_once(session_factory, verifier, registry)
    finally:
        registry.completed = True


async def recover_provider_sessions_once(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
) -> int:
    """Reconcile every enabled account, refreshing only cold or expiring sessions."""

    attempted = 0
    for provider in SUPPORTED_ACCOUNT_PROVIDERS:
        async with session_factory() as session:
            account_runtime = await _load_enabled_account_runtime(session, provider)
            await session.rollback()
        if account_runtime is None:
            registry.outcomes[provider] = "not_checked"
            registry.forget_provider(provider)
            continue

        recoverable = account_runtime.auth_status in RECOVERABLE_PROVIDER_AUTH_STATUSES
        revision = account_runtime.recovery_revision
        new_recovery_revision = recoverable and not registry.has_attempted_auth_revision(revision)
        if recoverable and not new_recovery_revision:
            # Still observe the sanitized process state every tick, but preserve the
            # one-provider-attempt-per-persisted-auth-revision contract.
            try:
                await verifier.session_snapshot(provider)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- telemetry details remain redacted.
                LOGGER.warning("Provider runtime session snapshot failed provider=%s", provider.value)
            continue

        if new_recovery_revision:
            restored = await _restore_locally_reusable_session(
                session_factory,
                verifier,
                registry,
                account_runtime,
            )
            if restored is not None:
                registry.mark_auth_revision_attempted(revision)
                attempted += 1
                continue
        else:
            try:
                snapshot = await verifier.session_snapshot(provider)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- telemetry details remain redacted.
                snapshot = None
                LOGGER.warning("Provider runtime session snapshot failed provider=%s", provider.value)
            credential_version = account_runtime.credentials.credential_version
            loop = asyncio.get_running_loop()
            now = loop.time()
            if (
                snapshot is not None
                and snapshot.state is ProviderSessionRuntimeState.AUTHENTICATING
            ):
                # A reservation or explicit verification already owns the sidecar auth lock.
                # Re-read on the next tick instead of queueing a keepalive behind it.
                continue
            if snapshot is not None and snapshot.state is ProviderSessionRuntimeState.BLOCKED:
                retry = registry.prewarm_retry_state.get(provider)
                if retry is None or retry[0] != credential_version:
                    # A protection state created outside this manager (for example by a
                    # reservation) must not trigger an immediate login attempt.
                    registry.finish_prewarm(
                        provider,
                        credential_version,
                        outcome="provider_blocked",
                        now=now,
                    )
                    continue
                if now < retry[2]:
                    continue
            if (
                snapshot is not None
                and snapshot.state is ProviderSessionRuntimeState.READY
                and snapshot.locally_reusable
                and snapshot.credential_generation
                == str(account_runtime.credentials.credential_version)
                and snapshot.local_reuse_remaining_seconds is not None
                and snapshot.local_reuse_remaining_seconds
                > registry.SESSION_REFRESH_WINDOW_SECONDS
            ):
                registry.outcomes[provider] = "authenticated"
                continue

        credential_version = account_runtime.credentials.credential_version
        loop = asyncio.get_running_loop()
        now = loop.time()
        if new_recovery_revision and account_runtime.auth_status == "provider_blocked":
            retry = registry.prewarm_retry_state.get(provider)
            if retry is None or retry[0] != credential_version:
                # A protection revision may be persisted by a reservation outside this
                # manager. Observe the full protection cooldown before the revision's
                # single recovery attempt instead of immediately logging in again.
                registry.finish_prewarm(
                    provider,
                    credential_version,
                    outcome="provider_blocked",
                    now=now,
                )
                continue
        if not registry.begin_prewarm(
            provider,
            credential_version,
            now=now,
            bypass_backoff=(
                new_recovery_revision and account_runtime.auth_status != "provider_blocked"
            ),
        ):
            continue
        if new_recovery_revision:
            # Fence only after this tick owns the provider attempt. A blocked revision
            # remains eligible for its one recovery attempt after the cooldown expires.
            registry.mark_auth_revision_attempted(revision)
        outcome: RailProviderAuthStatus | None = None
        try:
            outcome = await _prewarm_account(
                session_factory,
                verifier,
                registry,
                account_runtime,
            )
            attempted += 1
        finally:
            registry.finish_prewarm(
                provider,
                credential_version,
                outcome=outcome,
                now=loop.time(),
            )
    return attempted


# Keep the previous internal name for downstream imports while the behavior now also
# covers provider-blocked revisions. New code should use ``recover_provider_sessions_once``.
recover_auth_required_provider_sessions_once = recover_provider_sessions_once


async def maintain_provider_sessions(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
    *,
    interval_seconds: float = PROVIDER_AUTH_RECOVERY_INTERVAL_SECONDS,
) -> None:
    """Reconcile enabled provider sessions with bounded refresh and failure backoff."""

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await recover_provider_sessions_once(
                session_factory,
                verifier,
                registry,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- database/provider details stay redacted.
            LOGGER.warning("Provider runtime maintenance tick failed")


async def run_provider_session_manager(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
    *,
    interval_seconds: float = PROVIDER_AUTH_RECOVERY_INTERVAL_SECONDS,
) -> None:
    """Warm stored accounts at startup, then keep each enabled session reusable."""

    await prewarm_provider_sessions(session_factory, verifier, registry)
    await maintain_provider_sessions(
        session_factory,
        verifier,
        registry,
        interval_seconds=interval_seconds,
    )
