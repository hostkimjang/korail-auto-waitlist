from __future__ import annotations

import ast
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.korail_browser_seat_source as legacy_source
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserTrainSnapshot,
)
from rail_waitlist.observations.contracts import SeatObservationRequest
from rail_waitlist.provider_adapters import korail_browser_observation_policy as policy

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "provider_adapters" / "korail_browser_observation_policy.py"
LEGACY_PATH = SOURCE_ROOT / "korail_browser_seat_source.py"
OWNER_MODULE = "rail_waitlist.provider_adapters.korail_browser_observation_policy"
OWNER_DEFINITIONS = {"build_observation_search_request", "project_observation_result"}
EXPECTED_ALL = ("build_observation_search_request", "project_observation_result")
KOREA = ZoneInfo("Asia/Seoul")


def _request(
    *,
    train_number: str = "43",
    departure_at: datetime | None = None,
    seat_class: SeatClass = SeatClass.STANDARD,
) -> SeatObservationRequest:
    return SeatObservationRequest(
        provider=Provider.KORAIL,
        origin_node_id="NAT010000",
        destination_node_id="NAT014445",
        origin="서울",
        destination="부산",
        train_number=train_number,
        departure_at=departure_at or datetime(2026, 8, 3, 6, 45, tzinfo=UTC),
        seat_class=seat_class,
        passenger_count=1,
    )


def _result(
    *,
    train_number: str = "00043",
    departure_at: datetime | None = None,
) -> BrowserSeatSearchResult:
    return BrowserSeatSearchResult(
        origin="서울",
        destination="부산",
        travel_date=date(2026, 8, 3),
        passenger_count=1,
        observed_at=datetime(2026, 8, 3, 6, 40, tzinfo=UTC),
        trains=[
            BrowserTrainSnapshot(
                train_number=train_number,
                train_type="KTX",
                departure_at=departure_at or datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
                arrival_at=datetime(2026, 8, 3, 18, 30, tzinfo=KOREA),
                standard="available",
                first="limited",
                expected_delay_minutes=7,
            ),
            BrowserTrainSnapshot(
                train_number=train_number,
                train_type="KTX",
                departure_at=departure_at or datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
                arrival_at=datetime(2026, 8, 3, 18, 31, tzinfo=KOREA),
                standard="sold_out",
                first="not_offered",
                expected_delay_minutes=11,
            ),
        ],
    )


def _normalize_train_number(value: object) -> str:
    return str(value).lstrip("0")


class _ObservationTransport:
    def __init__(self, result: BrowserSeatSearchResult) -> None:
        self.result = result
        self.requests: list[BrowserSeatSearchRequest] = []

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.requests.append(request)
        return self.result

    async def close(self) -> None:
        return None


def test_observation_policy_has_exact_pure_owner_boundary() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports = {
        (node.level, node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert definitions == OWNER_DEFINITIONS
    assert policy.__all__ == EXPECTED_ALL
    assert imports == {
        (0, "__future__"),
        (0, "collections.abc"),
        (0, "datetime"),
        (0, "zoneinfo"),
        (2, "domain"),
        (2, "korail_sidecar.browser_contracts"),
        (2, "observations.contracts"),
    }
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    assert not any(
        isinstance(node, ast.Name) and node.id.casefold() in {"logger", "transport"}
        for node in ast.walk(tree)
    )
    for name in OWNER_DEFINITIONS:
        assert getattr(policy, name).__module__ == OWNER_MODULE


def test_source_keeps_observation_owner_identity_and_exact_legacy_surface() -> None:
    assert (
        legacy_source.KorailBrowserSeatSource._build_observation_search_request
        is policy.build_observation_search_request
    )
    assert (
        legacy_source.KorailBrowserSeatSource._project_observation_result
        is policy.project_observation_result
    )
    assert len({name for name in vars(legacy_source) if not name.startswith("_")}) == 56
    assert (
        len(
            {
                name
                for name in vars(legacy_source)
                if name.startswith("_") and not name.startswith("__")
            }
        )
        == 10
    )
    assert not hasattr(legacy_source, "__all__")
    assert not hasattr(legacy_source, "_observation_policy_owner")

    tree = ast.parse(LEGACY_PATH.read_text(encoding="utf-8"), filename=str(LEGACY_PATH))
    deleted_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Delete)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert deleted_names == {
        "_auth_policy_owner",
        "_observation_policy_owner",
        "_reservation_policy_owner",
        "_window_policy_owner",
    }


async def test_source_resolves_picker_and_train_normalizer_at_observation_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector_inputs: list[tuple[datetime, datetime]] = []
    normalized_inputs: list[object] = []
    transport = _ObservationTransport(_result())
    source = legacy_source.KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://adapter.invalid",
        cache_ttl_seconds=30,
        timeout_seconds=5,
        rate_limit_cooldown_seconds=60,
        protection_cooldown_seconds=60,
        transport=transport,
        monotonic=lambda: 100.0,
    )

    def choose_departure_from(start: datetime, end: datetime) -> time:
        selector_inputs.append((start, end))
        return time(15)

    def normalize_train_number(value: object) -> str:
        normalized_inputs.append(value)
        return str(value).lstrip("0")

    monkeypatch.setattr(source, "_browser_departure_from", choose_departure_from)
    monkeypatch.setattr(legacy_source, "_normalize_train_number", normalize_train_number)

    observed = await source.observe(
        _request(),
        origin="서울",
        destination="부산",
    )

    assert selector_inputs == [
        (
            datetime(2026, 8, 3, 15, tzinfo=KOREA),
            datetime(2026, 8, 3, 23, 59, 59, tzinfo=KOREA),
        )
    ]
    assert normalized_inputs == ["43", "00043"]
    assert len(transport.requests) == 1
    assert observed[0].status == "available"
    assert observed[0].delay_minutes == 7


def test_supported_request_preserves_kst_service_day_and_selector_boundary() -> None:
    selector_inputs: list[tuple[datetime, datetime]] = []

    def select_departure_from(start: datetime, end: datetime) -> time:
        selector_inputs.append((start, end))
        return time(15)

    result = policy.build_observation_search_request(
        _request(),
        enabled=True,
        origin="서울",
        destination="부산",
        select_departure_from=select_departure_from,
    )

    assert result is not None
    assert selector_inputs == [
        (
            datetime(2026, 8, 3, 15, tzinfo=KOREA),
            datetime(2026, 8, 3, 23, 59, 59, tzinfo=KOREA),
        )
    ]
    assert result.travel_date == date(2026, 8, 3)
    assert result.departure_from == time(15)
    assert result.departure_to == time(23, 59, 59)
    assert result.passenger_count == 1


@pytest.mark.parametrize(
    ("enabled", "updates", "selector_result", "expected_selector_calls"),
    [
        (False, {}, time(15), 0),
        (True, {"provider": Provider.SRT}, time(15), 0),
        (True, {"passenger_count": 2}, time(15), 0),
        (True, {"seat_class": SeatClass.INFANT}, time(15), 0),
        (True, {}, None, 1),
    ],
)
def test_unsupported_request_fails_closed_before_search(
    enabled: bool,
    updates: dict[str, object],
    selector_result: time | None,
    expected_selector_calls: int,
) -> None:
    selector_calls = 0

    def select_departure_from(_start: datetime, _end: datetime) -> time | None:
        nonlocal selector_calls
        selector_calls += 1
        return selector_result

    request = _request().model_copy(update=updates)

    assert (
        policy.build_observation_search_request(
            request,
            enabled=enabled,
            origin="서울",
            destination="부산",
            select_departure_from=select_departure_from,
        )
        is None
    )
    assert selector_calls == expected_selector_calls


@pytest.mark.parametrize(
    ("seat_class", "cache_ttl_seconds", "expected_status", "freshness_seconds"),
    [
        (SeatClass.STANDARD, 45, "available", 30),
        (SeatClass.FIRST, -5, "limited", 0),
    ],
)
def test_exact_result_projects_class_delay_and_bounded_freshness(
    seat_class: SeatClass,
    cache_ttl_seconds: int,
    expected_status: str,
    freshness_seconds: int,
) -> None:
    result = _result()

    projected = policy.project_observation_result(
        _request(seat_class=seat_class),
        result,
        normalize_train_number=_normalize_train_number,
        cache_ttl_seconds=cache_ttl_seconds,
    )

    assert projected is not None
    assert len(projected) == 1
    assert projected[0].seat_class == seat_class
    assert projected[0].status == expected_status
    assert projected[0].source == "korail-official-page-browser"
    assert projected[0].observed_at == result.observed_at
    assert projected[0].fresh_until == result.observed_at + timedelta(seconds=freshness_seconds)
    assert projected[0].delay_minutes == 7
    assert result.trains[1].expected_delay_minutes == 11


@pytest.mark.parametrize(
    "result",
    [
        _result(train_number="47"),
        _result(departure_at=datetime(2026, 8, 3, 15, 45, 1, tzinfo=KOREA)),
    ],
    ids=["train-number", "kst-second"],
)
def test_non_exact_identity_returns_no_observation(result: BrowserSeatSearchResult) -> None:
    assert (
        policy.project_observation_result(
            _request(),
            result,
            normalize_train_number=_normalize_train_number,
            cache_ttl_seconds=30,
        )
        is None
    )
