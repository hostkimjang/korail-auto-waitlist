from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

import rail_waitlist.reservation_confirmation as legacy_confirmation
import rail_waitlist.schemas as legacy_schemas
from rail_waitlist.domain import Provider
from rail_waitlist.provider_registry import official_url_policy as canonical
from rail_waitlist.reservations.provider_confirmation import korail, srt

API_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HOST_ROOTS = {
    Provider.KORAIL: ("korail.com", "letskorail.com"),
    Provider.SRT: ("srail.kr",),
    Provider.MOCK: ("example.invalid",),
}


@dataclass(frozen=True)
class HostValue:
    host: str | None


def test_official_url_policy_has_one_canonical_compatibility_surface() -> None:
    assert canonical.OFFICIAL_HOST_ROOTS == EXPECTED_HOST_ROOTS
    assert legacy_schemas.OFFICIAL_HOST_ROOTS is canonical.OFFICIAL_HOST_ROOTS
    assert legacy_schemas.is_official_provider_host is canonical.is_official_provider_host
    assert (
        legacy_confirmation.require_official_handoff_url is canonical.require_official_handoff_url
    )
    assert korail.require_official_handoff_url is canonical.require_official_handoff_url
    assert srt.require_official_handoff_url is canonical.require_official_handoff_url
    assert canonical.is_official_provider_host.__module__ == (
        "rail_waitlist.provider_registry.official_url_policy"
    )
    assert canonical.require_official_handoff_url.__module__ == (
        "rail_waitlist.provider_registry.official_url_policy"
    )


@pytest.mark.parametrize(
    ("provider", "hostname", "expected"),
    [
        (Provider.KORAIL, "korail.com", True),
        (Provider.KORAIL, "WWW.KORAIL.COM", True),
        (Provider.KORAIL, "ticket.letskorail.com.", True),
        (Provider.SRT, "etk.srail.kr", True),
        (Provider.MOCK, "example.invalid", True),
        (Provider.KORAIL, None, False),
        (Provider.KORAIL, "", False),
        (Provider.KORAIL, "evilkorail.com", False),
        (Provider.KORAIL, "korail.com.evil.example", False),
        (Provider.KORAIL, "etk.srail.kr", False),
        (Provider.SRT, "www.korail.com", False),
    ],
)
def test_official_provider_host_matching_is_provider_scoped(
    provider: Provider,
    hostname: str | None,
    expected: bool,
) -> None:
    assert canonical.is_official_provider_host(provider, HostValue(hostname)) is expected


def test_legacy_schema_host_adapter_uses_the_same_canonical_policy() -> None:
    assert legacy_schemas.is_official_provider_host(
        Provider.KORAIL,
        AnyHttpUrl("https://www.korail.com/ticket/search"),
    )
    assert not legacy_schemas.is_official_provider_host(
        Provider.KORAIL,
        AnyHttpUrl("https://etk.srail.kr/hpg/hra/02/list"),
    )


@pytest.mark.parametrize(
    ("provider", "value"),
    [
        (Provider.KORAIL, "https://www.korail.com/ticket/mypage"),
        (Provider.KORAIL, "https://TICKET.LETSKORAIL.COM.:8443/path?next=1#section"),
        (Provider.SRT, "https://etk.srail.kr/hpg/hra/02/list"),
        (Provider.MOCK, "https://example.invalid/reservation"),
    ],
)
def test_handoff_policy_accepts_official_https_and_returns_the_original_value(
    provider: Provider,
    value: str,
) -> None:
    assert canonical.require_official_handoff_url(provider, value) is value


@pytest.mark.parametrize(
    ("provider", "value", "message"),
    [
        (Provider.KORAIL, "http://www.korail.com/ticket", "credential-free HTTPS"),
        (Provider.SRT, "https://member:secret@etk.srail.kr/list", "credential-free HTTPS"),
        (Provider.SRT, "https:///list", "credential-free HTTPS"),
        (Provider.KORAIL, "https://evilkorail.com/ticket", "provider allowlist"),
        (Provider.KORAIL, "https://korail.com.evil.example/ticket", "provider allowlist"),
        (Provider.KORAIL, "https://etk.srail.kr/list", "provider allowlist"),
        (Provider.SRT, "https://www.korail.com/ticket", "provider allowlist"),
    ],
)
def test_handoff_policy_rejects_unsafe_or_cross_provider_urls(
    provider: Provider,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        canonical.require_official_handoff_url(provider, value)


def test_policy_import_does_not_load_schema_or_confirmation_hubs() -> None:
    script = """
import json
import sys
from rail_waitlist.provider_registry import official_url_policy as policy
print(json.dumps({
    "schemas": "rail_waitlist.schemas" in sys.modules,
    "confirmation": "rail_waitlist.reservation_confirmation" in sys.modules,
    "module": policy.require_official_handoff_url.__module__,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
    )

    assert json.loads(completed.stdout) == {
        "schemas": False,
        "confirmation": False,
        "module": "rail_waitlist.provider_registry.official_url_policy",
    }
