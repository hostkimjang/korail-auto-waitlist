from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

import rail_waitlist.korail_browser_seat_source as legacy_source
from rail_waitlist.korail_sidecar.contracts import (
    KorailLoginVerifyRequest,
    KorailLoginVerifyResult,
)
from rail_waitlist.provider_account_management.contracts import ProviderCredentials
from rail_waitlist.provider_account_management.login_verification import (
    ProviderLoginVerificationOutcome,
)
from rail_waitlist.provider_adapters import korail_browser_auth_policy as policy

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "provider_adapters" / "korail_browser_auth_policy.py"
LEGACY_PATH = SOURCE_ROOT / "korail_browser_seat_source.py"
OWNER_MODULE = "rail_waitlist.provider_adapters.korail_browser_auth_policy"
OWNER_DEFINITIONS = {
    "build_login_verify_request",
    "project_login_verification_failure",
    "project_login_verification_result",
}
EXPECTED_ALL = (
    "build_login_verify_request",
    "project_login_verification_failure",
    "project_login_verification_result",
)


def _credentials(
    *,
    login_id: str = "fixture-login-identifier",
    credential_version: int = 7,
) -> ProviderCredentials:
    return ProviderCredentials(
        login_method="phone",
        login_id=login_id,
        password="fixture-login-password",
        credential_version=credential_version,
    )


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class _ReplacementAdapterFailure(RuntimeError):
    def __init__(self, *, protection: bool = False, rate_limited: bool = False) -> None:
        super().__init__("fixture_adapter_failure")
        self.protection = protection
        self.rate_limited = rate_limited


class _LoginTransport:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        outcome: str = "authenticated",
    ) -> None:
        self.error = error
        self.outcome = outcome
        self.calls: list[tuple[str, KorailLoginVerifyRequest]] = []

    async def verify_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        return await self._run("verify_login", request)

    async def prewarm_login(self, request: KorailLoginVerifyRequest) -> KorailLoginVerifyResult:
        return await self._run("prewarm_login", request)

    async def _run(
        self,
        operation: str,
        request: KorailLoginVerifyRequest,
    ) -> KorailLoginVerifyResult:
        self.calls.append((operation, request))
        if self.error is not None:
            raise self.error
        return KorailLoginVerifyResult(outcome=self.outcome)

    async def close(self) -> None:
        return None


def _source(
    transport: _LoginTransport,
    *,
    enabled: bool = True,
) -> legacy_source.KorailBrowserSeatSource:
    return legacy_source.KorailBrowserSeatSource(
        enabled=enabled,
        adapter_url="http://adapter.invalid",
        cache_ttl_seconds=30,
        timeout_seconds=5,
        rate_limit_cooldown_seconds=60,
        protection_cooldown_seconds=60,
        transport=transport,
        monotonic=lambda: 100.0,
    )


def test_auth_policy_has_exact_pure_owner_boundary() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.level, node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert definitions == OWNER_DEFINITIONS
    assert policy.__all__ == EXPECTED_ALL
    assert imports == {
        (0, "__future__"),
        (0, "typing"),
        (0, "pydantic"),
        (2, "korail_sidecar.contracts"),
        (2, "provider_account_management.contracts"),
        (2, "provider_account_management.login_verification"),
    }
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    assert not any(
        isinstance(node, ast.Name)
        and node.id.casefold() in {"logger", "transport", "cooldown", "http"}
        for node in ast.walk(tree)
    )
    for name in OWNER_DEFINITIONS:
        assert getattr(policy, name).__module__ == OWNER_MODULE


def test_source_keeps_auth_owner_identity_and_exact_legacy_surface() -> None:
    assert (
        legacy_source.KorailBrowserSeatSource._build_login_verify_request
        is policy.build_login_verify_request
    )
    assert (
        legacy_source.KorailBrowserSeatSource._project_login_verification_failure
        is policy.project_login_verification_failure
    )
    assert (
        legacy_source.KorailBrowserSeatSource._project_login_verification_result
        is policy.project_login_verification_result
    )
    assert len({name for name in vars(legacy_source) if not name.startswith("_")}) == 56
    assert (
        len(
            {
                name
                for name in vars(legacy_source)
                if name.startswith("_") and not name.startswith("__")
            }
        )
        == 10
    )
    assert not hasattr(legacy_source, "__all__")
    assert not hasattr(legacy_source, "_auth_policy_owner")

    tree = ast.parse(LEGACY_PATH.read_text(encoding="utf-8"), filename=str(LEGACY_PATH))
    deleted_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Delete)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert deleted_names == {
        "_auth_policy_owner",
        "_observation_policy_owner",
        "_reservation_policy_owner",
        "_window_policy_owner",
    }


def test_login_request_preserves_method_version_and_secret_redaction() -> None:
    credentials = _credentials()

    request = policy.build_login_verify_request(credentials)

    assert request.credential.login_method == "phone"
    assert request.credential.version == "7"
    assert _digest(request.credential.login_id.get_secret_value()) == _digest(credentials.login_id)
    assert _digest(request.credential.password.get_secret_value()) == _digest(credentials.password)
    assert repr(request.credential.login_id) == "SecretStr('**********')"
    assert repr(request.credential.password) == "SecretStr('**********')"


def test_invalid_login_request_stays_a_validation_error() -> None:
    with pytest.raises(ValueError):
        policy.build_login_verify_request(_credentials(login_id=""))


@pytest.mark.parametrize(
    "outcome",
    ["authenticated", "auth_required", "provider_blocked", "failed"],
)
def test_wire_login_result_maps_to_exact_provider_outcome(outcome: str) -> None:
    result = KorailLoginVerifyResult(outcome=outcome)

    projected = policy.project_login_verification_result(result)

    assert projected.outcome is ProviderLoginVerificationOutcome(outcome)


@pytest.mark.parametrize(
    "outcome",
    ["invalid_identifier", "provider_blocked", "failed"],
)
def test_code_owned_login_failure_maps_without_backend_detail(outcome: str) -> None:
    projected = policy.project_login_verification_failure(outcome)

    assert projected.outcome is ProviderLoginVerificationOutcome(outcome)


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        ("verify_login", _ReplacementAdapterFailure(protection=True)),
        ("prewarm_login", _ReplacementAdapterFailure(rate_limited=True)),
    ],
)
async def test_source_resolves_transport_and_adapter_failure_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error: _ReplacementAdapterFailure,
) -> None:
    transport = _LoginTransport(error=error)
    source = _source(transport)
    monkeypatch.setattr(legacy_source, "_AdapterFailure", _ReplacementAdapterFailure)

    result = await getattr(source, operation)(_credentials())

    assert result.outcome is ProviderLoginVerificationOutcome.PROVIDER_BLOCKED
    assert [name for name, _request in transport.calls] == [operation]


@pytest.mark.parametrize(
    ("enabled", "transport_error", "expected"),
    [
        (False, None, ProviderLoginVerificationOutcome.FAILED),
        (
            True,
            ValueError("fixture_validation_error"),
            ProviderLoginVerificationOutcome.INVALID_IDENTIFIER,
        ),
    ],
)
async def test_source_fails_closed_without_retry_or_extra_transport_call(
    enabled: bool,
    transport_error: Exception | None,
    expected: ProviderLoginVerificationOutcome,
) -> None:
    transport = _LoginTransport(error=transport_error)
    source = _source(transport, enabled=enabled)

    result = await source.verify_login(_credentials())

    assert result.outcome is expected
    assert len(transport.calls) == int(enabled)


async def test_success_projection_remains_outside_the_transport_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(_LoginTransport())

    def reject_result(_result: KorailLoginVerifyResult) -> None:
        raise ValueError("fixture_projection_error")

    monkeypatch.setattr(source, "_project_login_verification_result", reject_result)

    with pytest.raises(ValueError, match="fixture_projection_error"):
        await source.prewarm_login(_credentials())
