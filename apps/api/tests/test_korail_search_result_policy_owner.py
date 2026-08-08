from __future__ import annotations

import ast
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_browser_automation as legacy
from rail_waitlist.korail_sidecar import search_result_policy as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
SCRIPT_ROOT = API_ROOT / "scripts"
CONSTANTS = {
    "ADULT_FARE_PATTERN",
    "DELAY_ESTIMATE_PATTERN",
    "KST",
    "OFFICIAL_TRAIN_TYPE_PATTERN",
}
FUNCTIONS = {
    "is_supported_korail_train_kind",
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "service_datetimes",
    "status_from_seat_box",
    "visible_departure_matches",
}
MOVED_SYMBOLS = CONSTANTS | FUNCTIONS
EXPECTED_CANONICAL_CONSUMERS = {
    "korail_browser_automation.py": MOVED_SYMBOLS,
    "korail_sidecar/pydoll/search_actor.py": {
        "parse_expected_delay_minutes",
        "parse_official_train_type",
        "parse_unambiguous_adult_fare",
        "service_datetimes",
        "status_from_seat_box",
    },
    "korail_sidecar/http_replay.py": {
        "parse_official_train_type",
        "parse_unambiguous_adult_fare",
    },
    "korail_sidecar/playwright/search_form.py": {
        "visible_departure_matches",
    },
    "korail_sidecar/playwright/result_reader.py": {
        "parse_expected_delay_minutes",
        "parse_official_train_type",
        "parse_unambiguous_adult_fare",
        "service_datetimes",
        "status_from_seat_box",
        "visible_departure_matches",
    },
    "provider_adapters/korail_reservation_controls.py": {"status_from_seat_box"},
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_module_name(node: ast.Call, bindings: dict[str, str]) -> str | None:
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return None
    if not isinstance(node.args[0].value, str):
        return None
    if isinstance(node.func, ast.Name) and node.func.id == "__import__":
        return node.args[0].value
    if _resolved_name(node.func, bindings) == "importlib.import_module":
        return node.args[0].value
    return None


def _resolved_name(node: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Call):
        return _call_module_name(node, bindings)
    return None


def _import_bindings(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                bindings[local_name] = alias.name if alias.asname else local_name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                bindings[alias.asname or alias.name] = ".".join(
                    part for part in (module, alias.name) if part
                )

    # Resolve simple module forwarding such as ``policy = importlib.import_module(...)``.
    for _ in range(2):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            resolved = _resolved_name(value, bindings)
            if resolved is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = resolved
    return bindings


def _moved_policy_symbol(value: str, owner_module: str) -> str | None:
    prefixes = {f"{owner_module}.", f"{owner_module.rsplit('.', maxsplit=1)[-1]}."}
    for symbol in MOVED_SYMBOLS:
        if any(value.endswith(f"{prefix}{symbol}") for prefix in prefixes):
            return symbol
    return None


def _policy_references(tree: ast.Module, owner_module: str) -> set[str]:
    bindings = _import_bindings(tree)
    references = {
        symbol
        for value in bindings.values()
        if (symbol := _moved_policy_symbol(value, owner_module)) is not None
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call)):
            resolved = _resolved_name(node, bindings)
            if resolved is not None:
                symbol = _moved_policy_symbol(resolved, owner_module)
                if symbol is not None:
                    references.add(symbol)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            module = _resolved_name(node.args[0], bindings)
            if module is not None:
                symbol = _moved_policy_symbol(f"{module}.{node.args[1].value}", owner_module)
                if symbol is not None:
                    references.add(symbol)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith(owner_module) and any(alias.name == "*" for alias in node.names):
                references.update(MOVED_SYMBOLS)
    return references


def test_policy_has_one_canonical_owner_and_exact_legacy_aliases() -> None:
    owner_tree = _tree(SOURCE_ROOT / "korail_sidecar" / "search_result_policy.py")
    legacy_tree = _tree(SOURCE_ROOT / "korail_browser_automation.py")
    owner_functions = {
        node.name
        for node in owner_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    owner_assignments = {
        target.id
        for node in owner_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    legacy_definitions = {
        node.name
        for node in legacy_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    legacy_policy_aliases = {
        alias.asname or alias.name: alias.name
        for node in legacy_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "korail_sidecar.search_result_policy"
        for alias in node.names
        if alias.name in MOVED_SYMBOLS
    }

    assert owner_functions == FUNCTIONS
    assert CONSTANTS <= owner_assignments
    assert FUNCTIONS.isdisjoint(legacy_definitions)
    assert legacy_policy_aliases == {symbol: symbol for symbol in MOVED_SYMBOLS}
    for symbol in MOVED_SYMBOLS:
        assert getattr(legacy, symbol) is getattr(owner, symbol)
    for symbol in FUNCTIONS:
        assert getattr(owner, symbol).__module__ == owner.__name__


def test_pre_move_protocol_zero_function_globals_restore_to_the_owner() -> None:
    for symbol in FUNCTIONS:
        payload = f"crail_waitlist.korail_browser_automation\n{symbol}\n.".encode()
        assert pickle.loads(payload) is getattr(owner, symbol)


@pytest.mark.parametrize(
    "first_import",
    [
        "canonical",
        "legacy",
        "search_actor",
        "replay",
        "playwright",
        "result_reader",
        "search_form",
        "reservation",
    ],
)
def test_policy_import_orders_keep_one_owner(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.search_result_policy",
    "legacy": "rail_waitlist.korail_browser_automation",
    "search_actor": "rail_waitlist.korail_pydoll_search_actor",
    "replay": "rail_waitlist.korail_sidecar.http_replay",
    "playwright": "rail_waitlist.korail_sidecar.playwright.client",
    "result_reader": "rail_waitlist.korail_sidecar.playwright.result_reader",
    "search_form": "rail_waitlist.korail_sidecar.playwright.search_form",
    "reservation": "rail_waitlist.provider_adapters.korail_reservation_controls",
}
importlib.import_module(modules[sys.argv[1]])
from rail_waitlist import korail_browser_automation as legacy
from rail_waitlist import korail_pydoll_search_actor as search_actor
from rail_waitlist.korail_sidecar import http_replay as replay
from rail_waitlist.korail_sidecar import search_result_policy as owner
from rail_waitlist.korail_sidecar.playwright import client as playwright
from rail_waitlist.korail_sidecar.playwright import result_reader
from rail_waitlist.korail_sidecar.playwright import search_form
from rail_waitlist.provider_adapters import korail_reservation_controls as reservation

all_symbols = (
    "is_supported_korail_train_kind",
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "service_datetimes",
    "status_from_seat_box",
    "visible_departure_matches",
)
search_actor_symbols = (
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "service_datetimes",
    "status_from_seat_box",
)
playwright_symbols = (
    "parse_expected_delay_minutes",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "service_datetimes",
    "status_from_seat_box",
    "visible_departure_matches",
)
print(json.dumps({
    "legacy": all(getattr(legacy, name) is getattr(owner, name) for name in all_symbols),
    "search_form": search_form.visible_departure_matches is owner.visible_departure_matches,
    "result_reader": all(
        getattr(result_reader, name) is getattr(owner, name) for name in playwright_symbols
    ),
    "replay": all(
        getattr(replay, name) is getattr(owner, name)
        for name in ("parse_official_train_type", "parse_unambiguous_adult_fare")
    ),
    "search_actor": all(
        getattr(search_actor, name) is getattr(owner, name) for name in search_actor_symbols
    ),
    "reservation": reservation.status_from_seat_box is owner.status_from_seat_box,
    "constants": all(
        getattr(legacy, name) is getattr(owner, name)
        for name in (
            "ADULT_FARE_PATTERN",
            "DELAY_ESTIMATE_PATTERN",
            "KST",
            "OFFICIAL_TRAIN_TYPE_PATTERN",
        )
    ),
    "modules": sorted({getattr(owner, name).__module__ for name in all_symbols}),
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
        "constants": True,
        "legacy": True,
        "modules": ["rail_waitlist.korail_sidecar.search_result_policy"],
        "result_reader": True,
        "replay": True,
        "reservation": True,
        "search_actor": True,
        "search_form": True,
    }


def test_policy_has_exact_canonical_consumers_and_no_legacy_symbol_imports() -> None:
    canonical_consumers: dict[str, set[str]] = {}
    legacy_symbol_imports: dict[str, set[str]] = {}
    roots = [SOURCE_ROOT]
    if SCRIPT_ROOT.exists():
        roots.append(SCRIPT_ROOT)

    for root in roots:
        for path in root.rglob("*.py"):
            relative_path = (
                path.relative_to(SOURCE_ROOT).as_posix()
                if root == SOURCE_ROOT
                else (f"scripts/{path.relative_to(SCRIPT_ROOT).as_posix()}")
            )
            tree = _tree(path)
            canonical_references = _policy_references(tree, "korail_sidecar.search_result_policy")
            legacy_references = _policy_references(tree, "korail_browser_automation")
            if canonical_references:
                canonical_consumers[relative_path] = canonical_references
            if legacy_references:
                legacy_symbol_imports[relative_path] = legacy_references

    assert canonical_consumers == EXPECTED_CANONICAL_CONSUMERS
    assert legacy_symbol_imports == {}


def test_legacy_boundary_detector_covers_direct_module_package_and_dynamic_imports() -> None:
    sources = (
        "from rail_waitlist.korail_browser_automation import service_datetimes",
        "import rail_waitlist.korail_browser_automation as legacy\nlegacy.service_datetimes",
        "from rail_waitlist import korail_browser_automation as legacy\nlegacy.service_datetimes",
        (
            "import importlib\n"
            "importlib.import_module('rail_waitlist.korail_browser_automation').service_datetimes"
        ),
        (
            "from importlib import import_module as load\n"
            "legacy = load('rail_waitlist.korail_browser_automation')\n"
            "getattr(legacy, 'service_datetimes')"
        ),
        "__import__('rail_waitlist').korail_browser_automation.service_datetimes",
    )

    for source in sources:
        assert _policy_references(ast.parse(source), "korail_browser_automation") == {
            "service_datetimes"
        }
