from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import Provider
from rail_waitlist.provider_execution import lease_application as lease_application_module
from rail_waitlist.provider_execution.contracts import ExecutionLeaseGrant
from rail_waitlist.provider_execution.lease_application import (
    ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
    PROVIDER_EXECUTION_LEASE_DURATION,
    ExecutionLeaseAcquisitionDependencies,
    acquire_anonymous_public_execution_lease,
)

NOW = datetime(2026, 8, 6, 12, 30, tzinfo=UTC)


@pytest.mark.parametrize("provider", [Provider.KORAIL, Provider.SRT])
async def test_acquisition_policy_passes_exact_public_epoch(
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()
    owner_token_calls = 0
    captured: dict[str, object] = {}
    grant = ExecutionLeaseGrant(
        provider=provider,
        account_scope=ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        owner_token="a" * 32,
        fencing_token=7,
        expires_at=NOW + PROVIDER_EXECUTION_LEASE_DURATION,
    )

    def owner_token_factory() -> str:
        nonlocal owner_token_calls
        owner_token_calls += 1
        return grant.owner_token

    class FakeService:
        def __init__(self, received_session_factory) -> None:
            captured["session_factory"] = received_session_factory

        async def acquire(
            self,
            received_provider,
            account_scope,
            owner_token,
            *,
            now,
            expires_at,
        ):
            captured.update(
                provider=received_provider,
                account_scope=account_scope,
                owner_token=owner_token,
                now=now,
                expires_at=expires_at,
            )
            return grant

    monkeypatch.setattr(lease_application_module, "ProviderExecutionLeaseService", FakeService)

    service, acquired = await acquire_anonymous_public_execution_lease(
        provider,
        NOW,
        dependencies=ExecutionLeaseAcquisitionDependencies(
            session_factory=session_factory,
            owner_token_factory=owner_token_factory,
        ),
    )

    assert isinstance(service, FakeService)
    assert acquired is grant
    assert owner_token_calls == 1
    assert captured == {
        "session_factory": session_factory,
        "provider": provider,
        "account_scope": "anonymous/public",
        "owner_token": "a" * 32,
        "now": NOW,
        "expires_at": NOW + timedelta(minutes=2),
    }


async def test_acquisition_policy_returns_created_service_when_epoch_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_service: object | None = None

    class BusyService:
        def __init__(self, _session_factory) -> None:
            nonlocal captured_service
            captured_service = self

        async def acquire(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(lease_application_module, "ProviderExecutionLeaseService", BusyService)

    service, grant = await acquire_anonymous_public_execution_lease(
        Provider.SRT,
        NOW,
        dependencies=ExecutionLeaseAcquisitionDependencies(
            session_factory=object(),
            owner_token_factory=lambda: "b" * 32,
        ),
    )

    assert service is captured_service
    assert grant is None


async def test_acquisition_policy_propagates_token_factory_failure_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    failure = RuntimeError("token factory failed")

    class FakeService:
        def __init__(self, _session_factory) -> None:
            events.append("service")

        async def acquire(self, *_args, **_kwargs):
            events.append("acquire")
            return None

    def failing_token_factory() -> str:
        events.append("token")
        raise failure

    monkeypatch.setattr(lease_application_module, "ProviderExecutionLeaseService", FakeService)

    with pytest.raises(RuntimeError, match="token factory failed") as raised:
        await acquire_anonymous_public_execution_lease(
            Provider.SRT,
            NOW,
            dependencies=ExecutionLeaseAcquisitionDependencies(
                session_factory=object(),
                owner_token_factory=failing_token_factory,
            ),
        )

    assert raised.value is failure
    assert events == ["service", "token"]


async def test_acquisition_policy_propagates_provider_service_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("lease storage failed")

    class FailingService:
        def __init__(self, _session_factory) -> None:
            pass

        async def acquire(self, *_args, **_kwargs):
            raise failure

    monkeypatch.setattr(lease_application_module, "ProviderExecutionLeaseService", FailingService)

    with pytest.raises(RuntimeError, match="lease storage failed") as raised:
        await acquire_anonymous_public_execution_lease(
            Provider.KORAIL,
            NOW,
            dependencies=ExecutionLeaseAcquisitionDependencies(
                session_factory=object(),
                owner_token_factory=lambda: "c" * 32,
            ),
        )

    assert raised.value is failure


async def test_worker_wrapper_wires_current_session_factory_to_canonical_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_session_factory = object()
    service = object()
    grant = ExecutionLeaseGrant(
        provider=Provider.SRT,
        account_scope=ANONYMOUS_PUBLIC_ACCOUNT_SCOPE,
        owner_token="d" * 32,
        fencing_token=1,
        expires_at=NOW + timedelta(minutes=2),
    )
    captured: dict[str, object] = {}

    async def fake_acquire(provider, now, *, dependencies):
        captured.update(provider=provider, now=now, dependencies=dependencies)
        return service, grant

    monkeypatch.setattr(worker_module, "SessionFactory", current_session_factory)
    monkeypatch.setattr(
        worker_module,
        "acquire_anonymous_public_execution_lease",
        fake_acquire,
    )

    result = await worker_module._acquire_execution_lease(Provider.SRT, NOW)

    dependencies = captured["dependencies"]
    assert isinstance(dependencies, ExecutionLeaseAcquisitionDependencies)
    assert dependencies.session_factory is current_session_factory
    assert captured == {
        "provider": Provider.SRT,
        "now": NOW,
        "dependencies": dependencies,
    }
    assert result == (service, grant)
    assert worker_module.ANONYMOUS_PUBLIC_ACCOUNT_SCOPE is ANONYMOUS_PUBLIC_ACCOUNT_SCOPE
    assert worker_module.PROVIDER_EXECUTION_LEASE_DURATION is PROVIDER_EXECUTION_LEASE_DURATION
