from __future__ import annotations

import json
import subprocess
import sys
from inspect import signature
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import rail_waitlist.services as services_module
import rail_waitlist.watch_management.http as watch_http
from rail_waitlist.models import Watch
from rail_waitlist.watch_management.lookup_application import WatchLookupNotFound
from rail_waitlist.watch_management.lookup_application import find_watch as find_watch_application

API_ROOT = Path(__file__).resolve().parents[1]


class RecordingSession:
    def __init__(self, result: Watch | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[type[Watch], str]] = []

    async def get(self, model: type[Watch], identity: str) -> Watch | None:
        self.calls.append((model, identity))
        if self.error is not None:
            raise self.error
        return self.result


def test_lookup_and_compatibility_wrappers_keep_explicit_public_contracts() -> None:
    assert find_watch_application.__module__ == (
        "rail_waitlist.watch_management.lookup_application"
    )
    assert services_module.find_watch.__module__ == "rail_waitlist.services"
    assert signature(services_module.find_watch) == signature(find_watch_application)
    assert signature(watch_http._find_watch_or_404) == signature(find_watch_application)


async def test_lookup_returns_the_same_watch_after_one_session_get() -> None:
    watch = cast(Watch, object())
    session = RecordingSession(result=watch)

    result = await find_watch_application(cast(AsyncSession, session), "watch-1")

    assert result is watch
    assert session.calls == [(Watch, "watch-1")]


async def test_lookup_raises_transport_independent_not_found_after_one_get() -> None:
    session = RecordingSession()

    with pytest.raises(WatchLookupNotFound) as caught:
        await find_watch_application(cast(AsyncSession, session), "missing")

    assert str(caught.value) == "watch not found"
    assert session.calls == [(Watch, "missing")]


async def test_lookup_does_not_translate_database_errors() -> None:
    database_error = RuntimeError("database unavailable")
    session = RecordingSession(error=database_error)

    with pytest.raises(RuntimeError) as caught:
        await find_watch_application(cast(AsyncSession, session), "watch-1")

    assert caught.value is database_error


@pytest.mark.parametrize("module", [services_module, watch_http])
async def test_transport_wrappers_translate_only_canonical_not_found(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
) -> None:
    async def missing(_session: AsyncSession, _watch_id: str) -> Watch:
        raise WatchLookupNotFound("watch not found")

    monkeypatch.setattr(module, "find_watch_application", missing)
    wrapper = (
        services_module.find_watch if module is services_module else watch_http._find_watch_or_404
    )

    with pytest.raises(HTTPException) as caught:
        await wrapper(cast(AsyncSession, object()), "missing")

    assert caught.value.status_code == 404
    assert caught.value.detail == "watch not found"


@pytest.mark.parametrize("module", [services_module, watch_http])
async def test_transport_wrappers_preserve_found_identity_and_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
) -> None:
    watch = cast(Watch, object())

    async def found(_session: AsyncSession, _watch_id: str) -> Watch:
        return watch

    monkeypatch.setattr(module, "find_watch_application", found)
    wrapper = (
        services_module.find_watch if module is services_module else watch_http._find_watch_or_404
    )
    assert await wrapper(cast(AsyncSession, object()), "watch-1") is watch

    unexpected = RuntimeError("unexpected lookup failure")

    async def failed(_session: AsyncSession, _watch_id: str) -> Watch:
        raise unexpected

    monkeypatch.setattr(module, "find_watch_application", failed)
    with pytest.raises(RuntimeError) as caught:
        await wrapper(cast(AsyncSession, object()), "watch-1")

    assert caught.value is unexpected


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/watches/missing-watch", None),
        ("PATCH", "/api/v1/watches/missing-watch", {}),
        ("DELETE", "/api/v1/watches/missing-watch", None),
        ("POST", "/api/v1/watches/missing-watch/start", None),
        ("POST", "/api/v1/watches/missing-watch/pause", None),
        ("POST", "/api/v1/watches/missing-watch/cancel", None),
        (
            "POST",
            "/api/v1/watches/missing-watch/mock-transition?target=watching",
            None,
        ),
    ],
)
async def test_every_watch_lookup_route_preserves_exact_missing_contract(
    client: object,
    method: str,
    path: str,
    body: object,
) -> None:
    request = client.request
    response = await request(method, path, json=body)

    assert response.status_code == 404
    assert response.json() == {"detail": "watch not found"}


@pytest.mark.parametrize("import_order", ["canonical-first", "services-first", "http-first"])
def test_lookup_import_orders_preserve_canonical_binding(import_order: str) -> None:
    script = r"""
import json
import sys

if sys.argv[1] == "canonical-first":
    import rail_waitlist.watch_management.lookup_application as canonical
    import rail_waitlist.services as services
    import rail_waitlist.watch_management.http as http
elif sys.argv[1] == "services-first":
    import rail_waitlist.services as services
    import rail_waitlist.watch_management.lookup_application as canonical
    import rail_waitlist.watch_management.http as http
else:
    import rail_waitlist.watch_management.http as http
    import rail_waitlist.watch_management.lookup_application as canonical
    import rail_waitlist.services as services

print(json.dumps({
    "canonical_module": canonical.find_watch.__module__,
    "http_binding": http.find_watch_application is canonical.find_watch,
    "services_binding": services.find_watch_application is canonical.find_watch,
    "services_is_wrapper": services.find_watch is not canonical.find_watch,
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
        "canonical_module": "rail_waitlist.watch_management.lookup_application",
        "http_binding": True,
        "services_binding": True,
        "services_is_wrapper": True,
    }
