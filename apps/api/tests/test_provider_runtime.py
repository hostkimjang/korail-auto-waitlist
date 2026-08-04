from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select

import rail_waitlist.provider_runtime as provider_runtime_module
from rail_waitlist.domain import Provider, ReservationPolicy, WatchStatus
from rail_waitlist.models import RailProviderAccount, Watch, WatchTransitionHistory
from rail_waitlist.provider_login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
    ProviderSessionRuntimeSnapshot,
    ProviderSessionRuntimeState,
)
from rail_waitlist.provider_runtime import (
    ProviderRuntimePrewarmRegistry,
    maintain_provider_sessions,
    prewarm_provider_sessions,
    recover_provider_sessions_once,
)
from rail_waitlist.security import secret_box


@dataclass
class StubRuntimeVerifier:
    prewarm_calls: list[tuple[Provider, int]] = field(default_factory=list)
    outcomes: dict[Provider, ProviderLoginVerificationOutcome] = field(default_factory=dict)
    snapshots: dict[Provider, ProviderSessionRuntimeSnapshot] = field(default_factory=dict)

    async def prewarm(self, provider, credentials):
        self.prewarm_calls.append((provider, credentials.credential_version))
        return ProviderLoginVerification(
            self.outcomes.get(provider, ProviderLoginVerificationOutcome.AUTHENTICATED)
        )

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

    verifier = StubRuntimeVerifier()
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
            for account in (
                await session.scalars(select(RailProviderAccount))
            ).all()
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

    verifier = StubRuntimeVerifier(outcomes={Provider.KORAIL: outcome})
    registry = ProviderRuntimePrewarmRegistry()
    await prewarm_provider_sessions(app.state.test_session_factory, verifier, registry)

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == Provider.KORAIL
            )
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
                select(RailProviderAccount).where(
                    RailProviderAccount.provider == provider
                )
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
        session_factory=app.state.test_session_factory
    )
    registry = ProviderRuntimePrewarmRegistry()
    await prewarm_provider_sessions(app.state.test_session_factory, verifier, registry)

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == Provider.KORAIL
            )
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

    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 1
    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 0
    assert verifier.prewarm_calls == [(Provider.KORAIL, 7)]

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == Provider.KORAIL
            )
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

    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 1
    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 0
    assert verifier.prewarm_calls == []

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == Provider.SRT
            )
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

    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 1
    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 0
    assert verifier.prewarm_calls == [(Provider.SRT, 1)]

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == Provider.SRT
            )
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

    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 1
    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 0

    async with app.state.test_session_factory() as session:
        account = await session.scalar(
            select(RailProviderAccount).where(
                RailProviderAccount.provider == Provider.KORAIL
            )
        )
        assert account is not None
        account.updated_at = datetime.now(UTC)
        await session.commit()

    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 1
    assert await recover_provider_sessions_once(
        app.state.test_session_factory,
        verifier,
        registry,
    ) == 0
    assert verifier.prewarm_calls == [
        (Provider.KORAIL, 9),
        (Provider.KORAIL, 9),
    ]
    assert len(registry.attempted_auth_revisions) == 1


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
