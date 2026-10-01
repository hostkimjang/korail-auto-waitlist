from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rail_waitlist.korail_browser_adapter_service import create_adapter_app
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.contracts import KorailLoginVerifyResult
from rail_waitlist.korail_sidecar.pydoll.login_submission import PydollLoginResponseUnavailable

TOKEN = "k" * 32


class FailedLogin:
    def __init__(self, submitted: bool) -> None:
        self.submitted = submitted

    async def verify_credentials(self, _credential: object) -> bool:
        if self.submitted:
            raise PydollLoginResponseUnavailable()
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
