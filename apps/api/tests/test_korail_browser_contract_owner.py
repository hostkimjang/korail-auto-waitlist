from __future__ import annotations

import json
import pickle
import subprocess
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from rail_waitlist import korail_browser_automation as legacy
from rail_waitlist.korail_sidecar import browser_contracts as contracts
from rail_waitlist.korail_sidecar import browser_protection as protection

API_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SYMBOLS = {
    "AdapterErrorReason",
    "AdapterModel",
    "BrowserAdapterError",
    "BrowserClient",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserSourceUnavailable",
    "BrowserTrainSnapshot",
    "KorailTrainType",
    "ProtectionTrigger",
    "SOURCE_NAME",
    "SeatStatus",
}
PROTECTION_SYMBOLS = {
    "GENERIC_PROTECTION_TRIGGERS",
    "PROTECTION_MARKERS",
    "RATE_LIMIT_RESOURCE_TYPES",
    "is_rate_limit_response",
    "protection_trigger_from_http_response",
    "protection_trigger_from_text",
}
LEGACY_PICKLE_SYMBOLS = {
    "AdapterModel",
    "BrowserSeatSearchRequest",
    "BrowserTrainSnapshot",
    "BrowserSeatSearchResult",
    "BrowserAdapterError",
    "BrowserProtectionDetected",
    "BrowserRateLimited",
    "BrowserSourceUnavailable",
    "BrowserClient",
    "protection_trigger_from_http_response",
    "is_rate_limit_response",
    "protection_trigger_from_text",
}


def test_legacy_browser_contract_surface_is_an_exact_owner_alias() -> None:
    for symbol in CONTRACT_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(contracts, symbol)
    for symbol in PROTECTION_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(protection, symbol)

    assert contracts.AdapterModel.__module__ == "rail_waitlist.korail_sidecar.browser_contracts"
    assert contracts.BrowserClient.__module__ == "rail_waitlist.korail_sidecar.browser_contracts"
    assert protection.protection_trigger_from_text.__module__ == (
        "rail_waitlist.korail_sidecar.browser_protection"
    )
    assert legacy.BaseModel is BaseModel
    assert legacy.Protocol.__module__ == "typing"
    assert legacy.validate_korail_general_search_url.__module__.endswith(
        "provider_registry.korail_search_url_policy"
    )


def test_browser_contract_keeps_request_result_and_error_invariants() -> None:
    request = contracts.BrowserSeatSearchRequest(
        origin=" 서울역 ",
        destination="부산역",
        travel_date=date(2026, 8, 8),
        departure_from=time(8),
        departure_to=time(12),
    )
    assert (request.origin, request.destination) == ("서울", "부산")
    assert request.cache_key() == ("서울", "부산", "2026-08-08", "08:00:00", "12:00:00", 1)
    with pytest.raises(ValidationError, match="origin and destination must differ"):
        contracts.BrowserSeatSearchRequest.model_validate(
            {**request.model_dump(), "destination": "서울역"}
        )

    departure = datetime(2026, 8, 8, 23, tzinfo=UTC)
    train = contracts.BrowserTrainSnapshot(
        train_number="254",
        train_type="KTX-산천",
        departure_at=departure,
        arrival_at=datetime(2026, 8, 9, 0, 7, tzinfo=UTC),
        standard="available",
        first="sold_out",
    )
    result = contracts.BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 8),
        passenger_count=1,
        observed_at=departure,
        trains=[train],
    )
    assert result.model_config["extra"] == "forbid"
    assert get_args(contracts.BrowserSeatSearchResult.model_fields["trains"].annotation)[0] is (
        contracts.BrowserTrainSnapshot
    )
    with pytest.raises(ValidationError, match="schedule datetimes must include a timezone"):
        contracts.BrowserTrainSnapshot.model_validate(
            {**train.model_dump(), "departure_at": departure.replace(tzinfo=None)}
        )

    blocked = contracts.BrowserProtectionDetected("marker_captcha", "result")
    assert isinstance(blocked, contracts.BrowserAdapterError)
    assert (blocked.reason, blocked.trigger, blocked.stage) == (
        "provider_access_restricted",
        "marker_captcha",
        "result",
    )
    assert contracts.BrowserRateLimited().reason == "rate_limited"
    assert contracts.BrowserSourceUnavailable("submit").stage == "submit"


def test_browser_protection_policy_preserves_fail_closed_classification() -> None:
    assert protection.protection_trigger_from_http_response(403, "document") == "http_403_main"
    assert protection.protection_trigger_from_http_response(403, "script") == (
        "http_403_subresource"
    )
    assert protection.protection_trigger_from_http_response(200, "document") is None
    for resource_type in ("document", "fetch", "xhr"):
        assert protection.is_rate_limit_response(429, resource_type)
    for resource_type in ("font", "image", "script", "stylesheet"):
        assert not protection.is_rate_limit_response(429, resource_type)
    assert protection.protection_trigger_from_text("code: -8002") == "marker_code_8002"
    assert protection.protection_trigger_from_text("비정상   접근") == "marker_abnormal_access"
    assert protection.protection_trigger_from_text("정상 안내") is None
    assert protection.protection_trigger_from_replay_text("이용 제한") == "marker_abnormal_access"
    assert protection.protection_trigger_from_replay_text("비정상 접근") == "marker_abnormal_access"
    assert protection.normalize_replay_protection_trigger("http_403") == "http_403_business"
    assert protection.normalize_replay_protection_trigger("marker_code_8003") == "marker_code_8003"


def test_pre_move_browser_contract_pickles_restore_canonical_objects() -> None:
    legacy_global_prefix = b"crail_waitlist.korail_browser_automation\n"
    for symbol in LEGACY_PICKLE_SYMBOLS:
        owner = protection if symbol in PROTECTION_SYMBOLS else contracts
        payload = legacy_global_prefix + symbol.encode("ascii") + b"\n."
        assert pickle.loads(payload) is getattr(owner, symbol)


def test_browser_contract_import_orders_keep_one_identity_and_passive_owner_imports() -> None:
    script = r"""
import json
import sys

order = sys.argv[1]
if order == "contracts-first":
    from rail_waitlist.korail_sidecar import browser_contracts
    passive = "rail_waitlist.korail_browser_automation" not in sys.modules
elif order == "protection-first":
    from rail_waitlist.korail_sidecar import browser_protection
    passive = "rail_waitlist.korail_browser_automation" not in sys.modules
else:
    from rail_waitlist import korail_browser_automation
    passive = True

from rail_waitlist import korail_browser_automation as legacy
from rail_waitlist.korail_sidecar import browser_contracts as contracts
from rail_waitlist.korail_sidecar import browser_protection as protection

contract_symbols = %r
protection_symbols = %r
print(json.dumps({
    "passive": passive,
    "contract_identity": all(
        getattr(legacy, name) is getattr(contracts, name) for name in contract_symbols
    ),
    "protection_identity": all(
        getattr(legacy, name) is getattr(protection, name) for name in protection_symbols
    ),
}, sort_keys=True))
""" % (sorted(CONTRACT_SYMBOLS), sorted(PROTECTION_SYMBOLS))

    for order in ("contracts-first", "protection-first", "legacy-first"):
        completed = subprocess.run(
            [sys.executable, "-c", script, order],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "passive": True,
            "contract_identity": True,
            "protection_identity": True,
        }
