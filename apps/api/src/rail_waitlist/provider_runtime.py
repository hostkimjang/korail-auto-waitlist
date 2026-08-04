from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .domain import Provider
from .models import RailProviderAccount
from .provider_accounts import (
    SUPPORTED_ACCOUNT_PROVIDERS,
    ProviderCredentials,
    get_enabled_provider_credentials,
    update_provider_auth_status,
)
from .provider_login_verification import (
    ProviderLoginVerificationOutcome,
    ProviderLoginVerifier,
    ProviderSessionRuntimeState,
)
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

    outcomes: dict[Provider, RailProviderAuthStatus] = field(default_factory=dict)
    attempted_auth_revisions: set[tuple[Provider, int, int]] = field(default_factory=set)
    completed: bool = False

    def outcome_for(self, provider: Provider) -> RailProviderAuthStatus | None:
        return self.outcomes.get(provider)

    def has_attempted_auth_revision(self, revision: tuple[Provider, int, int]) -> bool:
        return revision in self.attempted_auth_revisions

    def mark_auth_revision_attempted(self, revision: tuple[Provider, int, int]) -> None:
        provider = revision[0]
        self.attempted_auth_revisions = {
            attempted
            for attempted in self.attempted_auth_revisions
            if attempted[0] != provider
        }
        self.attempted_auth_revisions.add(revision)


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
            ).where(
                RailProviderAccount.provider == provider,
                RailProviderAccount.enabled.is_(True),
            ).with_for_update()
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
                    # Import locally to avoid coupling the provider-account module graph
                    # to watch orchestration at import time.
                    from .services import resume_watches_after_verified_provider_login

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
                from .services import resume_watches_after_verified_provider_login

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
        or snapshot.credential_generation
        != str(account_runtime.credentials.credential_version)
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
    """Warm every enabled account and persist only a generation-current success."""

    try:
        for provider in SUPPORTED_ACCOUNT_PROVIDERS:
            async with session_factory() as session:
                account_runtime = await _load_enabled_account_runtime(session, provider)
                # Do not retain a database transaction during external provider I/O.
                await session.rollback()
            if account_runtime is None:
                registry.outcomes[provider] = "not_checked"
                continue
            if account_runtime.auth_status in RECOVERABLE_PROVIDER_AUTH_STATUSES:
                # One external login attempt is allowed for each persisted auth failure
                # revision. A failed recovery is not looped until a new revision exists.
                registry.mark_auth_revision_attempted(account_runtime.recovery_revision)
            await _prewarm_account(
                session_factory,
                verifier,
                registry,
                account_runtime,
            )
    finally:
        registry.completed = True


async def recover_provider_sessions_once(
    session_factory: async_sessionmaker[AsyncSession],
    verifier: ProviderLoginVerifier,
    registry: ProviderRuntimePrewarmRegistry,
) -> int:
    """Reconcile or reverify each new recoverable auth revision once per process."""

    attempted = 0
    for provider in SUPPORTED_ACCOUNT_PROVIDERS:
        async with session_factory() as session:
            account_runtime = await _load_enabled_account_runtime(session, provider)
            await session.rollback()
        if (
            account_runtime is None
            or account_runtime.auth_status not in RECOVERABLE_PROVIDER_AUTH_STATUSES
        ):
            continue
        revision = account_runtime.recovery_revision
        if registry.has_attempted_auth_revision(revision):
            continue
        # Fence before provider I/O so overlapping maintenance ticks cannot duplicate login.
        registry.mark_auth_revision_attempted(revision)
        attempted += 1
        restored = await _restore_locally_reusable_session(
            session_factory,
            verifier,
            registry,
            account_runtime,
        )
        if restored is None:
            await _prewarm_account(session_factory, verifier, registry, account_runtime)
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
    """Recover later auth failures without periodically repeating provider logins."""

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
    """Verify stored accounts at startup, then recover each later auth failure once."""

    await prewarm_provider_sessions(session_factory, verifier, registry)
    await maintain_provider_sessions(
        session_factory,
        verifier,
        registry,
        interval_seconds=interval_seconds,
    )
