from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import rail_waitlist.provider_account_management.runtime as provider_runtime_module
from rail_waitlist.domain import Provider, ReservationPolicy, WatchStatus
from rail_waitlist.models import RailProviderAccount, Watch, WatchTransitionHistory
from rail_waitlist.provider_account_management.runtime import (
    ProviderRuntimePrewarmRegistry,
    maintain_provider_sessions,
    prewarm_provider_sessions,
    recover_provider_sessions_once,
)
from rail_waitlist.provider_login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
    ProviderSessionRuntimeSnapshot,
    ProviderSessionRuntimeState,
)
from rail_waitlist.security import secret_box


@dataclass
class StubRuntimeVerifier:
    prewarm_calls: list[tuple[Provider, int]] = field(default_factory=list)
    outcomes: dict[Provider, ProviderLoginVerificationOutcome] = field(default_factory=dict)
    snapshots: dict[Provider, ProviderSessionRuntimeSnapshot] = field(default_factory=dict)

    async def prewarm(self, provider, credentials):
        self.prewarm_calls.append((provider, credentials.credential_version))
        outcome = self.outcomes.get(
            provider,
            ProviderLoginVerificationOutcome.AUTHENTICATED,
        )
        if outcome is ProviderLoginVerificationOutcome.AUTHENTICATED:
            self.snapshots[provider] = runtime_snapshot(
                provider,
                credential_generation=str(credentials.credential_version),
            )
        return ProviderLoginVerification(outcome)

    async def session_snapshot(self, provider):
        return self.snapshots.get(provider) or ProviderSessionRuntimeSnapshot(
            provider=provider,
            state=ProviderSessionRuntimeState.READY,
            credential_generation="4",
            created_age_seconds=12.0,
            last_verified_age_seconds=3.0,
            last_used_age_seconds=1.0,
            local_reuse_remaining_seconds=240.0,
            locally_reusable=True,
        )


def runtime_snapshot(
    provider: Provider,
    *,
    state: ProviderSessionRuntimeState = ProviderSessionRuntimeState.READY,
    credential_generation: str | None = "4",
    remaining_seconds: float | None = 240.0,
    last_verified_age_seconds: float | None = 3.0,
    locally_reusable: bool = True,
) -> ProviderSessionRuntimeSnapshot:
    return ProviderSessionRuntimeSnapshot(
        provider=provider,
        state=state,
        credential_generation=credential_generation,
        created_age_seconds=12.0,
        last_verified_age_seconds=last_verified_age_seconds,
        last_used_age_seconds=1.0,
        local_reuse_remaining_seconds=remaining_seconds,
        locally_reusable=locally_reusable,
    )


def _cold_snapshot(provider: Provider) -> ProviderSessionRuntimeSnapshot:
    return runtime_snapshot(
        provider,
        state=ProviderSessionRuntimeState.COLD,
        credential_generation=None,
        remaining_seconds=None,
        locally_reusable=False,
    )


async def _seed_account(
    app,
    *,
    auth_status: str,
    provider: Provider = Provider.KORAIL,
    credential_version: int = 9,
    login_id: str = "1234567890",
    updated_at: datetime | None = None,
) -> None:
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=provider,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": login_id,
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=credential_version,
                last_auth_status=auth_status,
                updated_at=updated_at or (datetime.now(UTC) - timedelta(minutes=1)),
            )
        )
        await session.commit()


def _clear_backoff(registry: ProviderRuntimePrewarmRegistry, credential_version: int = 9) -> None:
    """Let the next tick run immediately without asserting on wall-clock delays."""

    retry = registry.prewarm_retry_state.get(Provider.KORAIL)
    failure_count = retry[1] if retry is not None else 1
    registry.prewarm_retry_state[Provider.KORAIL] = (credential_version, failure_count, 0.0)


async def test_startup_prewarm_recovers_enabled_auth_required_account_and_watch(app) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="authenticated",
                last_authenticated_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "0987654321",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=2,
                last_auth_status="auth_required",
                updated_at=now - timedelta(minutes=5),
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="수서",
            destination="부산",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="startup-prewarm-auth-required",
        )
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.RESERVING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="reservation_auth_required",
                created_at=now - timedelta(minutes=1),
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                state=ProviderSessionRuntimeState.COLD,
                credential_generation=None,
                remaining_seconds=None,
                locally_reusable=False,
            ),
            Provider.SRT: runtime_snapshot(
                Provider.SRT,
                state=ProviderSessionRuntimeState.COLD,
                credential_generation=None,
                remaining_seconds=None,
                locally_reusable=False,
            ),
        }
    )
    registry = ProviderRuntimePrewarmRegistry()
    await prewarm_provider_sessions(
        app.state.test_session_factory,
        verifier,
        registry,
    )

    assert verifier.prewarm_calls == [(Provider.KORAIL, 4), (Provider.SRT, 2)]
    assert registry.completed
    assert registry.outcome_for(Provider.KORAIL) == "authenticated"
    assert registry.outcome_for(Provider.SRT) == "authenticated"
    async with app.state.test_session_factory() as session:
        accounts = {
            account.provider: account
            for account in (await session.scalars(select(RailProviderAccount))).all()
        }
        resumed_watch = await session.get(Watch, watch_id)
        assert accounts[Provider.KORAIL].last_auth_status == "authenticated"
        assert accounts[Provider.SRT].last_auth_status == "authenticated"
        assert accounts[Provider.SRT].last_authenticated_at is not None
        assert resumed_watch is not None
        assert resumed_watch.status is WatchStatus.SCHEDULED


@pytest.mark.parametrize(
    ("outcome", "registry_status"),
    [
        (ProviderLoginVerificationOutcome.AUTH_REQUIRED, "auth_required"),
        (ProviderLoginVerificationOutcome.PROVIDER_BLOCKED, "provider_blocked"),
        (ProviderLoginVerificationOutcome.FAILED, "failed"),
    ],
)
async def test_startup_prewarm_failure_does_not_demote_authenticated_account(
    app,
    outcome: ProviderLoginVerificationOutcome,
    registry_status: str,
) -> None:
    successful_at = datetime.now(UTC) - timedelta(hours=1)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="authenticated",
                last_authenticated_at=successful_at,
                updated_at=successful_at,
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: outcome},
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                state=ProviderSessionRuntimeState.COLD,
                credential_generation=None,
                remaining_seconds=None,
                locally_reusable=False,
            )
        },
    )
    registry = ProviderRuntimePrewarmRegistry()
    await prewarm_provider_sessions(app.state.test_session_factory, verifier, registry)

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        assert account.last_auth_status == "authenticated"
        assert account.last_authenticated_at is not None
        assert account.last_authenticated_at.replace(tzinfo=UTC) == successful_at
        assert account.updated_at.replace(tzinfo=UTC) == successful_at
    assert registry.outcome_for(Provider.KORAIL) == registry_status


@dataclass
class ConcurrentCredentialReplacementVerifier(StubRuntimeVerifier):
    session_factory: object | None = None

    async def prewarm(self, provider, credentials):
        result = await super().prewarm(provider, credentials)
        assert self.session_factory is not None
        async with self.session_factory() as session:
            account = await session.scalar(
                select(RailProviderAccount).where(RailProviderAccount.provider == provider)
            )
            assert account is not None
            account.credential_version += 1
            await session.commit()
        return result


async def test_startup_prewarm_success_does_not_persist_stale_credential_generation(app) -> None:
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="auth_required",
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    verifier = ConcurrentCredentialReplacementVerifier(
        session_factory=app.state.test_session_factory,
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                state=ProviderSessionRuntimeState.COLD,
                credential_generation=None,
                remaining_seconds=None,
                locally_reusable=False,
            )
        },
    )
    registry = ProviderRuntimePrewarmRegistry()
    await prewarm_provider_sessions(app.state.test_session_factory, verifier, registry)

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        assert account.credential_version == 5
        assert account.last_auth_status == "auth_required"
    assert registry.outcome_for(Provider.KORAIL) == "not_checked"


async def test_later_auth_required_revision_is_recovered_once_and_resumes_watch(app) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=7,
                last_auth_status="auth_required",
                updated_at=now,
            )
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            destination="서울",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="runtime-auth-recovery-success",
        )
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.RESERVING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="reservation_auth_required",
                created_at=now,
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    verifier = StubRuntimeVerifier()
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 1
    )
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )
    assert verifier.prewarm_calls == [(Provider.KORAIL, 7)]

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        resumed_watch = await session.get(Watch, watch_id)
        assert account is not None
        assert account.last_auth_status == "authenticated"
        assert account.last_authenticated_at is not None
        assert resumed_watch is not None
        assert resumed_watch.status is WatchStatus.SCHEDULED


async def test_ready_srt_provider_blocked_session_is_reconciled_without_relogin(app) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "0987654321",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=1,
                last_auth_status="provider_blocked",
                updated_at=now,
            )
        )
        watch = Watch(
            provider=Provider.SRT,
            origin="대전",
            destination="수서",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(21),
            time_to=time(23, 30),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="runtime-srt-provider-blocked-recovery",
        )
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.RESERVING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="reservation_provider_blocked",
                created_at=now,
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.SRT: ProviderSessionRuntimeSnapshot(
                provider=Provider.SRT,
                state=ProviderSessionRuntimeState.READY,
                credential_generation="1",
                created_age_seconds=15.0,
                last_verified_age_seconds=2.0,
                last_used_age_seconds=1.0,
                local_reuse_remaining_seconds=240.0,
                locally_reusable=True,
            )
        }
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 1
    )
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )
    assert verifier.prewarm_calls == []

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        resumed_watch = await session.get(Watch, watch_id)
        assert account is not None
        assert account.last_auth_status == "authenticated"
        assert account.last_authenticated_at is not None
        assert resumed_watch is not None
        assert resumed_watch.status is WatchStatus.SCHEDULED
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch_id)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(1)
        )
        assert latest_transition is not None
        assert latest_transition.reason == "provider_login_reverified_after_provider_block"


async def test_provider_blocked_observation_watch_resumes_with_an_immediate_due_check(
    app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    deferred_until = now + timedelta(minutes=5)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="provider_blocked",
                updated_at=now,
            )
        )
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            destination="서울",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.NOTIFY_ONLY,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="runtime-korail-provider-blocked-observation-recovery",
            cooldown_until=deferred_until,
            next_check_at=deferred_until,
        )
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.WATCHING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="provider_account_provider_blocked_before_observation",
                created_at=now,
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    monkeypatch.setattr(
        "rail_waitlist.watch_management.transition_runtime.get_execution_provider",
        lambda _provider: SimpleNamespace(
            capabilities=lambda: SimpleNamespace(seat_monitoring=True)
        ),
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            StubRuntimeVerifier(),
            registry,
        )
        == 1
    )

    async with app.state.test_session_factory() as session:
        resumed_watch = await session.get(Watch, watch_id)
        assert resumed_watch is not None
        assert resumed_watch.status is WatchStatus.SCHEDULED
        assert resumed_watch.cooldown_until is None
        assert resumed_watch.next_check_at is not None
        assert resumed_watch.next_check_at.replace(tzinfo=UTC) < deferred_until
        latest_transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(WatchTransitionHistory.watch_id == watch_id)
            .order_by(WatchTransitionHistory.created_at.desc())
            .limit(1)
        )
        assert latest_transition is not None
        assert latest_transition.reason == "provider_login_reverified_before_observation"


async def test_blocked_srt_session_reverification_is_bounded_per_revision(app) -> None:
    first_revision_at = datetime.now(UTC) - timedelta(minutes=1)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.SRT,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "0987654321",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=1,
                last_auth_status="provider_blocked",
                updated_at=first_revision_at,
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        outcomes={Provider.SRT: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={
            Provider.SRT: ProviderSessionRuntimeSnapshot(
                provider=Provider.SRT,
                state=ProviderSessionRuntimeState.BLOCKED,
                credential_generation="1",
                created_age_seconds=15.0,
                last_verified_age_seconds=None,
                last_used_age_seconds=1.0,
                local_reuse_remaining_seconds=None,
                locally_reusable=False,
            )
        },
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )
    retry = registry.prewarm_retry_state[Provider.SRT]
    assert retry[0] == 1
    assert retry[1] == 1
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )
    assert registry.prewarm_retry_state[Provider.SRT] == retry
    registry.prewarm_retry_state[Provider.SRT] = (retry[0], retry[1], float("-inf"))
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 1
    )
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )
    assert verifier.prewarm_calls == [(Provider.SRT, 1)]

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.SRT)
        )
        assert account is not None
        assert account.last_auth_status == "provider_blocked"


async def test_failed_later_auth_recovery_is_not_repeated_until_new_revision(app) -> None:
    first_revision_at = datetime.now(UTC) - timedelta(minutes=1)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=9,
                last_auth_status="auth_required",
                updated_at=first_revision_at,
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.AUTH_REQUIRED}
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 1
    )
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        account.updated_at = datetime.now(UTC)
        await session.commit()

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 1
    )
    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 0
    )
    assert verifier.prewarm_calls == [
        (Provider.KORAIL, 9),
        (Provider.KORAIL, 9),
    ]
    assert len(registry.auth_revision_attempts) == 1


async def test_adapter_outage_does_not_consume_the_auth_recovery_budget(app) -> None:
    await _seed_account(app, auth_status="auth_required")

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.FAILED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    for _ in range(6):
        assert (
            await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
            == 1
        )
        _clear_backoff(registry)

    # A transport failure never reached the credential check, so it must keep retrying
    # instead of stranding the account until an operator re-saves the credential.
    _revision, started, verdicts = registry.auth_revision_attempts[Provider.KORAIL]
    assert (started, verdicts) == (6, 0)
    assert len(verifier.prewarm_calls) == 6


async def test_local_failure_backoff_recovers_faster_than_a_provider_verdict(app) -> None:
    await _seed_account(app, auth_status="auth_required")

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.FAILED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )

    _generation, failure_count, retry_not_before = registry.prewarm_retry_state[Provider.KORAIL]
    assert failure_count == 1
    assert retry_not_before - asyncio.get_running_loop().time() <= (
        ProviderRuntimePrewarmRegistry.LOCAL_FAILURE_INITIAL_BACKOFF_SECONDS
    )


async def test_auth_required_recovery_stops_after_the_bounded_attempt_budget(app) -> None:
    await _seed_account(app, auth_status="auth_required")

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.AUTH_REQUIRED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    for _ in range(ProviderRuntimePrewarmRegistry.AUTH_RECOVERY_MAX_ATTEMPTS):
        assert (
            await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
            == 1
        )
        _clear_backoff(registry)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    _revision, _started, verdicts = registry.auth_revision_attempts[Provider.KORAIL]
    assert verdicts == ProviderRuntimePrewarmRegistry.AUTH_RECOVERY_MAX_ATTEMPTS
    assert len(verifier.prewarm_calls) == ProviderRuntimePrewarmRegistry.AUTH_RECOVERY_MAX_ATTEMPTS


async def test_recovered_login_resumes_before_the_budget_is_exhausted(app) -> None:
    await _seed_account(app, auth_status="auth_required")

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.AUTH_REQUIRED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    _clear_backoff(registry)
    verifier.outcomes[Provider.KORAIL] = ProviderLoginVerificationOutcome.AUTHENTICATED
    verifier.snapshots[Provider.KORAIL] = _cold_snapshot(Provider.KORAIL)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    assert registry.outcome_for(Provider.KORAIL) == "authenticated"

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        assert account.last_auth_status == "authenticated"


async def test_blocked_revision_keeps_a_single_recovery_attempt(app) -> None:
    await _seed_account(app, auth_status="provider_blocked")

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    # The protection cooldown is observed before the revision's single login attempt.
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    assert verifier.prewarm_calls == []

    _clear_backoff(registry)
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )

    _clear_backoff(registry)
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9)]


async def test_authenticated_account_recovers_cold_sidecar_session(app) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="authenticated",
                last_authenticated_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                state=ProviderSessionRuntimeState.COLD,
                credential_generation=None,
                remaining_seconds=None,
                locally_reusable=False,
            )
        }
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            registry,
        )
        == 1
    )
    assert verifier.prewarm_calls == [(Provider.KORAIL, 4)]
    assert registry.outcome_for(Provider.KORAIL) == "authenticated"


@pytest.mark.parametrize(
    ("last_verified_age_seconds", "remaining_seconds", "expected_attempts"),
    # A 1800 second KORAIL reuse window refreshes from 450 seconds before expiry, so a
    # failed attempt still has room to retry. 600 seconds remaining is still outside it.
    [(1_710.0, 90.0, 1), (1_560.0, 240.0, 1), (1_200.0, 600.0, 0)],
)
async def test_authenticated_ready_session_refreshes_only_inside_window(
    app,
    last_verified_age_seconds: float,
    remaining_seconds: float,
    expected_attempts: int,
) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="authenticated",
                last_authenticated_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                remaining_seconds=remaining_seconds,
                last_verified_age_seconds=last_verified_age_seconds,
            )
        }
    )

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            ProviderRuntimePrewarmRegistry(completed=True),
        )
        == expected_attempts
    )
    assert verifier.prewarm_calls == ([(Provider.KORAIL, 4)] if expected_attempts else [])


async def test_failed_keepalive_waits_for_cooldown_before_retry(
    app,
) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="authenticated",
                last_authenticated_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.FAILED},
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                state=ProviderSessionRuntimeState.COLD,
                credential_generation=None,
                remaining_seconds=None,
                locally_reusable=False,
            )
        },
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    generation, failure_count, _retry_not_before = registry.prewarm_retry_state[Provider.KORAIL]
    assert (generation, failure_count) == (4, 1)
    registry.prewarm_retry_state[Provider.KORAIL] = (generation, failure_count, 0.0)
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    assert verifier.prewarm_calls == [
        (Provider.KORAIL, 4),
        (Provider.KORAIL, 4),
    ]


@pytest.mark.parametrize(
    "state",
    [ProviderSessionRuntimeState.AUTHENTICATING, ProviderSessionRuntimeState.BLOCKED],
)
async def test_authenticated_account_does_not_compete_with_active_or_blocked_session(
    app,
    state: ProviderSessionRuntimeState,
) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext=secret_box.encrypt_dict(
                    {
                        "login_method": "membership_number",
                        "login_id": "1234567890",
                        "password": "test-password",
                    }
                ),
                enabled=True,
                credential_version=4,
                last_auth_status="authenticated",
                last_authenticated_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.KORAIL: runtime_snapshot(
                Provider.KORAIL,
                state=state,
                remaining_seconds=None,
                locally_reusable=False,
            )
        }
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    assert verifier.prewarm_calls == []
    if state is ProviderSessionRuntimeState.BLOCKED:
        generation, failure_count, retry_not_before = registry.prewarm_retry_state[Provider.KORAIL]
        assert (generation, failure_count) == (4, 1)
        assert retry_not_before > asyncio.get_running_loop().time()
    else:
        assert registry.prewarm_retry_state == {}


def test_keepalive_backoff_is_generation_scoped_exponential_and_capped() -> None:
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    now = 1_000.0

    for failure_count, expected_delay in enumerate(
        (60.0, 120.0, 240.0, 480.0, 900.0, 900.0),
        start=1,
    ):
        assert registry.begin_prewarm(Provider.KORAIL, 4, now=now)
        registry.finish_prewarm(
            Provider.KORAIL,
            4,
            outcome="auth_required",
            now=now,
        )
        assert registry.prewarm_retry_state[Provider.KORAIL] == (
            4,
            failure_count,
            now + expected_delay,
        )
        now += expected_delay

    assert registry.begin_prewarm(Provider.KORAIL, 5, now=now - 1.0)
    registry.finish_prewarm(
        Provider.KORAIL,
        5,
        outcome="provider_blocked",
        now=now,
    )
    assert registry.prewarm_retry_state[Provider.KORAIL] == (5, 1, now + 900.0)


def test_local_failure_backoff_is_short_and_separately_capped() -> None:
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    now = 1_000.0

    # An unreachable adapter never reaches the credential check, so its retry schedule
    # stays short enough for the session to recover as soon as the adapter is back.
    for failure_count, expected_delay in enumerate(
        (5.0, 10.0, 20.0, 40.0, 60.0, 60.0),
        start=1,
    ):
        assert registry.begin_prewarm(Provider.KORAIL, 4, now=now)
        registry.finish_prewarm(
            Provider.KORAIL,
            4,
            outcome="failed",
            now=now,
        )
        assert registry.prewarm_retry_state[Provider.KORAIL] == (
            4,
            failure_count,
            now + expected_delay,
        )
        now += expected_delay


def test_session_refresh_threshold_is_korail_scoped() -> None:
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    # A 30 minute KORAIL window starts refreshing 7.5 minutes before expiry so a failed
    # attempt still leaves room to retry before the session is gone.
    assert registry.session_refresh_threshold_seconds(Provider.KORAIL, 1_350.0, 450.0) == 450.0
    assert registry.session_refresh_threshold_seconds(Provider.KORAIL, 0.0, 10_000.0) == 600.0
    assert registry.session_refresh_threshold_seconds(Provider.KORAIL, None, 450.0) == 120.0
    # An unusually short configured window must not produce a threshold that refreshes on
    # every tick.
    assert registry.session_refresh_threshold_seconds(Provider.KORAIL, 100.0, 100.0) == 100.0

    # SRT anchors its reuse deadline on last_used_at and its reusing prewarm never
    # refreshes last_verified_at, so the derived window would grow without bound.
    assert registry.session_refresh_threshold_seconds(Provider.SRT, 1_350.0, 450.0) == 120.0
    assert registry.session_refresh_threshold_seconds(Provider.SRT, 5_000.0, 240.0) == 120.0


async def test_srt_long_lived_session_keeps_the_fixed_refresh_window(app) -> None:
    """A stale SRT last_verified_at must not turn every tick into a login."""

    await _seed_account(
        app,
        auth_status="authenticated",
        provider=Provider.SRT,
        credential_version=4,
        login_id="0987654321",
        updated_at=datetime.now(UTC),
    )

    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.SRT: runtime_snapshot(
                Provider.SRT,
                remaining_seconds=240.0,
                last_verified_age_seconds=5_000.0,
            )
        }
    )

    assert (
        await recover_provider_sessions_once(
            app.state.test_session_factory,
            verifier,
            ProviderRuntimePrewarmRegistry(completed=True),
        )
        == 0
    )
    assert verifier.prewarm_calls == []


async def test_maintenance_tick_failure_is_redacted_and_does_not_stop_manager(
    app,
    monkeypatch,
) -> None:
    calls = 0

    async def recover_once(*_args, **_kwargs) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("sensitive upstream response")
        raise asyncio.CancelledError

    monkeypatch.setattr(
        provider_runtime_module,
        "recover_provider_sessions_once",
        recover_once,
    )

    with pytest.raises(asyncio.CancelledError):
        await maintain_provider_sessions(
            app.state.test_session_factory,
            StubRuntimeVerifier(),
            ProviderRuntimePrewarmRegistry(completed=True),
            interval_seconds=0,
        )

    assert calls == 2


async def test_runtime_status_api_is_secret_free_and_reports_local_reuse(
    app,
    client,
) -> None:
    app.state.provider_login_verifier = StubRuntimeVerifier()
    app.state.provider_runtime_prewarm_registry = ProviderRuntimePrewarmRegistry(
        outcomes={
            Provider.KORAIL: "authenticated",
            Provider.SRT: "authenticated",
        },
        completed=True,
    )

    response = await client.get("/api/v1/provider-runtime-status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert [item["provider"] for item in payload] == ["korail", "srt"]
    assert all(item["state"] == "ready" for item in payload)
    assert all(item["locally_reusable"] for item in payload)
    assert all(item["local_reuse_remaining_seconds"] == 240.0 for item in payload)
    serialized = response.text.casefold()
    assert "password" not in serialized
    assert "cookie" not in serialized
    assert "token" not in serialized
