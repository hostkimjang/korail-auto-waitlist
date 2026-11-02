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
from rail_waitlist.provider_account_management.application import (
    clear_provider_protection_halt,
    record_provider_protection_halt,
)
from rail_waitlist.provider_account_management.models import auth_revision_micros
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
    verifications: dict[Provider, ProviderLoginVerification] = field(default_factory=dict)

    async def prewarm(self, provider, credentials):
        self.prewarm_calls.append((provider, credentials.credential_version))
        if provider in self.verifications:
            return self.verifications[provider]
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


async def test_official_post_failure_waits_without_spending_credential_verdict_budget(app) -> None:
    await _seed_account(app, auth_status="auth_required")
    verifier = StubRuntimeVerifier(
        verifications={
            Provider.KORAIL: ProviderLoginVerification(
                ProviderLoginVerificationOutcome.FAILED,
                failure_kind="provider_submission_failed",
                retry_after_seconds=300,
            )
        },
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    assert registry.outcome_for(Provider.KORAIL) == "failed"
    assert registry.provider_submission_failures == {Provider.KORAIL: (9, 300)}
    retry = registry.prewarm_retry_state[Provider.KORAIL]
    assert 299 <= retry[2] - asyncio.get_running_loop().time() <= 300
    assert registry.auth_revision_attempts[Provider.KORAIL][1:] == (1, 0)

    # A newly persisted auth-required revision normally gets an immediate attempt.
    # The same-generation official POST failure must retain its provider retry deadline.
    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        account.updated_at = datetime.now(UTC)
        await session.commit()
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9)]

    # An unreachable local adapter after the deadline gets the fast local schedule.
    _clear_backoff(registry)
    verifier.verifications.clear()
    verifier.outcomes[Provider.KORAIL] = ProviderLoginVerificationOutcome.FAILED
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    assert registry.provider_submission_failures == {}
    generation, count, deadline = registry.prewarm_retry_state[Provider.KORAIL]
    assert (generation, count) == (9, 1)
    assert 4 <= deadline - asyncio.get_running_loop().time() <= 5


@pytest.mark.parametrize("prior_failure", [False, True])
async def test_provider_deferral_preserves_outcome_submission_state_and_revision_budget(
    app, prior_failure
) -> None:
    await _seed_account(app, auth_status="auth_required")
    verifier = StubRuntimeVerifier(
        verifications={
            Provider.KORAIL: ProviderLoginVerification(
                ProviderLoginVerificationOutcome.FAILED,
                failure_kind="provider_cooldown",
                cooldown_reason="provider_unavailable",
                retry_after_seconds=800,
            )
        },
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    registry.outcomes[Provider.KORAIL] = "failed"
    if prior_failure:
        registry.record_submission_failure(Provider.KORAIL, 9, retry_after_seconds=300)
        registry.prewarm_retry_state[Provider.KORAIL] = (9, 2, 0)
    failures = dict(registry.provider_submission_failures)
    retries = dict(registry.prewarm_retry_state)

    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    assert registry.provider_submission_failures == failures
    assert registry.prewarm_retry_state == retries
    assert registry.auth_revision_attempts == {}
    assert registry.prewarm_in_flight == set()
    assert registry.outcome_for(Provider.KORAIL) == "failed"
    assert (
        799
        <= registry.provider_deferred_until[Provider.KORAIL] - (asyncio.get_running_loop().time())
        <= 800
    )
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9)]
    async with app.state.test_session_factory() as session:
        account = await session.scalar(select(RailProviderAccount))
        assert account.last_auth_status == "auth_required"
        assert account.last_authenticated_at is None


def test_provider_deferred_deadline_survives_generation_change_without_advancing_failure() -> None:
    registry = ProviderRuntimePrewarmRegistry()
    registry.defer_prewarm(Provider.KORAIL, retry_after_seconds=900, now=1000)
    registry.defer_prewarm(Provider.KORAIL, retry_after_seconds=1, now=1001)
    assert not registry.begin_prewarm(Provider.KORAIL, 10, now=1500, bypass_backoff=True)
    assert registry.provider_deferred_until == {Provider.KORAIL: 1900}
    assert registry.prewarm_retry_state == {}
    assert registry.auth_revision_attempts == {}
    assert registry.begin_prewarm(Provider.SRT, 10, now=1500)
    assert registry.begin_prewarm(Provider.KORAIL, 10, now=1900)


def test_submission_failure_backoff_is_bounded_and_cannot_be_bypassed() -> None:
    registry = ProviderRuntimePrewarmRegistry()
    now = 1000.0
    for count, delay in enumerate((300.0, 600.0, 900.0, 900.0), start=1):
        assert registry.begin_prewarm(Provider.KORAIL, 4, now=now)
        registry.record_submission_failure(Provider.KORAIL, 4, retry_after_seconds=300)
        registry.finish_prewarm(Provider.KORAIL, 4, outcome="failed", now=now)
        assert registry.prewarm_retry_state[Provider.KORAIL] == (4, count, now + delay)
        assert not registry.begin_prewarm(
            Provider.KORAIL, 4, now=now + delay - 1, bypass_backoff=True
        )
        now += delay

    assert registry.begin_prewarm(Provider.KORAIL, 5, now=now - 1)
    assert registry.provider_submission_failures == {}
    registry.finish_prewarm(Provider.KORAIL, 5, outcome="failed", now=now)
    assert registry.prewarm_retry_state[Provider.KORAIL] == (5, 1, now + 5)


def test_long_official_submission_outage_keeps_the_retry_ceiling() -> None:
    registry = ProviderRuntimePrewarmRegistry()
    registry.record_submission_failure(Provider.KORAIL, 4, retry_after_seconds=300)
    registry.prewarm_retry_state[Provider.KORAIL] = (4, 100_000, 1000)

    registry.finish_prewarm(Provider.KORAIL, 4, outcome="failed", now=1000)

    assert registry.prewarm_retry_state[Provider.KORAIL] == (4, 100_001, 1900)


@pytest.mark.parametrize("cleanup", ["success", "forget", "local_failure", "provider_verdict"])
def test_submission_failure_metadata_is_cleared_on_state_change(cleanup: str) -> None:
    registry = ProviderRuntimePrewarmRegistry()
    registry.record_submission_failure(Provider.KORAIL, 4, retry_after_seconds=300)
    registry.finish_prewarm(Provider.KORAIL, 4, outcome="failed", now=1000)
    if cleanup == "forget":
        registry.forget_provider(Provider.KORAIL)
    elif cleanup == "local_failure":
        registry.record_submission_failure(Provider.KORAIL, 4, retry_after_seconds=None)
        registry.finish_prewarm(Provider.KORAIL, 4, outcome="failed", now=1000)
        assert registry.prewarm_retry_state[Provider.KORAIL] == (4, 1, 1005)
    else:
        registry.finish_prewarm(
            Provider.KORAIL,
            4,
            outcome="authenticated" if cleanup == "success" else "auth_required",
            now=1000,
        )
    assert registry.provider_submission_failures == {}


def test_korail_submission_failure_does_not_change_srt_or_local_retry_policy() -> None:
    registry = ProviderRuntimePrewarmRegistry()
    registry.finish_prewarm(Provider.KORAIL, 4, outcome="failed", now=1000)
    registry.record_submission_failure(Provider.KORAIL, 4, retry_after_seconds=300)
    registry.finish_prewarm(Provider.KORAIL, 4, outcome="failed", now=1005)
    assert registry.prewarm_retry_state[Provider.KORAIL] == (4, 1, 1305)
    registry.record_submission_failure(Provider.SRT, 4, retry_after_seconds=300)
    registry.finish_prewarm(Provider.SRT, 4, outcome="failed", now=1000)
    assert registry.prewarm_retry_state[Provider.SRT] == (4, 1, 1005)
    assert registry.provider_submission_failures == {Provider.KORAIL: (4, 300)}


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


async def test_blocked_revision_retries_after_each_protection_backoff(app) -> None:
    await _seed_account(app, auth_status="provider_blocked")

    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    # The protection backoff is observed before the first recovery login.
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

    # A repeated verdict waits again instead of retrying on the next tick ...
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 0
    )
    # ... and retries once that backoff has elapsed instead of stopping for good.
    _clear_backoff(registry)
    assert (
        await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)
        == 1
    )
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, 9)]


async def _tick(app, verifier, registry) -> int:
    return await recover_provider_sessions_once(app.state.test_session_factory, verifier, registry)


async def test_authenticated_revision_paces_keepalive_after_protection_verdict(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert await _tick(app, verifier, registry) == 1
    # The first protection backoff waits 30 minutes and is scoped to this generation.
    generation, _count, retry_not_before = registry.prewarm_retry_state[Provider.KORAIL]
    assert generation == 9
    assert 1790 < retry_not_before - asyncio.get_running_loop().time() <= 1800

    # The halted revision never retries inside the backoff, whether the sidecar reports
    # the block or a cold actor, and retries once the backoff has elapsed.
    for snapshot in (
        runtime_snapshot(
            Provider.KORAIL,
            state=ProviderSessionRuntimeState.BLOCKED,
            remaining_seconds=None,
            locally_reusable=False,
        ),
        _cold_snapshot(Provider.KORAIL),
    ):
        verifier.snapshots[Provider.KORAIL] = snapshot
        assert await _tick(app, verifier, registry) == 0
        _clear_backoff(registry)
        assert await _tick(app, verifier, registry) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9)] * 3
    # Each repeated verdict escalates the wait toward the protection ceiling.
    _generation, failure_count, _retry = registry.prewarm_retry_state[Provider.KORAIL]
    assert failure_count == 3

    async with app.state.test_session_factory() as session:
        account = await session.scalar(select(RailProviderAccount))
        assert account is not None
        assert account.last_auth_status == "authenticated"


@pytest.mark.parametrize("change", ["credential_generation", "auth_revision"])
async def test_protection_halt_lifts_on_new_generation_or_revision(app, change: str) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, registry) == 1

    async with app.state.test_session_factory() as session:
        account = await session.scalar(select(RailProviderAccount))
        assert account is not None
        if change == "credential_generation":
            account.credential_version = 10
        else:
            account.updated_at = datetime.now(UTC)
        await session.commit()
    expected_version = 10 if change == "credential_generation" else 9
    _clear_backoff(registry, credential_version=expected_version)

    assert await _tick(app, verifier, registry) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, expected_version)]


async def test_positive_sidecar_session_lifts_protection_halt(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, registry) == 1

    verifier.snapshots[Provider.KORAIL] = runtime_snapshot(
        Provider.KORAIL, credential_generation="9", remaining_seconds=1_500.0
    )
    assert await _tick(app, verifier, registry) == 0
    assert registry.outcome_for(Provider.KORAIL) == "authenticated"
    assert registry.protection_halted_revisions == {}

    # Once the official session lapses, ordinary keepalive recovery resumes.
    verifier.snapshots[Provider.KORAIL] = _cold_snapshot(Provider.KORAIL)
    verifier.outcomes[Provider.KORAIL] = ProviderLoginVerificationOutcome.AUTHENTICATED
    assert await _tick(app, verifier, registry) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, 9)]


@pytest.mark.parametrize(
    "outcome",
    [ProviderLoginVerificationOutcome.FAILED, ProviderLoginVerificationOutcome.AUTH_REQUIRED],
)
async def test_non_protection_keepalive_failures_keep_retrying(
    app, outcome: ProviderLoginVerificationOutcome
) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: outcome},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)

    assert await _tick(app, verifier, registry) == 1
    _clear_backoff(registry)
    assert await _tick(app, verifier, registry) == 1
    assert registry.protection_halted_revisions == {}


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
    # KORAIL scores login submissions by spacing, so a persistent block settles at one
    # login every two hours instead of several per hour. The first retry waits 30 minutes
    # because 15-minute retries after a verdict were blocked again in operation.
    for failure_count, expected_delay in enumerate(
        (1_800.0, 3_600.0, 7_200.0, 7_200.0),
        start=1,
    ):
        registry.finish_prewarm(
            Provider.KORAIL,
            5,
            outcome="provider_blocked",
            now=now,
        )
        assert registry.prewarm_retry_state[Provider.KORAIL] == (
            5,
            failure_count,
            now + expected_delay,
        )
        now += expected_delay


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
    # The two hour maximum window refreshes 10 minutes before expiry, so keepalive logins
    # stay about 110 minutes apart.
    assert registry.session_refresh_threshold_seconds(Provider.KORAIL, 6_600.0, 600.0) == 600.0
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


async def _halt_row(app):
    async with app.state.test_session_factory() as session:
        return (
            await session.execute(
                select(
                    RailProviderAccount.credential_version,
                    RailProviderAccount.updated_at,
                    RailProviderAccount.last_auth_status,
                    RailProviderAccount.protection_halt_credential_version,
                    RailProviderAccount.protection_halt_revision_micros,
                ).where(RailProviderAccount.provider == Provider.KORAIL)
            )
        ).first()


async def test_keepalive_protection_verdict_persists_halt_across_restart(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, registry) == 1

    row = await _halt_row(app)
    assert row is not None
    assert row.last_auth_status == "authenticated"
    assert row.protection_halt_credential_version == 9
    assert row.protection_halt_revision_micros is not None

    # A fresh process (empty registry) must rehydrate the persisted fence and wait out
    # the protection backoff before its next login on the same revision.
    restarted = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, restarted) == 0
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9)]
    _clear_backoff(restarted)
    assert await _tick(app, verifier, restarted) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, 9)]

    row = await _halt_row(app)
    assert row is not None
    assert row.protection_halt_credential_version == 9
    assert row.last_auth_status == "authenticated"


async def test_halted_keepalive_retry_success_lifts_halt_and_resumes_watch(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, registry) == 1

    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="서울",
            destination="부산",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.NOTIFY_ONLY,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="halt-retry-resume",
        )
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.WATCHING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="provider_account_provider_blocked_before_observation",
                created_at=now - timedelta(minutes=1),
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    # Once the protection backoff elapses, the automatic retry logs in successfully.
    verifier.outcomes[Provider.KORAIL] = ProviderLoginVerificationOutcome.AUTHENTICATED
    _clear_backoff(registry)
    assert await _tick(app, verifier, registry) == 1
    assert registry.outcome_for(Provider.KORAIL) == "authenticated"

    row = await _halt_row(app)
    assert row is not None
    assert row.protection_halt_credential_version is None
    assert row.protection_halt_revision_micros is None
    async with app.state.test_session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.status is WatchStatus.SCHEDULED


async def test_official_positive_session_clears_persisted_halt(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, registry) == 1
    before = await _halt_row(app)
    assert before is not None and before.protection_halt_credential_version == 9

    # A watch parked by the pre-observation hold must resume with the clear.
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="서울",
            destination="부산",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=time(9),
            time_to=time(12),
            passenger_count=1,
            mode="official",
            reservation_policy=ReservationPolicy.NOTIFY_ONLY,
            status=WatchStatus.AUTH_REQUIRED,
            dedupe_key="halt-clear-resume",
        )
        watch.transition_history.append(
            WatchTransitionHistory(
                from_status=WatchStatus.WATCHING,
                to_status=WatchStatus.AUTH_REQUIRED,
                reason="provider_account_provider_blocked_before_observation",
                created_at=now - timedelta(minutes=1),
            )
        )
        session.add(watch)
        await session.commit()
        watch_id = watch.id

    restarted = ProviderRuntimePrewarmRegistry(completed=True)
    verifier.snapshots[Provider.KORAIL] = runtime_snapshot(
        Provider.KORAIL,
        credential_generation="9",
        remaining_seconds=1_500.0,
    )
    assert await _tick(app, verifier, restarted) == 0
    assert restarted.outcome_for(Provider.KORAIL) == "authenticated"

    after = await _halt_row(app)
    assert after is not None
    assert after.protection_halt_credential_version is None
    assert after.protection_halt_revision_micros is None
    # The positive evidence stands on the same revision; clearing must not rotate it.
    assert after.updated_at == before.updated_at
    assert after.last_auth_status == "authenticated"
    async with app.state.test_session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        assert watch.status is WatchStatus.SCHEDULED


async def test_persisted_halt_expires_when_row_revision_rotates(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, registry) == 1
    row = await _halt_row(app)
    assert row is not None and row.protection_halt_revision_micros is not None

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        account.updated_at = datetime.now(UTC) + timedelta(seconds=1)
        await session.commit()

    restarted = ProviderRuntimePrewarmRegistry(completed=True)
    assert await _tick(app, verifier, restarted) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, 9)]


async def test_protection_halt_persistence_helpers_use_revision_compare_and_swap(
    app,
) -> None:
    await _seed_account(app, auth_status="authenticated")
    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(RailProviderAccount.provider == Provider.KORAIL)
        )
        assert account is not None
        stored_updated_at = account.updated_at

    stale_micros = auth_revision_micros(stored_updated_at) - 1
    async with app.state.test_session_factory() as session:
        recorded = await record_provider_protection_halt(
            session,
            Provider.KORAIL,
            expected_credential_version=9,
            expected_revision_micros=stale_micros,
        )
    assert recorded is False
    row = await _halt_row(app)
    assert row is not None and row.protection_halt_credential_version is None

    async with app.state.test_session_factory() as session:
        recorded = await record_provider_protection_halt(
            session,
            Provider.KORAIL,
            expected_credential_version=9,
            expected_revision_micros=auth_revision_micros(stored_updated_at),
        )
    assert recorded is True
    row = await _halt_row(app)
    assert row is not None
    assert row.protection_halt_credential_version == 9
    assert row.updated_at == stored_updated_at

    async with app.state.test_session_factory() as session:
        cleared = await clear_provider_protection_halt(
            session,
            Provider.KORAIL,
            expected_credential_version=9,
            expected_revision_micros=auth_revision_micros(stored_updated_at) + 1,
            expected_updated_at=stored_updated_at,
        )
    assert cleared is False
    async with app.state.test_session_factory() as session:
        cleared = await clear_provider_protection_halt(
            session,
            Provider.KORAIL,
            expected_credential_version=9,
            expected_revision_micros=auth_revision_micros(stored_updated_at),
            expected_updated_at=stored_updated_at,
        )
    assert cleared is True
    row = await _halt_row(app)
    assert row is not None
    assert row.protection_halt_credential_version is None
    assert row.updated_at == stored_updated_at


async def _seed_watch(
    app,
    *,
    policy: ReservationPolicy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
    status: WatchStatus = WatchStatus.WATCHING,
    provider: Provider = Provider.KORAIL,
) -> None:
    now = datetime.now(UTC)
    async with app.state.test_session_factory() as session:
        session.add(
            Watch(
                provider=provider,
                origin="서울",
                destination="부산",
                travel_date=(now + timedelta(days=1)).date(),
                time_from=time(9),
                time_to=time(12),
                passenger_count=1,
                mode="official",
                reservation_policy=policy,
                status=status,
                dedupe_key=f"keepalive-{policy.value}-{status.value}",
            )
        )
        await session.commit()


def _idle_ready_snapshot(last_verified_age_seconds: float) -> ProviderSessionRuntimeSnapshot:
    # A two hour local reuse window leaves far more than the 600 second refresh threshold,
    # so only the keepalive interval can make the manager touch this session.
    return runtime_snapshot(
        Provider.KORAIL,
        credential_generation="9",
        remaining_seconds=7_200.0 - last_verified_age_seconds,
        last_verified_age_seconds=last_verified_age_seconds,
    )


@pytest.mark.parametrize(
    ("keepalive_seconds", "last_verified_age_seconds", "policy", "status", "expected_attempts"),
    [
        (900, 900.0, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT, WatchStatus.WATCHING, 1),
        (900, 1_200.0, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT, WatchStatus.COOLDOWN, 1),
        (900, 899.0, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT, WatchStatus.WATCHING, 0),
        (900, 1_200.0, ReservationPolicy.NOTIFY_ONLY, WatchStatus.WATCHING, 0),
        (900, 1_200.0, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT, WatchStatus.PAUSED, 0),
        (900, 1_200.0, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT, WatchStatus.COMPLETED, 0),
        (0, 1_200.0, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT, WatchStatus.WATCHING, 0),
    ],
)
async def test_idle_korail_session_is_probed_only_for_auto_reservation_demand(
    app,
    keepalive_seconds: int,
    last_verified_age_seconds: float,
    policy: ReservationPolicy,
    status: WatchStatus,
    expected_attempts: int,
) -> None:
    await _seed_account(app, auth_status="authenticated")
    await _seed_watch(app, policy=policy, status=status)
    verifier = StubRuntimeVerifier(
        snapshots={Provider.KORAIL: _idle_ready_snapshot(last_verified_age_seconds)}
    )
    registry = ProviderRuntimePrewarmRegistry(
        completed=True, korail_session_keepalive_seconds=keepalive_seconds
    )

    assert await _tick(app, verifier, registry) == expected_attempts
    assert verifier.prewarm_calls == ([(Provider.KORAIL, 9)] if expected_attempts else [])
    assert registry.outcome_for(Provider.KORAIL) == "authenticated"
    # A positive probe resets last_verified, so the next tick leaves the session alone.
    assert await _tick(app, verifier, registry) == 0
    assert len(verifier.prewarm_calls) == expected_attempts


async def test_srt_session_is_never_probed_by_the_korail_keepalive(app) -> None:
    await _seed_account(
        app, auth_status="authenticated", provider=Provider.SRT, login_id="0987654321"
    )
    await _seed_watch(app, provider=Provider.SRT)
    verifier = StubRuntimeVerifier(
        snapshots={
            Provider.SRT: runtime_snapshot(
                Provider.SRT,
                credential_generation="9",
                remaining_seconds=6_000.0,
                last_verified_age_seconds=5_000.0,
            )
        }
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True, korail_session_keepalive_seconds=900)

    assert await _tick(app, verifier, registry) == 0
    assert verifier.prewarm_calls == []


async def test_failed_keepalive_probe_keeps_its_backoff_while_session_reads_ready(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    await _seed_watch(app)
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.FAILED},
        snapshots={Provider.KORAIL: _idle_ready_snapshot(1_200.0)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True, korail_session_keepalive_seconds=900)

    assert await _tick(app, verifier, registry) == 1
    # The snapshot still reads READY; the ready branch must not erase the retry clock
    # and turn an unreachable adapter into a probe on every 10 second tick.
    assert await _tick(app, verifier, registry) == 0
    generation, failure_count, _retry_not_before = registry.prewarm_retry_state[Provider.KORAIL]
    assert (generation, failure_count) == (9, 1)

    _clear_backoff(registry)
    assert await _tick(app, verifier, registry) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, 9)]


async def test_failed_watch_lookup_keeps_the_failed_probe_backoff(
    app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_account(app, auth_status="authenticated")
    await _seed_watch(app)
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.FAILED},
        snapshots={Provider.KORAIL: _idle_ready_snapshot(1_200.0)},
    )
    registry = ProviderRuntimePrewarmRegistry(completed=True, korail_session_keepalive_seconds=900)
    assert await _tick(app, verifier, registry) == 1
    failed_probe_retry = registry.prewarm_retry_state[Provider.KORAIL]

    lookup = provider_runtime_module._has_session_keepalive_demand

    def unavailable_database():
        raise RuntimeError("database unavailable")

    async def lookup_with_unavailable_database(_session_factory, provider):
        return await lookup(unavailable_database, provider)

    monkeypatch.setattr(
        provider_runtime_module,
        "_has_session_keepalive_demand",
        lookup_with_unavailable_database,
    )
    # The failed lookup skips this tick's probe without erasing the failed probe's clock.
    assert await _tick(app, verifier, registry) == 0
    assert registry.prewarm_retry_state[Provider.KORAIL] == failed_probe_retry

    monkeypatch.undo()
    # Once the lookup recovers, the next probe still waits out that backoff.
    assert await _tick(app, verifier, registry) == 0
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9)]


async def test_keepalive_on_a_halted_revision_clears_the_halt_and_probes(app) -> None:
    await _seed_account(app, auth_status="authenticated")
    verifier = StubRuntimeVerifier(
        outcomes={Provider.KORAIL: ProviderLoginVerificationOutcome.PROVIDER_BLOCKED},
        snapshots={Provider.KORAIL: _cold_snapshot(Provider.KORAIL)},
    )
    assert await _tick(app, verifier, ProviderRuntimePrewarmRegistry(completed=True)) == 1
    halted = await _halt_row(app)
    assert halted is not None and halted.protection_halt_credential_version == 9

    await _seed_watch(app)
    verifier.outcomes[Provider.KORAIL] = ProviderLoginVerificationOutcome.AUTHENTICATED
    verifier.snapshots[Provider.KORAIL] = _idle_ready_snapshot(1_200.0)
    restarted = ProviderRuntimePrewarmRegistry(completed=True, korail_session_keepalive_seconds=900)

    # A locally reusable session of this generation lifts the halt as before, and the due
    # keepalive is not parked behind the protection backoff the halt would otherwise start.
    assert await _tick(app, verifier, restarted) == 1
    assert verifier.prewarm_calls == [(Provider.KORAIL, 9), (Provider.KORAIL, 9)]
    assert restarted.protection_halted_revisions == {}
    cleared = await _halt_row(app)
    assert cleared is not None and cleared.protection_halt_credential_version is None


@pytest.mark.parametrize("value", [-1, 1, 299, 3_601])
def test_keepalive_interval_rejects_values_outside_documented_range(value: int) -> None:
    with pytest.raises(ValueError, match="KORAIL_SESSION_KEEPALIVE_SECONDS"):
        ProviderRuntimePrewarmRegistry(korail_session_keepalive_seconds=value)


def test_keepalive_due_is_korail_scoped_and_needs_a_verified_age() -> None:
    registry = ProviderRuntimePrewarmRegistry(korail_session_keepalive_seconds=900)

    assert registry.session_keepalive_due(Provider.KORAIL, 900.0) is True
    assert registry.session_keepalive_due(Provider.KORAIL, 899.9) is False
    assert registry.session_keepalive_due(Provider.KORAIL, None) is False
    assert registry.session_keepalive_due(Provider.SRT, 10_000.0) is False
    assert (
        ProviderRuntimePrewarmRegistry().session_keepalive_due(Provider.KORAIL, 10_000.0) is False
    )
