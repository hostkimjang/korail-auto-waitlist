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
PROVIDER_AUTH_RECOVERY_INTERVAL_SECONDS = 10.0
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
    KORAIL_SESSION_REFRESH_FRACTION = 0.25
    SESSION_REFRESH_SAFETY_FRACTION = 0.5
    SESSION_REFRESH_MAX_SECONDS = 600.0
    PREWARM_INITIAL_BACKOFF_SECONDS = 60.0
    PREWARM_MAX_BACKOFF_SECONDS = 900.0
    LOCAL_FAILURE_INITIAL_BACKOFF_SECONDS = 5.0
    LOCAL_FAILURE_MAX_BACKOFF_SECONDS = 60.0
    AUTH_RECOVERY_MAX_ATTEMPTS = 5
    # Outcomes that never reached the provider's credential check. They describe this
    # deployment's own adapter or database, so retrying them quickly cannot lock an
    # account out and is the only way a stranded session recovers on its own.
    LOCAL_FAILURE_OUTCOMES = frozenset({"failed", "not_checked"})

    outcomes: dict[Provider, RailProviderAuthStatus] = field(default_factory=dict)
    # provider -> (auth revision, attempts started, provider verdicts observed)
    auth_revision_attempts: dict[Provider, tuple[tuple[Provider, int, int], int, int]] = field(
        default_factory=dict
    )
    prewarm_in_flight: set[Provider] = field(default_factory=set)
    prewarm_retry_state: dict[Provider, tuple[int, int, float]] = field(default_factory=dict)
    completed: bool = False

    def outcome_for(self, provider: Provider) -> RailProviderAuthStatus | None:
        return self.outcomes.get(provider)

    def _auth_revision_counts(self, revision: tuple[Provider, int, int]) -> tuple[int, int]:
        recorded = self.auth_revision_attempts.get(revision[0])
        if recorded is None or recorded[0] != revision:
            return (0, 0)
        return (recorded[1], recorded[2])

    def auth_revision_started_count(self, revision: tuple[Provider, int, int]) -> int:
        """Count attempts begun for this revision, including adapter-side failures."""

        return self._auth_revision_counts(revision)[0]

    def auth_revision_attempt_count(self, revision: tuple[Provider, int, int]) -> int:
        """Count provider verdicts observed for this revision."""

        return self._auth_revision_counts(revision)[1]

    def max_recovery_attempts(self, auth_status: RailProviderAuthStatus) -> int:
        """Bound automatic recovery per persisted auth revision by its failure meaning."""

        # A protection verdict keeps its single attempt: logging in repeatedly under
        # provider protection is what escalates a block. Credential expiry instead needs a
        # retry budget so one adapter outage cannot strand the account until a human acts.
        if auth_status == "provider_blocked":
            return 1
        return self.AUTH_RECOVERY_MAX_ATTEMPTS

    def consumes_recovery_budget(self, outcome: RailProviderAuthStatus | None) -> bool:
        """Spend a revision's recovery budget only on a real provider verdict."""

        return outcome is not None and outcome not in self.LOCAL_FAILURE_OUTCOMES

    def session_refresh_threshold_seconds(
        self,
        provider: Provider,
        last_verified_age_seconds: float | None,
        local_reuse_remaining_seconds: float | None,
    ) -> float:
        """Refresh KORAIL early enough that one failed attempt still leaves retry room."""

        # Only KORAIL anchors its reuse deadline on last_verified_at and refreshes that
        # timestamp on every successful prewarm, so only KORAIL's reuse window can be
        # derived from telemetry. SRT anchors on last_used_at and its reusing prewarm
        # leaves last_verified_at untouched, which would inflate the estimate on every
        # tick until the manager logged in continuously. SRT keeps the fixed window.
        if provider is not Provider.KORAIL:
            return self.SESSION_REFRESH_WINDOW_SECONDS
        if last_verified_age_seconds is None or local_reuse_remaining_seconds is None:
            return self.SESSION_REFRESH_WINDOW_SECONDS
        reuse_window_seconds = last_verified_age_seconds + local_reuse_remaining_seconds
        return min(
            max(
                self.SESSION_REFRESH_WINDOW_SECONDS,
                reuse_window_seconds * self.KORAIL_SESSION_REFRESH_FRACTION,
            ),
            self.SESSION_REFRESH_MAX_SECONDS,
            # A threshold that reaches the window itself would prewarm on every tick even
            # when an operator configures an unusually short reuse TTL.
            reuse_window_seconds * self.SESSION_REFRESH_SAFETY_FRACTION,
        )

    def mark_auth_revision_attempted(self, revision: tuple[Provider, int, int]) -> None:
        started, verdicts = self._auth_revision_counts(revision)
        self.auth_revision_attempts[revision[0]] = (revision, started, verdicts + 1)

    def mark_auth_revision_started(self, revision: tuple[Provider, int, int]) -> None:
        """Retire the revision's immediate retry so later attempts honour the backoff."""

        started, verdicts = self._auth_revision_counts(revision)
        self.auth_revision_attempts[revision[0]] = (revision, started + 1, verdicts)

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

    def backoff_seconds(self, outcome: RailProviderAuthStatus, failure_count: int) -> float:
        """Separate a provider's credential verdict from this deployment's own outage."""

        if outcome == "provider_blocked":
            # Protection responses use the safest interval from the first failure.
            return self.PREWARM_MAX_BACKOFF_SECONDS
        if outcome in self.LOCAL_FAILURE_OUTCOMES:
            initial = self.LOCAL_FAILURE_INITIAL_BACKOFF_SECONDS
            ceiling = self.LOCAL_FAILURE_MAX_BACKOFF_SECONDS
        else:
            initial = self.PREWARM_INITIAL_BACKOFF_SECONDS
            ceiling = self.PREWARM_MAX_BACKOFF_SECONDS
        growth = float(2 ** (failure_count - 1))
        return min(initial * growth, ceiling)

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
                previous[1] + 1 if previous is not None and previous[0] == credential_version else 1
            )
            self.prewarm_retry_state[provider] = (
                credential_version,
                failure_count,
                now + self.backoff_seconds(outcome, failure_count),
            )

    def forget_provider(self, provider: Provider) -> None:
        self.prewarm_in_flight.discard(provider)
        self.prewarm_retry_state.pop(provider, None)
        self.auth_revision_attempts.pop(provider, None)


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
                        credential_version=credentials.credential_version,
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
                    credential_version=credentials.credential_version,
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
        recovery_started = registry.auth_revision_started_count(revision)
        recovery_verdicts = registry.auth_revision_attempt_count(revision)
        recovery_attempt = recoverable and recovery_verdicts < registry.max_recovery_attempts(
            account_runtime.auth_status
        )
        if recoverable and not recovery_attempt:
            # Still observe the sanitized process state every tick, but stop retrying a
            # revision that has spent its recovery budget. Only a newer persisted auth
            # revision or credential generation starts a fresh budget.
            try:
                await verifier.session_snapshot(provider)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- telemetry details remain redacted.
                LOGGER.warning(
                    "Provider runtime session snapshot failed provider=%s",
                    provider.value,
                )
            continue

        if recovery_attempt:
            restored = await _restore_locally_reusable_session(
                session_factory,
                verifier,
                registry,
                account_runtime,
            )
            if restored is not None:
                registry.mark_auth_revision_started(revision)
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
                LOGGER.warning(
                    "Provider runtime session snapshot failed provider=%s",
                    provider.value,
                )
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
                > registry.session_refresh_threshold_seconds(
                    provider,
                    snapshot.last_verified_age_seconds,
                    snapshot.local_reuse_remaining_seconds,
                )
            ):
                registry.outcomes[provider] = "authenticated"
                continue

        credential_version = account_runtime.credentials.credential_version
        loop = asyncio.get_running_loop()
        now = loop.time()
        if recovery_attempt and account_runtime.auth_status == "provider_blocked":
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
                recovery_attempt
                and recovery_started == 0
                and account_runtime.auth_status != "provider_blocked"
            ),
        ):
            continue
        if recovery_attempt:
            # Retire the revision's immediate attempt as soon as this tick owns it, so a
            # repeated adapter outage falls back to the local-failure backoff instead of
            # re-entering the bypass on every tick.
            registry.mark_auth_revision_started(revision)
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
            if recovery_attempt and registry.consumes_recovery_budget(outcome):
                # Spend the budget only once this tick produced a provider verdict. An
                # adapter outage never reached the credential check, so it must not
                # exhaust recovery before the adapter is reachable again.
                registry.mark_auth_revision_attempted(revision)
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
