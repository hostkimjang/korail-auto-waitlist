from __future__ import annotations

import ast
import asyncio
import base64
import json
import pickle
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll import search_schedule_commit as owner
from rail_waitlist.korail_sidecar.pydoll.search_driver import SearchHourCandidate

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_schedule_commit.py"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.search_schedule_commit"
BROWSER_MODULE = "rail_waitlist.korail_pydoll_browser"
OWNER_SYMBOLS = (
    "PydollScheduleCommitPort",
    "click_hour_and_confirm",
    "wait_for_schedule",
    "wait_for_schedule_date",
)
HOOKS = {
    "_wait_for_schedule": ("_wait_for_schedule_commit", "wait_for_schedule"),
    "_wait_for_schedule_date": (
        "_wait_for_schedule_date_commit",
        "wait_for_schedule_date",
    ),
    "_click_hour_and_confirm": (
        "_click_hour_and_confirm_commit",
        "click_hour_and_confirm",
    ),
}
LEGACY_PICKLES = {
    "_wait_for_schedule": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX3NjaGVkdWxlCnAxCnRS"
        "cDIKLg=="
    ),
    "_wait_for_schedule_date": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX3NjaGVkdWxlX2RhdGUK"
        "cDEKdFJwMgou"
    ),
    "_click_hour_and_confirm": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2NsaWNrX2hvdXJfYW5kX2NvbmZpcm0K"
        "cDEKdFJwMgou"
    ),
}


class _Element:
    def __init__(
        self,
        error: BaseException | None = None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.clicks = 0
        self.error = error
        self.events = events

    async def click(self) -> None:
        if self.events is not None:
            self.events.append("click")
        self.clicks += 1
        if self.error is not None:
            raise self.error


class _Port:
    def __init__(self) -> None:
        self.schedules: list[tuple[date, int] | BaseException] = []
        self.states: list[object | BaseException] = []
        self.schedule_calls = 0
        self.state_calls = 0

    async def current_schedule(self) -> tuple[date, int]:
        self.schedule_calls += 1
        current = self.schedules.pop(0)
        if isinstance(current, BaseException):
            raise current
        return current

    async def _read_control_state(self, _element: object) -> object:
        self.state_calls += 1
        current = self.states.pop(0)
        if isinstance(current, BaseException):
            raise current
        return current


def _state(*container_classes: str) -> object:
    return SimpleNamespace(container_classes=container_classes)


def _candidate(element: _Element) -> SearchHourCandidate:
    return SearchHourCandidate(element=element, hour=9, state=_state())


def _clock(
    values: list[float],
    events: list[str] | None = None,
):  # type: ignore[no-untyped-def]
    iterator = iter(values)

    def monotonic() -> float:
        if events is not None:
            events.append("monotonic")
        return next(iterator)

    return monotonic


def _timeout(value: float, events: list[str] | None = None):  # type: ignore[no-untyped-def]
    def timeout_seconds() -> float:
        if events is not None:
            events.append("timeout")
        return value

    return timeout_seconds


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    relative_parent = path.relative_to(SOURCE_ROOT).parent
    package = ["rail_waitlist", *relative_parent.parts]
    keep = max(0, len(package) - node.level + 1)
    return ".".join([*package[:keep], *([] if node.module is None else [node.module])])


def _resolved_name(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent is not None else None
    if isinstance(node, ast.Call) and node.args:
        function = _resolved_name(node.func, bindings)
        first = node.args[0]
        if (
            function in {"__import__", "importlib.import_module"}
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
        ):
            return first.value
    return None


def _module_references(source: str, path: Path, module: str) -> bool:
    tree = ast.parse(source, filename=str(path))
    bindings: dict[str, str] = {}
    parent, _, member = module.rpartition(".")
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
                found = found or alias.name == module
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = ".".join(
                        part for part in (resolved, alias.name) if part
                    )
            found = (
                found
                or resolved == module
                or (resolved == parent and any(alias.name in {member, "*"} for alias in node.names))
            )
    for _ in range(3):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _resolved_name(node.value, bindings)
            if value is not None:
                for target in targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = value
    return found or any(
        _resolved_name(node, bindings) == module
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Call))
    )


def test_schedule_commit_owner_has_exact_surface_and_dependency_boundary() -> None:
    tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    direct_imports = {
        (alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imports = {
        (node.level, node.module, tuple((alias.name, alias.asname) for alias in node.names))
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }

    assert definitions == set(OWNER_SYMBOLS)
    assert owner.__all__ == OWNER_SYMBOLS
    assert direct_imports == set()
    assert from_imports == {
        (0, "__future__", (("annotations", None),)),
        (0, "collections.abc", (("Awaitable", None), ("Callable", None))),
        (0, "datetime", (("date", None),)),
        (0, "typing", (("Any", None), ("Protocol", None))),
        (2, "browser_contracts", (("BrowserSourceUnavailable", None),)),
        (
            1,
            "search_driver",
            (("SearchControlState", None), ("SearchHourCandidate", None)),
        ),
    }
    assert all(getattr(owner, name).__module__ == OWNER_MODULE for name in OWNER_SYMBOLS)


@pytest.mark.asyncio
async def test_browser_preserves_hooks_pickles_surface_and_late_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_type = browser._PydollSession
    for legacy_name, (hook_name, owner_name) in HOOKS.items():
        assert getattr(session_type, hook_name) is getattr(owner, owner_name)
        wrapper = getattr(session_type, legacy_name)
        assert wrapper.__module__ == BROWSER_MODULE
        assert wrapper.__qualname__ == f"_PydollSession.{legacy_name}"
        assert pickle.loads(base64.b64decode(LEGACY_PICKLES[legacy_name])) is wrapper

    assert len({name for name in vars(browser) if not name.startswith("_")}) == 82
    private_names = {
        name for name in vars(browser) if name.startswith("_") and not name.startswith("__")
    }
    assert len(private_names) == 29
    assert "_search_schedule_commit_owner" not in private_names
    assert not hasattr(browser, "__all__")

    session = session_type("https://www.korail.com/ticket/search/general", 1_000, True)
    monotonic = Mock(return_value=0.0)
    sleep = AsyncMock()
    monkeypatch.setattr(browser.time, "monotonic", monotonic)
    monkeypatch.setattr(browser.asyncio, "sleep", sleep)
    for wrapper_name, hook_name, args, result in (
        ("_wait_for_schedule", "_wait_for_schedule_commit", (date(2026, 8, 3), 9), None),
        ("_wait_for_schedule_date", "_wait_for_schedule_date_commit", (date(2026, 8, 3),), None),
        (
            "_click_hour_and_confirm",
            "_click_hour_and_confirm_commit",
            (_candidate(_Element()),),
            True,
        ),
    ):
        hook = AsyncMock(return_value=result)
        monkeypatch.setattr(session, hook_name, hook)
        assert await getattr(session, wrapper_name)(*args) is result
        timeout_seconds = hook.await_args.kwargs["timeout_seconds"]
        assert timeout_seconds() == 1.0
        session.timeout_ms = 2_500
        assert timeout_seconds() == 2.5
        session.timeout_ms = 1_000
        assert hook.await_args.kwargs["monotonic"] is monotonic
        assert hook.await_args.kwargs["sleep"] is sleep
        if wrapper_name.startswith("_wait_for_schedule"):
            assert hook.await_args.kwargs["source_unavailable_type"] is BrowserSourceUnavailable


def test_schedule_commit_has_one_consumer_and_passive_import_orders() -> None:
    probe_path = SOURCE_ROOT / "_schedule_commit_reference_probe.py"
    canonical_forms = (
        f"import {OWNER_MODULE}",
        "from rail_waitlist.korail_sidecar.pydoll import search_schedule_commit",
        f"from {OWNER_MODULE} import wait_for_schedule",
        f"import importlib\nowner = importlib.import_module('{OWNER_MODULE}')",
    )
    assert all(_module_references(source, probe_path, OWNER_MODULE) for source in canonical_forms)

    legacy_targets = tuple(f"{BROWSER_MODULE}._PydollSession.{name}" for name in HOOKS)
    legacy_forms = (
        f"from {BROWSER_MODULE} import _PydollSession\n_PydollSession._wait_for_schedule",
        "from rail_waitlist import korail_pydoll_browser as legacy\n"
        "legacy._PydollSession._wait_for_schedule_date",
        f"import importlib\nlegacy = importlib.import_module('{BROWSER_MODULE}')\n"
        "legacy._PydollSession._click_hour_and_confirm",
    )
    assert all(
        any(_module_references(source, probe_path, target) for target in legacy_targets)
        for source in legacy_forms
    )

    consumers: set[str] = set()
    reentries: set[str] = set()
    for module_path in sorted(SOURCE_ROOT.rglob("*.py")):
        if module_path == OWNER_PATH:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT).as_posix()
        source = module_path.read_text(encoding="utf-8")
        if _module_references(source, module_path, OWNER_MODULE):
            consumers.add(relative_path)
        if relative_path != "korail_pydoll_browser.py" and any(
            _module_references(source, module_path, target) for target in legacy_targets
        ):
            reentries.add(relative_path)
    assert consumers == {"korail_pydoll_browser.py"}
    assert reentries == set()

    script = r"""
import importlib
import json
import sys

modules = {
    "browser": "rail_waitlist.korail_pydoll_browser",
    "owner": "rail_waitlist.korail_sidecar.pydoll.search_schedule_commit",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import search_schedule_commit as owner

session = browser._PydollSession
print(json.dumps({
    "identity": all((
        session._wait_for_schedule_commit is owner.wait_for_schedule,
        session._wait_for_schedule_date_commit is owner.wait_for_schedule_date,
        session._click_hour_and_confirm_commit is owner.click_hour_and_confirm,
    )),
    "optional_backend_loaded": any(
        name == "pydoll" or name.startswith("pydoll.") for name in sys.modules
    ),
}, sort_keys=True))
"""
    for first_import in ("owner", "browser"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script, first_import],
            cwd=API_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(completed.stdout) == {
            "identity": True,
            "optional_backend_loaded": False,
        }


@pytest.mark.asyncio
async def test_full_schedule_retries_source_errors_but_preserves_other_failures() -> None:
    selected = date(2026, 8, 3)
    sleep = AsyncMock()
    port = _Port()
    port.schedules = [
        BrowserSourceUnavailable("transient"),
        (selected, 8),
        (selected, 9),
    ]
    dependency_events: list[str] = []
    await owner.wait_for_schedule(
        port,
        selected,
        9,
        timeout_seconds=_timeout(1, dependency_events),
        monotonic=_clock([0, 0.1, 0.2, 0.3], dependency_events),
        sleep=sleep,
    )
    assert dependency_events[:2] == ["monotonic", "timeout"]
    assert port.schedule_calls == 3
    assert sleep.await_count == 2

    timeout = _Port()
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await owner.wait_for_schedule(
            timeout,
            selected,
            9,
            timeout_seconds=_timeout(0),
            monotonic=_clock([0, 0]),
            sleep=AsyncMock(),
        )
    assert raised.value.stage == "departure_schedule_readback"

    for error in (RuntimeError("opaque"), asyncio.CancelledError()):
        failing = _Port()
        failing.schedules = [error]
        with pytest.raises(type(error)):
            await owner.wait_for_schedule(
                failing,
                selected,
                9,
                timeout_seconds=_timeout(1),
                monotonic=_clock([0, 0.1]),
                sleep=AsyncMock(),
            )


@pytest.mark.asyncio
async def test_schedule_date_ignores_hour_and_preserves_timeout_and_errors() -> None:
    selected = date(2026, 8, 3)
    port = _Port()
    port.schedules = [BrowserSourceUnavailable("transient"), (selected, 23)]
    sleep = AsyncMock()
    dependency_events: list[str] = []
    await owner.wait_for_schedule_date(
        port,
        selected,
        timeout_seconds=_timeout(1, dependency_events),
        monotonic=_clock([0, 0.1, 0.2], dependency_events),
        sleep=sleep,
    )
    assert dependency_events[:2] == ["monotonic", "timeout"]
    assert port.schedule_calls == 2
    sleep.assert_awaited_once_with(0.1)

    timeout = _Port()
    with pytest.raises(BrowserSourceUnavailable) as raised:
        await owner.wait_for_schedule_date(
            timeout,
            selected,
            timeout_seconds=_timeout(0),
            monotonic=_clock([0, 0]),
            sleep=AsyncMock(),
        )
    assert raised.value.stage == "departure_schedule_readback"

    for error in (ValueError("opaque"), asyncio.CancelledError()):
        failing = _Port()
        failing.schedules = [error]
        with pytest.raises(type(error)):
            await owner.wait_for_schedule_date(
                failing,
                selected,
                timeout_seconds=_timeout(1),
                monotonic=_clock([0, 0.1]),
                sleep=AsyncMock(),
            )


@pytest.mark.asyncio
async def test_click_hour_once_requires_exact_current_and_uses_one_second_cap() -> None:
    dependency_events: list[str] = []
    element = _Element(events=dependency_events)
    port = _Port()
    port.states = [_state("currently"), _state("current")]
    sleep = AsyncMock()
    assert await owner.click_hour_and_confirm(
        port,
        _candidate(element),
        timeout_seconds=_timeout(5, dependency_events),
        monotonic=_clock([0, 0.1, 0.2], dependency_events),
        sleep=sleep,
    )
    assert dependency_events[:3] == ["click", "monotonic", "timeout"]
    assert element.clicks == 1
    assert port.state_calls == 2
    sleep.assert_awaited_once_with(0.05)

    stalled_element = _Element()
    stalled = _Port()
    stalled.states = [_state("selected")]
    assert not await owner.click_hour_and_confirm(
        stalled,
        _candidate(stalled_element),
        timeout_seconds=_timeout(5),
        monotonic=_clock([0, 0.9, 1.01]),
        sleep=AsyncMock(),
    )
    assert stalled_element.clicks == 1

    for element_error, state_error in (
        (RuntimeError("click"), None),
        (None, RuntimeError("read")),
        (None, asyncio.CancelledError()),
    ):
        failing_element = _Element(element_error)
        failing = _Port()
        if state_error is not None:
            failing.states = [state_error]
        expected = element_error or state_error
        assert expected is not None
        with pytest.raises(type(expected)):
            await owner.click_hour_and_confirm(
                failing,
                _candidate(failing_element),
                timeout_seconds=_timeout(1),
                monotonic=_clock([0, 0.1]),
                sleep=AsyncMock(),
            )
