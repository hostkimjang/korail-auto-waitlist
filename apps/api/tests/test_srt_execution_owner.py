from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import rail_waitlist.provider_adapters.srt_source_runtime as canonical
from rail_waitlist.config import FULLSTACK_E2E_SRT_FIXTURE_URL, Settings
from rail_waitlist.srt_execution import (
    ManagedSrtSeatObserver as LegacyManagedSrtSeatObserver,
)
from rail_waitlist.srt_execution import (
    SrtExecutionSourceConfig as LegacySrtExecutionSourceConfig,
)
from rail_waitlist.srt_execution import SrtSeatObserver as LegacySrtSeatObserver
from rail_waitlist.srt_execution import (
    default_srt_execution_source as legacy_default_srt_execution_source,
)
from rail_waitlist.srt_execution import (
    srt_background_monitoring_enabled as legacy_srt_background_monitoring_enabled,
)

API_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_srt_execution_symbols_are_exact_canonical_objects() -> None:
    assert LegacySrtSeatObserver is canonical.SrtSeatObserver
    assert LegacyManagedSrtSeatObserver is canonical.ManagedSrtSeatObserver
    assert LegacySrtExecutionSourceConfig is canonical.SrtExecutionSourceConfig
    assert legacy_default_srt_execution_source is canonical.default_srt_execution_source
    assert legacy_srt_background_monitoring_enabled is canonical.srt_background_monitoring_enabled
    assert canonical.SrtSeatObserver.__module__ == (
        "rail_waitlist.provider_adapters.srt_source_runtime"
    )
    assert canonical.ManagedSrtSeatObserver.__module__ == (
        "rail_waitlist.provider_adapters.srt_source_runtime"
    )
    assert canonical.SrtExecutionSourceConfig.__module__ == (
        "rail_waitlist.provider_adapters.srt_source_runtime"
    )


def test_srt_execution_source_config_preserves_settings_mapping() -> None:
    settings = Settings(
        _env_file=None,
        redis_url="redis://redis:6379/8",
        srt_seat_status_cache_ttl_seconds=19,
        srt_seat_status_timeout_seconds=21,
        seat_status_rate_limit_cooldown_seconds=602,
        seat_status_protection_cooldown_seconds=502,
    )

    assert canonical.SrtExecutionSourceConfig.from_settings(settings) == (
        canonical.SrtExecutionSourceConfig(
            redis_url="redis://redis:6379/8",
            cache_ttl_seconds=19,
            timeout_seconds=21,
            rate_limit_cooldown_seconds=602,
            protection_cooldown_seconds=502,
            fixture_url=None,
        )
    )


def test_disabled_srt_execution_source_fails_before_selecting_a_source(monkeypatch) -> None:
    monkeypatch.setattr(
        canonical,
        "_source_for_config",
        lambda _config: pytest.fail("disabled SRT execution created a local source"),
    )
    monkeypatch.setattr(
        canonical,
        "SrtProviderAdapterClient",
        lambda *_args: pytest.fail("disabled SRT execution created a sidecar client"),
    )

    with pytest.raises(RuntimeError, match="not explicitly enabled"):
        canonical.default_srt_execution_source(Settings(_env_file=None))


def test_enabled_sidecar_source_preserves_exact_connection_settings(monkeypatch) -> None:
    captured: list[tuple[object, ...]] = []
    sidecar = object()

    def sidecar_factory(*args: object) -> object:
        captured.append(args)
        return sidecar

    monkeypatch.setattr(canonical, "SrtProviderAdapterClient", sidecar_factory)
    monkeypatch.setattr(
        canonical,
        "_source_for_config",
        lambda _config: pytest.fail("sidecar mode created a local source"),
    )
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        srt_seat_status_enabled=True,
        srt_seat_monitoring_enabled=True,
        srt_provider_adapter_enabled=True,
        srt_provider_adapter_token="s" * 32,
        srt_seat_status_timeout_seconds=21,
        srt_provider_adapter_timeout_seconds=47,
    )

    assert canonical.default_srt_execution_source(settings) is sidecar
    assert captured == [
        (
            "http://srt-provider-adapter:8002",
            47,
            "s" * 32,
        )
    ]


def test_enabled_local_source_preserves_exact_source_config(monkeypatch) -> None:
    captured: list[canonical.SrtExecutionSourceConfig] = []
    local_source = object()

    def local_factory(config: canonical.SrtExecutionSourceConfig) -> object:
        captured.append(config)
        return local_source

    monkeypatch.setattr(canonical, "_source_for_config", local_factory)
    monkeypatch.setattr(
        canonical,
        "SrtProviderAdapterClient",
        lambda *_args: pytest.fail("local mode created a sidecar client"),
    )
    settings = Settings(
        _env_file=None,
        EXPERIMENTAL_RAIL_ENABLED=True,
        srt_seat_status_enabled=True,
        srt_seat_monitoring_enabled=True,
        redis_url="redis://redis:6379/7",
        srt_seat_status_cache_ttl_seconds=23,
        srt_seat_status_timeout_seconds=29,
        seat_status_rate_limit_cooldown_seconds=603,
        seat_status_protection_cooldown_seconds=503,
    )

    assert canonical.default_srt_execution_source(settings) is local_source
    assert captured == [
        canonical.SrtExecutionSourceConfig(
            redis_url="redis://redis:6379/7",
            cache_ttl_seconds=23,
            timeout_seconds=29,
            rate_limit_cooldown_seconds=603,
            protection_cooldown_seconds=503,
            fixture_url=None,
        )
    ]


@pytest.mark.parametrize("fixture_url", [None, FULLSTACK_E2E_SRT_FIXTURE_URL])
def test_local_source_composition_injects_only_an_explicit_fixture(
    monkeypatch,
    fixture_url: str | None,
) -> None:
    redis = object()
    cooldown_store = object()
    source = object()
    redis_calls: list[tuple[str, bool]] = []
    fixture_calls: list[str] = []
    source_kwargs: list[dict[str, object]] = []
    fixture_client_factory = object()

    class FakeRedis:
        @classmethod
        def from_url(cls, url: str, *, decode_responses: bool) -> object:
            redis_calls.append((url, decode_responses))
            return redis

    def cooldown_factory(value: object) -> object:
        assert value is redis
        return cooldown_store

    def fixture_factory(url: str) -> object:
        fixture_calls.append(url)
        return fixture_client_factory

    def source_factory(**kwargs: object) -> object:
        source_kwargs.append(kwargs)
        return source

    monkeypatch.setattr(canonical, "Redis", FakeRedis)
    monkeypatch.setattr(canonical, "RedisCooldownStore", cooldown_factory)
    monkeypatch.setattr(canonical, "fullstack_srt_client_factory", fixture_factory)
    monkeypatch.setattr(canonical, "SrtLiveSeatSource", source_factory)
    config = canonical.SrtExecutionSourceConfig(
        redis_url="redis://redis:6379/6",
        cache_ttl_seconds=31,
        timeout_seconds=37,
        rate_limit_cooldown_seconds=604,
        protection_cooldown_seconds=504,
        fixture_url=fixture_url,
    )

    result = canonical._source_for_config(config)

    assert result.source is source
    assert result.redis is redis
    assert redis_calls == [("redis://redis:6379/6", True)]
    assert fixture_calls == ([] if fixture_url is None else [fixture_url])
    expected_kwargs = {
        "enabled": True,
        "cache_ttl_seconds": 31,
        "timeout_seconds": 37,
        "rate_limit_cooldown_seconds": 604,
        "protection_cooldown_seconds": 504,
        "cooldown_store": cooldown_store,
    }
    if fixture_url is not None:
        expected_kwargs.update(
            client_factory=fixture_client_factory,
            source_name="fullstack-srt-fixture",
        )
    assert source_kwargs == [expected_kwargs]


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_srt_execution_import_orders_share_exact_symbols(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    import rail_waitlist.provider_adapters.srt_source_runtime as canonical
    import rail_waitlist.srt_execution as legacy
else:
    import rail_waitlist.srt_execution as legacy
    import rail_waitlist.provider_adapters.srt_source_runtime as canonical

names = [
    "SrtSeatObserver",
    "ManagedSrtSeatObserver",
    "SrtExecutionSourceConfig",
    "default_srt_execution_source",
    "srt_background_monitoring_enabled",
]
print(json.dumps({
    "identities": [getattr(legacy, name) is getattr(canonical, name) for name in names],
    "modules": [getattr(canonical, name).__module__ for name in names],
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, import_order],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "identities": [True, True, True, True, True],
        "modules": ["rail_waitlist.provider_adapters.srt_source_runtime"] * 5,
    }
