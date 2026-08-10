from __future__ import annotations

import ast
import asyncio
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll import dom_interaction as owner
from rail_waitlist.korail_sidecar.pydoll.live_dom import PydollControlState

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "dom_interaction.py"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.dom_interaction"
BROWSER_MODULE = "rail_waitlist.korail_pydoll_browser"
OWNER_SYMBOLS = (
    "PydollDomInteractionPort",
    "click_exact_text",
    "evaluate_text",
    "evaluate_value",
    "find_exact_visible",
    "has_exact_visible",
    "wait_for_dialog",
    "wait_for_enabled_exact_text",
    "wait_for_exact_text",
    "wait_for_value",
    "wait_for_visible_elements",
)
LEGACY_TO_OWNER = {
    "_evaluate_value": "evaluate_value",
    "_evaluate_text": "evaluate_text",
    "_wait_for_value": "wait_for_value",
    "_click_exact_text": "click_exact_text",
    "_wait_for_exact_text": "wait_for_exact_text",
    "_wait_for_enabled_exact_text": "wait_for_enabled_exact_text",
    "_wait_for_visible_elements": "wait_for_visible_elements",
    "_wait_for_dialog": "wait_for_dialog",
    "_find_exact_visible": "find_exact_visible",
    "_has_exact_visible": "has_exact_visible",
}
LEGACY_PICKLES = {
    "_evaluate_value": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2V2YWx1YXRlX3ZhbHVlCnAxCnRScDIK"
        "Lg=="
    ),
    "_evaluate_text": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2V2YWx1YXRlX3RleHQKcDEKdFJwMgou"
    ),
    "_wait_for_value": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX3ZhbHVlCnAxCnRScDIK"
        "Lg=="
    ),
    "_click_exact_text": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2NsaWNrX2V4YWN0X3RleHQKcDEKdFJw"
        "Mgou"
    ),
    "_wait_for_exact_text": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX2V4YWN0X3RleHQKcDEK"
        "dFJwMgou"
    ),
    "_wait_for_enabled_exact_text": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX2VuYWJsZWRfZXhhY3Rf"
        "dGV4dApwMQp0UnAyCi4="
    ),
    "_wait_for_visible_elements": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX3Zpc2libGVfZWxlbWVu"
        "dHMKcDEKdFJwMgou"
    ),
    "_wait_for_dialog": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX2RpYWxvZwpwMQp0UnAy"
        "Ci4="
    ),
    "_find_exact_visible": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2ZpbmRfZXhhY3RfdmlzaWJsZQpwMQp0"
        "UnAyCi4="
    ),
    "_has_exact_visible": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2hhc19leGFjdF92aXNpYmxlCnAxCnRS"
        "cDIKLg=="
    ),
}


class _AwaitableText:
    def __init__(self, value: str = "", error: BaseException | None = None) -> None:
        self.value = value
        self.error = error

    def __await__(self):  # type: ignore[no-untyped-def]
        async def resolve() -> str:
            if self.error is not None:
                raise self.error
            return self.value

        return resolve().__await__()


class _Element:
    def __init__(
        self,
        text: str = "",
        *,
        text_error: BaseException | None = None,
        click_error: BaseException | None = None,
    ) -> None:
        self.text = _AwaitableText(text, text_error)
        self.click_error = click_error
        self.clicks = 0

    async def click(self) -> None:
        self.clicks += 1
        if self.click_error is not None:
            raise self.click_error


class _Tab:
    def __init__(self, values: list[object | BaseException]) -> None:
        self.values = values
        self.scripts: list[tuple[str, bool]] = []

    async def execute_script(self, script: str, *, return_by_value: bool) -> object:
        self.scripts.append((script, return_by_value))
        current = self.values.pop(0)
        if isinstance(current, BaseException):
            raise current
        return {"result": {"result": {"value": current}}}


class _Port:
    def __init__(self) -> None:
        self.values: list[object | BaseException] = []
        self.finds: list[object | BaseException] = []
        self.visible: list[list[object] | BaseException] = []
        self.states: dict[object, PydollControlState | BaseException] = {}
        self.visible_calls: list[tuple[str, object]] = []

    async def _evaluate_value(self, _selector: str) -> object:
        current = self.values.pop(0)
        if isinstance(current, BaseException):
            raise current
        return current

    async def _find_exact_visible(
        self,
        _selector: str,
        _text: str,
        *,
        scope: object = None,
    ) -> object:
        del scope
        current = self.finds.pop(0)
        if isinstance(current, BaseException):
            raise current
        return current

    async def _visible_elements(self, selector: str, *, scope: object = None) -> list[object]:
        self.visible_calls.append((selector, scope))
        current = self.visible.pop(0)
        if isinstance(current, BaseException):
            raise current
        return current

    async def _read_control_state(self, element: object) -> PydollControlState:
        current = self.states[element]
        if isinstance(current, BaseException):
            raise current
        return current

    def _control_state_log_value(self, state: PydollControlState) -> tuple[object, ...]:
        return (state.enabled, state.aria_disabled, state.read_error)


def _state(*, enabled: bool, read_error: bool = False) -> PydollControlState:
    return PydollControlState(
        enabled=enabled,
        aria_disabled="false" if enabled else "true",
        disabled_attribute=not enabled,
        classes=(),
        container_classes=(),
        slide_classes=(),
        read_error=read_error,
    )


def _clock(values: list[float], events: list[str] | None = None):  # type: ignore[no-untyped-def]
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


def test_dom_interaction_owner_has_exact_surface_and_dependency_boundary() -> None:
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
        (0, "logging", (("Logger", None),)),
        (0, "typing", (("Any", None), ("Protocol", None))),
        (2, "browser_contracts", (("BrowserSourceUnavailable", None),)),
        (1, "live_dom", (("PydollControlState", None),)),
    }
    assert all(getattr(owner, name).__module__ == OWNER_MODULE for name in OWNER_SYMBOLS)


@pytest.mark.asyncio
async def test_browser_preserves_hooks_pickles_surface_and_late_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_type = browser._PydollSession
    for legacy_name, owner_name in LEGACY_TO_OWNER.items():
        hook_name = f"{legacy_name}_interaction"
        assert getattr(session_type, hook_name) is getattr(owner, owner_name)
        wrapper = getattr(session_type, legacy_name)
        assert wrapper.__module__ == BROWSER_MODULE
        assert wrapper.__qualname__ == f"_PydollSession.{legacy_name}"
        assert pickle.loads(base64.b64decode(LEGACY_PICKLES[legacy_name])) is wrapper

    assert len({name for name in vars(browser) if not name.startswith("_")}) == 84
    private_names = {
        name for name in vars(browser) if name.startswith("_") and not name.startswith("__")
    }
    assert len(private_names) == 29
    assert "_dom_interaction_owner" not in private_names
    assert not hasattr(browser, "__all__")

    session = session_type("https://www.korail.com/ticket/search/general", 1_000, True)
    monotonic = Mock(return_value=0.0)
    sleep = AsyncMock()
    monkeypatch.setattr(browser.time, "monotonic", monotonic)
    monkeypatch.setattr(browser.asyncio, "sleep", sleep)
    wait_hook = AsyncMock()
    monkeypatch.setattr(session, "_wait_for_value_interaction", wait_hook)
    await session._wait_for_value("#station", "대전")
    timeout_seconds = wait_hook.await_args.kwargs["timeout_seconds"]
    assert timeout_seconds() == 1.0
    session.timeout_ms = 2_500
    assert timeout_seconds() == 2.5
    assert wait_hook.await_args.kwargs["monotonic"] is monotonic
    assert wait_hook.await_args.kwargs["sleep"] is sleep
    assert wait_hook.await_args.kwargs["source_unavailable_type"] is BrowserSourceUnavailable

    enabled_hook = AsyncMock(return_value=object())
    monkeypatch.setattr(session, "_wait_for_enabled_exact_text_interaction", enabled_hook)
    await session._wait_for_enabled_exact_text("button", "적용")
    assert enabled_hook.await_args.kwargs["event_logger"] is browser.logger
    assert enabled_hook.await_args.args[0] is session


def test_dom_interaction_has_one_consumer_and_passive_import_orders() -> None:
    probe_path = SOURCE_ROOT / "_dom_interaction_reference_probe.py"
    canonical_forms = (
        f"import {OWNER_MODULE}",
        "from rail_waitlist.korail_sidecar.pydoll import dom_interaction",
        f"from {OWNER_MODULE} import wait_for_dialog",
        f"import importlib\nowner = importlib.import_module('{OWNER_MODULE}')",
    )
    assert all(_module_references(source, probe_path, OWNER_MODULE) for source in canonical_forms)

    legacy_targets = tuple(f"{BROWSER_MODULE}._PydollSession.{name}" for name in LEGACY_TO_OWNER)
    legacy_forms = (
        f"from {BROWSER_MODULE} import _PydollSession\n_PydollSession._evaluate_value",
        "from rail_waitlist import korail_pydoll_browser as legacy\n"
        "legacy._PydollSession._wait_for_dialog",
        f"import importlib\nlegacy = importlib.import_module('{BROWSER_MODULE}')\n"
        "legacy._PydollSession._has_exact_visible",
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
    "owner": "rail_waitlist.korail_sidecar.pydoll.dom_interaction",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import dom_interaction as owner

session = browser._PydollSession
mapping = {
    "_evaluate_value": "evaluate_value",
    "_evaluate_text": "evaluate_text",
    "_wait_for_value": "wait_for_value",
    "_click_exact_text": "click_exact_text",
    "_wait_for_exact_text": "wait_for_exact_text",
    "_wait_for_enabled_exact_text": "wait_for_enabled_exact_text",
    "_wait_for_visible_elements": "wait_for_visible_elements",
    "_wait_for_dialog": "wait_for_dialog",
    "_find_exact_visible": "find_exact_visible",
    "_has_exact_visible": "has_exact_visible",
}
print(json.dumps({
    "identity": all(
        getattr(session, legacy + "_interaction") is getattr(owner, canonical)
        for legacy, canonical in mapping.items()
    ),
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
async def test_evaluation_and_exact_actions_use_current_live_dependencies() -> None:
    first = _Tab(["station-value"])
    second = _Tab([123])
    session = browser._PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    session._tab = first
    assert await session._evaluate_value("#station") == "station-value"
    session._tab = second
    assert await session._evaluate_text(".count") == "123"
    assert "document.querySelector('#station')?.value" in first.scripts[0][0]
    assert "document.querySelector('.count')?.innerText" in second.scripts[0][0]

    for error in (RuntimeError("opaque"), asyncio.CancelledError()):
        with pytest.raises(type(error)):
            await owner.evaluate_value(_Tab([error]), "#station")

    exact = _Element("  로그아웃  ")
    detached = _Element(text_error=RuntimeError("detached"))
    cancelled = _Element(text_error=asyncio.CancelledError())
    port = _Port()
    port.visible = [[_Element("로그인"), exact]]
    assert await owner.find_exact_visible(port, "button", "로그아웃") is exact
    port.visible = [[detached, _Element("로그   아웃")]]
    assert await owner.has_exact_visible(port, "button", "로그 아웃") is True
    port.visible = [[cancelled]]
    with pytest.raises(asyncio.CancelledError):
        await owner.has_exact_visible(port, "button", "로그아웃")

    clickable = _Element("조회")
    port.finds = [clickable]
    await owner.click_exact_text(port, "button", "조회")
    assert clickable.clicks == 1
    for error in (RuntimeError("click"), asyncio.CancelledError()):
        failing_click = _Port()
        failing_click.finds = [_Element("조회", click_error=error)]
        with pytest.raises(type(error)):
            await owner.click_exact_text(failing_click, "button", "조회")


@pytest.mark.asyncio
async def test_generic_waits_preserve_polling_stages_errors_and_dependency_order() -> None:
    events: list[str] = []
    sleep = AsyncMock()
    value_port = _Port()
    value_port.values = ["waiting", "  대전역  "]
    await owner.wait_for_value(
        value_port,
        "#station",
        "대전",
        contains=True,
        timeout_seconds=_timeout(1, events),
        monotonic=_clock([0, 0.1, 0.2], events),
        sleep=sleep,
    )
    assert events[:2] == ["monotonic", "timeout"]
    sleep.assert_awaited_once_with(0.1)

    with pytest.raises(BrowserSourceUnavailable) as value_timeout:
        await owner.wait_for_value(
            _Port(),
            "#station",
            "대전",
            timeout_seconds=_timeout(0),
            monotonic=_clock([0, 0]),
            sleep=AsyncMock(),
        )
    assert value_timeout.value.stage == "input_readback"

    exact = _Element("대전")
    exact_port = _Port()
    exact_port.finds = [LookupError("wait"), exact]
    assert (
        await owner.wait_for_exact_text(
            exact_port,
            "a",
            "대전",
            timeout_seconds=_timeout(1),
            monotonic=_clock([0, 0.1, 0.2]),
            sleep=AsyncMock(),
        )
        is exact
    )
    with pytest.raises(BrowserSourceUnavailable) as exact_timeout:
        await owner.wait_for_exact_text(
            _Port(),
            "a",
            "대전",
            timeout_seconds=_timeout(0),
            monotonic=_clock([0, 0]),
            sleep=AsyncMock(),
        )
    assert exact_timeout.value.stage == "visible_control"

    visible = _Element("visible")
    visible_port = _Port()
    visible_port.visible = [[], [visible]]
    assert await owner.wait_for_visible_elements(
        visible_port,
        "button",
        failure_stage="buttons",
        timeout_seconds=_timeout(1),
        monotonic=_clock([0, 0.1, 0.2]),
        sleep=AsyncMock(),
        event_logger=Mock(),
    ) == [visible]
    visible_logger = Mock()
    with pytest.raises(BrowserSourceUnavailable) as visible_timeout:
        await owner.wait_for_visible_elements(
            _Port(),
            "button",
            failure_stage="buttons",
            timeout_seconds=_timeout(0),
            monotonic=_clock([0, 0]),
            sleep=AsyncMock(),
            event_logger=visible_logger,
        )
    assert visible_timeout.value.stage == "buttons"
    assert visible_logger.warning.call_args.args == (
        "KORAIL Pydoll controls unavailable stage=%s visible=0",
        "buttons",
    )

    dialog = _Element("다른 창")
    target_dialog = _Element("기차역 조회 창")
    dialog_port = _Port()
    dialog_port.visible = [[dialog], [target_dialog]]
    assert (
        await owner.wait_for_dialog(
            dialog_port,
            "기차역 조회",
            timeout_seconds=_timeout(1),
            monotonic=_clock([0, 0.1, 0.2]),
            sleep=AsyncMock(),
        )
        is target_dialog
    )
    with pytest.raises(BrowserSourceUnavailable) as dialog_timeout:
        await owner.wait_for_dialog(
            _Port(),
            "기차역 조회",
            timeout_seconds=_timeout(0),
            monotonic=_clock([0, 0]),
            sleep=AsyncMock(),
        )
    assert dialog_timeout.value.stage == "dialog"

    for error in (RuntimeError("find"), asyncio.CancelledError()):
        failing_exact = _Port()
        failing_exact.finds = [error]
        with pytest.raises(type(error)):
            await owner.wait_for_exact_text(
                failing_exact,
                "a",
                "대전",
                timeout_seconds=_timeout(1),
                monotonic=_clock([0, 0.1]),
                sleep=AsyncMock(),
            )

    for error in (RuntimeError("opaque"), asyncio.CancelledError()):
        failing = _Port()
        failing.values = [error]
        with pytest.raises(type(error)):
            await owner.wait_for_value(
                failing,
                "#station",
                "대전",
                timeout_seconds=_timeout(1),
                monotonic=_clock([0, 0.1]),
                sleep=AsyncMock(),
            )


@pytest.mark.asyncio
async def test_enabled_wait_uses_live_exact_state_and_secret_free_failure_logs() -> None:
    disabled = _Element(" 5시 ")
    enabled = _Element("05시")
    ignored = _Element("secret-station")
    port = _Port()
    port.visible = [[disabled, ignored, enabled]]
    port.states = {disabled: _state(enabled=False), enabled: _state(enabled=True)}
    assert (
        await owner.wait_for_enabled_exact_text(
            port,
            "a.hour",
            "05시",
            accepted_labels=("5시",),
            timeout_seconds=_timeout(1),
            monotonic=_clock([0, 0.1]),
            sleep=AsyncMock(),
            event_logger=Mock(),
        )
        is enabled
    )

    blocked = _Element("credential-secret")
    timeout_port = _Port()
    timeout_port.visible = [[blocked]]
    timeout_port.states = {blocked: _state(enabled=False, read_error=True)}
    event_logger = Mock()
    with pytest.raises(BrowserSourceUnavailable) as timeout:
        await owner.wait_for_enabled_exact_text(
            timeout_port,
            "secret-selector",
            "credential-secret",
            failure_stage="departure_hour_disabled",
            timeout_seconds=_timeout(0.05),
            monotonic=_clock([0, 0.01, 0.06]),
            sleep=AsyncMock(),
            event_logger=event_logger,
        )
    assert timeout.value.stage == "departure_hour_disabled"
    logged = str(event_logger.warning.call_args)
    assert "credential-secret" not in logged
    assert "secret-selector" not in logged
    assert "departure_hour_disabled" in logged

    for error in (RuntimeError("opaque"), asyncio.CancelledError()):
        failing = _Port()
        failing.visible = [error]
        with pytest.raises(type(error)):
            await owner.wait_for_enabled_exact_text(
                failing,
                "button",
                "적용",
                timeout_seconds=_timeout(1),
                monotonic=_clock([0, 0.1]),
                sleep=AsyncMock(),
                event_logger=Mock(),
            )
