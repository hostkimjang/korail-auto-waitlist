from __future__ import annotations

from typing import Literal

from pydantic import SecretStr

from ..korail_sidecar.contracts import (
    KorailCredentialRequest,
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
)
from ..provider_account_management.contracts import ProviderCredentials
from ..provider_account_management.login_verification import (
    ProviderLoginVerification,
    ProviderLoginVerificationOutcome,
)

__all__ = (
    "build_login_verify_request",
    "project_login_verification_failure",
    "project_login_verification_result",
)


def build_login_verify_request(credentials: ProviderCredentials) -> KorailLoginVerifyRequest:
    """Build the redacted sidecar credential contract for one login attempt."""
    return KorailLoginVerifyRequest(
        credential=KorailCredentialRequest(
            login_method=credentials.login_method,
            login_id=SecretStr(credentials.login_id),
            password=SecretStr(credentials.password),
            version=str(credentials.credential_version),
        )
    )


def project_login_verification_result(
    result: KorailLoginVerifyResult,
) -> ProviderLoginVerification:
    """Map one validated sidecar result onto the provider-neutral outcome."""
    return ProviderLoginVerification(ProviderLoginVerificationOutcome(result.outcome))


def project_login_verification_failure(
    outcome: Literal["invalid_identifier", "provider_blocked", "failed"],
) -> ProviderLoginVerification:
    """Project a code-owned, secret-free failure classification."""
    return ProviderLoginVerification(ProviderLoginVerificationOutcome(outcome))
