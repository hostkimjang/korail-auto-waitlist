from __future__ import annotations

import ast
import pickle
import subprocess
import sys
from pathlib import Path

from rail_waitlist import fullstack_srt_fixture as legacy
from rail_waitlist.provider_adapters import srt_fullstack_fixture as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src"
OWNER_PATH = SOURCE_ROOT / "rail_waitlist" / "provider_adapters" / "srt_fullstack_fixture.py"
FACADE_PATH = SOURCE_ROOT / "rail_waitlist" / "fullstack_srt_fixture.py"
LOCAL_NAMES = {
    "FixtureSrtTrain",
    "FullstackSrtFixtureClient",
    "fullstack_srt_client_factory",
}
LEGACY_PUBLIC = {
    "FixtureSrtTrain",
    "FullstackSrtFixtureClient",
    "Request",
    "annotations",
    "dataclass",
    "fullstack_srt_client_factory",
    "json",
    "urlencode",
    "urlopen",
}


def test_srt_fullstack_fixture_owner_and_facade_have_exact_surfaces() -> None:
    owner_tree = ast.parse(OWNER_PATH.read_text(encoding="utf-8"), filename=str(OWNER_PATH))
    facade_tree = ast.parse(FACADE_PATH.read_text(encoding="utf-8"), filename=str(FACADE_PATH))
    definitions = {
        node.name
        for node in owner_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    facade_definitions = {
        node.name
        for node in facade_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    facade_assignments = {
        target.id
        for node in facade_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert definitions == LOCAL_NAMES
    assert facade_definitions == set()
    assert facade_assignments == set()
    assert {name for name in vars(legacy) if not name.startswith("_")} == LEGACY_PUBLIC
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == set()
    assert not hasattr(legacy, "__all__")
    for name in LEGACY_PUBLIC:
        assert getattr(legacy, name) is getattr(owner, name)
    for name in LOCAL_NAMES:
        assert getattr(owner, name).__module__ == owner.__name__


def test_old_srt_fullstack_fixture_pickles_restore_canonical_objects() -> None:
    for name in LOCAL_NAMES:
        payload = f"crail_waitlist.fullstack_srt_fixture\n{name}\n.".encode()
        assert pickle.loads(payload) is getattr(owner, name)


def test_srt_fullstack_fixture_import_order_preserves_identity() -> None:
    script = """
import sys
if sys.argv[1] == 'owner-first':
    from rail_waitlist.provider_adapters import srt_fullstack_fixture as owner
    assert 'rail_waitlist.fullstack_srt_fixture' not in sys.modules
    from rail_waitlist import fullstack_srt_fixture as legacy
else:
    from rail_waitlist import fullstack_srt_fixture as legacy
    from rail_waitlist.provider_adapters import srt_fullstack_fixture as owner
names = ('FixtureSrtTrain', 'FullstackSrtFixtureClient', 'fullstack_srt_client_factory')
assert all(getattr(legacy, name) is getattr(owner, name) for name in names)
"""
    for order in ("owner-first", "legacy-first"):
        subprocess.run(
            [sys.executable, "-W", "error", "-c", script, order],
            cwd=API_ROOT,
            check=True,
        )


def test_srt_fullstack_fixture_facade_reassignment_is_one_way(monkeypatch) -> None:
    original = owner.urlopen

    monkeypatch.setattr(legacy, "urlopen", object())

    assert owner.urlopen is original


def test_srt_fullstack_fixture_has_two_canonical_consumers_and_no_legacy_reentry() -> None:
    canonical: set[str] = set()
    legacy_consumers: set[str] = set()
    for path in sorted((SOURCE_ROOT / "rail_waitlist").rglob("*.py")):
        if path in {OWNER_PATH, FACADE_PATH}:
            continue
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "provider_adapters.srt_fullstack_fixture" and node.level == 1:
                canonical.add(relative)
            if node.module == "srt_fullstack_fixture" and node.level == 1:
                if any(alias.name == "fullstack_srt_client_factory" for alias in node.names):
                    canonical.add(relative)
            if node.module == "fullstack_srt_fixture" and node.level in {1, 2}:
                legacy_consumers.add(relative)

    assert canonical == {
        "rail_waitlist/main.py",
        "rail_waitlist/provider_adapters/srt_source_runtime.py",
    }
    assert legacy_consumers == set()
