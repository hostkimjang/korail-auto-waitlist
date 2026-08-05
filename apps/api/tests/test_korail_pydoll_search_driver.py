from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import rail_waitlist.korail_pydoll_browser as browser_module
import rail_waitlist.korail_pydoll_search_driver as search_driver_module
from rail_waitlist.korail_browser_automation import BrowserSourceUnavailable
from rail_waitlist.korail_pydoll_browser import _PydollSession
from rail_waitlist.korail_pydoll_contracts import PydollPageSnapshot, PydollTrainRow


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
    growth.assert_awaited_once_with({("KTX 1", "1", "서울 → 대전(06:00 ~ 07:00)")})


def test_search_driver_keeps_hour_candidate_compatibility_identity() -> None:
    assert browser_module._HourCandidate is search_driver_module.SearchHourCandidate


def test_search_driver_has_no_browser_or_actor_dependencies() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_pydoll_search_driver.py"
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
