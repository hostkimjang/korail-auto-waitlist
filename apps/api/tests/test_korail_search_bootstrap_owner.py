from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_search_bootstrap as legacy
from rail_waitlist.provider_adapters import korail_search_bootstrap as bootstrap_owner
from rail_waitlist.provider_registry import korail_search_contracts as contract_owner
from rail_waitlist.provider_registry import korail_search_url_policy as url_owner

API_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_SYMBOLS = (
    "OFFICIAL_KORAIL_STATION_DATA_URL",
    "OFFICIAL_KORAIL_RESULT_URL",
    "MIN_STATION_COUNT",
    "MAX_STATION_COUNT",
    "STATION_REQUEST_TIMEOUT",
    "KorailStationIdentityUnavailable",
    "KorailStationIdentity",
    "KorailStationIdentityCatalog",
    "KorailStationIdentityResolver",
    "parse_korail_station_identities",
    "build_korail_general_search_url",
    "validate_korail_general_search_url",
)
BOOTSTRAP_MODULE = "rail_waitlist.provider_adapters.korail_search_bootstrap"
CONTRACT_MODULE = "rail_waitlist.provider_registry.korail_search_contracts"
URL_POLICY_MODULE = "rail_waitlist.provider_registry.korail_search_url_policy"
OWNER_BY_SYMBOL = {
    "OFFICIAL_KORAIL_STATION_DATA_URL": bootstrap_owner,
    "OFFICIAL_KORAIL_RESULT_URL": url_owner,
    "MIN_STATION_COUNT": bootstrap_owner,
    "MAX_STATION_COUNT": bootstrap_owner,
    "STATION_REQUEST_TIMEOUT": bootstrap_owner,
    "KorailStationIdentityUnavailable": bootstrap_owner,
    "KorailStationIdentity": contract_owner,
    "KorailStationIdentityCatalog": bootstrap_owner,
    "KorailStationIdentityResolver": bootstrap_owner,
    "parse_korail_station_identities": bootstrap_owner,
    "build_korail_general_search_url": url_owner,
    "validate_korail_general_search_url": url_owner,
}


def test_legacy_bootstrap_is_an_exact_alias_facade() -> None:
    for symbol in SUPPORTED_SYMBOLS:
        owner = OWNER_BY_SYMBOL[symbol]
        assert getattr(legacy, symbol) is getattr(owner, symbol)

    for symbol in (
        "KorailStationIdentityUnavailable",
        "KorailStationIdentityCatalog",
        "KorailStationIdentityResolver",
        "parse_korail_station_identities",
    ):
        assert getattr(bootstrap_owner, symbol).__module__ == BOOTSTRAP_MODULE
    for symbol in (
        "build_korail_general_search_url",
        "validate_korail_general_search_url",
    ):
        assert getattr(url_owner, symbol).__module__ == URL_POLICY_MODULE
    assert contract_owner.KorailStationIdentity.__module__ == CONTRACT_MODULE


def test_legacy_bootstrap_contains_no_runtime_definitions() -> None:
    module_path = API_ROOT / "src" / "rail_waitlist" / "korail_search_bootstrap.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    assert not any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for node in tree.body
    )


@pytest.mark.parametrize(
    ("import_order", "legacy_loaded_by_first_import"),
    [
        ("canonical-first", False),
        ("legacy-first", True),
        ("timetable-first", False),
        ("pydoll-first", False),
    ],
)
def test_bootstrap_import_orders_keep_one_owner(
    import_order: str,
    legacy_loaded_by_first_import: bool,
) -> None:
    script = r"""
import json
import sys

order = sys.argv[1]
if order == "canonical-first":
    from rail_waitlist.provider_registry import korail_search_url_policy as first
elif order == "legacy-first":
    from rail_waitlist import korail_search_bootstrap as first
elif order == "timetable-first":
    from rail_waitlist.timetable_management import schemas as first
else:
    from rail_waitlist import korail_pydoll_browser as first

loaded = "rail_waitlist.korail_search_bootstrap" in sys.modules
from rail_waitlist import korail_search_bootstrap as legacy
from rail_waitlist.provider_adapters import korail_search_bootstrap as bootstrap_owner
from rail_waitlist.provider_registry import korail_search_contracts as contract_owner
from rail_waitlist.provider_registry import korail_search_url_policy as url_owner

symbols = (
    "OFFICIAL_KORAIL_STATION_DATA_URL",
    "OFFICIAL_KORAIL_RESULT_URL",
    "MIN_STATION_COUNT",
    "MAX_STATION_COUNT",
    "STATION_REQUEST_TIMEOUT",
    "KorailStationIdentityUnavailable",
    "KorailStationIdentity",
    "KorailStationIdentityCatalog",
    "KorailStationIdentityResolver",
    "parse_korail_station_identities",
    "build_korail_general_search_url",
    "validate_korail_general_search_url",
)
print(json.dumps({
    "identity": (
        legacy.OFFICIAL_KORAIL_STATION_DATA_URL
        is bootstrap_owner.OFFICIAL_KORAIL_STATION_DATA_URL
        and legacy.OFFICIAL_KORAIL_RESULT_URL is url_owner.OFFICIAL_KORAIL_RESULT_URL
        and legacy.MIN_STATION_COUNT is bootstrap_owner.MIN_STATION_COUNT
        and legacy.MAX_STATION_COUNT is bootstrap_owner.MAX_STATION_COUNT
        and legacy.STATION_REQUEST_TIMEOUT is bootstrap_owner.STATION_REQUEST_TIMEOUT
        and legacy.KorailStationIdentityUnavailable
        is bootstrap_owner.KorailStationIdentityUnavailable
        and legacy.KorailStationIdentity is contract_owner.KorailStationIdentity
        and legacy.KorailStationIdentityCatalog is bootstrap_owner.KorailStationIdentityCatalog
        and legacy.KorailStationIdentityResolver is bootstrap_owner.KorailStationIdentityResolver
        and legacy.parse_korail_station_identities
        is bootstrap_owner.parse_korail_station_identities
        and legacy.build_korail_general_search_url
        is url_owner.build_korail_general_search_url
        and legacy.validate_korail_general_search_url
        is url_owner.validate_korail_general_search_url
    ),
    "legacy_loaded_by_first_import": loaded,
    "identity_module": contract_owner.KorailStationIdentity.__module__,
    "resolver_module": bootstrap_owner.KorailStationIdentityResolver.__module__,
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
        "identity": True,
        "legacy_loaded_by_first_import": legacy_loaded_by_first_import,
        "identity_module": CONTRACT_MODULE,
        "resolver_module": BOOTSTRAP_MODULE,
    }


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_value"),
    [
        (
            "gASVagAAAAAAAACMJXJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXJjaF9ib290c3RyYXCUjBVLb3JhaWxTdGF0aW9uSWRlbnRpdHmUk5QpgZR9lCiMBGNvZGWUjAQwMDEwlIwEbmFtZZSMB0RhZWplb26UdWIu",
            contract_owner.KorailStationIdentity,
            contract_owner.KorailStationIdentity(code="0010", name="Daejeon"),
        ),
        (
            "gASVvQAAAAAAAACMJXJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXJjaF9ib290c3RyYXCUjBxLb3JhaWxTdGF0aW9uSWRlbnRpdHlDYXRhbG9nlJOUKYGUfZQojAdieV9uYW1llH2UjAdkYWVqZW9ulGgAjBVLb3JhaWxTdGF0aW9uSWRlbnRpdHmUk5QpgZR9lCiMBGNvZGWUjAQwMDEwlIwEbmFtZZSMB0RhZWplb26UdWJzjAdieV9jb2RllH2UaA1oCnN1Yi4=",
            bootstrap_owner.KorailStationIdentityCatalog,
            bootstrap_owner.KorailStationIdentityCatalog(
                by_name={
                    "daejeon": contract_owner.KorailStationIdentity(code="0010", name="Daejeon")
                },
                by_code={"0010": contract_owner.KorailStationIdentity(code="0010", name="Daejeon")},
            ),
        ),
    ],
)
def test_legacy_dataclass_pickle_restores_canonical_object(
    payload: str,
    expected_type: type[object],
    expected_value: object,
) -> None:
    restored = pickle.loads(base64.b64decode(payload))

    assert type(restored) is expected_type
    assert restored == expected_value


def test_legacy_exception_pickle_restores_canonical_object() -> None:
    payload = base64.b64decode(
        "gASVfQAAAAAAAACMJXJhaWxfd2FpdGxpc3Qua29yYWlsX3NlYXJjaF9ib290c3RyYXCUjCBLb3JhaWxTdGF0aW9uSWRlbnRpdHlVbmF2YWlsYWJsZZSTlIwob2ZmaWNpYWwgc3RhdGlvbiBpZGVudGl0eSBpcyB1bmF2YWlsYWJsZZSFlFKULg=="
    )
    restored = pickle.loads(payload)

    assert type(restored) is bootstrap_owner.KorailStationIdentityUnavailable
    assert str(restored) == "official station identity is unavailable"


def test_production_consumers_use_exact_canonical_objects() -> None:
    from rail_waitlist import (
        korail_browser_automation,
        korail_browser_mode_smoke,
        korail_pydoll_browser,
        korail_pydoll_search_actor,
    )
    from rail_waitlist import schemas as central_schemas
    from rail_waitlist.korail_sidecar import runtime as sidecar_runtime
    from rail_waitlist.timetable_management import schemas as timetable_schemas

    assert (
        korail_browser_automation.validate_korail_general_search_url
        is url_owner.validate_korail_general_search_url
    )
    assert (
        korail_browser_mode_smoke.KorailStationIdentityResolver
        is bootstrap_owner.KorailStationIdentityResolver
    )
    assert (
        korail_pydoll_browser.KorailStationIdentityResolver
        is bootstrap_owner.KorailStationIdentityResolver
    )
    assert (
        korail_pydoll_browser.build_korail_general_search_url
        is url_owner.build_korail_general_search_url
    )
    assert (
        korail_pydoll_search_actor.KorailStationIdentityResolver
        is bootstrap_owner.KorailStationIdentityResolver
    )
    assert (
        sidecar_runtime.KorailStationIdentityResolver
        is bootstrap_owner.KorailStationIdentityResolver
    )
    assert (
        central_schemas.validate_korail_general_search_url
        is url_owner.validate_korail_general_search_url
    )
    assert (
        timetable_schemas.validate_korail_general_search_url
        is url_owner.validate_korail_general_search_url
    )


def test_legacy_facade_reassignment_does_not_weaken_canonical_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rail_waitlist.timetable_management import schemas as timetable_schemas

    original = url_owner.validate_korail_general_search_url
    monkeypatch.setattr(legacy, "validate_korail_general_search_url", lambda value: value)

    assert url_owner.validate_korail_general_search_url is original
    assert timetable_schemas.validate_korail_general_search_url is original
    with pytest.raises(ValueError):
        timetable_schemas.validate_korail_general_search_url("https://evil.example/")
