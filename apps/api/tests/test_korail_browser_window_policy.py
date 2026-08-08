from __future__ import annotations

import ast
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.korail_browser_seat_source as legacy_source
from rail_waitlist.provider_adapters import korail_browser_window_policy as policy

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "provider_adapters" / "korail_browser_window_policy.py"
LEGACY_PATH = SOURCE_ROOT / "korail_browser_seat_source.py"
OWNER_MODULE = "rail_waitlist.provider_adapters.korail_browser_window_policy"
KOREA = ZoneInfo("Asia/Seoul")


def test_window_policy_has_exact_pure_owner_boundary() -> None:
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

    assert definitions == {"select_browser_departure_from"}
    assert policy.__all__ == ("select_browser_departure_from",)
    assert imports == {
        (0, "__future__"),
        (0, "datetime"),
        (0, "zoneinfo"),
    }
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    assert policy.select_browser_departure_from.__module__ == OWNER_MODULE


def test_source_keeps_window_owner_identity_and_exact_legacy_surface() -> None:
    assert (
        legacy_source.KorailBrowserSeatSource._select_browser_departure_from
        is policy.select_browser_departure_from
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
    assert not hasattr(legacy_source, "_window_policy_owner")

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


def test_window_policy_has_one_direct_production_consumer() -> None:
    consumers: set[str] = set()
    for module_path in sorted(SOURCE_ROOT.rglob("*.py")):
        if module_path == OWNER_PATH:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        if any(
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "provider_adapters"
            and any(alias.name == "korail_browser_window_policy" for alias in node.names)
            for node in ast.walk(tree)
        ):
            consumers.add(module_path.relative_to(SOURCE_ROOT).as_posix())

    assert consumers == {"korail_browser_seat_source.py"}


@pytest.mark.parametrize(
    ("local_from", "local_to", "now", "expected"),
    [
        (
            datetime(2026, 8, 4, 5, 45, tzinfo=KOREA),
            datetime(2026, 8, 4, 9, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, tzinfo=KOREA),
            time(0),
        ),
        (
            datetime(2026, 8, 3, 8, tzinfo=KOREA),
            datetime(2026, 8, 3, 12, tzinfo=KOREA),
            datetime(2026, 8, 2, 21, 30, tzinfo=UTC),
            time(8),
        ),
        (
            datetime(2026, 8, 3, 5, tzinfo=KOREA),
            datetime(2026, 8, 3, 12, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, 47, tzinfo=KOREA),
            time(6),
        ),
        (
            datetime(2026, 8, 2, 5, tzinfo=KOREA),
            datetime(2026, 8, 2, 9, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, tzinfo=KOREA),
            None,
        ),
        (
            datetime(2026, 8, 3, 5, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, 30, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, 47, tzinfo=KOREA),
            None,
        ),
        (
            datetime(2026, 8, 3, 5, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, tzinfo=KOREA),
            time(6),
        ),
        (
            datetime(2026, 8, 3, 8, tzinfo=KOREA),
            datetime(2026, 8, 3, 7, tzinfo=KOREA),
            datetime(2026, 8, 3, 6, tzinfo=KOREA),
            None,
        ),
    ],
    ids=[
        "future",
        "today-future",
        "current-hour",
        "past-date",
        "elapsed",
        "inclusive",
        "inverted",
    ],
)
def test_window_policy_preserves_kst_picker_boundaries(
    local_from: datetime,
    local_to: datetime,
    now: datetime,
    expected: time | None,
) -> None:
    assert policy.select_browser_departure_from(local_from, local_to, now=now) == expected


def test_source_resolves_now_and_window_selector_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = object.__new__(legacy_source.KorailBrowserSeatSource)
    current_now = datetime(2026, 8, 3, 1, 23, tzinfo=UTC)
    selector_calls: list[tuple[datetime, datetime, datetime, ZoneInfo]] = []
    patched_timezone = ZoneInfo("UTC")

    def select(
        local_from: datetime,
        local_to: datetime,
        *,
        now: datetime,
        timezone: ZoneInfo,
    ) -> time:
        selector_calls.append((local_from, local_to, now, timezone))
        return time(9)

    monkeypatch.setattr(source, "_now", lambda: current_now, raising=False)
    monkeypatch.setattr(source, "_select_browser_departure_from", select)
    monkeypatch.setattr(legacy_source, "KOREA", patched_timezone)
    local_from = datetime(2026, 8, 3, 5, tzinfo=KOREA)
    local_to = datetime(2026, 8, 3, 12, tzinfo=KOREA)

    assert source._browser_departure_from(local_from, local_to) == time(9)
    assert selector_calls == [(local_from, local_to, current_now, patched_timezone)]
