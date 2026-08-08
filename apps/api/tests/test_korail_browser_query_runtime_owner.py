from __future__ import annotations

import ast
import asyncio
import base64
import json
import pickle
import subprocess
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.korail_browser_seat_source as legacy
from rail_waitlist.korail_browser_automation import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
)
from rail_waitlist.provider_adapters import korail_browser_query_runtime as owner

API_ROOT = Path(__file__).resolve().parents[1]
KOREA = ZoneInfo("Asia/Seoul")
LEGACY_PICKLES = {
    "cache": (
        "gASVPAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jAtfQ2FjaGVFbnRyeZSTlC4="
    ),
    "query": (
        "gASVPwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jA5fUXVlcnlDb29sZG93bpSTlC4="
    ),
    "provider": (
        "gASVQgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jBFfUHJvdmlkZXJDb29sZG93bpSTlC4="
    ),
    "drain": (
        "gASVXAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jCtLb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5kcmFpbl9wZW5kaW5nX2NhbGxzlJOULg=="
    ),
    "search": (
        "gASVUAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jB9Lb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5fc2VhcmNolJOULg=="
    ),
    "load": (
        "gASVTgAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jB1Lb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5fbG9hZJSTlC4="
    ),
    "cooldown": (
        "gASVVwAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WU"
        "jCZLb3JhaWxCcm93c2VyU2VhdFNvdXJjZS5fb3Blbl9jb29sZG93bpSTlC4="
    ),
}


def _request(*, origin: str = "서울", destination: str = "부산") -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin=origin,
        destination=destination,
        travel_date=date(2026, 8, 3),
        departure_from=time(0),
        departure_to=time(18),
        passenger_count=1,
    )


def _result(request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
    departure = datetime.combine(request.travel_date, time(10), tzinfo=KOREA)
    return BrowserSeatSearchResult(
        origin=request.origin,
        destination=request.destination,
        travel_date=request.travel_date,
        passenger_count=1,
        observed_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
        trains=[
            BrowserTrainSnapshot(
                train_number="43",
                train_type="KTX",
                departure_at=departure,
                arrival_at=departure.replace(hour=12),
                standard="available",
                first="sold_out",
            )
        ],
    )


class ControlledTransport:
    def __init__(self, result: BrowserSeatSearchResult, *, blocked: bool = False) -> None:
        self.result = result
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.events: list[str] = []

    async def search(self, _request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.result

    async def close(self) -> None:
        self.events.append("close")


def _source(transport: ControlledTransport) -> legacy.KorailBrowserSeatSource:
    return legacy.KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://korail-browser:8091",
        cache_ttl_seconds=30,
        timeout_seconds=1,
        rate_limit_cooldown_seconds=1800,
        protection_cooldown_seconds=300,
        transport=transport,
        monotonic=lambda: 100.0,
        now=lambda: datetime(2026, 8, 1, 12, tzinfo=KOREA),
    )


def test_runtime_owner_preserves_legacy_aliases_pickles_and_dependency_surface() -> None:
    assert legacy.SOURCE_FAILURE_COOLDOWN_MAX_SECONDS == 300
    assert legacy._CacheEntry is owner._CacheEntry
    assert legacy._QueryCooldown is owner._QueryCooldown
    assert legacy._ProviderCooldown is owner._ProviderCooldown
    assert legacy.asyncio is owner.asyncio
    assert legacy.dataclass is owner.dataclass
    assert pickle.loads(base64.b64decode(LEGACY_PICKLES["cache"])) is owner._CacheEntry
    assert pickle.loads(base64.b64decode(LEGACY_PICKLES["query"])) is owner._QueryCooldown
    assert pickle.loads(base64.b64decode(LEGACY_PICKLES["provider"])) is owner._ProviderCooldown
    assert (
        pickle.loads(base64.b64decode(LEGACY_PICKLES["drain"]))
        is legacy.KorailBrowserSeatSource.drain_pending_calls
    )
    assert (
        pickle.loads(base64.b64decode(LEGACY_PICKLES["search"]))
        is legacy.KorailBrowserSeatSource._search
    )
    assert (
        pickle.loads(base64.b64decode(LEGACY_PICKLES["load"]))
        is legacy.KorailBrowserSeatSource._load
    )
    assert (
        pickle.loads(base64.b64decode(LEGACY_PICKLES["cooldown"]))
        is legacy.KorailBrowserSeatSource._open_cooldown
    )
    wildcard: dict[str, object] = {}
    exec("from rail_waitlist.korail_browser_seat_source import *", wildcard)
    assert {"SOURCE_FAILURE_COOLDOWN_MAX_SECONDS", "asyncio", "dataclass"} <= wildcard.keys()


def test_runtime_owner_has_no_source_reverse_dependency_or_duplicate_legacy_definitions() -> None:
    owner_path = (
        API_ROOT / "src" / "rail_waitlist" / "provider_adapters" / "korail_browser_query_runtime.py"
    )
    source_path = API_ROOT / "src" / "rail_waitlist" / "korail_browser_seat_source.py"
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    owner_imports = {
        node.module
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    source_definitions = {
        node.name
        for node in source_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "korail_browser_seat_source" not in owner_imports
    assert {"_CacheEntry", "_QueryCooldown", "_ProviderCooldown"}.isdisjoint(source_definitions)

    code = """
import json
import sys
from rail_waitlist.provider_adapters import korail_browser_query_runtime as owner
legacy_loaded_before = "rail_waitlist.korail_browser_seat_source" in sys.modules
import rail_waitlist.korail_browser_seat_source as legacy
print(json.dumps({
    "legacy_loaded_before": legacy_loaded_before,
    "cache": legacy._CacheEntry is owner._CacheEntry,
    "query": legacy._QueryCooldown is owner._QueryCooldown,
    "provider": legacy._ProviderCooldown is owner._ProviderCooldown,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload == {
        "legacy_loaded_before": False,
        "cache": True,
        "query": True,
        "provider": True,
    }


async def test_source_resolves_load_and_transport_after_construction() -> None:
    request = _request()
    transport = ControlledTransport(_result(request))
    source = _source(transport)
    loaded: list[owner.QueryKey] = []

    async def replacement_load(
        key: owner.QueryKey,
        value: BrowserSeatSearchRequest,
    ) -> BrowserSeatSearchResult:
        loaded.append(key)
        return _result(value)

    source._load = replacement_load
    assert await source._search(request) == _result(request)
    assert loaded == [request.cache_key()]

    first_request = _request()
    second_request = _request(origin="대전", destination="서울")
    first = ControlledTransport(_result(first_request), blocked=True)
    second = ControlledTransport(_result(second_request))
    gated_source = _source(first)
    first_task = asyncio.create_task(gated_source._search(first_request))
    await asyncio.wait_for(first.started.wait(), timeout=1)
    second_task = asyncio.create_task(gated_source._search(second_request))
    await asyncio.sleep(0)
    assert first.calls == 1
    gated_source._transport = second
    first.release.set()

    first_result, second_result = await asyncio.gather(first_task, second_task)
    assert first_result.origin == "서울"
    assert second_result.origin == "대전"
    assert first.calls == 1
    assert second.calls == 1


async def test_cancelled_waiter_keeps_shared_load_until_runtime_drain() -> None:
    request = _request()
    transport = ControlledTransport(_result(request), blocked=True)
    source = _source(transport)
    waiter = asyncio.create_task(source._search(request))
    await asyncio.wait_for(transport.started.wait(), timeout=1)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    drain = asyncio.create_task(source.drain_pending_calls())
    await asyncio.sleep(0)
    assert not drain.done()

    transport.release.set()
    await asyncio.wait_for(drain, timeout=1)
    assert source._query_runtime._inflight == {}
    assert transport.calls == 1


async def test_close_uses_public_drain_seam_before_transport_close() -> None:
    request = _request()
    transport = ControlledTransport(_result(request))
    source = _source(transport)

    async def drain() -> None:
        transport.events.append("drain")

    source.drain_pending_calls = drain
    await source.close()

    assert transport.events == ["drain", "close"]
