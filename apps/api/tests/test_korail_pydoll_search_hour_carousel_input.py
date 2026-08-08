from __future__ import annotations

import ast
import asyncio
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll import search_hour_carousel_input as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_hour_carousel_input.py"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.search_hour_carousel_input"
BROWSER_MODULE = "rail_waitlist.korail_pydoll_browser"
OWNER_SYMBOLS = (
    "PydollHourCarouselInputPort",
    "dispatch_mouse_event",
    "navigate_hour_carousel_by_keyboard",
    "swipe_hour_carousel",
)
LEGACY_PICKLES = {
    "_swipe_hour_carousel": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3N3aXBlX2hvdXJfY2Fyb3VzZWwKcDEK"
        "dFJwMgou"
    ),
    "_navigate_hour_carousel_by_keyboard": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX25hdmlnYXRlX2hvdXJfY2Fyb3VzZWxf"
        "Ynlfa2V5Ym9hcmQKcDEKdFJwMgou"
    ),
    "_dispatch_mouse_event": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2Rpc3BhdGNoX21vdXNlX2V2ZW50CnAx"
        "CnRScDIKLg=="
    ),
}


class _Tab:
    def __init__(self, error: BaseException | None = None) -> None:
        self.commands: list[dict[str, object]] = []
        self.error = error

    async def _execute_command(self, command: dict[str, object]) -> None:
        if self.error is not None:
            raise self.error
        self.commands.append(command)


class _Viewport:
    def __init__(self, bounds: dict[str, object]) -> None:
        self.bounds = bounds
        self.scrolled = 0

    async def scroll_into_view(self) -> None:
        self.scrolled += 1

    async def get_bounds_using_js(self) -> dict[str, object]:
        return self.bounds


class _Dialog:
    def __init__(self, focused: object = True, error: BaseException | None = None) -> None:
        self.focused = focused
        self.error = error

    async def execute_script(self, *_args: object, **_kwargs: object) -> object:
        if self.error is not None:
            raise self.error
        return {"result": {"result": {"value": self.focused}}}


class _Port:
    def __init__(self, *, tab: _Tab | None = None, viewports: list[object] | None = None) -> None:
        self._tab = tab or _Tab()
        self.viewports = [] if viewports is None else viewports
        self.mouse_events: list[tuple[str, float, float, dict[str, object]]] = []
        self.drag_error: BaseException | None = None
        self.pressed = False

    async def _visible_elements(self, selector: str, *, scope: object = None) -> list[object]:
        assert selector == ".slideWrap .slick-list"
        assert scope is not None
        return self.viewports

    async def _dispatch_mouse_event(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        buttons: int,
        button: str | None = None,
        click_count: int | None = None,
    ) -> None:
        options: dict[str, object] = {"buttons": buttons}
        if button is not None:
            options["button"] = button
        if click_count is not None:
            options["click_count"] = click_count
        self.mouse_events.append((event_type, x, y, options))
        if event_type == "mousePressed":
            self.pressed = True
        elif event_type == "mouseReleased":
            self.pressed = False
        elif self.pressed and self.drag_error is not None:
            error = self.drag_error
            self.drag_error = None
            raise error


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


def test_carousel_input_has_exact_owner_boundary() -> None:
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
    assert direct_imports == {("asyncio", None)}
    assert from_imports == {
        (0, "__future__", (("annotations", None),)),
        (0, "typing", (("Any", None), ("Protocol", None))),
        (2, "browser_contracts", (("BrowserSourceUnavailable", None),)),
    }
    assert all(getattr(owner, name).__module__ == OWNER_MODULE for name in OWNER_SYMBOLS)


def test_browser_keeps_wrapper_hooks_surface_and_legacy_pickles() -> None:
    session_type = browser._PydollSession
    assert session_type._swipe_hour_carousel_input is owner.swipe_hour_carousel
    assert (
        session_type._navigate_hour_carousel_by_keyboard_input
        is owner.navigate_hour_carousel_by_keyboard
    )
    assert session_type._dispatch_mouse_event_input is owner.dispatch_mouse_event
    for name in LEGACY_PICKLES:
        wrapper = getattr(session_type, name)
        assert wrapper.__module__ == BROWSER_MODULE
        assert wrapper.__qualname__ == f"_PydollSession.{name}"
        assert pickle.loads(base64.b64decode(LEGACY_PICKLES[name])) is wrapper

    assert len({name for name in vars(browser) if not name.startswith("_")}) == 82
    private_names = {
        name for name in vars(browser) if name.startswith("_") and not name.startswith("__")
    }
    assert len(private_names) == 29
    assert "_search_hour_carousel_input_owner" not in private_names
    assert not hasattr(browser, "__all__")


def test_carousel_input_has_one_consumer_and_passive_import_orders() -> None:
    probe_path = SOURCE_ROOT / "_carousel_input_reference_probe.py"
    canonical_forms = (
        f"import {OWNER_MODULE}",
        "from rail_waitlist.korail_sidecar.pydoll import search_hour_carousel_input",
        f"from {OWNER_MODULE} import swipe_hour_carousel",
        f"import importlib\nowner = importlib.import_module('{OWNER_MODULE}')",
    )
    assert all(_module_references(source, probe_path, OWNER_MODULE) for source in canonical_forms)

    legacy_targets = tuple(f"{BROWSER_MODULE}._PydollSession.{name}" for name in LEGACY_PICKLES)
    legacy_forms = (
        f"from {BROWSER_MODULE} import _PydollSession\n_PydollSession._swipe_hour_carousel",
        "from rail_waitlist import korail_pydoll_browser as legacy\n"
        "legacy._PydollSession._dispatch_mouse_event",
        f"import importlib\nlegacy = importlib.import_module('{BROWSER_MODULE}')\n"
        "legacy._PydollSession._navigate_hour_carousel_by_keyboard",
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
    "owner": "rail_waitlist.korail_sidecar.pydoll.search_hour_carousel_input",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import search_hour_carousel_input as owner

session = browser._PydollSession
print(json.dumps({
    "identity": all((
        session._swipe_hour_carousel_input is owner.swipe_hour_carousel,
        session._navigate_hour_carousel_by_keyboard_input
            is owner.navigate_hour_carousel_by_keyboard,
        session._dispatch_mouse_event_input is owner.dispatch_mouse_event,
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
async def test_dispatch_rounds_coordinates_and_resolves_the_current_tab() -> None:
    session = browser._PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    first = _Tab()
    second = _Tab()
    session._tab = first
    await session._dispatch_mouse_event("mouseMoved", 10.6, 20.4, buttons=0)
    session._tab = second
    await session._dispatch_mouse_event(
        "mousePressed",
        30.5,
        40.5,
        buttons=1,
        button="left",
        click_count=1,
    )

    assert first.commands == [
        {
            "method": "Input.dispatchMouseEvent",
            "params": {"type": "mouseMoved", "x": 11, "y": 20, "buttons": 0},
        }
    ]
    assert second.commands == [
        {
            "method": "Input.dispatchMouseEvent",
            "params": {
                "type": "mousePressed",
                "x": 30,
                "y": 40,
                "buttons": 1,
                "button": "left",
                "clickCount": 1,
            },
        }
    ]


@pytest.mark.asyncio
async def test_swipe_preserves_direction_bounds_failures_and_single_cancel_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(owner.asyncio, "sleep", sleep)
    bounds = {"x": 100, "y": 20, "width": 200, "height": 40}
    for direction, expected in ((".slick-next", (250, 150)), (".slick-prev", (150, 250))):
        viewport = _Viewport(bounds)
        port = _Port(viewports=[viewport])
        await owner.swipe_hour_carousel(port, object(), direction)
        assert viewport.scrolled == 1
        assert [event[0] for event in port.mouse_events] == [
            "mouseMoved",
            "mousePressed",
            *("mouseMoved" for _ in range(10)),
            "mouseReleased",
        ]
        assert port.mouse_events[0][1] == expected[0]
        assert port.mouse_events[-1][1] == expected[1]
        assert port.mouse_events[-1][3]["buttons"] == 0
    assert sleep.await_count == 20

    for port in (
        _Port(viewports=[]),
        _Port(viewports=[_Viewport({"x": 0, "y": 0, "width": 20, "height": 10})]),
    ):
        with pytest.raises(BrowserSourceUnavailable) as raised:
            await owner.swipe_hour_carousel(port, object(), ".slick-next")
        assert raised.value.stage == "departure_hour_navigate"

    for error in (RuntimeError("opaque"), asyncio.CancelledError()):
        port = _Port(viewports=[_Viewport(bounds)])
        port.drag_error = error
        expected_error = (
            asyncio.CancelledError
            if isinstance(error, asyncio.CancelledError)
            else BrowserSourceUnavailable
        )
        with pytest.raises(expected_error):
            await owner.swipe_hour_carousel(port, object(), ".slick-next")
        assert port.mouse_events[-1][0] == "mouseReleased"
        assert port.pressed is False


@pytest.mark.asyncio
async def test_keyboard_is_fail_closed_but_preserves_direction_and_cancellation() -> None:
    idle = _Port()
    assert (
        await owner.navigate_hour_carousel_by_keyboard(idle, _Dialog(False), ".slick-next") is False
    )
    assert idle._tab.commands == []

    for direction, key, code in (
        (".slick-prev", "ArrowLeft", 37),
        (".slick-next", "ArrowRight", 39),
    ):
        port = _Port()
        assert await owner.navigate_hour_carousel_by_keyboard(port, _Dialog(), direction) is True
        assert [command["params"]["type"] for command in port._tab.commands] == [
            "rawKeyDown",
            "keyUp",
        ]
        assert all(command["method"] == "Input.dispatchKeyEvent" for command in port._tab.commands)
        assert all(command["params"]["key"] == key for command in port._tab.commands)
        assert all(
            command["params"]["windowsVirtualKeyCode"] == code for command in port._tab.commands
        )

    assert (
        await owner.navigate_hour_carousel_by_keyboard(
            _Port(), _Dialog(error=RuntimeError("opaque")), ".slick-next"
        )
        is False
    )
    with pytest.raises(asyncio.CancelledError):
        await owner.navigate_hour_carousel_by_keyboard(
            _Port(),
            _Dialog(error=asyncio.CancelledError()),
            ".slick-next",
        )
