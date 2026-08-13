from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import rail_waitlist.korail_pydoll_browser as browser_module
import rail_waitlist.korail_sidecar.pydoll.search_driver as search_driver_module
from rail_waitlist.korail_browser_automation import BrowserSourceUnavailable
from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_pydoll_contracts import PydollPageSnapshot, PydollTrainRow
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollReservationListSnapshot


class _ClickControl:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self) -> None:
        self.clicks += 1


@pytest.mark.asyncio
async def test_search_driver_resolves_submit_seam_after_session_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    click_exact = AsyncMock()
    monkeypatch.setattr(session, "_click_exact_text", click_exact)

    await session.submit_once()

    with pytest.raises(BrowserSourceUnavailable) as exc_info:
        await session.submit_once()
    assert exc_info.value.stage == "submit_button"
    click_exact.assert_awaited_once_with("button", "열차 조회")


@pytest.mark.asyncio
async def test_search_driver_resolves_snapshot_and_readback_seams_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    snapshot = PydollPageSnapshot(body_text="KORAIL", rows=())
    snapshot_reader = AsyncMock(
        side_effect=[
            snapshot,
            PydollPageSnapshot(
                body_text="KORAIL",
                rows=(),
                network_responses=((403, "document"),),
            ),
        ]
    )
    evaluate_value = AsyncMock(
        side_effect=lambda selector: {
            "input[name='txtGoStart']": "대전",
            "#startDate": "2026-08-02(일) 06:00",
        }[selector]
    )
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)
    monkeypatch.setattr(session, "_evaluate_value", evaluate_value)

    assert await session.current_station("departure") == "대전"
    assert await session.current_schedule() == (date(2026, 8, 2), 6)
    result = await session.wait_for_result()

    assert result.network_responses == ((403, "document"),)
    assert snapshot_reader.await_count == 2


@pytest.mark.asyncio
async def test_search_driver_returns_maintenance_snapshot_without_waiting_for_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    outage = PydollPageSnapshot(
        body_text="점검 안내",
        rows=(),
        url="https://www.korail.com/rejectservice_job.html",
    )
    snapshot_reader = AsyncMock(return_value=outage)
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)

    assert await session.wait_for_result() == outage
    snapshot_reader.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_session_open_returns_initial_maintenance_page_before_control_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    session._tab = SimpleNamespace(go_to=AsyncMock())
    outage = PydollPageSnapshot(
        body_text="점검 안내",
        rows=(),
        url="https://www.korail.com/rejectservice_job.html",
    )
    snapshot_reader = AsyncMock(return_value=outage)
    wait_for_control = AsyncMock()
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)
    monkeypatch.setattr(session, "_wait_for_exact_text", wait_for_control)

    assert await session.open() == outage
    wait_for_control.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_open_classifies_maintenance_dom_after_load_signal_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_load_timeout = type("PageLoadTimeout", (Exception,), {"__module__": "pydoll.exceptions"})
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    session._tab = SimpleNamespace(go_to=AsyncMock(side_effect=page_load_timeout()))
    outage = PydollPageSnapshot(
        body_text="점검 안내",
        rows=(),
        url="https://www.korail.com/rejectservice_job.html",
    )
    snapshot_reader = AsyncMock(return_value=outage)
    wait_for_control = AsyncMock()
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)
    monkeypatch.setattr(session, "_wait_for_exact_text", wait_for_control)

    assert await session.open() == outage
    snapshot_reader.assert_awaited_once_with()
    wait_for_control.assert_not_awaited()


@pytest.mark.asyncio
async def test_reservation_list_reader_waits_past_loading_until_explicit_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    session._tab = SimpleNamespace(go_to=AsyncMock())
    loading = PydollReservationListSnapshot(
        url="https://www.korail.com/ticket/reservation/list",
        page_marker_visible=True,
        explicit_empty_visible=True,
        loading_visible=True,
    )
    ready = PydollReservationListSnapshot(
        url="https://www.korail.com/ticket/reservation/list",
        page_marker_visible=True,
        explicit_empty_visible=True,
    )
    reader = AsyncMock(side_effect=[loading, ready, ready])
    monkeypatch.setattr(session._search_driver, "reservation_list_snapshot", reader)

    result = await session.read_reservation_list()

    assert result.stable_observation is True
    assert result.official_read_completed is True
    assert reader.await_count == 3
    session._tab.go_to.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_driver_resolves_station_helpers_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    trigger = _ClickControl()
    target = _ClickControl()
    dialog = object()
    find_exact = AsyncMock(side_effect=[trigger, target])
    wait_for_dialog = AsyncMock(return_value=dialog)
    wait_for_value = AsyncMock()
    monkeypatch.setattr(session, "_find_exact_visible", find_exact)
    monkeypatch.setattr(session, "_wait_for_dialog", wait_for_dialog)
    monkeypatch.setattr(session, "_wait_for_value", wait_for_value)

    await session.choose_station("departure", "대전")

    assert trigger.clicks == 1
    assert target.clicks == 1
    assert find_exact.await_args_list[0].args == ("a", "출발역 선택")
    assert find_exact.await_args_list[1].args == ("a", "대전")
    assert find_exact.await_args_list[1].kwargs == {"scope": dialog}
    wait_for_dialog.assert_awaited_once_with("기차역 조회")
    wait_for_value.assert_awaited_once_with("input[name='txtGoStart']", "대전")


@pytest.mark.asyncio
async def test_search_driver_resolves_result_growth_seam_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    first = PydollTrainRow("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)", ())
    second = PydollTrainRow("KTX 2", "2", "서울 → 대전(07:00 ~ 08:00)", ())
    initial = PydollPageSnapshot(body_text="조회 결과", rows=(first,))
    grown = PydollPageSnapshot(body_text="조회 결과", rows=(first, second))
    more = _ClickControl()
    find_exact = AsyncMock(side_effect=[more, LookupError("더보기")])
    growth = AsyncMock(return_value=(grown, True))
    monkeypatch.setattr(session, "_find_exact_visible", find_exact)
    monkeypatch.setattr(session, "_wait_for_result_growth", growth)

    result = await session.expand_results(initial, 19)

    assert result.rows == (first, second)
    assert more.clicks == 1
    assert find_exact.await_count == 2
    growth.assert_awaited_once_with({("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)")})


@pytest.mark.asyncio
async def test_result_growth_evaluates_snapshot_read_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    first = PydollTrainRow("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)", ())
    second = PydollTrainRow("KTX 2", "2", "서울 → 대전(07:00 ~ 08:00)", ())
    snapshot_reader = AsyncMock(
        side_effect=[
            PydollPageSnapshot("A", (first,)),
            PydollPageSnapshot("A+B", (first, second)),
        ]
    )
    clock = {"now": 0.0}

    def advance_clock(_delay: float) -> None:
        clock["now"] = 1.0

    sleep = AsyncMock(side_effect=advance_clock)
    session._search_driver._monotonic = Mock(side_effect=lambda: clock["now"])
    session._search_driver._sleep = sleep
    monkeypatch.setattr(session, "_snapshot", snapshot_reader)

    snapshot, progressed = await session._wait_for_result_growth(
        {("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)")}
    )

    assert progressed is True
    assert snapshot.rows == (first, second)
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_search_driver_stops_repeated_window_after_merging_latest_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    original = PydollTrainRow(
        "KTX 1",
        "1",
        "서울 → 대전(06:00 ~ 07:00)",
        (),
        "old",
    )
    second = PydollTrainRow("KTX 2", "2", "서울 → 대전(07:00 ~ 08:00)", ())
    updated = PydollTrainRow(
        "KTX 1",
        "1",
        "서울 → 대전(06:00 ~ 07:00)",
        (),
        "latest",
    )
    controls = [_ClickControl(), _ClickControl()]
    find_exact = AsyncMock(side_effect=controls)
    growth = AsyncMock(
        side_effect=[
            (PydollPageSnapshot("B", (second,)), True),
            (PydollPageSnapshot("A latest", (updated,)), False),
        ]
    )
    monkeypatch.setattr(session, "_find_exact_visible", find_exact)
    monkeypatch.setattr(session, "_wait_for_result_growth", growth)

    result = await session.expand_results(PydollPageSnapshot("A", (original,)), 19)

    assert result.rows == (updated, second)
    assert [control.clicks for control in controls] == [1, 1]
    assert growth.await_args_list[0].args[0] == {("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)")}
    assert growth.await_args_list[1].args[0] == {
        ("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)"),
        ("KTX 2", "2", "서울 → 대전(07:00 ~ 08:00)"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("max_actions", [0, -1])
async def test_search_driver_skips_dom_expansion_for_nonpositive_action_limit(
    monkeypatch: pytest.MonkeyPatch,
    max_actions: int,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    initial = PydollPageSnapshot(
        "A",
        (PydollTrainRow("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)", ()),),
    )
    find_exact = AsyncMock()
    growth = AsyncMock()
    monkeypatch.setattr(session, "_find_exact_visible", find_exact)
    monkeypatch.setattr(session, "_wait_for_result_growth", growth)

    result = await session.expand_results(initial, max_actions)

    assert result == initial
    find_exact.assert_not_awaited()
    growth.assert_not_awaited()


def test_search_driver_keeps_hour_candidate_compatibility_identity() -> None:
    assert browser_module._HourCandidate is search_driver_module.SearchHourCandidate


def test_search_driver_has_no_browser_or_actor_dependencies() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_sidecar"
        / "pydoll"
        / "search_driver.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.rsplit(".", 1)[-1])
            if node.module == "rail_waitlist":
                imported_roots.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)

    assert imported_roots.isdisjoint(
        {
            "korail_pydoll_auth_actor",
            "korail_pydoll_browser",
            "korail_pydoll_login_driver",
            "korail_pydoll_reservation_actor",
            "korail_pydoll_reservation_driver",
            "korail_pydoll_search_actor",
        }
    )
    source = module_path.read_text(encoding="utf-8")
    assert "Input.dispatchMouseEvent" not in source
    assert "parse_official_train_type" not in source
