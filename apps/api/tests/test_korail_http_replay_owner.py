from __future__ import annotations

import ast
import base64
import json
import logging
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from rail_waitlist import korail_http_replay as legacy
from rail_waitlist.korail_sidecar import http_replay as owner

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
OWNER_MODULE = "rail_waitlist.korail_sidecar.http_replay"
PUBLIC_SYMBOLS = {
    "Any",
    "Awaitable",
    "BrowserSeatSearchRequest",
    "BrowserSeatSearchResult",
    "BrowserTrainSnapshot",
    "Callable",
    "HttpReplayInvalidCapture",
    "HttpReplayInvalidResponse",
    "HttpReplayLeaseInvalid",
    "HttpReplayPlan",
    "HttpReplayProtectionDetected",
    "HttpReplayRateLimited",
    "HttpReplayRequest",
    "HttpReplaySessionInvalid",
    "HttpReplaySourceUnavailable",
    "KST",
    "KorailHttpReplayClient",
    "KorailHttpReplayError",
    "KorailHttpReplayInvalidCapture",
    "KorailHttpReplayInvalidResponse",
    "KorailHttpReplayLeaseInvalid",
    "KorailHttpReplayPlan",
    "KorailHttpReplayProtectionDetected",
    "KorailHttpReplayRateLimited",
    "KorailHttpReplaySessionInvalid",
    "KorailHttpReplaySourceUnavailable",
    "LeaseValidator",
    "Literal",
    "MAX_PAGES",
    "MAX_RESPONSE_BYTES",
    "Mapping",
    "OFFICIAL_HOST",
    "ProtectionTrigger",
    "ReplayErrorReason",
    "SeatStatus",
    "Self",
    "Sequence",
    "UTC",
    "ZoneInfo",
    "annotations",
    "asyncio",
    "body_slice",
    "build_http_replay_plan",
    "build_korail_http_replay_plan",
    "dataclass",
    "date",
    "datetime",
    "field",
    "httpx",
    "inspect",
    "json",
    "logging",
    "parse_official_train_type",
    "parse_unambiguous_adult_fare",
    "protection_trigger_from_replay_text",
    "re",
    "time",
    "timedelta",
    "urljoin",
    "urlsplit",
}
PRIVATE_SYMBOLS = {
    "_CapturedCookie",
    "_CapturedRequest",
    "_FORBIDDEN_REQUEST_HEADERS",
    "_FieldSpan",
    "_ParsedPage",
    "_REQUIRED_FIELDS",
    "_SESSION_MARKERS",
    "_SIMPLE_COOKIE_DOMAIN",
    "_captured_body",
    "_captured_cookies",
    "_captured_headers",
    "_event_was_redirected",
    "_expected_delay_minutes",
    "_header_value",
    "_is_ktx_family",
    "_is_session_redirect",
    "_multipart_boundary",
    "_multipart_field_spans",
    "_normalize_station",
    "_normalize_train_number",
    "_one_span",
    "_parse_page",
    "_protection_marker",
    "_request_candidate",
    "_required_string",
    "_row_arrival",
    "_row_datetime",
    "_row_departure",
    "_seat_status",
    "_seat_string",
    "_session_marker",
    "_validate_business_url",
    "_validate_captured_date_hour",
    "_validate_captured_route",
}
LEGACY_PICKLES = {
    "KorailHttpReplayError": (
        "gASVPgAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwV"
        "S29yYWlsSHR0cFJlcGxheUVycm9ylJOULg=="
    ),
    "KorailHttpReplayInvalidCapture": (
        "gASVRwAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwe"
        "S29yYWlsSHR0cFJlcGxheUludmFsaWRDYXB0dXJllJOULg=="
    ),
    "HttpReplayRequest": (
        "gASVOgAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwR"
        "SHR0cFJlcGxheVJlcXVlc3SUk5Qu"
    ),
    "KorailHttpReplayPlan": (
        "gASVPQAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwU"
        "S29yYWlsSHR0cFJlcGxheVBsYW6Uk5Qu"
    ),
    "KorailHttpReplayClient": (
        "gASVPwAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwW"
        "S29yYWlsSHR0cFJlcGxheUNsaWVudJSTlC4="
    ),
    "build_korail_http_replay_plan": (
        "gASVRgAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwd"
        "YnVpbGRfa29yYWlsX2h0dHBfcmVwbGF5X3BsYW6Uk5Qu"
    ),
    "_ParsedPage": (
        "gASVNAAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwLX1BhcnNlZFBhZ2WUk5Qu"
    ),
    "_parse_page": (
        "gASVNAAAAAAAAACMIHJhaWxfd2FpdGxpc3Qua29yYWlsX2h0dHBfcmVwbGF5lIwLX3BhcnNlX3BhZ2WUk5Qu"
    ),
}


def test_legacy_http_replay_is_an_assignment_only_exact_facade() -> None:
    path = SOURCE_ROOT / "korail_http_replay.py"
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
    assert imports == {("korail_sidecar", 1, "http_replay", "_owner")}
    assert len(PUBLIC_SYMBOLS) == 60
    assert len(PRIVATE_SYMBOLS) == 34
    assert set(assignments) == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy) if not name.startswith("_")} == PUBLIC_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy, symbol) is getattr(owner, symbol)


def test_http_replay_definitions_have_one_canonical_owner() -> None:
    path = SOURCE_ROOT / "korail_sidecar" / "http_replay.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert definitions
    for symbol in definitions:
        value = getattr(owner, symbol)
        assert value.__module__ == OWNER_MODULE
        assert getattr(legacy, symbol) is value


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_http_replay_pickles_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize(
    "first_import",
    ["canonical", "legacy", "browser", "search_actor", "manager"],
)
def test_http_replay_import_orders_keep_one_owner_and_safe_logging(first_import: str) -> None:
    script = r"""
import importlib
import json
import logging
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.http_replay",
    "legacy": "rail_waitlist.korail_http_replay",
    "browser": "rail_waitlist.korail_pydoll_browser",
    "search_actor": "rail_waitlist.korail_pydoll_search_actor",
    "manager": "rail_waitlist.korail_sidecar.pydoll.http_replay",
}
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.INFO)
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_http_replay" in sys.modules

from rail_waitlist import korail_http_replay as legacy
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_search_actor as search_actor
from rail_waitlist.korail_sidecar import http_replay as owner
from rail_waitlist.korail_sidecar.pydoll import http_replay as manager

print(json.dumps({
    "client_identity": (
        browser.KorailHttpReplayClient
        is legacy.KorailHttpReplayClient
        is owner.KorailHttpReplayClient
    ),
    "httpcore_level": logging.getLogger("httpcore").level,
    "httpx_level": logging.getLogger("httpx").level,
    "legacy_loaded_before": legacy_loaded_before,
    "module": owner.KorailHttpReplayClient.__module__,
    "plan_identity": (
        manager.KorailHttpReplayPlan
        is search_actor.KorailHttpReplayPlan
        is browser.KorailHttpReplayPlan
        is legacy.KorailHttpReplayPlan
        is owner.KorailHttpReplayPlan
    ),
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
        "client_identity": True,
        "httpcore_level": logging.WARNING,
        "httpx_level": logging.WARNING,
        "legacy_loaded_before": first_import == "legacy",
        "module": OWNER_MODULE,
        "plan_identity": True,
    }


def _resolve_import_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = ["rail_waitlist", *path.relative_to(SOURCE_ROOT).parent.parts]
    parent = package[: len(package) - node.level + 1]
    return ".".join([*parent, *(node.module or "").split(".")])


def test_http_replay_has_exact_canonical_consumers_and_no_legacy_reverse_import() -> None:
    owner_path = SOURCE_ROOT / "korail_sidecar" / "http_replay.py"
    owner_tree = ast.parse(
        owner_path.read_text(encoding="utf-8"),
        filename=str(owner_path),
    )
    owner_imports = {
        _resolve_import_module(owner_path, node)
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.ImportFrom)
    }
    canonical_consumers: set[str] = set()

    for path in SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and _resolve_import_module(path, node) == OWNER_MODULE
            ):
                canonical_consumers.add(relative_path)

    assert "rail_waitlist.korail_http_replay" not in owner_imports
    assert canonical_consumers == {
        "korail_pydoll_browser.py",
        "korail_sidecar/pydoll/search_actor.py",
        "korail_sidecar/pydoll/http_replay.py",
    }
