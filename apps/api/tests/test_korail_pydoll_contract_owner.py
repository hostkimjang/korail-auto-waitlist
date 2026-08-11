from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_pydoll_auth_contracts as legacy_auth
from rail_waitlist import korail_pydoll_contracts as legacy_page
from rail_waitlist import korail_pydoll_reservation_contracts as legacy_reservation
from rail_waitlist.korail_sidecar.pydoll import auth_contracts as auth_owner
from rail_waitlist.korail_sidecar.pydoll import page_contracts as page_owner
from rail_waitlist.korail_sidecar.pydoll import reservation_contracts as reservation_owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"

PUBLIC_SURFACES = {
    "korail_pydoll_contracts.py": {
        "KORAIL_ROUTE_HEADING",
        "PydollPageSnapshot",
        "PydollSeatBox",
        "PydollTrainRow",
        "annotations",
        "dataclass",
        "normalize_korail_station",
        "normalize_korail_train_number",
        "re",
    },
    "korail_pydoll_auth_contracts.py": {
        "KorailCredentialInput",
        "KorailLoginMethod",
        "StrEnum",
        "annotations",
        "dataclass",
        "field",
    },
    "korail_pydoll_reservation_contracts.py": {
        "Callable",
        "KorailCredentialInput",
        "KorailReservationOutcome",
        "KorailReservationProgress",
        "KorailReservationProgressCallback",
        "KorailReservationProgressStage",
        "KorailReservedSeat",
        "KorailReservationRequest",
        "KorailReservationResult",
        "KorailReservationSeatClass",
        "StrEnum",
        "Literal",
        "annotations",
        "clock_time",
        "dataclass",
        "date",
        "datetime",
        "field",
    },
}
MODULES = {
    "korail_pydoll_contracts.py": (legacy_page, page_owner, "page_contracts"),
    "korail_pydoll_auth_contracts.py": (legacy_auth, auth_owner, "auth_contracts"),
    "korail_pydoll_reservation_contracts.py": (
        legacy_reservation,
        reservation_owner,
        "reservation_contracts",
    ),
}
OWNER_DEFINITIONS = {
    page_owner: {
        "PydollSeatBox",
        "PydollTrainRow",
        "PydollPageSnapshot",
        "normalize_korail_station",
        "normalize_korail_train_number",
    },
    auth_owner: {"KorailLoginMethod", "KorailCredentialInput"},
    reservation_owner: {
        "KorailReservationSeatClass",
        "KorailReservationOutcome",
        "KorailReservationProgress",
        "KorailReservationProgressCallback",
        "KorailReservationProgressStage",
        "KorailReservedSeat",
        "KorailReservationRequest",
        "KorailReservationResult",
    },
}
LEGACY_PICKLES = {
    (page_owner, "PydollPageSnapshot"): (
        "gASVQAAAAAAAAACMJXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9jb250cmFjdHOU"
        "jBJQeWRvbGxQYWdlU25hcHNob3SUk5Qu"
    ),
    (page_owner, "normalize_korail_train_number"): (
        "gASVSwAAAAAAAACMJXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9jb250cmFjdHOU"
        "jB1ub3JtYWxpemVfa29yYWlsX3RyYWluX251bWJlcpSTlC4="
    ),
    (auth_owner, "KorailLoginMethod"): (
        "gASVRAAAAAAAAACMKnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2NvbnRyYWN0c5SM"
        "EUtvcmFpbExvZ2luTWV0aG9klJOULg=="
    ),
    (auth_owner, "KorailCredentialInput"): (
        "gASVSAAAAAAAAACMKnJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9hdXRoX2NvbnRyYWN0c5SM"
        "FUtvcmFpbENyZWRlbnRpYWxJbnB1dJSTlC4="
    ),
    (reservation_owner, "KorailReservationOutcome"): (
        "gASVUgAAAAAAAACMMXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9jb250"
        "cmFjdHOUjBhLb3JhaWxSZXNlcnZhdGlvbk91dGNvbWWUk5Qu"
    ),
    (reservation_owner, "KorailReservationRequest"): (
        "gASVUgAAAAAAAACMMXJhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9yZXNlcnZhdGlvbl9jb250"
        "cmFjdHOUjBhLb3JhaWxSZXNlcnZhdGlvblJlcXVlc3SUk5Qu"
    ),
}


def test_legacy_contract_modules_are_assignment_only_exact_facades() -> None:
    for filename, (legacy, owner, owner_name) in MODULES.items():
        path = SOURCE_ROOT / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        imports = {
            (node.module, node.level, alias.name, alias.asname)
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name != "annotations"
        }
        assignments = {
            target.id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

        assert definitions == set()
        assert imports == {("korail_sidecar.pydoll", 1, owner_name, "_owner")}
        assert set(assignments) == PUBLIC_SURFACES[filename]
        assert {name for name in vars(legacy) if not name.startswith("_")} == (
            PUBLIC_SURFACES[filename]
        )
        for symbol, value in assignments.items():
            assert isinstance(value, ast.Attribute)
            assert isinstance(value.value, ast.Name)
            assert value.value.id == "_owner"
            assert value.attr == symbol
            assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_pydoll_contract_namespace_is_passive_and_owners_are_canonical() -> None:
    package_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "__init__.py"
    package_tree = ast.parse(
        package_path.read_text(encoding="utf-8"),
        filename=str(package_path),
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef))
        for node in package_tree.body
    )

    for owner, symbols in OWNER_DEFINITIONS.items():
        for symbol in symbols:
            assert getattr(owner, symbol).__module__ == owner.__name__


def test_pydoll_contract_owners_have_exact_leaf_import_boundaries() -> None:
    expected = {
        "page_contracts.py": {
            ("__future__", 0),
            ("dataclasses", 0),
        },
        "auth_contracts.py": {
            ("__future__", 0),
            ("dataclasses", 0),
            ("enum", 0),
        },
        "reservation_contracts.py": {
            ("__future__", 0),
            ("collections.abc", 0),
            ("dataclasses", 0),
            ("datetime", 0),
            ("enum", 0),
            ("typing", 0),
            ("auth_contracts", 1),
        },
    }
    owner_root = SOURCE_ROOT / "korail_sidecar" / "pydoll"

    for filename, expected_imports in expected.items():
        path = owner_root / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports_from = {
            (node.module, node.level)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        direct_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert imports_from == expected_imports
        assert direct_imports == ({"re"} if filename == "page_contracts.py" else set())


@pytest.mark.parametrize(
    ("owner", "symbol", "payload"),
    [(*key, value) for key, value in LEGACY_PICKLES.items()],
)
def test_pre_move_pickle_globals_restore_to_the_canonical_owner(
    owner: object,
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_import_orders_keep_one_owner_without_legacy_reentry(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

first_modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.page_contracts",
    "legacy": "rail_waitlist.korail_pydoll_contracts",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(first_modules[sys.argv[1]])
legacy_names = (
    "rail_waitlist.korail_pydoll_contracts",
    "rail_waitlist.korail_pydoll_auth_contracts",
    "rail_waitlist.korail_pydoll_reservation_contracts",
)
legacy_loaded_before = any(name in sys.modules for name in legacy_names)
from rail_waitlist import korail_pydoll_auth_contracts as legacy_auth
from rail_waitlist import korail_pydoll_contracts as legacy_page
from rail_waitlist import korail_pydoll_reservation_contracts as legacy_reservation
from rail_waitlist.korail_sidecar.pydoll import auth_contracts as auth_owner
from rail_waitlist.korail_sidecar.pydoll import page_contracts as page_owner
from rail_waitlist.korail_sidecar.pydoll import reservation_contracts as reservation_owner

print(json.dumps({
    "identity": all((
        legacy_page.PydollPageSnapshot is page_owner.PydollPageSnapshot,
        legacy_page.normalize_korail_train_number is page_owner.normalize_korail_train_number,
        legacy_auth.KorailLoginMethod is auth_owner.KorailLoginMethod,
        legacy_auth.KorailCredentialInput is auth_owner.KorailCredentialInput,
        legacy_reservation.KorailReservationOutcome is reservation_owner.KorailReservationOutcome,
        legacy_reservation.KorailReservationRequest is reservation_owner.KorailReservationRequest,
    )),
    "legacy_loaded_before": legacy_loaded_before,
    "modules": sorted({
        page_owner.PydollPageSnapshot.__module__,
        auth_owner.KorailCredentialInput.__module__,
        reservation_owner.KorailReservationRequest.__module__,
    }),
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
        "identity": True,
        "legacy_loaded_before": first_import == "legacy",
        "modules": [
            "rail_waitlist.korail_sidecar.pydoll.auth_contracts",
            "rail_waitlist.korail_sidecar.pydoll.page_contracts",
            "rail_waitlist.korail_sidecar.pydoll.reservation_contracts",
        ],
    }


def test_production_consumers_import_exact_canonical_contract_owners() -> None:
    expected = {
        "korail_sidecar.pydoll.page_contracts": {
            "korail_browser_mode_smoke.py",
            "korail_sidecar/pydoll/auth_actor.py",
            "korail_pydoll_browser.py",
            "korail_pydoll_reservation_actor.py",
            "korail_sidecar/pydoll/login_driver.py",
            "korail_sidecar/pydoll/page_safety.py",
            "korail_sidecar/pydoll/search_snapshot_policy.py",
            "korail_sidecar/pydoll/reservation_actor.py",
            "korail_sidecar/pydoll/reservation_driver.py",
            "korail_sidecar/pydoll/search_actor.py",
            "korail_sidecar/pydoll/search_driver.py",
        },
        "korail_sidecar.pydoll.auth_contracts": {
            "korail_sidecar/http.py",
            "korail_sidecar/pydoll/auth_actor.py",
            "korail_pydoll_browser.py",
            "korail_pydoll_reservation_actor.py",
            "korail_sidecar/pydoll/login_driver.py",
            "korail_sidecar/pydoll/reservation_actor.py",
            "korail_sidecar/pydoll/reservation_driver.py",
            "korail_sidecar/pydoll/reservation_contracts.py",
        },
        "korail_sidecar.pydoll.reservation_contracts": {
            "korail_sidecar/http.py",
            "korail_pydoll_browser.py",
            "korail_pydoll_reservation_actor.py",
            "korail_sidecar/pydoll/reservation_actor.py",
            "korail_sidecar/pydoll/reservation_driver.py",
        },
    }
    actual = {module: set() for module in expected}

    for path in SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in actual:
                actual[node.module].add(relative_path)
            elif (
                relative_path == "korail_sidecar/pydoll/reservation_contracts.py"
                and isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module == "auth_contracts"
            ):
                actual["korail_sidecar.pydoll.auth_contracts"].add(relative_path)
            elif (
                relative_path == "korail_sidecar/http.py"
                and isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module in {"pydoll.auth_contracts", "pydoll.reservation_contracts"}
            ):
                actual[f"korail_sidecar.{node.module}"].add(relative_path)
            elif (
                relative_path.startswith("korail_sidecar/pydoll/")
                and isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module == "page_contracts"
            ):
                actual["korail_sidecar.pydoll.page_contracts"].add(relative_path)
            elif (
                relative_path.startswith("korail_sidecar/pydoll/")
                and isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module in {"auth_contracts", "reservation_contracts"}
            ):
                actual[f"korail_sidecar.pydoll.{node.module}"].add(relative_path)

    assert actual == expected
