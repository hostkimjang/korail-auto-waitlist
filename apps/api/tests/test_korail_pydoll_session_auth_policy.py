from __future__ import annotations

import pytest

from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_sidecar.pydoll.session_auth_policy import (
    is_korail_session_authenticated,
)


class _AuthenticationSignals:
    def __init__(
        self,
        *,
        official: bool | BaseException,
        header: bool | BaseException,
    ) -> None:
        self.official = official
        self.header = header
        self.official_calls = 0
        self.header_calls = 0

    async def _probe_official_authenticated_session(self) -> bool:
        self.official_calls += 1
        if isinstance(self.official, BaseException):
            raise self.official
        return self.official

    async def _has_authenticated_header(self) -> bool:
        self.header_calls += 1
        if isinstance(self.header, BaseException):
            raise self.header
        return self.header


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("official", "header", "expected", "expected_header_calls"),
    [
        (True, False, True, 0),
        (False, True, True, 1),
        (False, False, False, 1),
    ],
)
async def test_session_auth_policy_combines_conclusive_signals(
    official: bool,
    header: bool,
    expected: bool,
    expected_header_calls: int,
) -> None:
    signals = _AuthenticationSignals(official=official, header=header)

    assert await is_korail_session_authenticated(signals) is expected
    assert signals.official_calls == 1
    assert signals.header_calls == expected_header_calls


@pytest.mark.asyncio
async def test_session_auth_policy_accepts_positive_header_after_unavailable_official_probe() -> (
    None
):
    signals = _AuthenticationSignals(
        official=BrowserSourceUnavailable("session_keepalive"),
        header=True,
    )

    assert await is_korail_session_authenticated(signals) is True
    assert signals.official_calls == 1
    assert signals.header_calls == 1


@pytest.mark.asyncio
async def test_session_auth_policy_reraises_original_uncertainty_when_header_is_absent() -> None:
    uncertainty = BrowserSourceUnavailable("session_keepalive")
    signals = _AuthenticationSignals(official=uncertainty, header=False)

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await is_korail_session_authenticated(signals)

    assert captured.value is uncertainty
    assert signals.official_calls == 1
    assert signals.header_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        BrowserProtectionDetected(stage="session_keepalive"),
        BrowserRateLimited(),
    ],
    ids=("protected", "rate_limited"),
)
async def test_session_auth_policy_does_not_catch_explicit_provider_failures(
    error: Exception,
) -> None:
    signals = _AuthenticationSignals(official=error, header=True)

    with pytest.raises(type(error)) as captured:
        await is_korail_session_authenticated(signals)

    assert captured.value is error
    assert signals.official_calls == 1
    assert signals.header_calls == 0
