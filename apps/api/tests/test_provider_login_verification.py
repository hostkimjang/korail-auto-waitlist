from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from SRT import SRTLoginError

from rail_waitlist import provider_login_verification as legacy_login
from rail_waitlist.domain import Provider
from rail_waitlist.korail_sidecar.contracts import KorailSessionStateResult
from rail_waitlist.provider_account_management import login_verification as login_owner
from rail_waitlist.provider_account_management.contracts import ProviderCredentials
from rail_waitlist.provider_account_management.login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
    ProviderLoginVerifier,
    ProviderSessionRuntimeState,
)
from rail_waitlist.srt_sidecar.session_contract import (
    SrtSessionActorSnapshot,
    SrtSessionActorState,
)


@dataclass
class StubKorailLoginVerifier:
    outcome: ProviderLoginVerificationOutcome = ProviderLoginVerificationOutcome.AUTHENTICATED
    calls: list[ProviderCredentials] = field(default_factory=list)

    async def verify_login(
        self,
        credentials: ProviderCredentials,
    ) -> ProviderLoginVerification:
        self.calls.append(credentials)
        return ProviderLoginVerification(self.outcome)


@dataclass
class StubSrtLoginVerifier:
    authenticated: bool = True
    error: Exception | None = None
    verify_calls: list[ProviderCredentials] = field(default_factory=list)
    prewarm_calls: list[ProviderCredentials] = field(default_factory=list)

    async def verify_credentials(self, credentials: ProviderCredentials) -> bool:
        self.verify_calls.append(credentials)
        if self.error is not None:
            raise self.error
        return self.authenticated

    async def prewarm_credentials(self, credentials: ProviderCredentials) -> bool:
        self.prewarm_calls.append(credentials)
        if self.error is not None:
            raise self.error
        return self.authenticated


async def test_korail_login_verification_delegates_once_without_srt_login():
    korail = StubKorailLoginVerifier()
    srt = StubSrtLoginVerifier()
    verifier = ProviderLoginVerifier(korail, srt)
    credentials = ProviderCredentials(
        login_method="email",
        login_id="member@example.com",
        password="temporary-password",
        credential_version=0,
    )

    result = await verifier.verify(Provider.KORAIL, credentials)

    assert result.outcome is ProviderLoginVerificationOutcome.AUTHENTICATED
    assert korail.calls == [credentials]
    assert srt.verify_calls == []


async def test_srt_login_failure_is_not_retried():
    srt = StubSrtLoginVerifier(error=SRTLoginError("login failed"))
    verifier = ProviderLoginVerifier(StubKorailLoginVerifier(), srt)
    credentials = ProviderCredentials(
        login_method="phone",
        login_id="01012345678",
        password="temporary-password",
        credential_version=0,
    )

    result = await verifier.verify(Provider.SRT, credentials)

    assert result.outcome is ProviderLoginVerificationOutcome.AUTH_REQUIRED
    assert srt.verify_calls == [credentials]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("invalid identifier"), ProviderLoginVerificationOutcome.INVALID_IDENTIFIER),
        (RuntimeError("unexpected"), ProviderLoginVerificationOutcome.FAILED),
    ],
)
async def test_srt_login_verification_fails_closed(error, expected):
    srt = StubSrtLoginVerifier(error=error)
    verifier = ProviderLoginVerifier(StubKorailLoginVerifier(), srt)
    credentials = ProviderCredentials(
        login_method="membership_number",
        login_id="1234567890",
        password="temporary-password",
        credential_version=0,
    )

    result = await verifier.verify(Provider.SRT, credentials)
    assert result.outcome is expected


async def test_srt_prewarm_uses_the_explicit_reusable_session_path_once():
    srt = StubSrtLoginVerifier()
    verifier = ProviderLoginVerifier(StubKorailLoginVerifier(), srt)
    credentials = ProviderCredentials(
        login_method="membership_number",
        login_id="1234567890",
        password="temporary-password",
        credential_version=3,
    )

    result = await verifier.prewarm(Provider.SRT, credentials)

    assert result.outcome is ProviderLoginVerificationOutcome.AUTHENTICATED
    assert srt.prewarm_calls == [credentials]
    assert srt.verify_calls == []


async def test_session_snapshot_maps_korail_and_prefers_remote_srt_status() -> None:
    class KorailSessionStub(StubKorailLoginVerifier):
        async def session_state(self) -> KorailSessionStateResult:
            return KorailSessionStateResult(
                state="ready",
                credential_generation="korail-generation",
                created_age_seconds=12.0,
                last_verified_age_seconds=8.0,
                last_used_age_seconds=3.0,
                local_reuse_remaining_seconds=120.0,
                locally_reusable=True,
            )

    class RemoteSrtStatusStub(StubSrtLoginVerifier):
        async def session_status(self) -> object:
            return SimpleNamespace(
                state=SrtSessionActorState.BLOCKED,
                credential_generation=7,
                locally_reusable=False,
            )

        def session_snapshot(self) -> SrtSessionActorSnapshot:
            raise AssertionError("remote session status must take precedence")

    korail_verifier = ProviderLoginVerifier(KorailSessionStub(), StubSrtLoginVerifier())
    korail = await korail_verifier.session_snapshot(Provider.KORAIL)
    assert korail.state is ProviderSessionRuntimeState.READY
    assert korail.credential_generation == "korail-generation"
    assert korail.local_reuse_remaining_seconds == 120.0
    assert korail.locally_reusable is True

    srt_verifier = ProviderLoginVerifier(StubKorailLoginVerifier(), RemoteSrtStatusStub())
    srt = await srt_verifier.session_snapshot(Provider.SRT)
    assert srt.state is ProviderSessionRuntimeState.BLOCKED
    assert srt.credential_generation == "7"
    assert srt.created_age_seconds is None
    assert srt.last_verified_age_seconds is None
    assert srt.last_used_age_seconds is None
    assert srt.local_reuse_remaining_seconds is None
    assert srt.locally_reusable is False


async def test_local_srt_snapshot_uses_one_clock_and_clamps_ages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LocalSrtSnapshotStub(StubSrtLoginVerifier):
        def session_snapshot(self) -> SrtSessionActorSnapshot:
            return SrtSessionActorSnapshot(
                state=SrtSessionActorState.READY,
                credential_generation=4,
                created_at_monotonic=80.0,
                last_verified_at_monotonic=110.0,
                last_used_at_monotonic=95.0,
                local_reuse_until_monotonic=130.0,
                locally_reusable=True,
            )

    clock_calls = 0

    def monotonic() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 100.0

    monkeypatch.setattr(login_owner.time, "monotonic", monotonic)
    verifier = ProviderLoginVerifier(StubKorailLoginVerifier(), LocalSrtSnapshotStub())

    snapshot = await verifier.session_snapshot(Provider.SRT)

    assert clock_calls == 1
    assert snapshot.state is ProviderSessionRuntimeState.READY
    assert snapshot.credential_generation == "4"
    assert snapshot.created_age_seconds == 20.0
    assert snapshot.last_verified_age_seconds == 0.0
    assert snapshot.last_used_age_seconds == 5.0
    assert snapshot.local_reuse_remaining_seconds == 30.0


def test_canonical_default_factory_is_one_way_and_explicit_srt_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_srt = StubSrtLoginVerifier()
    explicit_srt = StubSrtLoginVerifier()
    factory_calls = 0

    def canonical_factory() -> StubSrtLoginVerifier:
        nonlocal factory_calls
        factory_calls += 1
        return canonical_srt

    def legacy_factory() -> StubSrtLoginVerifier:
        raise AssertionError("legacy facade reassignment must not reach the owner")

    monkeypatch.setattr(login_owner, "default_srt_reservation_executor", canonical_factory)
    monkeypatch.setattr(legacy_login, "default_srt_reservation_executor", legacy_factory)
    assert login_owner.default_srt_reservation_executor is canonical_factory

    explicit = ProviderLoginVerifier(StubKorailLoginVerifier(), explicit_srt)
    assert explicit._srt is explicit_srt
    assert factory_calls == 0

    defaulted = ProviderLoginVerifier(StubKorailLoginVerifier())
    assert defaulted._srt is canonical_srt
    assert factory_calls == 1
