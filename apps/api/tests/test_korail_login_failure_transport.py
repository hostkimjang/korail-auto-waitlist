from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rail_waitlist.korail_browser_adapter_service import create_adapter_app
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.contracts import KorailLoginVerifyResult
from rail_waitlist.korail_sidecar.provider_cooldown import (
    MemoryProviderCooldown,
    ProviderCooldownDeferred,
)
from rail_waitlist.korail_sidecar.pydoll.login_submission import (
    LoginSubmissionSnapshot,
    PydollLoginResponseUnavailable,
)

TOKEN = "k" * 32


@pytest.fixture(autouse=True)
def isolated_provider_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rail_waitlist.korail_browser_adapter_service._build_provider_cooldown",
        MemoryProviderCooldown,
    )


class FailedLogin:
    def __init__(self, submitted: bool, snapshot: LoginSubmissionSnapshot | None = None) -> None:
        self.submitted = submitted
        self.snapshot = snapshot

    async def verify_credentials(self, _credential: object) -> bool:
        if self.submitted:
            raise PydollLoginResponseUnavailable(self.snapshot)
        raise BrowserSourceUnavailable("login_response")

    async def prewarm_credentials(self, credential: object) -> bool:
        return await self.verify_credentials(credential)


async def ready() -> None:
    return


@pytest.mark.parametrize("path", ["/v1/verify-login", "/v1/prewarm-login"])
@pytest.mark.parametrize("submitted", [False, True])
def test_login_failure_keeps_submission_retry_metadata(path: str, submitted: bool) -> None:
    app = create_adapter_app(
        token=TOKEN, readiness_probe=ready, reservation_client=FailedLogin(submitted)
    )
    with TestClient(app) as client:
        response = client.post(
            path,
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "credential": {
                    "login_id": "fixture-account",
                    "password": "fixture-password",
                    "version": "fixture-v1",
                }
            },
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    if submitted:
        assert response.json() == {
            "outcome": "failed",
            "failure_kind": "provider_submission_failed",
            "retry_after_seconds": 300,
        }
        assert response.headers["retry-after"] == "300"
    else:
        assert response.json() == {"outcome": "failed"}
        assert "retry-after" not in response.headers
    assert "fixture-account" not in response.text
    assert "fixture-password" not in response.text


@pytest.mark.parametrize("path", ["/v1/verify-login", "/v1/prewarm-login"])
@pytest.mark.parametrize(
    ("snapshot", "diagnostic"),
    [
        (None, "state=not_observed failure=none status=none"),
        (
            LoginSubmissionSnapshot("failed", 500, "http_error"),
            "state=failed failure=http_error status=500",
        ),
        (
            LoginSubmissionSnapshot("ambiguous", failure="ambiguous"),
            "state=ambiguous failure=ambiguous status=none",
        ),
        (
            LoginSubmissionSnapshot("failed", failure="timeout"),
            "state=failed failure=timeout status=none",
        ),
        (
            LoginSubmissionSnapshot("failed", 200, "network_error"),
            "state=failed failure=network_error status=200",
        ),
    ],
)
def test_submission_failure_logs_closed_diagnostics_without_credentials(
    path: str,
    snapshot: LoginSubmissionSnapshot | None,
    diagnostic: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_adapter_app(
        token=TOKEN, readiness_probe=ready, reservation_client=FailedLogin(True, snapshot)
    )
    with caplog.at_level("WARNING"), TestClient(app) as client:
        response = client.post(
            path,
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "credential": {
                    "login_id": "fixture-account",
                    "password": "fixture-password",
                    "version": "fixture-v1",
                }
            },
        )
    assert response.json() == {
        "outcome": "failed",
        "failure_kind": "provider_submission_failed",
        "retry_after_seconds": 300,
    }
    assert response.headers["retry-after"] == "300"
    assert f"stage=login_response {diagnostic}" in caplog.text
    for secret in ("fixture-account", "fixture-password", "fixture-v1", TOKEN):
        assert secret not in caplog.text
        assert secret not in response.text
    assert "state=" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"outcome": "failed", "retry_after_seconds": 300},
        {"outcome": "failed", "failure_kind": "provider_submission_failed"},
        {
            "outcome": "authenticated",
            "failure_kind": "provider_submission_failed",
            "retry_after_seconds": 300,
        },
    ]
    + [
        {
            "outcome": "failed",
            "failure_kind": "provider_submission_failed",
            "retry_after_seconds": value,
        }
        for value in [True, "300", 299, 901]
    ],
)
def test_incomplete_submission_failure_contract_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        KorailLoginVerifyResult.model_validate(payload)


@pytest.mark.parametrize("path", ["/v1/verify-login", "/v1/prewarm-login"])
@pytest.mark.parametrize("retry", [1, 900, 86400])
@pytest.mark.parametrize(
    "reason", ["provider_unavailable", "provider_access_restricted", "cooldown_store_unavailable"]
)
def test_login_admission_deferral_is_not_a_submission_failure(path, retry, reason) -> None:
    class DeferredLogin:
        async def verify_credentials(self, _credential: object) -> bool:
            raise ProviderCooldownDeferred(reason, retry)

        async def prewarm_credentials(self, credential: object) -> bool:
            return await self.verify_credentials(credential)

    app = create_adapter_app(token=TOKEN, readiness_probe=ready, reservation_client=DeferredLogin())
    with TestClient(app) as client:
        response = client.post(
            path,
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "credential": {
                    "login_id": "fixture-account",
                    "password": "fixture-password",
                    "version": "fixture-v1",
                }
            },
        )
    assert response.status_code == 200
    assert response.headers["retry-after"] == str(retry)
    assert response.json() == {
        "outcome": "failed",
        "failure_kind": "provider_cooldown",
        "retry_after_seconds": retry,
        "cooldown_reason": reason,
    }
    assert "fixture-account" not in response.text
    assert "fixture-password" not in response.text


@pytest.mark.parametrize(
    "updates",
    [
        {"retry_after_seconds": True},
        {"retry_after_seconds": "1"},
        {"retry_after_seconds": 0},
        {"retry_after_seconds": 86401},
        {"cooldown_reason": None},
        {"cooldown_reason": "untrusted"},
        {"outcome": "authenticated"},
        {"failure_kind": "unknown"},
        {"cookie": "fixture-secret"},
    ],
)
def test_invalid_login_deferral_contract_is_rejected(updates) -> None:
    payload = {
        "outcome": "failed",
        "failure_kind": "provider_cooldown",
        "retry_after_seconds": 1,
        "cooldown_reason": "provider_unavailable",
        **updates,
    }
    with pytest.raises(ValidationError):
        KorailLoginVerifyResult.model_validate(payload)
