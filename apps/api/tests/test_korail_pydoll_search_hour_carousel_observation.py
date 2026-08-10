from __future__ import annotations

import ast
import asyncio
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.browser_contracts import BrowserSourceUnavailable
from rail_waitlist.korail_sidecar.pydoll import search_hour_carousel_observation as owner
from rail_waitlist.korail_sidecar.pydoll.search_driver import SearchHourCandidate

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_PATH = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "search_hour_carousel_observation.py"
OWNER_MODULE = "rail_waitlist.korail_sidecar.pydoll.search_hour_carousel_observation"
BROWSER_MODULE = "rail_waitlist.korail_pydoll_browser"
OWNER_SYMBOLS = (
    "PydollHourCarouselObservationPort",
    "find_hour_navigation_control",
    "hour_carousel_control_metadata",
    "log_hour_window_navigation_failure",
    "read_hour_candidates",
    "wait_for_hour_animation",
    "wait_for_hour_window_change",
)
HOOKS = {
    "_read_hour_candidates": ("_read_hour_candidates_observation", "read_hour_candidates"),
    "_wait_for_hour_window_change": (
        "_wait_for_hour_window_change_observation",
        "wait_for_hour_window_change",
    ),
    "_log_hour_window_navigation_failure": (
        "_log_hour_window_navigation_failure_observation",
        "log_hour_window_navigation_failure",
    ),
    "_wait_for_hour_animation": (
        "_wait_for_hour_animation_observation",
        "wait_for_hour_animation",
    ),
    "_hour_carousel_control_metadata": (
        "_hour_carousel_control_metadata_observation",
        "hour_carousel_control_metadata",
    ),
    "_find_hour_navigation_control": (
        "_find_hour_navigation_control_observation",
        "find_hour_navigation_control",
    ),
}
LEGACY_PICKLES = {
    "_read_hour_candidates": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3JlYWRfaG91cl9jYW5kaWRhdGVzCnAx"
        "CnRScDIKLg=="
    ),
    "_wait_for_hour_window_change": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX2hvdXJfd2luZG93X2No"
        "YW5nZQpwMQp0UnAyCi4="
    ),
    "_log_hour_window_navigation_failure": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2xvZ19ob3VyX3dpbmRvd19uYXZpZ2F0"
        "aW9uX2ZhaWx1cmUKcDEKdFJwMgou"
    ),
    "_wait_for_hour_animation": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX3dhaXRfZm9yX2hvdXJfYW5pbWF0aW9u"
        "CnAxCnRScDIKLg=="
    ),
    "_hour_carousel_control_metadata": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2hvdXJfY2Fyb3VzZWxfY29udHJvbF9t"
        "ZXRhZGF0YQpwMQp0UnAyCi4="
    ),
    "_find_hour_navigation_control": (
        "Y19fYnVpbHRpbl9fCmdldGF0dHIKcDAKKGNyYWlsX3dhaXRsaXN0LmtvcmFpbF9weWRv"
        "bGxfYnJvd3NlcgpfUHlkb2xsU2Vzc2lvbgpWX2ZpbmRfaG91cl9uYXZpZ2F0aW9uX2Nv"
        "bnRyb2wKcDEKdFJwMgou"
    ),
}


class _AwaitableText:
    def __init__(self, value: str) -> None:
        self.value = value

    def __await__(self):  # type: ignore[no-untyped-def]
        async def resolve() -> str:
            return self.value

        return resolve().__await__()


class _Element:
    def __init__(self, label: str, *, enabled: bool = True) -> None:
        self.text = _AwaitableText(label)
        self.state = SimpleNamespace(enabled=enabled)


class _Scope:
    def __init__(self, elements: list[object]) -> None:
        self.elements = elements
        self.calls: list[tuple[str, bool, bool]] = []

    async def query(self, selector: str, *, find_all: bool, raise_exc: bool) -> list[object]:
        self.calls.append((selector, find_all, raise_exc))
        return self.elements


class _Dialog:
    def __init__(self, value: object = 0, *, error: BaseException | None = None) -> None:
        self.value = value
        self.error = error
        self.scripts: list[str] = []

    async def execute_script(self, script: str, **_kwargs: object) -> object:
        self.scripts.append(script)
        if self.error is not None:
            raise self.error
        return {"result": {"result": {"value": self.value}}}


class _Port:
    def __init__(self) -> None:
        self.visible: list[object] = []
        self.visible_calls: list[tuple[str, object]] = []
        self.reads: list[list[SearchHourCandidate] | BaseException] = []
        self.animation_calls: list[tuple[object, tuple[int, ...]]] = []
        self.metadata: tuple[object, ...] = ()

    async def _visible_elements(self, selector: str, *, scope: object = None) -> list[object]:
        self.visible_calls.append((selector, scope))
        return self.visible

    async def _read_control_state(self, element: object) -> object:
        return element.state  # type: ignore[attr-defined, no-any-return]

    async def _read_hour_candidates(
        self,
        _selector: str,
        *,
        scope: object,
        visible_only: bool = True,
    ) -> list[SearchHourCandidate]:
        assert scope is not None
        assert visible_only is True
        current = self.reads.pop(0)
        if isinstance(current, BaseException):
            raise current
        return current

    def _current_hour_window(
        self,
        candidates: list[SearchHourCandidate],
    ) -> list[SearchHourCandidate]:
        return candidates

    async def _wait_for_hour_animation(
        self,
        dialog: object,
        expected_hours: tuple[int, ...],
    ) -> None:
        self.animation_calls.append((dialog, expected_hours))

    async def _hour_carousel_control_metadata(self, _dialog: object) -> tuple[object, ...]:
        return self.metadata


def _candidate(hour: int, *, enabled: bool = True) -> SearchHourCandidate:
    element = _Element(f"{hour:02d}시", enabled=enabled)
    return SearchHourCandidate(element=element, hour=hour, state=element.state)


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


def _clock(values: list[float]):  # type: ignore[no-untyped-def]
    iterator = iter(values)
    return lambda: next(iterator)


def test_observation_owner_has_exact_surface_and_dependency_boundary() -> None:
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
    assert direct_imports == {("re", None)}
    assert from_imports == {
        (0, "__future__", (("annotations", None),)),
        (0, "collections.abc", (("Awaitable", None), ("Callable", None))),
        (0, "logging", (("Logger", None),)),
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
async def test_browser_preserves_hooks_wrappers_pickles_surface_and_late_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_type = browser._PydollSession
    for legacy_name, (hook_name, owner_name) in HOOKS.items():
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
    assert "_search_hour_carousel_observation_owner" not in private_names
    assert not hasattr(browser, "__all__")

    session = session_type("https://www.korail.com/ticket/search/general", 1_000, True)
    monotonic = Mock(return_value=0.0)
    sleep = AsyncMock()
    wait_hook = AsyncMock(return_value=False)
    monkeypatch.setattr(browser.time, "monotonic", monotonic)
    monkeypatch.setattr(browser.asyncio, "sleep", sleep)
    monkeypatch.setattr(session, "_wait_for_hour_window_change_observation", wait_hook)
    assert await session._wait_for_hour_window_change(object(), (10,), ".slick-next") is False
    assert wait_hook.await_args.kwargs["monotonic"] is monotonic
    assert wait_hook.await_args.kwargs["sleep"] is sleep

    log_hook = AsyncMock()
    monkeypatch.setattr(session, "_log_hour_window_navigation_failure_observation", log_hook)
    await session._log_hour_window_navigation_failure(object(), (10,))
    assert log_hook.await_args.kwargs["event_logger"] is browser.logger

    metadata_hook = AsyncMock(return_value=())
    monkeypatch.setattr(
        session_type,
        "_hour_carousel_control_metadata_observation",
        staticmethod(metadata_hook),
    )
    assert await session_type._hour_carousel_control_metadata(object()) == ()
    assert (
        metadata_hook.await_args.kwargs["sanitize_class_tokens"] is browser._sanitized_class_tokens
    )


def test_observation_has_one_consumer_and_passive_import_orders() -> None:
    probe_path = SOURCE_ROOT / "_carousel_observation_reference_probe.py"
    canonical_forms = (
        f"import {OWNER_MODULE}",
        "from rail_waitlist.korail_sidecar.pydoll import search_hour_carousel_observation",
        f"from {OWNER_MODULE} import read_hour_candidates",
        f"import importlib\nowner = importlib.import_module('{OWNER_MODULE}')",
    )
    assert all(_module_references(source, probe_path, OWNER_MODULE) for source in canonical_forms)

    legacy_targets = tuple(f"{BROWSER_MODULE}._PydollSession.{name}" for name in HOOKS)
    legacy_forms = (
        f"from {BROWSER_MODULE} import _PydollSession\n_PydollSession._read_hour_candidates",
        "from rail_waitlist import korail_pydoll_browser as legacy\n"
        "legacy._PydollSession._wait_for_hour_animation",
        f"import importlib\nlegacy = importlib.import_module('{BROWSER_MODULE}')\n"
        "legacy._PydollSession._find_hour_navigation_control",
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
    "owner": "rail_waitlist.korail_sidecar.pydoll.search_hour_carousel_observation",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist.korail_sidecar.pydoll import search_hour_carousel_observation as owner

session = browser._PydollSession
print(json.dumps({
    "identity": all((
        session._read_hour_candidates_observation is owner.read_hour_candidates,
        session._wait_for_hour_window_change_observation is owner.wait_for_hour_window_change,
        session._log_hour_window_navigation_failure_observation
            is owner.log_hour_window_navigation_failure,
        session._wait_for_hour_animation_observation is owner.wait_for_hour_animation,
        session._hour_carousel_control_metadata_observation
            is owner.hour_carousel_control_metadata,
        session._find_hour_navigation_control_observation
            is owner.find_hour_navigation_control,
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
async def test_candidate_reading_keeps_exact_visible_and_raw_catalog_contracts() -> None:
    valid = _Element(" 09시 ")
    duplicate = _Element("09시", enabled=False)
    invalid = _Element("9시")
    port = _Port()
    port.visible = [valid, invalid, duplicate]
    visible = await owner.read_hour_candidates(port, "a.hour", scope=object())
    assert [(item.element, item.hour, item.state.enabled) for item in visible] == [
        (valid, 9, True),
        (duplicate, 9, False),
    ]
    assert port.visible_calls[0][0] == "a.hour"

    hidden = _Element("23시")
    scope = _Scope([hidden, _Element("24:00")])
    port.visible = [invalid]
    raw = await owner.read_hour_candidates(
        port,
        "a.hour",
        scope=scope,
        visible_only=False,
    )
    assert [(item.element, item.hour) for item in raw] == [(hidden, 23)]
    assert scope.calls == [("a.hour", True, False)]
    assert len(port.visible_calls) == 1


@pytest.mark.asyncio
async def test_window_polling_and_animation_preserve_stability_errors_and_cancellation() -> None:
    sleep = AsyncMock()
    for direction, before, hours in (
        (".slick-next", (10,), (11, 12)),
        (".slick-prev", (10,), (8, 9)),
    ):
        port = _Port()
        port.reads = [[_candidate(hour) for hour in hours] for _ in range(2)]
        dialog = object()
        assert await owner.wait_for_hour_window_change(
            port,
            dialog,
            before,
            direction,
            timeout_seconds=1,
            default_timeout_seconds=9,
            monotonic=_clock([0, 0.01, 0.02]),
            sleep=sleep,
        )
        assert port.animation_calls == [(dialog, hours)]

    stalled = _Port()
    stalled.reads = [[_candidate(11)], [_candidate(10)], [_candidate(11)]]
    assert not await owner.wait_for_hour_window_change(
        stalled,
        object(),
        (10,),
        ".slick-next",
        timeout_seconds=0.05,
        default_timeout_seconds=9,
        monotonic=_clock([0, 0.01, 0.02, 0.03, 0.06]),
        sleep=sleep,
    )
    assert stalled.animation_calls == []

    settled = _Port()
    settled.reads = [[_candidate(8), _candidate(9)]]
    animation_sleep = AsyncMock()
    await owner.wait_for_hour_animation(
        settled,
        _Dialog(250),
        (8, 9),
        sleep=animation_sleep,
    )
    animation_sleep.assert_awaited_once_with(0.3)

    for dialog, expected_error in (
        (_Dialog(250), BrowserSourceUnavailable),
        (_Dialog(error=RuntimeError("opaque")), BrowserSourceUnavailable),
        (_Dialog(error=asyncio.CancelledError()), asyncio.CancelledError),
    ):
        failing = _Port()
        failing.reads = [[_candidate(7)]]
        with pytest.raises(expected_error) as raised:
            await owner.wait_for_hour_animation(
                failing,
                dialog,
                (8,),
                sleep=AsyncMock(),
            )
        if isinstance(raised.value, BrowserSourceUnavailable):
            assert raised.value.stage == "departure_hour_navigate"


@pytest.mark.asyncio
async def test_metadata_logging_and_arrow_selection_are_bounded_and_fail_closed() -> None:
    sanitizer = Mock(side_effect=lambda value: tuple(str(value).split()))
    metadata_dialog = _Dialog(
        [
            {
                "tag": "button-name-is-too-long",
                "classes": ["next", "enabled"],
                "relation": "inside-too-long",
                "parentClasses": ["slideWrap"],
            },
            "ignored",
        ]
    )
    metadata = await owner.hour_carousel_control_metadata(
        metadata_dialog,
        sanitize_class_tokens=sanitizer,
    )
    assert metadata == (("button-name-is-t", ("next", "enabled"), "inside-t", ("slideWrap",)),)
    assert ".slice(0, 24)" in metadata_dialog.scripts[0]
    assert "textContent" not in metadata_dialog.scripts[0]
    assert (
        await owner.hour_carousel_control_metadata(
            _Dialog("not-a-list"),
            sanitize_class_tokens=sanitizer,
        )
        == ()
    )
    assert (
        await owner.hour_carousel_control_metadata(
            _Dialog(error=RuntimeError("opaque")),
            sanitize_class_tokens=sanitizer,
        )
        == ()
    )
    with pytest.raises(asyncio.CancelledError):
        await owner.hour_carousel_control_metadata(
            _Dialog(error=asyncio.CancelledError()),
            sanitize_class_tokens=sanitizer,
        )

    port = _Port()
    port.reads = [[_candidate(11)]]
    port.metadata = metadata
    event_logger = Mock()
    await owner.log_hour_window_navigation_failure(
        port,
        object(),
        (10,),
        event_logger=event_logger,
    )
    assert event_logger.warning.call_args.args[1:] == ((10,), (11,), metadata)

    enabled = _Element("next", enabled=True)
    disabled = _Element("next", enabled=False)
    port.visible = [disabled, enabled]
    assert await owner.find_hour_navigation_control(port, ".slick-next", scope=object()) is enabled
    port.visible = [enabled, _Element("duplicate", enabled=True)]
    assert await owner.find_hour_navigation_control(port, ".slick-next", scope=object()) is None
    port.visible = []
    assert await owner.find_hour_navigation_control(port, ".slick-prev", scope=object()) is None

    async def cancelled(_selector: str, *, scope: object = None) -> list[object]:
        raise asyncio.CancelledError

    port._visible_elements = cancelled  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await owner.find_hour_navigation_control(port, ".slick-next", scope=object())
