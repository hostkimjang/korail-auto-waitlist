from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain import Provider
from ..korail_sidecar.provider_cooldown import ProviderCooldownDeferred as _ProviderCooldownDeferred
from .application import (
    SUPPORTED_ACCOUNT_PROVIDERS,
    clear_provider_protection_halt,
    get_enabled_provider_credentials,
    record_provider_protection_halt,
    update_provider_auth_status,
)
from .contracts import ProviderCredentials
from .login_verification import (
    ProviderLoginVerificationOutcome,
    ProviderLoginVerifier,
    ProviderSessionRuntimeState,
)
from .models import RailProviderAccount, active_protection_halt_version
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
    protection_halted: bool = False

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
    # Protection verdicts retry on an escalating schedule instead of stopping the account
    # until a human re-verifies it. KORAIL's macro detection scores login submissions by
    # their spacing: logins about 23 minutes apart were blocked half the time, while gaps
    # of two hours or more usually passed. After a verdict, 15-minute retries were blocked
    # again in two of three samples while 30-minute retries passed in both, so the first
    # retry waits 30 minutes and doubling caps a persistent block at one login every two
    # hours (1800 -> 3600 -> 7200 seconds).
    PROTECTION_INITIAL_BACKOFF_SECONDS = 1800.0
    PROTECTION_MAX_BACKOFF_SECONDS = 7200.0
    LOCAL_FAILURE_INITIAL_BACKOFF_SECONDS = 5.0
    LOCAL_FAILURE_MAX_BACKOFF_SECONDS = 60.0
    AUTH_RECOVERY_MAX_ATTEMPTS = 5
    # These outcomes do not establish a credential verdict. A failed official submission
    # has its own slower retry policy; an adapter/database failure can recover quickly.
    LOCAL_FAILURE_OUTCOMES = frozenset({"failed", "not_checked"})

    outcomes: dict[Provider, RailProviderAuthStatus] = field(default_factory=dict)
    # provider -> (auth revision, attempts started, provider verdicts observed)
    auth_revision_attempts: dict[Provider, tuple[tuple[Provider, int, int], int, int]] = field(
        default_factory=dict
    )
    prewarm_in_flight: set[Provider] = field(default_factory=set)
    prewarm_retry_state: dict[Provider, tuple[int, int, float]] = field(default_factory=dict)
    # provider -> (credential generation, minimum official-submission retry interval)
    provider_submission_failures: dict[Provider, tuple[int, int]] = field(default_factory=dict)
    provider_deferred_until: dict[Provider, float] = field(default_factory=dict)
    # provider -> persisted auth revision whose own keepalive login got a protection verdict
    protection_halted_revisions: dict[Provider, tuple[Provider, int, int]] = field(
        default_factory=dict
    )
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

    def recovery_attempt_allowed(
        self, auth_status: RailProviderAuthStatus, verdicts_observed: int
    ) -> bool:
        """Bound automatic recovery per persisted auth revision by its failure meaning."""

        # Protection recovery is paced by the escalating protection backoff rather than a
        # count, so a temporary block lifts on its own. Credential rejection keeps a count
        # budget because repeated wrong-password logins can lock the provider account.
        if auth_status == "provider_blocked":
            return True
        return verdicts_observed < self.AUTH_RECOVERY_MAX_ATTEMPTS

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

    def halt_after_protection_verdict(self, revision: tuple[Provider, int, int]) -> None:
        """Fence a revision the provider answered with protection until a login succeeds."""

        # The persisted status may still read authenticated. The fence keeps observation
        # parked and makes every keepalive for this revision wait out the escalating
        # protection backoff, including the first one after a restart.
        self.protection_halted_revisions[revision[0]] = revision

    def is_protection_halted(self, revision: tuple[Provider, int, int]) -> bool:
        return self.protection_halted_revisions.get(revision[0]) == revision

    def clear_protection_halt(self, provider: Provider) -> None:
        self.protection_halted_revisions.pop(provider, None)

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
        if now < self.provider_deferred_until.get(provider, 0):
            return False
        submission_failure = self.provider_submission_failures.get(provider)
        if submission_failure is not None and submission_failure[0] != credential_version:
            self.provider_submission_failures.pop(provider, None)
            self.prewarm_retry_state.pop(provider, None)
            submission_failure = None
        retry = self.prewarm_retry_state.get(provider)
        if (
            (not bypass_backoff or submission_failure is not None)
            and retry is not None
            and retry[0] == credential_version
            and now < retry[2]
        ):
            return False
        self.prewarm_in_flight.add(provider)
        return True

    def defer_prewarm(self, provider: Provider, *, retry_after_seconds: int, now: float) -> None:
        """Preserve actual failure state and revision budgets when no call was admitted."""
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 86400:
            raise ValueError("invalid provider cooldown interval")
        self.provider_deferred_until[provider] = max(
            self.provider_deferred_until.get(provider, 0), now + retry_after_seconds
        )

    def record_submission_failure(
        self,
        provider: Provider,
        credential_version: int,
        *,
        retry_after_seconds: int | None,
    ) -> None:
        """Keep remote failure pacing separate from local outages and other providers."""

        previous = self.provider_submission_failures.get(provider)
        if provider is not Provider.KORAIL or retry_after_seconds is None:
            self.provider_submission_failures.pop(provider, None)
            if previous is not None:
                self.prewarm_retry_state.pop(provider, None)
            return
        if type(retry_after_seconds) is not int or not 300 <= retry_after_seconds <= 900:
            raise ValueError("invalid submission retry interval")
        if previous is None or previous[0] != credential_version:
            # A preceding local outage must not advance the remote failure sequence.
            self.prewarm_retry_state.pop(provider, None)
        self.provider_submission_failures[provider] = (credential_version, retry_after_seconds)

    def backoff_seconds(self, outcome: RailProviderAuthStatus, failure_count: int) -> float:
        """Separate a provider's credential verdict from this deployment's own outage."""

        if outcome == "provider_blocked":
            initial = self.PROTECTION_INITIAL_BACKOFF_SECONDS
            ceiling = self.PROTECTION_MAX_BACKOFF_SECONDS
        elif outcome in self.LOCAL_FAILURE_OUTCOMES:
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
            self.provider_submission_failures.pop(provider, None)
            return
        if outcome is not None:
            if outcome != "failed":
                self.record_submission_failure(
                    provider, credential_version, retry_after_seconds=None
                )
            previous = self.prewarm_retry_state.get(provider)
            failure_count = (
                previous[1] + 1 if previous is not None and previous[0] == credential_version else 1
            )
            submission_failure = self.provider_submission_failures.get(provider)
            if (
                outcome == "failed"
                and submission_failure is not None
                and submission_failure[0] == credential_version
            ):
                delay = min(
                    submission_failure[1] * float(2 ** min(failure_count - 1, 2)),
                    self.PREWARM_MAX_BACKOFF_SECONDS,
                )
            else:
                delay = self.backoff_seconds(outcome, failure_count)
            self.prewarm_retry_state[provider] = (
                credential_version,
                failure_count,
                now + delay,
            )

    def forget_provider(self, provider: Provider) -> None:
        self.prewarm_in_flight.discard(provider)
        self.prewarm_retry_state.pop(provider, None)
        self.provider_submission_failures.pop(provider, None)
        self.auth_revision_attempts.pop(provider, None)
        self.protection_halted_revisions.pop(provider, None)


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
                RailProviderAccount.protection_halt_credential_version,
                RailProviderAccount.protection_halt_revision_micros,
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
        protection_halted=active_protection_halt_version(
            credential_version=row.credential_version,
            updated_at=row.updated_at,
            halt_credential_version=row.protection_halt_credential_version,
            halt_revision_micros=row.protection_halt_revision_micros,
        )
        is not None,
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
    except _ProviderCooldownDeferred:
        raise
    except Exception:  # noqa: BLE001 -- provider exception text may contain secrets.
        registry.record_submission_failure(
            provider, credentials.credential_version, retry_after_seconds=None
        )
        registry.outcomes[provider] = "failed"
        LOGGER.warning("Provider runtime prewarm failed provider=%s", provider.value)
        return "failed"

    if verification.failure_kind == "provider_cooldown":
        if verification.cooldown_reason is None or verification.retry_after_seconds is None:
            raise ValueError("invalid provider cooldown verification")
        raise _ProviderCooldownDeferred(
            verification.cooldown_reason, verification.retry_after_seconds
        )
    outcome = _account_status(verification.outcome)
    registry.record_submission_failure(
        provider,
        credentials.credential_version,
        retry_after_seconds=(
            verification.retry_after_seconds
            if verification.failure_kind == "provider_submission_failed"
            else None
        ),
    )
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
    registry.record_submission_failure(
        provider, credentials.credential_version, retry_after_seconds=None
    )
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

        submission_failure = registry.provider_submission_failures.get(provider)
        if (
            submission_failure is not None
            and submission_failure[0] != account_runtime.credentials.credential_version
        ):
            registry.record_submission_failure(
                provider,
                account_runtime.credentials.credential_version,
                retry_after_seconds=None,
            )

        recoverable = account_runtime.auth_status in RECOVERABLE_PROVIDER_AUTH_STATUSES
        revision = account_runtime.recovery_revision
        if account_runtime.protection_halted:
            # Rehydrate the persisted fence before any decision: a restart must wait out
            # the protection backoff before logging in again on a revision the provider
            # already answered with protection. A successful login, a newer revision, or
            # an official positive session (which clears the persisted pair) lifts it.
            registry.halt_after_protection_verdict(revision)
        recovery_started = registry.auth_revision_started_count(revision)
        recovery_verdicts = registry.auth_revision_attempt_count(revision)
        recovery_attempt = recoverable and registry.recovery_attempt_allowed(
            account_runtime.auth_status, recovery_verdicts
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
                registry.prewarm_retry_state.pop(provider, None)
                registry.provider_submission_failures.pop(provider, None)
                # An official positive session of this generation supersedes the halt.
                registry.clear_protection_halt(provider)
                if account_runtime.protection_halted:
                    await _clear_persisted_protection_halt(
                        session_factory,
                        account_runtime,
                        revision,
                    )
                continue
            if registry.is_protection_halted(revision):
                retry = registry.prewarm_retry_state.get(provider)
                if retry is None or retry[0] != credential_version:
                    # A halt rehydrated after a restart has no retry clock yet. Start the
                    # protection backoff instead of logging in immediately.
                    registry.finish_prewarm(
                        provider,
                        credential_version,
                        outcome="provider_blocked",
                        now=now,
                    )
                    continue
                # Otherwise begin_prewarm below admits the retry once the escalating
                # protection backoff has elapsed.

        credential_version = account_runtime.credentials.credential_version
        loop = asyncio.get_running_loop()
        now = loop.time()
        if recovery_attempt and account_runtime.auth_status == "provider_blocked":
            retry = registry.prewarm_retry_state.get(provider)
            if retry is None or retry[0] != credential_version:
                # A protection revision may be persisted by a reservation outside this
                # manager. Observe the protection backoff before the next recovery
                # attempt instead of immediately logging in again.
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
        outcome: RailProviderAuthStatus | None = None
        deferred = False
        try:
            outcome = await _prewarm_account(
                session_factory,
                verifier,
                registry,
                account_runtime,
            )
            attempted += 1
        except _ProviderCooldownDeferred as error:
            deferred = True
            registry.defer_prewarm(
                provider, retry_after_seconds=error.retry_after_seconds, now=loop.time()
            )
        finally:
            registry.finish_prewarm(
                provider,
                credential_version,
                outcome=outcome,
                now=loop.time(),
            )
            if recovery_attempt and not deferred:
                # A local or actual provider attempt retires immediate retry; admission
                # deferral only schedules the next tick and consumes no revision budget.
                registry.mark_auth_revision_started(revision)
            if recovery_attempt and registry.consumes_recovery_budget(outcome):
                # Spend the budget only once this tick produced a provider verdict. An
                # adapter outage never reached the credential check, so it must not
                # exhaust recovery before the adapter is reachable again.
                registry.mark_auth_revision_attempted(revision)
            if not recovery_attempt and outcome == "provider_blocked":
                registry.halt_after_protection_verdict(revision)
                await _persist_protection_halt(session_factory, account_runtime, revision)
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


async def _persist_protection_halt(
    session_factory: async_sessionmaker[AsyncSession],
    account_runtime: _EnabledAccountRuntime,
    revision: tuple[Provider, int, int],
) -> None:
    """Persist the halt for the revision a keepalive login saw protection on.

    A failed write stays fail-closed: the process-local fence already stops this
    revision, so a database error only costs durability until the next verdict.
    """
    try:
        async with session_factory() as session:
            await record_provider_protection_halt(
                session,
                account_runtime.provider,
                expected_credential_version=revision[1],
                expected_revision_micros=revision[2],
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- database details stay redacted.
        LOGGER.warning(
            "Provider runtime protection-halt persistence failed provider=%s",
            account_runtime.provider.value,
        )


async def _clear_persisted_protection_halt(
    session_factory: async_sessionmaker[AsyncSession],
    account_runtime: _EnabledAccountRuntime,
    revision: tuple[Provider, int, int],
) -> None:
    """Drop the persisted halt that an official positive session superseded.

    The compare-and-swap binds to the halted revision, so a stale clear can never
    erase a newer fence. If the write fails, the halt stays active and the next
    positive session retries it, which keeps the fail-closed side safe.
    """
    verified_at = datetime.now(UTC)
    try:
        async with session_factory() as session:
            cleared = await clear_provider_protection_halt(
                session,
                account_runtime.provider,
                expected_credential_version=revision[1],
                expected_revision_micros=revision[2],
                expected_updated_at=account_runtime.updated_at,
                commit=False,
            )
            if not cleared:
                await session.rollback()
                return
            # Watches deferred while the halt was active parked themselves outside
            # the due selector with no retry clock. The official positive session
            # is the verification event that releases them, exactly as a fresh
            # successful login would, so resume in the same transaction that
            # clears the fence.
            from .auth_recovery_runtime import resume_watches_after_verified_provider_login

            await resume_watches_after_verified_provider_login(
                session,
                account_runtime.provider,
                verified_at,
                credential_version=revision[1],
            )
            await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- database details stay redacted.
        LOGGER.warning(
            "Provider runtime protection-halt clear failed provider=%s",
            account_runtime.provider.value,
        )
