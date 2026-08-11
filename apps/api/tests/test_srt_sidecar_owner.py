from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import json
import pickle
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from rail_waitlist import provider_accounts as legacy_accounts
from rail_waitlist import schemas as central_schemas
from rail_waitlist import srt_provider_adapter as legacy_client
from rail_waitlist import srt_provider_adapter_contract as legacy_contracts
from rail_waitlist import srt_provider_adapter_service as legacy_service
from rail_waitlist import srt_reservation as legacy_reservation
from rail_waitlist.provider_account_management import contracts as account_contracts
from rail_waitlist.provider_account_management import schemas as account_schemas
from rail_waitlist.srt_sidecar import application as application_owner
from rail_waitlist.srt_sidecar import client as client_owner
from rail_waitlist.srt_sidecar import contracts as contract_owner
from rail_waitlist.srt_sidecar import http as http_owner
from rail_waitlist.srt_sidecar import runtime as runtime_owner
from rail_waitlist.srt_sidecar import session_contract as session_owner

API_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_MODULE = "rail_waitlist.srt_sidecar.contracts"
CLIENT_MODULE = "rail_waitlist.srt_sidecar.client"
SESSION_MODULE = "rail_waitlist.srt_sidecar.session_contract"
CREDENTIAL_MODULE = "rail_waitlist.provider_account_management.contracts"
SCHEMA_SHA256 = "eb5fd68726db4de933a57ce03c6ed2d4f68ad36636b631523337dc7ce2eb7ced"

CONTRACT_SYMBOLS = {
    "BaseModel",
    "ConfigDict",
    "Field",
    "KOREA",
    "Literal",
    "Provider",
    "ProviderCredentials",
    "ReservationConfirmationOutcome",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "ReservationRequest",
    "ReservationResult",
    "SeatClass",
    "SeatObservationRequest",
    "SeatObservationResult",
    "SecretStr",
    "SrtConfirmReservationRequest",
    "SrtConfirmReservationResult",
    "SrtCredentialRequest",
    "SrtLoginRequest",
    "SrtLoginResult",
    "SrtObserveRequest",
    "SrtObserveResult",
    "SrtOfficialSeatStatus",
    "SrtProviderAdapterModel",
    "SrtReservationConfirmationResult",
    "SrtReservationConfirmationTarget",
    "SrtReserveOnceRequest",
    "SrtReserveOnceResult",
    "SrtSessionActorState",
    "SrtSessionStatus",
    "SrtTimetableOverlayRequest",
    "SrtTimetableOverlayResult",
    "SrtTimetableSearchRequest",
    "SrtTimetableSearchResult",
    "SrtTimetableTrain",
    "TimetableItem",
    "ZoneInfo",
    "datetime",
    "model_validator",
}
CLIENT_SYMBOLS = {
    "ProviderCredentials",
    "ReservationConfirmationResult",
    "ReservationConfirmationTarget",
    "ReservationRequest",
    "ReservationResult",
    "SRTError",
    "SRTNetFunnelError",
    "SRT_PROVIDER_ADAPTER_ORIGIN",
    "SeatObservationRequest",
    "SeatObservationResult",
    "SrtConfirmReservationRequest",
    "SrtConfirmReservationResult",
    "SrtCredentialRequest",
    "SrtLoginRequest",
    "SrtLoginResult",
    "SrtObserveRequest",
    "SrtObserveResult",
    "SrtProviderAdapterClient",
    "SrtProviderAdapterUnavailable",
    "SrtReservationConfirmationTarget",
    "SrtReserveOnceRequest",
    "SrtReserveOnceResult",
    "SrtSessionStatus",
    "SrtTimetableOverlayRequest",
    "SrtTimetableOverlayResult",
    "SrtTimetableSearchRequest",
    "SrtTimetableSearchResult",
    "SrtTimetableTrain",
    "TimetableItem",
    "ValidationError",
    "datetime",
    "httpx",
    "urlsplit",
    "validate_srt_provider_adapter_url",
}
MODEL_NAMES = (
    "SrtProviderAdapterModel",
    "SrtCredentialRequest",
    "SrtSessionStatus",
    "SrtLoginRequest",
    "SrtLoginResult",
    "SrtObserveRequest",
    "SrtObserveResult",
    "SrtTimetableOverlayRequest",
    "SrtTimetableOverlayResult",
    "SrtTimetableSearchRequest",
    "SrtTimetableTrain",
    "SrtTimetableSearchResult",
    "SrtReserveOnceRequest",
    "SrtReserveOnceResult",
    "SrtReservationConfirmationTarget",
    "SrtConfirmReservationRequest",
    "SrtReservationConfirmationResult",
    "SrtConfirmReservationResult",
)
SERVICE_SYMBOLS = {
    "Callable",
    "FastAPI",
    "HTTPException",
    "JSONResponse",
    "Protocol",
    "Redis",
    "RedisCooldownStore",
    "Request",
    "RequestException",
    "RequestValidationError",
    "ReservationRequest",
    "ReservationResult",
    "Response",
    "SRTError",
    "SRTLoginError",
    "SRTNetFunnelError",
    "SRTNotLoggedInError",
    "SRTResponseError",
    "SeatObservationRequest",
    "SrtConfirmReservationRequest",
    "SrtConfirmReservationResult",
    "SrtLiveSeatSource",
    "SrtLoginRequest",
    "SrtLoginResult",
    "SrtObserveRequest",
    "SrtObserveResult",
    "SrtProviderExecutor",
    "SrtProviderSource",
    "SrtReservationConfirmationResult",
    "SrtReserveOnceRequest",
    "SrtReserveOnceResult",
    "SrtSessionStatus",
    "SrtTimetableOverlayRequest",
    "SrtTimetableOverlayResult",
    "SrtTimetableSearchRequest",
    "SrtTimetableSearchResult",
    "SrtTimetableTrain",
    "TimetableItem",
    "annotations",
    "app",
    "asynccontextmanager",
    "configure_service_file_logging",
    "create_srt_provider_adapter_app",
    "default_srt_reservation_executor",
    "hmac",
    "os",
    "request_validation_exception_handler",
    "time",
}


def test_credentials_and_session_contracts_have_one_canonical_identity() -> None:
    assert legacy_accounts.ProviderCredentials is account_contracts.ProviderCredentials
    assert account_schemas.RailLoginMethod is account_contracts.RailLoginMethod
    assert central_schemas.RailLoginMethod is account_contracts.RailLoginMethod
    assert legacy_reservation.SrtSessionActorState is session_owner.SrtSessionActorState
    assert legacy_reservation.SrtSessionActorSnapshot is session_owner.SrtSessionActorSnapshot
    assert account_contracts.ProviderCredentials.__module__ == CREDENTIAL_MODULE
    assert session_owner.SrtSessionActorState.__module__ == SESSION_MODULE
    assert session_owner.SrtSessionActorSnapshot.__module__ == SESSION_MODULE


def test_provider_credentials_preserve_secret_dataclass_shape() -> None:
    credential = account_contracts.ProviderCredentials(
        login_id="1234567890",
        password="private-password",
        credential_version=7,
    )

    assert [item.name for item in fields(credential)] == [
        "login_id",
        "password",
        "credential_version",
        "login_method",
    ]
    assert credential.login_method == "membership_number"
    assert "1234567890" not in repr(credential)
    assert "private-password" not in repr(credential)
    with pytest.raises(FrozenInstanceError):
        credential.__setattr__("credential_version", 8)

    restored = pickle.loads(pickle.dumps(credential, protocol=4))
    assert type(restored) is account_contracts.ProviderCredentials
    assert restored == credential


def test_provider_accounts_wildcard_keeps_the_credential_contract() -> None:
    namespace: dict[str, object] = {}

    exec("from rail_waitlist.provider_accounts import *", namespace)  # noqa: S102

    assert namespace["ProviderCredentials"] is account_contracts.ProviderCredentials
    assert namespace["RailLoginMethod"] is account_contracts.RailLoginMethod
    assert namespace["dataclass"] is account_contracts.dataclass
    assert namespace["field"] is account_contracts.field


def test_legacy_wire_and_client_surfaces_are_exact_aliases() -> None:
    for symbol in CONTRACT_SYMBOLS:
        assert getattr(legacy_contracts, symbol) is getattr(contract_owner, symbol)
    for symbol in CLIENT_SYMBOLS:
        assert getattr(legacy_client, symbol) is getattr(client_owner, symbol)

    assert {
        name for name in vars(legacy_contracts) if not name.startswith("_")
    } == CONTRACT_SYMBOLS | {"annotations"}
    assert {name for name in vars(legacy_client) if not name.startswith("_")} == (
        CLIENT_SYMBOLS | {"annotations"}
    )


def test_service_entrypoint_keeps_legacy_surface_and_canonical_route_owners() -> None:
    assert {name for name in vars(legacy_service) if not name.startswith("_")} == SERVICE_SYMBOLS
    assert legacy_service.SrtProviderSource is application_owner.SrtProviderSource
    assert legacy_service.SrtProviderExecutor is application_owner.SrtProviderExecutor
    assert http_owner.create_srt_provider_adapter_app.__module__ == (
        "rail_waitlist.srt_sidecar.http"
    )
    assert not hasattr(http_owner, "app")

    signature = inspect.signature(legacy_service.create_srt_provider_adapter_app)
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.parameters["monotonic"].default is legacy_service.time.monotonic
    assert {
        route.endpoint.__module__
        for route in legacy_service.app.routes
        if route.path.startswith("/v1/")
    } == {"rail_waitlist.srt_sidecar.http"}


def test_service_entrypoint_uses_the_canonical_runtime_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_dependencies = object()
    source = object()
    redis = object()
    received_factories: list[dict[str, object]] = []

    def fake_dependencies(**factories: object) -> object:
        received_factories.append(factories)
        return sentinel_dependencies

    def fake_build_default_source(*, dependencies: object, number_reader: object):
        assert dependencies is sentinel_dependencies
        assert callable(number_reader)
        return source, redis

    monkeypatch.setattr(runtime_owner, "default_runtime_dependencies", fake_dependencies)
    monkeypatch.setattr(runtime_owner, "build_default_source", fake_build_default_source)

    assert legacy_service._build_default_source() == (source, redis)
    assert len(received_factories) == 1
    assert set(received_factories[0]) == {
        "getenv",
        "redis_from_url",
        "cooldown_store_factory",
        "source_factory",
    }
    assert all(callable(factory) for factory in received_factories[0].values())


def test_service_runtime_factory_preserves_legacy_dependency_reassignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRedis:
        from_url_args: tuple[str, bool] | None = None

        @classmethod
        def from_url(cls, url: str, *, decode_responses: bool):
            cls.from_url_args = (url, decode_responses)
            return cls()

    class FakeCooldownStore:
        def __init__(self, redis: object) -> None:
            self.redis = redis

    class FakeSource:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(legacy_service, "Redis", FakeRedis)
    monkeypatch.setattr(legacy_service, "RedisCooldownStore", FakeCooldownStore)
    monkeypatch.setattr(legacy_service, "SrtLiveSeatSource", FakeSource)

    source, redis = legacy_service._build_default_source()

    assert isinstance(redis, FakeRedis)
    assert FakeRedis.from_url_args == ("redis://redis:6379/0", True)
    assert isinstance(source, FakeSource)
    assert isinstance(source.kwargs["cooldown_store"], FakeCooldownStore)
    assert source.kwargs["cooldown_store"].redis is redis


def test_wire_models_keep_canonical_modules_and_schema_fingerprint() -> None:
    payload = {}
    for name in MODEL_NAMES:
        model = getattr(contract_owner, name)
        assert model.__module__ == CONTRACT_MODULE
        payload[name] = model.model_json_schema()

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == SCHEMA_SHA256
    assert client_owner.SrtProviderAdapterClient.__module__ == CLIENT_MODULE
    assert client_owner.SrtProviderAdapterUnavailable.__module__ == CLIENT_MODULE
    assert client_owner.validate_srt_provider_adapter_url.__module__ == CLIENT_MODULE


def test_srt_sidecar_openapi_shape_is_unchanged() -> None:
    from rail_waitlist.srt_provider_adapter_service import app

    schema = app.openapi()
    encoded = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert hashlib.sha256(encoded).hexdigest() == (
        "f08b8f86b5d60ef62cbd5561a9aade75f681afe569bef3c2d5175dadd3760937"
    )
    assert len(schema["paths"]) == 7
    assert len(schema["components"]["schemas"]) == 36


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            "gASVswAAAAAAAACMH3JhaWxfd2FpdGxpc3QucHJvdmlkZXJfYWNjb3VudHOUjBNQcm92aWRlckNyZWRlbnRpYWxzlJOUKYGUfZQojAhsb2dpbl9pZJSMCjEyMzQ1Njc4OTCUjAhwYXNzd29yZJSMEHByaXZhdGUtcGFzc3dvcmSUjBJjcmVkZW50aWFsX3ZlcnNpb26USweMDGxvZ2luX21ldGhvZJSMEW1lbWJlcnNoaXBfbnVtYmVylHViLg==",
            account_contracts.ProviderCredentials,
        ),
        (
            "gASVRgAAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3Jlc2VydmF0aW9ulIwUU3J0U2Vzc2lvbkFjdG9yU3RhdGWUk5SMBXJlYWR5lIWUUpQu",
            session_owner.SrtSessionActorState,
        ),
        (
            "gASVMQEAAAAAAACMHXJhaWxfd2FpdGxpc3Quc3J0X3Jlc2VydmF0aW9ulIwXU3J0U2Vzc2lvbkFjdG9yU25hcHNob3SUk5QpgZR9lCiMBXN0YXRllGgAjBRTcnRTZXNzaW9uQWN0b3JTdGF0ZZSTlIwFcmVhZHmUhZRSlIwVY3JlZGVudGlhbF9nZW5lcmF0aW9ulEsHjBRjcmVhdGVkX2F0X21vbm90b25pY5RHP/AAAAAAAACMGmxhc3RfdmVyaWZpZWRfYXRfbW9ub3RvbmljlEdAAAAAAAAAAIwWbGFzdF91c2VkX2F0X21vbm90b25pY5RHQAgAAAAAAACMG2xvY2FsX3JldXNlX3VudGlsX21vbm90b25pY5RHQBAAAAAAAACMEGxvY2FsbHlfcmV1c2FibGWUiHViLg==",
            session_owner.SrtSessionActorSnapshot,
        ),
        (
            "gASVcgAAAAAAAACMInJhaWxfd2FpdGxpc3Quc3J0X3Byb3ZpZGVyX2FkYXB0ZXKUjB1TcnRQcm92aWRlckFkYXB0ZXJVbmF2YWlsYWJsZZSTlIwjU1JUIHByb3ZpZGVyIGFkYXB0ZXIgaXMgdW5hdmFpbGFibGWUhZRSlC4=",
            client_owner.SrtProviderAdapterUnavailable,
        ),
    ],
)
def test_pre_move_pickles_restore_canonical_types(
    payload: str,
    expected_type: type[object],
) -> None:
    restored = pickle.loads(base64.b64decode(payload))

    assert type(restored) is expected_type


def test_pre_move_session_status_pickle_restores_canonical_nested_types() -> None:
    payload = (
        "gASVtAEAAAAAAACMK3JhaWxfd2FpdGxpc3Quc3J0X3Byb3ZpZGVyX2FkYXB0ZXJfY29udHJhY3SU"
        "jBBTcnRTZXNzaW9uU3RhdHVzlJOUKYGUfZQojAhfX2RpY3RfX5R9lCiMBXN0YXRllIwdcmFpbF93"
        "YWl0bGlzdC5zcnRfcmVzZXJ2YXRpb26UjBRTcnRTZXNzaW9uQWN0b3JTdGF0ZZSTlIwFcmVhZHmU"
        "hZRSlIwVY3JlZGVudGlhbF9nZW5lcmF0aW9ulEsHjBBsb2NhbGx5X3JldXNhYmxllIiME2NyZWF0"
        "ZWRfYWdlX3NlY29uZHOUTowZbGFzdF92ZXJpZmllZF9hZ2Vfc2Vjb25kc5ROjBVsYXN0X3VzZWRf"
        "YWdlX3NlY29uZHOUTowdbG9jYWxfcmV1c2VfcmVtYWluaW5nX3NlY29uZHOUTowab2JzZXJ2YXRp"
        "b25fZGVmZXJyZWRfdW50aWyUTnWMEl9fcHlkYW50aWNfZXh0cmFfX5ROjBdfX3B5ZGFudGljX2Zp"
        "ZWxkc19zZXRfX5SPlChoD2gHaA6QjBRfX3B5ZGFudGljX3ByaXZhdGVfX5ROdWIu"
    )
    restored = pickle.loads(base64.b64decode(payload))

    assert type(restored) is contract_owner.SrtSessionStatus
    assert restored.state is session_owner.SrtSessionActorState.READY


def test_top_level_srt_facades_have_no_runtime_definitions() -> None:
    for relative_path in ("srt_provider_adapter_contract.py", "srt_provider_adapter.py"):
        path = API_ROOT / "src" / "rail_waitlist" / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            for node in tree.body
        )


@pytest.mark.parametrize(
    "first_import",
    ["credentials", "session", "contracts", "legacy-contract", "client", "service"],
)
def test_srt_owner_import_orders_keep_one_identity(first_import: str) -> None:
    script = r"""
import json
import sys

first = sys.argv[1]
if first == "credentials":
    from rail_waitlist.provider_account_management import contracts as imported
elif first == "session":
    from rail_waitlist.srt_sidecar import session_contract as imported
elif first == "contracts":
    from rail_waitlist.srt_sidecar import contracts as imported
elif first == "legacy-contract":
    from rail_waitlist import srt_provider_adapter_contract as imported
elif first == "client":
    from rail_waitlist.srt_sidecar import client as imported
else:
    from rail_waitlist import srt_provider_adapter_service as imported

loaded = {
    "legacy_contract": "rail_waitlist.srt_provider_adapter_contract" in sys.modules,
    "legacy_client": "rail_waitlist.srt_provider_adapter" in sys.modules,
    "reservation": "rail_waitlist.srt_reservation" in sys.modules,
    "srtrain": "SRT" in sys.modules,
    "provider_accounts": "rail_waitlist.provider_accounts" in sys.modules,
    "database": "rail_waitlist.database" in sys.modules,
    "security": "rail_waitlist.security" in sys.modules,
}
from rail_waitlist import provider_accounts as legacy_accounts
from rail_waitlist import srt_provider_adapter as legacy_client
from rail_waitlist import srt_provider_adapter_contract as legacy_contracts
from rail_waitlist import srt_reservation as legacy_reservation
from rail_waitlist.provider_account_management import contracts as account_owner
from rail_waitlist.srt_sidecar import client as client_owner
from rail_waitlist.srt_sidecar import contracts as contract_owner
from rail_waitlist.srt_sidecar import session_contract as session_owner

print(json.dumps({
    "loaded": loaded,
    "identity": all([
        legacy_accounts.ProviderCredentials is account_owner.ProviderCredentials,
        legacy_reservation.SrtSessionActorState is session_owner.SrtSessionActorState,
        legacy_reservation.SrtSessionActorSnapshot is session_owner.SrtSessionActorSnapshot,
        legacy_contracts.SrtSessionStatus is contract_owner.SrtSessionStatus,
        legacy_client.SrtProviderAdapterClient is client_owner.SrtProviderAdapterClient,
    ]),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, first_import],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["identity"] is True
    if first_import in {"credentials", "session", "contracts"}:
        assert result["loaded"] == {
            "database": False,
            "legacy_client": False,
            "legacy_contract": False,
            "provider_accounts": False,
            "reservation": False,
            "security": False,
            "srtrain": False,
        }


def test_legacy_client_reassignment_does_not_mutate_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = client_owner.SrtProviderAdapterClient

    monkeypatch.setattr(legacy_client, "SrtProviderAdapterClient", object())

    assert client_owner.SrtProviderAdapterClient is canonical
