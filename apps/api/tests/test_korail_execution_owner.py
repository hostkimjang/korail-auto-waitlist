from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import rail_waitlist.provider_adapters.korail_execution as canonical
from rail_waitlist.config import Settings
from rail_waitlist.korail_execution import (
    KorailExecutionSourceConfig as LegacyKorailExecutionSourceConfig,
)
from rail_waitlist.korail_execution import KorailSeatObserver as LegacyKorailSeatObserver
from rail_waitlist.korail_execution import (
    ManagedKorailSeatObserver as LegacyManagedKorailSeatObserver,
)
from rail_waitlist.korail_execution import (
    default_korail_execution_source as legacy_default_korail_execution_source,
)
from rail_waitlist.korail_execution import (
    korail_background_monitoring_enabled as legacy_korail_background_monitoring_enabled,
)

API_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_korail_execution_symbols_are_exact_canonical_objects() -> None:
    assert LegacyKorailSeatObserver is canonical.KorailSeatObserver
    assert LegacyManagedKorailSeatObserver is canonical.ManagedKorailSeatObserver
    assert LegacyKorailExecutionSourceConfig is canonical.KorailExecutionSourceConfig
    assert legacy_default_korail_execution_source is canonical.default_korail_execution_source
    assert (
        legacy_korail_background_monitoring_enabled
        is canonical.korail_background_monitoring_enabled
    )
    assert canonical.KorailSeatObserver.__module__ == (
        "rail_waitlist.provider_adapters.korail_execution"
    )
    assert canonical.ManagedKorailSeatObserver.__module__ == (
        "rail_waitlist.provider_adapters.korail_execution"
    )
    assert canonical.KorailExecutionSourceConfig.__module__ == (
        "rail_waitlist.provider_adapters.korail_execution"
    )


def test_korail_execution_source_config_preserves_settings_mapping() -> None:
    settings = Settings(
        _env_file=None,
        redis_url="redis://redis:6379/9",
        korail_browser_adapter_token="k" * 32,
        korail_browser_adapter_cache_ttl_seconds=17,
        korail_browser_adapter_timeout_seconds=45,
        seat_status_rate_limit_cooldown_seconds=601,
        seat_status_protection_cooldown_seconds=501,
    )

    assert canonical.KorailExecutionSourceConfig.from_settings(settings) == (
        canonical.KorailExecutionSourceConfig(
            redis_url="redis://redis:6379/9",
            adapter_url=settings.korail_browser_adapter_url,
            adapter_token="k" * 32,
            cache_ttl_seconds=17,
            timeout_seconds=45,
            rate_limit_cooldown_seconds=601,
            protection_cooldown_seconds=501,
            allow_fullstack_test_url=False,
        )
    )


def test_disabled_korail_execution_source_fails_before_creating_resources(monkeypatch) -> None:
    def fail_if_source_is_created(_config: canonical.KorailExecutionSourceConfig) -> None:
        raise AssertionError("disabled KORAIL execution must not create a source")

    monkeypatch.setattr(canonical, "_source_for_config", fail_if_source_is_created)

    with pytest.raises(RuntimeError, match="not explicitly enabled"):
        canonical.default_korail_execution_source(Settings(_env_file=None))


@pytest.mark.parametrize("import_order", ["canonical-first", "legacy-first"])
def test_korail_execution_import_orders_share_exact_symbols(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    import rail_waitlist.provider_adapters.korail_execution as canonical
    import rail_waitlist.korail_execution as legacy
else:
    import rail_waitlist.korail_execution as legacy
    import rail_waitlist.provider_adapters.korail_execution as canonical

names = [
    "KorailSeatObserver",
    "ManagedKorailSeatObserver",
    "KorailExecutionSourceConfig",
    "default_korail_execution_source",
    "korail_background_monitoring_enabled",
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
        "modules": ["rail_waitlist.provider_adapters.korail_execution"] * 5,
    }
