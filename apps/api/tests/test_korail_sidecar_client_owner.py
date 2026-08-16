from __future__ import annotations

import ast
import base64
import json
import logging
import pickle
import subprocess
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Self

import httpx
import pytest

from rail_waitlist import korail_browser_seat_source as legacy
from rail_waitlist.korail_browser_automation import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
)
from rail_waitlist.korail_sidecar import client as owner
from rail_waitlist.korail_sidecar.contracts import (
    KorailCredentialRequest,
    KorailReservationConfirmationRequest,
    KorailReserveOnceRequest,
)
from rail_waitlist.provider_call_context import (
    REQUEST_ID_HEADER,
    REQUEST_TIMEOUT_MS_HEADER,
    bind_request_id,
    validated_log_id,
)

API_ROOT = Path(__file__).resolve().parents[1]
MOVED_SYMBOLS = {
    "BrowserAdapterTransport",
    "HttpBrowserAdapterTransport",
    "_AdapterFailure",
}
LEGACY_PICKLES = {
    "BrowserAdapterTransport": (
        "gASVSAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WUjBdC"
        "cm93c2VyQWRhcHRlclRyYW5zcG9ydJSTlC4="
    ),
    "HttpBrowserAdapterTransport": (
        "gASVTAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WUjBtI"
        "dHRwQnJvd3NlckFkYXB0ZXJUcmFuc3BvcnSUk5Qu"
    ),
    "_AdapterFailure": (
        "gASVQAAAAAAAAACMKHJhaWxfd2FpdGxpc3Qua29yYWlsX2Jyb3dzZXJfc2VhdF9zb3VyY2WUjA9f"
        "QWRhcHRlckZhaWx1cmWUk5Qu"
    ),
}


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> object:
        return self._payload


class FakeHttpClient:
    def __init__(
        self, response: FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.requests: list[tuple[str, str, object | None]] = []
        self.request_headers: list[dict[str, str] | None] = []

    async def post(
        self,
        path: str,
        *,
        json: object,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.requests.append(("POST", path, json))
        self.request_headers.append(headers)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    async def get(self, path: str) -> FakeResponse:
        self.requests.append(("GET", path, None))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    async def aclose(self) -> None:
        return


class FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self.lines = lines
        self.status_code = status_code

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeStreamHttpClient:
    def __init__(self, lines: list[str]) -> None:
        self.response = FakeStreamResponse(lines)
        self.requests: list[tuple[str, str, object]] = []
        self.request_headers: list[dict[str, str] | None] = []

    def stream(
        self,
        method: str,
        path: str,
        *,
        json: object,
        headers: dict[str, str] | None = None,
    ) -> FakeStreamResponse:
        self.requests.append((method, path, json))
        self.request_headers.append(headers)
        return self.response


def search_request() -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        departure_from=time(14),
        departure_to=time(18),
        passenger_count=1,
    )


def reserve_request() -> KorailReserveOnceRequest:
    return KorailReserveOnceRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 7),
        train_number="43",
        train_type="KTX",
        departure_time=time(12),
        arrival_time=time(14),
        seat_class="general",
        credential=KorailCredentialRequest(
            login_method="membership_number",
            login_id="membership-secret",
            password="password-secret",
            version="credential:7",
        ),
    )


def confirmation_request() -> KorailReservationConfirmationRequest:
    return KorailReservationConfirmationRequest(
        attempt_id="attempt-fixture",
        candidate_id="candidate-fixture",
        train_number="43",
        origin="서울",
        destination="부산",
        departure_at=datetime(2026, 8, 7, 3, tzinfo=UTC),
        arrival_at=datetime(2026, 8, 7, 5, tzinfo=UTC),
        seat_class="standard",
        passenger_count=1,
        credential_version=7,
    )


def transport_with(client: FakeHttpClient) -> owner.HttpBrowserAdapterTransport:
    transport = object.__new__(owner.HttpBrowserAdapterTransport)
    transport._client = client  # type: ignore[assignment]
    return transport


def test_transport_leaf_has_exact_legacy_aliases_and_import_boundary() -> None:
    for symbol in MOVED_SYMBOLS:
        canonical = getattr(owner, symbol)
        assert getattr(legacy, symbol) is canonical
        assert canonical.__module__ == "rail_waitlist.korail_sidecar.client"
    assert legacy.Protocol is owner.Protocol
    assert legacy.urlsplit is owner.urlsplit
    assert legacy.httpx is owner.httpx

    legacy_path = API_ROOT / "src" / "rail_waitlist" / "korail_browser_seat_source.py"
    legacy_tree = ast.parse(legacy_path.read_text(encoding="utf-8"), filename=str(legacy_path))
    legacy_definitions = {
        node.name
        for node in legacy_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id: node.value
        for node in legacy_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in MOVED_SYMBOLS
    }
    client_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in legacy_tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "korail_sidecar"
        for alias in node.names
    }

    assert legacy_definitions.isdisjoint(MOVED_SYMBOLS)
    assert set(assignments) == MOVED_SYMBOLS
    assert client_imports == {("korail_sidecar", 1, "client", "_client_owner")}
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert value.attr == symbol
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_client_owner"

    owner_path = API_ROOT / "src" / "rail_waitlist" / "korail_sidecar" / "client.py"
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    owner_definitions = {
        node.name
        for node in owner_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports_from = {
        (node.module, node.level)
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert owner_definitions == MOVED_SYMBOLS
    assert imports_from == {
        ("__future__", 0),
        ("collections.abc", 0),
        ("typing", 0),
        ("urllib.parse", 0),
        ("pydantic", 0),
        ("browser_contracts", 1),
        ("reservations.contracts", 2),
        ("timetable_management.schemas", 2),
        ("provider_call_context", 2),
        ("contracts", 1),
    }


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_legacy_transport_pickle_globals_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["owner", "legacy"])
def test_transport_import_orders_keep_one_owner_without_canonical_reentry(
    first_import: str,
) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "owner": "rail_waitlist.korail_sidecar.client",
    "legacy": "rail_waitlist.korail_browser_seat_source",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_browser_seat_source" in sys.modules
owner = importlib.import_module("rail_waitlist.korail_sidecar.client")
legacy = importlib.import_module("rail_waitlist.korail_browser_seat_source")
symbols = ("BrowserAdapterTransport", "HttpBrowserAdapterTransport", "_AdapterFailure")
print(json.dumps({
    "identity": all(getattr(legacy, symbol) is getattr(owner, symbol) for symbol in symbols),
    "legacy_loaded_before": legacy_loaded_before,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, first_import],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "identity": True,
        "legacy_loaded_before": first_import == "legacy",
    }


def test_source_resolves_legacy_transport_global_at_construction_time(monkeypatch) -> None:
    sentinel = object()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def replacement(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(legacy, "HttpBrowserAdapterTransport", replacement)
    source = legacy.KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://korail-browser-adapter:8001",
        cache_ttl_seconds=1,
        timeout_seconds=30,
        rate_limit_cooldown_seconds=1800,
        protection_cooldown_seconds=300,
    )

    assert source._transport is sentinel
    assert calls == [
        (
            ("http://korail-browser-adapter:8001", 30, None),
            {"allow_fullstack_test_url": False},
        )
    ]


async def test_transport_constructor_keeps_exact_internal_origin_and_http_policy(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    class ClientStub:
        async def aclose(self) -> None:
            return

    def client_factory(**kwargs: object) -> ClientStub:
        captured.append(kwargs)
        return ClientStub()

    monkeypatch.setattr(owner.httpx, "AsyncClient", client_factory)
    production = owner.HttpBrowserAdapterTransport(
        "http://korail-browser-adapter:8001",
        30,
        "adapter-token",
    )
    fixture = owner.HttpBrowserAdapterTransport(
        "http://e2e-fake-upstream:8001",
        30,
        None,
        allow_fullstack_test_url=True,
    )
    for invalid in (
        "https://korail-browser-adapter:8001",
        "http://localhost:8001",
        "http://user@korail-browser-adapter:8001",
        "http://korail-browser-adapter:8001/path",
        "http://korail-browser-adapter:8001?query=1",
        "http://e2e-fake-upstream:8001",
    ):
        with pytest.raises(ValueError, match="exact internal sidecar origin"):
            owner.HttpBrowserAdapterTransport(invalid, 30, None)

    assert captured[0]["base_url"] == "http://korail-browser-adapter:8001"
    assert captured[0]["follow_redirects"] is False
    assert captured[0]["trust_env"] is False
    assert captured[0]["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer adapter-token",
    }
    assert captured[1]["headers"] == {"Accept": "application/json"}
    await production.close()
    await fixture.close()


async def test_search_transport_preserves_failure_classification() -> None:
    cases = (
        (429, {}, True, False),
        (403, {}, False, True),
        (423, {}, False, True),
        (500, {}, False, False),
        (200, {}, False, False),
    )
    for status, payload, rate_limited, protection in cases:
        transport = transport_with(FakeHttpClient(FakeResponse(status, payload)))
        with pytest.raises(owner._AdapterFailure) as captured:
            await transport.search(search_request())
        expected_reason = (
            "provider_access_restricted" if status in {403, 423, 429} else "source_unavailable"
        )
        assert captured.value.reason == expected_reason
        assert captured.value.rate_limited is rate_limited
        assert captured.value.protection is protection

    timeout = transport_with(FakeHttpClient(error=httpx.ReadTimeout("timeout")))
    with pytest.raises(owner._AdapterFailure) as captured:
        await timeout.search(search_request())
    assert captured.value.reason == "source_unavailable"
    assert captured.value.deadline_exceeded is True


async def test_search_transport_propagates_ambient_request_and_deadline_ids(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "4f7e5fa7b2aa4e70a8fd2cf4a535f1ee"
    response = BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        passenger_count=1,
        observed_at=datetime(2026, 8, 3, tzinfo=UTC),
        trains=[],
    )
    client = FakeHttpClient(FakeResponse(200, response.model_dump(mode="json")))
    transport = transport_with(client)
    monkeypatch.setattr(owner, "remaining_request_timeout_ms", lambda: 2234)

    with caplog.at_level(logging.INFO), bind_request_id(request_id):
        result = await transport.search(search_request())

    assert result.trains == []
    assert client.request_headers == [
        {
            REQUEST_ID_HEADER: request_id,
            REQUEST_TIMEOUT_MS_HEADER: "1234",
        }
    ]
    messages = [record.getMessage() for record in caplog.records]
    lifecycle = [message for message in messages if "provider_sidecar_request_" in message]
    assert len(lifecycle) == 2
    assert all(f"request_id={request_id}" in message for message in lifecycle)
    assert "event=provider_sidecar_request_started" in lifecycle[0]
    assert "event=provider_sidecar_request_completed" in lifecycle[1]


async def test_search_transport_generates_a_new_request_id_for_each_unbound_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        passenger_count=1,
        observed_at=datetime(2026, 8, 3, tzinfo=UTC),
        trains=[],
    )
    client = FakeHttpClient(FakeResponse(200, response.model_dump(mode="json")))
    transport = transport_with(client)
    monkeypatch.setattr(owner, "remaining_request_timeout_ms", lambda: None)

    await transport.search(search_request())
    await transport.search(search_request())

    request_ids: list[str] = []
    for headers in client.request_headers:
        assert headers is not None
        request_ids.append(headers[REQUEST_ID_HEADER])
    assert len(set(request_ids)) == 2
    assert all(validated_log_id(request_id) == request_id for request_id in request_ids)


async def test_search_transport_skips_sidecar_when_ambient_deadline_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeHttpClient(FakeResponse(200, {}))
    transport = transport_with(client)
    monkeypatch.setattr(owner, "remaining_request_timeout_ms", lambda: 1000)

    with caplog.at_level(logging.WARNING), pytest.raises(owner._AdapterFailure):
        await transport.search(search_request())

    assert client.requests == []
    assert "event=provider_sidecar_request_failed" in caplog.text
    assert "outcome=timeout" in caplog.text


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (httpx.ReadTimeout("timeout"), "timeout"),
        (httpx.ConnectError("offline"), "transport_error"),
    ],
)
async def test_search_transport_logs_closed_transport_outcomes(
    error: httpx.HTTPError,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = transport_with(FakeHttpClient(error=error))

    with caplog.at_level(logging.WARNING), pytest.raises(owner._AdapterFailure) as captured:
        await transport.search(search_request())

    assert "event=provider_sidecar_request_failed" in caplog.text
    assert f"outcome={outcome}" in caplog.text
    assert captured.value.deadline_exceeded is (outcome == "timeout")


async def test_search_transport_projects_sidecar_deadline_as_typed_failure() -> None:
    transport = transport_with(
        FakeHttpClient(FakeResponse(504, {"detail": {"reason": "source_unavailable"}}))
    )

    with pytest.raises(owner._AdapterFailure) as captured:
        await transport.search(search_request())

    assert captured.value.reason == "source_unavailable"
    assert captured.value.deadline_exceeded is True


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (FakeResponse(500, {}), "http_status"),
        (FakeResponse(200, {}), "validation_error"),
    ],
)
async def test_search_transport_logs_closed_response_failures(
    response: FakeResponse,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = transport_with(FakeHttpClient(response))

    with caplog.at_level(logging.WARNING), pytest.raises(owner._AdapterFailure):
        await transport.search(search_request())

    assert "event=provider_sidecar_request_failed" in caplog.text
    assert f"outcome={outcome}" in caplog.text


@pytest.mark.parametrize(
    ("payload", "retry_after"),
    [
        ({"detail": {"reason": "source_unavailable"}}, "300"),
        ({"detail": {"reason": "source_unavailable"}}, "1"),
    ],
)
async def test_search_transport_preserves_explicit_provider_outage_scope(
    payload: object,
    retry_after: str,
) -> None:
    transport = transport_with(
        FakeHttpClient(FakeResponse(503, payload, headers={"retry-after": retry_after}))
    )

    with pytest.raises(owner._AdapterFailure) as captured:
        await transport.search(search_request())

    assert captured.value.reason == "source_unavailable"
    assert captured.value.cooldown_scope == "provider"
    assert captured.value.retry_after_seconds == int(retry_after)


@pytest.mark.parametrize(
    ("payload", "headers"),
    [
        ({"detail": {"reason": "source_unavailable"}}, {}),
        ({"detail": {"reason": "source_unavailable"}}, {"retry-after": "0"}),
        ({"detail": {"reason": "source_unavailable"}}, {"retry-after": "86401"}),
        ({"detail": {"reason": "other"}}, {"retry-after": "300"}),
        ("not-an-object", {"retry-after": "300"}),
    ],
)
async def test_search_transport_keeps_untrusted_503_retry_metadata_query_local(
    payload: object,
    headers: dict[str, str],
) -> None:
    transport = transport_with(FakeHttpClient(FakeResponse(503, payload, headers=headers)))

    with pytest.raises(owner._AdapterFailure) as captured:
        await transport.search(search_request())

    assert captured.value.cooldown_scope == "query"
    assert captured.value.retry_after_seconds is None


async def test_session_state_keeps_all_non_200_responses_generic() -> None:
    for status in (403, 423, 429, 500):
        transport = transport_with(FakeHttpClient(FakeResponse(status, {})))

        with pytest.raises(owner._AdapterFailure) as captured:
            await transport.session_state()

        assert captured.value.reason == "source_unavailable"
        assert captured.value.rate_limited is False
        assert captured.value.protection is False


async def test_reserve_transport_serializes_secret_values_only_at_wire_boundary() -> None:
    client = FakeHttpClient(
        FakeResponse(
            200,
            {
                "outcome": "payment_required",
                "reason": "payment_required",
                "seat_clicked": True,
                "reservation_clicked": True,
            },
        )
    )
    transport = transport_with(client)
    request = KorailReserveOnceRequest(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 7),
        train_number="43",
        train_type="KTX",
        departure_time=time(12),
        arrival_time=time(14),
        seat_class="general",
        credential=KorailCredentialRequest(
            login_method="membership_number",
            login_id="membership-secret",
            password="password-secret",
            version="credential:7",
        ),
    )

    result = await transport.reserve(request)

    assert result.outcome == "payment_required"
    _, path, payload = client.requests[0]
    assert path == "/v1/reserve-once"
    assert isinstance(payload, dict)
    assert payload["credential"] == {
        "login_method": "membership_number",
        "login_id": "membership-secret",
        "password": "password-secret",
        "version": "credential:7",
    }


@pytest.mark.parametrize(
    ("status_code", "expected_outcome"),
    [
        (429, "rate_limited"),
        (403, "provider_blocked"),
        (423, "provider_blocked"),
        (500, "http_status"),
    ],
)
async def test_reservation_transport_logs_closed_http_failure_classification(
    status_code: int,
    expected_outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = transport_with(
        FakeHttpClient(FakeResponse(status_code, {"reason": "provider-secret-fixture"}))
    )

    with caplog.at_level(logging.WARNING), pytest.raises(owner._AdapterFailure):
        await transport.reserve(reserve_request())

    assert f"outcome={expected_outcome}" in caplog.text
    assert f"status_code={status_code}" in caplog.text
    assert "provider-secret-fixture" not in caplog.text


async def test_reserve_progress_stream_is_ordered_and_sent_once() -> None:
    stage_names = (
        "authenticated_session_ready",
        "target_rechecked",
        "seat_selected",
        "reservation_requested",
    )
    times = [datetime(2026, 8, 7, 3, 0, index, tzinfo=UTC) for index in range(4)]
    lines = [
        json.dumps({"type": "progress", "stage": stage, "occurred_at": occurred_at.isoformat()})
        for stage, occurred_at in zip(stage_names, times, strict=True)
    ]
    lines.append(
        json.dumps(
            {
                "type": "result",
                "result": {
                    "outcome": "payment_required",
                    "reason": "payment_required",
                    "seat_clicked": True,
                    "reservation_clicked": True,
                    "session_ready_at": times[0].isoformat(),
                    "target_rechecked_at": times[1].isoformat(),
                    "seat_selected_at": times[2].isoformat(),
                    "reservation_requested_at": times[3].isoformat(),
                },
            }
        )
    )
    client = FakeStreamHttpClient(lines)
    transport = transport_with(client)  # type: ignore[arg-type]
    observed = []

    async def on_progress(stage):
        observed.append(stage)

    result = await transport.reserve_with_progress(reserve_request(), on_progress)

    assert result.outcome == "payment_required"
    assert [item.stage for item in observed] == list(stage_names)
    assert len(client.requests) == 1
    assert client.requests[0][1] == "/v1/reserve-once/stream"


async def test_reserve_progress_stream_preserves_click_error_without_request_stage() -> None:
    stage_names = (
        "authenticated_session_ready",
        "target_rechecked",
        "seat_selected",
    )
    times = [datetime(2026, 8, 7, 3, 0, index, tzinfo=UTC) for index in range(3)]
    lines = [
        json.dumps({"type": "progress", "stage": stage, "occurred_at": occurred_at.isoformat()})
        for stage, occurred_at in zip(stage_names, times, strict=True)
    ]
    lines.append(
        json.dumps(
            {
                "type": "result",
                "result": {
                    "outcome": "failed",
                    "reason": "reservation_result_unknown:reservation_click_error",
                    "seat_clicked": True,
                    "reservation_clicked": False,
                    "session_ready_at": times[0].isoformat(),
                    "target_rechecked_at": times[1].isoformat(),
                    "seat_selected_at": times[2].isoformat(),
                },
            }
        )
    )
    transport = transport_with(FakeStreamHttpClient(lines))  # type: ignore[arg-type]
    observed = []

    async def on_progress(stage):
        observed.append(stage)

    result = await transport.reserve_with_progress(reserve_request(), on_progress)

    assert [item.stage for item in observed] == list(stage_names)
    assert result.reason == "reservation_result_unknown:reservation_click_error"
    assert result.reservation_clicked is False
    assert result.reservation_requested_at is None


@pytest.mark.parametrize(
    "lines",
    [
        [],
        ['{"type":"progress","stage":"target_rechecked","occurred_at":"2026-08-07T03:00:00Z"}'],
        [
            (
                '{"type":"result","result":{"outcome":"failed","reason":"failed",'
                '"seat_clicked":false,"reservation_clicked":false}}'
            ),
            (
                '{"type":"result","result":{"outcome":"failed","reason":"failed",'
                '"seat_clicked":false,"reservation_clicked":false}}'
            ),
        ],
    ],
)
async def test_reserve_progress_stream_fails_closed_without_one_valid_sequence(
    lines: list[str],
) -> None:
    client = FakeStreamHttpClient(lines)
    transport = transport_with(client)  # type: ignore[arg-type]

    async def on_progress(stage):
        return None

    with pytest.raises(owner._AdapterFailure) as captured:
        await transport.reserve_with_progress(reserve_request(), on_progress)

    assert captured.value.reason == "source_unavailable"
    assert captured.value.reservation_command_uncertain is True
    assert len(client.requests) == 1


async def test_reservation_transports_propagate_one_ambient_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "4f7e5fa7b2aa4e70a8fd2cf4a535f1ee"
    reserve_client = FakeHttpClient(
        FakeResponse(
            200,
            {
                "outcome": "failed",
                "reason": "fixture_terminal",
                "seat_clicked": False,
                "reservation_clicked": False,
            },
        )
    )
    stream_client = FakeStreamHttpClient(
        [
            json.dumps(
                {
                    "type": "result",
                    "result": {
                        "outcome": "failed",
                        "reason": "fixture_terminal",
                        "seat_clicked": False,
                        "reservation_clicked": False,
                    },
                }
            )
        ]
    )
    confirmation_client = FakeHttpClient(
        FakeResponse(
            200,
            {
                "outcome": "inconclusive",
                "diagnostic_code": "official_evidence_insufficient",
                "source": "korail-same-session-detail",
                "observed_at": "2026-08-07T03:00:00Z",
            },
        )
    )

    async def on_progress(_stage: object) -> None:
        return None

    with caplog.at_level(logging.INFO), bind_request_id(request_id):
        await transport_with(reserve_client).reserve(reserve_request())
        await transport_with(stream_client).reserve_with_progress(  # type: ignore[arg-type]
            reserve_request(),
            on_progress,
        )
        await transport_with(confirmation_client).confirm_reservation(confirmation_request())

    assert reserve_client.request_headers == [{REQUEST_ID_HEADER: request_id}]
    assert stream_client.request_headers == [{REQUEST_ID_HEADER: request_id}]
    assert confirmation_client.request_headers == [{REQUEST_ID_HEADER: request_id}]
    lifecycle = [
        record.getMessage()
        for record in caplog.records
        if "event=provider_sidecar_request_" in record.getMessage()
    ]
    assert len(lifecycle) == 6
    terminal_outcomes = {
        "reserve_once": "failed",
        "reserve_once_stream": "failed",
        "confirm_reservation": "inconclusive",
    }
    for operation, terminal_outcome in terminal_outcomes.items():
        correlated = [message for message in lifecycle if f"operation={operation} " in message]
        assert len(correlated) == 2
        assert all(f"request_id={request_id}" in message for message in correlated)
        completed = next(message for message in correlated if "request completed" in message)
        assert "outcome=completed" in completed
        assert f"terminal_outcome={terminal_outcome}" in completed
        assert "outcome=success" not in completed
        if operation == "confirm_reservation":
            assert "diagnostic_code=official_evidence_insufficient" in completed
            assert "phase=completed" in completed
    assert "attempt-fixture" not in caplog.text
    assert "candidate-fixture" not in caplog.text


async def test_reservation_transports_generate_fresh_ids_without_ambient_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_ids = iter(
        (
            "11111111111141118111111111111111",
            "22222222222242228222222222222222",
            "33333333333343338333333333333333",
        )
    )
    monkeypatch.setattr(owner, "new_log_id", lambda: next(generated_ids))
    reserve_client = FakeHttpClient(
        FakeResponse(
            200,
            {
                "outcome": "failed",
                "reason": "fixture_terminal",
                "seat_clicked": False,
                "reservation_clicked": False,
            },
        )
    )
    stream_client = FakeStreamHttpClient(
        [
            json.dumps(
                {
                    "type": "result",
                    "result": {
                        "outcome": "failed",
                        "reason": "fixture_terminal",
                        "seat_clicked": False,
                        "reservation_clicked": False,
                    },
                }
            )
        ]
    )
    confirmation_client = FakeHttpClient(
        FakeResponse(
            200,
            {
                "outcome": "inconclusive",
                "diagnostic_code": "official_evidence_insufficient",
                "source": "korail-same-session-detail",
                "observed_at": "2026-08-07T03:00:00Z",
            },
        )
    )

    async def on_progress(_stage: object) -> None:
        return None

    await transport_with(reserve_client).reserve(reserve_request())
    await transport_with(stream_client).reserve_with_progress(  # type: ignore[arg-type]
        reserve_request(),
        on_progress,
    )
    await transport_with(confirmation_client).confirm_reservation(confirmation_request())

    assert reserve_client.request_headers == [
        {REQUEST_ID_HEADER: "11111111111141118111111111111111"}
    ]
    assert stream_client.request_headers == [
        {REQUEST_ID_HEADER: "22222222222242228222222222222222"}
    ]
    assert confirmation_client.request_headers == [
        {REQUEST_ID_HEADER: "33333333333343338333333333333333"}
    ]


async def test_login_transports_serialize_secret_values_only_at_wire_boundary() -> None:
    credential = KorailCredentialRequest(
        login_method="membership_number",
        login_id="membership-secret",
        password="password-secret",
        version="credential:7",
    )
    expected_credential = {
        "login_method": "membership_number",
        "login_id": "membership-secret",
        "password": "password-secret",
        "version": "credential:7",
    }
    for method_name, expected_path in (
        ("verify_login", "/v1/verify-login"),
        ("prewarm_login", "/v1/prewarm-login"),
    ):
        client = FakeHttpClient(FakeResponse(200, {"outcome": "authenticated"}))
        transport = transport_with(client)

        result = await getattr(transport, method_name)(
            owner.KorailLoginVerifyRequest(credential=credential)
        )

        assert result.outcome == "authenticated"
        _, path, payload = client.requests[0]
        assert path == expected_path
        assert isinstance(payload, dict)
        assert payload["credential"] == expected_credential
