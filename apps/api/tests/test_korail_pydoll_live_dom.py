from __future__ import annotations

import ast
import asyncio
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import live_dom as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "live_dom.py"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.live_dom"
LEGACY_BROWSER_MODULE = "rail_waitlist.korail_pydoll_browser"
OWNER_SYMBOLS = (
    "PydollControlState",
    "read_control_state",
    "sanitized_class_tokens",
    "visible_elements",
)
LEGACY_PICKLES = {
    "_ControlState": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyCl9Db250cm9sU3RhdGUKcDAKLg=="
    ),
    "_read_control_state": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3JlYWRfY29udHJvbF9zdGF0ZQpwMQp0"
        "UnAyCi4="
    ),
    "_sanitized_class_tokens": (
        "Y3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9icm93c2VyCl9zYW5pdGl6ZWRfY2xhc3NfdG9rZW5zCnAwCi4="
    ),
    "_visible_elements": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3Zpc2libGVfZWxlbWVudHMKcDEKdFJw"
        "Mgou"
    ),
}


class _Element:
    def __init__(
        self,
        *,
        visible: bool = True,
        visible_error: BaseException | None = None,
        response: object = None,
        execute_error: BaseException | None = None,
    ) -> None:
        self._visible = visible
        self._visible_error = visible_error
        self._response = response
        self._execute_error = execute_error

    async def is_visible(self) -> bool:
        if self._visible_error is not None:
            raise self._visible_error
        return self._visible

    async def execute_script(self, *_args: object, **_kwargs: object) -> object:
        if self._execute_error is not None:
            raise self._execute_error
        return self._response


class _Root:
    def __init__(self, result: object) -> None:
        self._result = result

    async def query(
        self,
        selector: str,
        *,
        find_all: bool,
        raise_exc: bool,
    ) -> object:
        assert (selector, find_all, raise_exc) == ("button", True, False)
        return self._result


def _response(**values: object) -> dict[str, object]:
    return {"result": {"result": {"value": values}}}


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
        if (
            function == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            parent = _resolved_name(first, bindings)
            return f"{parent}.{node.args[1].value}" if parent is not None else None
    return None


def _module_references(source: str, path: Path, module: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    bindings: dict[str, str] = {}
    references: list[str] = []
    parent, _, member = module.rpartition(".")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
                if alias.name == module:
                    references.append(f"{node.lineno}:import")
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = ".".join(
                        part for part in (resolved, alias.name) if part
                    )
            if resolved == module or (
                resolved == parent and any(alias.name in {member, "*"} for alias in node.names)
            ):
                references.append(f"{node.lineno}:from")

    for _ in range(3):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _resolved_name(node.value, bindings)
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = value

    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Call)):
            if _resolved_name(node, bindings) == module:
                references.append(f"{node.lineno}:reference")
    return sorted(set(references))


def test_live_dom_has_exact_owner_boundary() -> None:
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
    assert all(getattr(owner, name).__module__ == OWNER_MODULE for name in OWNER_SYMBOLS)
    assert tuple(base.__name__ for base in owner.PydollControlState.__bases__) == (
        "ReservationControlState",
        "SearchControlState",
    )
    assert tuple(owner.PydollControlState.__dataclass_fields__) == (
        "enabled",
        "aria_disabled",
        "disabled_attribute",
        "classes",
        "container_classes",
        "slide_classes",
        "read_error",
    )
    assert owner.PydollControlState.__dataclass_params__.frozen is True
    assert direct_imports == {("re", None)}
    assert from_imports == {
        (0, "__future__", (("annotations", None),)),
        (0, "collections.abc", (("AsyncIterable", None),)),
        (0, "dataclasses", (("dataclass", None),)),
        (0, "typing", (("Any", None),)),
        (1, "reservation_driver", (("ReservationControlState", None),)),
        (1, "search_driver", (("SearchControlState", None),)),
        (1, "search_hour_policy", (("has_disabled_class", None),)),
    }


def test_browser_keeps_live_dom_aliases_surface_and_legacy_pickles() -> None:
    assert browser._ControlState is owner.PydollControlState
    assert browser._sanitized_class_tokens is owner.sanitized_class_tokens
    assert browser._PydollSession._read_control_state is owner.read_control_state
    assert browser._PydollSession._collect_visible_elements is owner.visible_elements
    assert browser._PydollSession._visible_elements is not owner.visible_elements
    assert browser._PydollSession._visible_elements.__module__ == browser.__name__

    assert len({name for name in vars(browser) if not name.startswith("_")}) == 84
    private_names = {
        name for name in vars(browser) if name.startswith("_") and not name.startswith("__")
    }
    assert len(private_names) == 29
    assert "_live_dom_owner" not in private_names
    assert not hasattr(browser, "__all__")

    targets = {
        "_ControlState": owner.PydollControlState,
        "_read_control_state": owner.read_control_state,
        "_sanitized_class_tokens": owner.sanitized_class_tokens,
        "_visible_elements": browser._PydollSession._visible_elements,
    }
    for legacy_name, payload in LEGACY_PICKLES.items():
        assert pickle.loads(base64.b64decode(payload)) is targets[legacy_name]


def test_live_dom_has_exact_consumers_and_passive_import_orders() -> None:
    probe_path = SOURCE_ROOT / "_live_dom_reference_probe.py"
    canonical_forms = (
        f"import {OWNER_MODULE}",
        "from rail_waitlist.korail_sidecar.pydoll import live_dom",
        f"from {OWNER_MODULE} import read_control_state",
        f"import importlib\nowner = importlib.import_module('{OWNER_MODULE}')",
    )
    for source in canonical_forms:
        assert _module_references(source, probe_path, OWNER_MODULE)

    legacy_target = f"{LEGACY_BROWSER_MODULE}._ControlState"
    legacy_forms = (
        f"from {LEGACY_BROWSER_MODULE} import _ControlState",
        "from rail_waitlist import korail_pydoll_browser as legacy\nlegacy._ControlState",
        f"import importlib\nlegacy = importlib.import_module('{LEGACY_BROWSER_MODULE}')\n"
        "legacy._ControlState",
        f"legacy = __import__('{LEGACY_BROWSER_MODULE}', fromlist=['*'])\nlegacy._ControlState",
    )
    for source in legacy_forms:
        assert _module_references(source, probe_path, legacy_target)

    canonical_consumers: set[str] = set()
    legacy_reentries: set[str] = set()
    for module_path in sorted(SOURCE_ROOT.rglob("*.py")):
        if module_path == OWNER_PATH:
            continue
        relative_path = module_path.relative_to(SOURCE_ROOT).as_posix()
        source = module_path.read_text(encoding="utf-8")
        if _module_references(source, module_path, OWNER_MODULE):
            canonical_consumers.add(relative_path)
        if relative_path != "korail_pydoll_browser.py" and any(
            _module_references(source, module_path, f"{LEGACY_BROWSER_MODULE}.{symbol}")
            for symbol in ("_ControlState", "_sanitized_class_tokens")
        ):
            legacy_reentries.add(relative_path)
    assert canonical_consumers == {
        "korail_pydoll_browser.py",
        "korail_sidecar/pydoll/dom_interaction.py",
    }
    assert legacy_reentries == set()

    script = r"""
import importlib
import json
import sys

modules = {
    "browser": "rail_waitlist.korail_pydoll_browser",
    "owner": "rail_waitlist.korail_sidecar.pydoll.live_dom",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import live_dom as owner

print(json.dumps({
    "identity": all((
        browser._ControlState is owner.PydollControlState,
        browser._sanitized_class_tokens is owner.sanitized_class_tokens,
        browser._PydollSession._read_control_state is owner.read_control_state,
        browser._PydollSession._collect_visible_elements is owner.visible_elements,
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
async def test_visible_elements_handles_supported_collections_and_detached_nodes() -> None:
    visible = _Element()
    hidden = _Element(visible=False)
    detached = _Element(visible_error=RuntimeError("detached"))

    assert await owner.visible_elements(_Root(None), "button") == []
    assert await owner.visible_elements(_Root([visible, hidden, detached]), "button") == [visible]

    async def generate():  # type: ignore[no-untyped-def]
        yield visible
        yield hidden

    assert await owner.visible_elements(_Root(generate()), "button") == [visible]


@pytest.mark.asyncio
async def test_control_state_reads_live_disabled_and_bounded_metadata() -> None:
    live = _Element(
        response=_response(
            ariaDisabled="FALSE",
            disabledAttribute=False,
            className="one two three four five six seven eight ignored",
            containerClassName="current invalid! " + ("x" * 41),
            slideClassName="slick-slide",
        )
    )
    state = await owner.read_control_state(live)

    assert state == owner.PydollControlState(
        enabled=True,
        aria_disabled="false",
        disabled_attribute=False,
        classes=("one", "two", "three", "four", "five", "six", "seven", "eight"),
        container_classes=("current",),
        slide_classes=("slick-slide",),
    )
    assert owner.sanitized_class_tokens("ok bad! " + ("x" * 41) + " tail") == ("ok", "tail")

    for field, value in (
        ("className", "disabled"),
        ("containerClassName", "off"),
        ("slideClassName", "slick-disabled"),
    ):
        blocked = await owner.read_control_state(
            _Element(
                response=_response(
                    ariaDisabled="false",
                    disabledAttribute=False,
                    **{field: value},
                )
            )
        )
        assert blocked.enabled is False


@pytest.mark.asyncio
async def test_live_dom_failures_are_bounded_without_swallowing_cancellation() -> None:
    expected = owner.PydollControlState(
        enabled=False,
        aria_disabled="read_error",
        disabled_attribute=False,
        classes=(),
        container_classes=(),
        slide_classes=(),
        read_error=True,
    )
    malformed = _Element(response={"result": {"result": {"value": []}}})
    failed = _Element(execute_error=RuntimeError("opaque provider detail"))

    assert await owner.read_control_state(malformed) == expected
    assert await owner.read_control_state(failed) == expected

    with pytest.raises(asyncio.CancelledError):
        await owner.read_control_state(_Element(execute_error=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await owner.visible_elements(
            _Root([_Element(visible_error=asyncio.CancelledError())]),
            "button",
        )
