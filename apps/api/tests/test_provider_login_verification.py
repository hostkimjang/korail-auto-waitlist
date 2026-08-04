from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from SRT import SRTLoginError

from rail_waitlist.domain import Provider
from rail_waitlist.provider_accounts import ProviderCredentials
from rail_waitlist.provider_login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
    ProviderLoginVerifier,
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
