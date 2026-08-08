import base64
import hashlib

import pytest
from pydantic import ValidationError

from rail_waitlist.config import (
    FULLSTACK_E2E_SRT_FIXTURE_URL,
    FULLSTACK_E2E_UPSTREAM_ORIGIN,
    Settings,
)


def test_initial_admin_registration_is_disabled_by_default():
    assert Settings(_env_file=None).auth_initial_registration_enabled is False


def test_protection_cooldown_defaults_to_five_minutes():
    assert Settings(_env_file=None).seat_status_protection_cooldown_seconds == 300


def test_live_seat_cache_defaults_to_one_second():
    settings = Settings(_env_file=None)

    assert settings.srt_seat_status_cache_ttl_seconds == 1
    assert settings.korail_browser_adapter_cache_ttl_seconds == 1


def test_removed_accountless_korail_settings_are_ignored(monkeypatch):
    removed_names = (
        "KORAIL_SEAT_STATUS_ENABLED",
        "KORAIL_SEAT_STATUS_CACHE_TTL_SECONDS",
        "KORAIL_SEAT_STATUS_TIMEOUT_SECONDS",
    )
    for name in removed_names:
        monkeypatch.setenv(name, "retired-setting")

    settings = Settings(_env_file=None)

    for name in removed_names:
        assert not hasattr(settings, name.casefold())


def test_initial_admin_registration_toggle_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("AUTH_INITIAL_REGISTRATION_ENABLED", "true")

    assert Settings(_env_file=None).auth_initial_registration_enabled is True


def test_srt_background_monitoring_toggle_is_disabled_by_default_and_read_from_env(
    monkeypatch,
):
    assert Settings(_env_file=None).srt_seat_monitoring_enabled is False

    monkeypatch.setenv("SRT_SEAT_MONITORING_ENABLED", "true")

    assert Settings(_env_file=None).srt_seat_monitoring_enabled is True


def test_korail_background_monitoring_toggle_is_disabled_by_default_and_read_from_env(
    monkeypatch,
):
    assert Settings(_env_file=None).korail_seat_monitoring_enabled is False

    monkeypatch.setenv("KORAIL_SEAT_MONITORING_ENABLED", "true")

    assert Settings(_env_file=None).korail_seat_monitoring_enabled is True


def test_secrets_are_read_directly_from_environment_fields(monkeypatch):
    monkeypatch.setenv("DATABASE_PASSWORD", "db password/with symbols")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "encryption-secret")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "session-secret")
    monkeypatch.setenv("TAGO_SERVICE_KEY", "tago-secret")
    monkeypatch.setenv("WEBPUSH_VAPID_PRIVATE_KEY", "vapid-private")
    monkeypatch.setenv("WEBPUSH_VAPID_PUBLIC_KEY", "vapid-public")

    settings = Settings(_env_file=None)

    assert "db+password%2Fwith+symbols" in settings.database_url
    assert settings.encryption_key() == base64.urlsafe_b64encode(
        hashlib.sha256(b"encryption-secret").digest()
    )
    assert settings.session_signing_key() == hashlib.sha256(b"session-secret").digest()
    assert settings.tago_key() == "tago-secret"
    assert settings.webpush_private_key() == "vapid-private"
    assert settings.webpush_public_key() == "vapid-public"


def test_secret_file_fallback_fields_are_not_part_of_settings():
    file_fields = {
        "database_password_file",
        "secret_encryption_key_file",
        "auth_session_secret_file",
        "tago_service_key_file",
        "webpush_vapid_private_key_file",
        "webpush_vapid_public_key_file",
    }

    assert file_fields.isdisjoint(Settings.model_fields)


def test_webpush_private_key_expands_escaped_pem_newlines():
    escaped_pem = "-----BEGIN PRIVATE KEY-----\\nabc123\\n-----END PRIVATE KEY-----"
    settings = Settings(_env_file=None, webpush_vapid_private_key=escaped_pem)

    assert settings.webpush_private_key() == escaped_pem.replace("\\n", "\n")


def test_webpush_private_key_leaves_base64url_value_unchanged():
    key = "BEq2dHRJq_g0-plain-base64url-key"
    settings = Settings(_env_file=None, webpush_vapid_private_key=key)

    assert settings.webpush_private_key() == key


@pytest.mark.parametrize(
    ("field_name", "env_name"),
    [
        ("secret_encryption_key", "SECRET_ENCRYPTION_KEY"),
        ("auth_session_secret", "AUTH_SESSION_SECRET"),
    ],
)
@pytest.mark.parametrize("invalid_value", [None, "x" * 31])
def test_production_requires_present_32_byte_secrets(field_name, env_name, invalid_value):
    values = {
        "secret_encryption_key": "e" * 32,
        "auth_session_secret": "s" * 32,
    }
    values[field_name] = invalid_value

    with pytest.raises(ValidationError, match=env_name):
        Settings(_env_file=None, environment="production", **values)


def test_production_secret_length_is_measured_in_utf8_bytes():
    settings = Settings(
        _env_file=None,
        environment="production",
        secret_encryption_key="가" * 11,
        auth_session_secret="나" * 11,
    )

    assert len(settings.secret_encryption_key.encode("utf-8")) == 33


def test_development_and_test_keep_secret_fallbacks():
    for environment in ("development", "test"):
        settings = Settings(
            _env_file=None,
            environment=environment,
            secret_encryption_key=None,
            auth_session_secret=None,
        )

        assert settings.encryption_key()
        assert settings.session_signing_key()


def test_browser_bridge_uses_db_pairing_and_is_disabled_by_default():
    settings = Settings(_env_file=None)

    assert settings.korail_browser_bridge_enabled is False
    assert not hasattr(settings, "korail_browser_bridge_token")


def test_production_browser_bridge_does_not_require_a_shared_env_token():
    base = {
        "environment": "production",
        "secret_encryption_key": "e" * 32,
        "auth_session_secret": "s" * 32,
    }

    enabled = Settings(
        _env_file=None,
        **base,
        korail_browser_bridge_enabled=True,
    )

    assert enabled.korail_browser_bridge_enabled is True


def test_experimental_server_browser_requires_a_distinct_internal_token():
    with pytest.raises(ValidationError, match="KORAIL_BROWSER_ADAPTER_TOKEN"):
        Settings(
            _env_file=None,
            EXPERIMENTAL_RAIL_ENABLED=True,
            korail_browser_adapter_enabled=True,
        )

    enabled = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        korail_browser_adapter_enabled=True,
        korail_browser_adapter_token="b" * 32,
    )

    assert enabled.korail_browser_adapter_enabled is True


def test_browser_transport_timeout_allows_the_sidecar_to_finish_first():
    settings = Settings(_env_file=None)

    assert settings.korail_browser_adapter_timeout_seconds == 90


def test_insecure_auth_cookie_allows_only_loopback_origins():
    settings = Settings(
        auth_cookie_secure=False,
        auth_allowed_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:8000",
            "http://[::1]:8000",
        ],
    )
    assert settings.auth_cookie_secure is False


@pytest.mark.parametrize(
    "origin",
    ["https://waitlist.example.com", "https://100.64.0.10", "http://192.168.0.10:8000"],
)
def test_insecure_auth_cookie_rejects_non_loopback_origin(origin):
    with pytest.raises(ValidationError, match="loopback"):
        Settings(auth_cookie_secure=False, auth_allowed_origins=[origin])


def test_auth_allowed_origins_are_read_from_comma_separated_environment(monkeypatch):
    monkeypatch.setenv("AUTH_ALLOWED_ORIGINS", "http://localhost:3000/, http://127.0.0.1:4173")

    settings = Settings(_env_file=None)

    assert settings.auth_allowed_origins == [
        "http://localhost:3000",
        "http://127.0.0.1:4173",
    ]


def test_fixed_internal_upstream_fixture_is_allowed_only_in_test_environment():
    fixture_values = {
        "tago_base_url": f"{FULLSTACK_E2E_UPSTREAM_ORIGIN}/tago",
        "korail_station_data_url": (f"{FULLSTACK_E2E_UPSTREAM_ORIGIN}/station_data.json"),
    }

    settings = Settings(_env_file=None, environment="test", **fixture_values)

    assert settings.tago_base_url == fixture_values["tago_base_url"]
    assert settings.korail_station_data_url == fixture_values["korail_station_data_url"]
    with pytest.raises(ValidationError, match="fixed internal fixture URL"):
        Settings(_env_file=None, environment="development", **fixture_values)


def test_srt_fullstack_fixture_is_allowed_only_at_the_fixed_test_origin():
    settings = Settings(
        _env_file=None,
        environment="test",
        srt_fullstack_fixture_url=FULLSTACK_E2E_SRT_FIXTURE_URL,
    )

    assert settings.srt_fullstack_fixture_url == FULLSTACK_E2E_SRT_FIXTURE_URL
    with pytest.raises(ValidationError, match="fixed internal fixture URL"):
        Settings(
            _env_file=None,
            environment="development",
            srt_fullstack_fixture_url=FULLSTACK_E2E_SRT_FIXTURE_URL,
        )
    with pytest.raises(ValidationError, match="fixed internal fixture URL"):
        Settings(
            _env_file=None,
            environment="test",
            srt_fullstack_fixture_url="http://untrusted-upstream:8001/srt/search",
        )


def test_test_upstream_override_rejects_arbitrary_internal_hosts():
    with pytest.raises(ValidationError, match="fixed internal fixture URL"):
        Settings(
            _env_file=None,
            environment="test",
            tago_base_url="http://untrusted-upstream:8001/tago",
        )
