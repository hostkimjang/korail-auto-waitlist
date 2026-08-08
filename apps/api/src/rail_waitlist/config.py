from __future__ import annotations

import base64
import hashlib
import ipaddress
from functools import lru_cache
from typing import Annotated
from urllib.parse import quote_plus, urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

OFFICIAL_TAGO_BASE_URL = "https://apis.data.go.kr/1613000/TrainInfo"
OFFICIAL_KORAIL_STATION_DATA_URL = "https://www.korail.com/public/st_info/station_data.json"
FULLSTACK_E2E_UPSTREAM_ORIGIN = "http://e2e-fake-upstream:8001"
FULLSTACK_E2E_SRT_FIXTURE_URL = f"{FULLSTACK_E2E_UPSTREAM_ORIGIN}/srt/search"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "KORAIL·SRT Waitlist API"
    environment: str = "development"
    database_url: str | None = None
    database_host: str = "postgres"
    database_port: int = 5432
    database_name: str = "rail"
    database_user: str = "rail"
    database_password: str | None = None
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    auto_create_schema: bool = False
    experimental_rail_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("EXPERIMENTAL_RAIL_ENABLED", "ENABLE_EXPERIMENTAL_RAIL"),
    )
    tago_service_key: str | None = None
    tago_base_url: str = OFFICIAL_TAGO_BASE_URL
    korail_station_data_url: str = OFFICIAL_KORAIL_STATION_DATA_URL
    secret_encryption_key: str | None = None
    auth_session_secret: str | None = None
    auth_session_hours: int = 12
    auth_cookie_secure: bool = True
    auth_initial_registration_enabled: bool = False
    auth_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"]
    )
    tago_cache_ttl_seconds: int = 300
    srt_seat_status_enabled: bool = False
    srt_seat_monitoring_enabled: bool = False
    srt_reservation_once_enabled: bool = False
    srt_fullstack_fixture_url: str | None = None
    srt_seat_status_cache_ttl_seconds: int = Field(default=1, ge=1, le=300)
    srt_seat_status_timeout_seconds: float = Field(default=8, ge=3, le=30)
    srt_provider_adapter_enabled: bool = False
    srt_provider_adapter_url: str = "http://srt-provider-adapter:8002"
    srt_provider_adapter_token: str | None = None
    srt_provider_adapter_timeout_seconds: float = Field(default=30, ge=3, le=120)
    seat_status_rate_limit_cooldown_seconds: int = Field(default=1800, ge=60, le=86400)
    seat_status_protection_cooldown_seconds: int = Field(default=300, ge=300, le=86400)
    korail_browser_bridge_enabled: bool = False
    korail_browser_adapter_enabled: bool = False
    korail_seat_monitoring_enabled: bool = False
    korail_reservation_once_enabled: bool = False
    korail_browser_adapter_url: str = "http://korail-browser-adapter:8001"
    korail_browser_adapter_token: str | None = None
    korail_browser_adapter_cache_ttl_seconds: int = Field(default=1, ge=1, le=300)
    korail_browser_adapter_timeout_seconds: float = Field(default=90, ge=30, le=180)
    webpush_vapid_private_key: str | None = None
    webpush_vapid_public_key: str | None = None
    webpush_vapid_subject: str = "mailto:admin@localhost"
    sse_poll_seconds: float = 1.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("auth_allowed_origins", mode="before")
    @classmethod
    def parse_auth_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def assemble_database_url(self) -> Settings:
        if self.database_url:
            return self
        password = self.database_password or "rail"
        self.database_url = (
            f"postgresql+asyncpg://{quote_plus(self.database_user)}:{quote_plus(password)}"
            f"@{self.database_host}:{self.database_port}/{quote_plus(self.database_name)}"
        )
        return self

    @model_validator(mode="after")
    def restrict_insecure_auth_cookie_to_loopback(self) -> Settings:
        if self.auth_cookie_secure:
            return self
        if not self.auth_allowed_origins:
            raise ValueError("insecure auth cookie requires loopback-only allowed origins")
        for origin in self.auth_allowed_origins:
            hostname = urlsplit(origin).hostname
            if hostname == "localhost":
                continue
            try:
                address = ipaddress.ip_address(hostname or "")
            except ValueError:
                raise ValueError(
                    "AUTH_COOKIE_SECURE=false is allowed only for loopback auth origins"
                ) from None
            if not address.is_loopback:
                raise ValueError(
                    "AUTH_COOKIE_SECURE=false is allowed only for loopback auth origins"
                )
        return self

    @model_validator(mode="after")
    def require_strong_production_secrets(self) -> Settings:
        if self.environment != "production":
            return self
        required_secrets = {
            "SECRET_ENCRYPTION_KEY": self.secret_encryption_key,
            "AUTH_SESSION_SECRET": self.auth_session_secret,
        }
        for env_name, material in required_secrets.items():
            if material is None or len(material.encode("utf-8")) < 32:
                raise ValueError(f"{env_name} must be at least 32 UTF-8 bytes in production")
        return self

    @model_validator(mode="after")
    def require_browser_adapter_token(self) -> Settings:
        if not (self.experimental_rail_enabled and self.korail_browser_adapter_enabled):
            return self
        token = self.korail_browser_adapter_token
        if token is None or len(token.encode("utf-8")) < 32:
            raise ValueError(
                "KORAIL_BROWSER_ADAPTER_TOKEN must be at least 32 UTF-8 bytes when enabled"
            )
        return self

    @model_validator(mode="after")
    def require_strict_srt_provider_adapter(self) -> Settings:
        parsed = urlsplit(self.srt_provider_adapter_url)
        if not (
            parsed.scheme == "http"
            and parsed.hostname == "srt-provider-adapter"
            and parsed.port == 8002
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        ):
            raise ValueError("SRT_PROVIDER_ADAPTER_URL must be the exact internal sidecar origin")
        self.srt_provider_adapter_url = "http://srt-provider-adapter:8002"
        if not self.srt_provider_adapter_enabled:
            return self
        token = self.srt_provider_adapter_token
        if token is None or len(token.encode("utf-8")) < 32:
            raise ValueError(
                "SRT_PROVIDER_ADAPTER_TOKEN must be at least 32 UTF-8 bytes when enabled"
            )
        return self

    @model_validator(mode="after")
    def restrict_upstream_overrides_to_fullstack_tests(self) -> Settings:
        configured = {
            "TAGO_BASE_URL": self.tago_base_url.rstrip("/"),
            "KORAIL_STATION_DATA_URL": self.korail_station_data_url,
        }
        official = {
            "TAGO_BASE_URL": OFFICIAL_TAGO_BASE_URL,
            "KORAIL_STATION_DATA_URL": OFFICIAL_KORAIL_STATION_DATA_URL,
        }
        test_only = {
            "TAGO_BASE_URL": f"{FULLSTACK_E2E_UPSTREAM_ORIGIN}/tago",
            "KORAIL_STATION_DATA_URL": (f"{FULLSTACK_E2E_UPSTREAM_ORIGIN}/station_data.json"),
        }
        for env_name, value in configured.items():
            if value == official[env_name]:
                continue
            if self.environment == "test" and value == test_only[env_name]:
                continue
            raise ValueError(f"{env_name} may only use the fixed internal fixture URL in test")
        self.tago_base_url = configured["TAGO_BASE_URL"]
        if self.srt_fullstack_fixture_url is not None:
            if not (
                self.environment == "test"
                and self.srt_fullstack_fixture_url == FULLSTACK_E2E_SRT_FIXTURE_URL
            ):
                raise ValueError(
                    "SRT_FULLSTACK_FIXTURE_URL may only use the fixed internal fixture URL in test"
                )
        return self

    def encryption_key(self) -> bytes:
        material = self.secret_encryption_key
        if not material:
            if self.environment not in {"development", "test"}:
                raise RuntimeError("SECRET_ENCRYPTION_KEY is required outside development")
            material = "development-only-key-change-before-deploy"
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def session_signing_key(self) -> bytes:
        material = self.auth_session_secret
        if material:
            return hashlib.sha256(material.encode("utf-8")).digest()
        return hashlib.sha256(self.encryption_key() + b":admin-session").digest()

    def tago_key(self) -> str | None:
        return self.tago_service_key

    def webpush_private_key(self) -> str | None:
        if not self.webpush_vapid_private_key:
            return None
        # dotenv values cannot contain a convenient raw PEM block. Accept its
        # conventional quoted ``\\n`` form without changing one-line base64url keys.
        return self.webpush_vapid_private_key.replace("\\n", "\n")

    def webpush_public_key(self) -> str | None:
        return self.webpush_vapid_public_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
