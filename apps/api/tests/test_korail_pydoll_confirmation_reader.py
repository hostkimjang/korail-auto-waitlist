from __future__ import annotations

import ast
import base64
import json
import pickle
import subprocess
import sys
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.korail_pydoll_browser as browser_module
from rail_waitlist import korail_pydoll_confirmation_reader as legacy_reader
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.korail_pydoll_browser import (
    PydollKorailBrowserClient,
    PydollPageSnapshot,
)
from rail_waitlist.korail_reservation_confirmation import KorailSameSessionDetailEvidence
from rail_waitlist.korail_sidecar.pydoll import confirmation_reader as owner
from rail_waitlist.korail_sidecar.pydoll.confirmation_reader import (
    KorailConfirmationSession,
    read_korail_same_session_confirmation,
)
from rail_waitlist.reservation_confirmation import ReservationConfirmationTarget

KOREA = ZoneInfo("Asia/Seoul")
API_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = API_ROOT / "src" / "rail_waitlist"
PUBLIC_SYMBOLS = {
    "Callable",
    "KORAIL_CONFIRMATION_SOURCE",
    "KORAIL_RESERVATION_LIST_SOURCE",
    "KorailConfirmationSession",
    "KorailConfirmationSnapshot",
    "KorailReservationListSession",
    "KorailSameSessionDetailEvidence",
    "PaymentDeadlineParser",
    "Protocol",
    "ReservationConfirmationTarget",
    "UTC",
    "ZoneInfo",
    "annotations",
    "date",
    "datetime",
    "is_rate_limit_response",
    "protection_trigger_from_http_response",
    "protection_trigger_from_text",
    "re",
    "read_korail_same_session_confirmation",
    "runtime_checkable",
    "urlsplit",
}
PRIVATE_SYMBOLS = {
    "_auth_required_evidence",
    "_blocked_evidence",
    "_confirmation_evidence_from_text",
    "_confirmation_snapshot_is_blocked",
    "_has_exact_route_markers",
    "_has_exact_text_marker",
    "_has_exact_train_number_marker",
    "_inconclusive_evidence",
    "_is_complete_detail_evidence",
    "_normalize_station",
    "_parse_korail_payment_deadline",
    "_reservation_date_markers",
    "_session_is_authenticated",
}
OWNER_DEFINITIONS = {
    "KorailConfirmationSession",
    "KorailConfirmationSnapshot",
    "KorailReservationListSession",
    "_auth_required_evidence",
    "_blocked_evidence",
    "_confirmation_evidence_from_text",
    "_confirmation_snapshot_is_blocked",
    "_has_exact_route_markers",
    "_has_exact_text_marker",
    "_has_exact_train_number_marker",
    "_inconclusive_evidence",
    "_is_complete_detail_evidence",
    "_normalize_station",
    "_parse_korail_payment_deadline",
    "_reservation_date_markers",
    "_session_is_authenticated",
    "read_korail_same_session_confirmation",
}
LEGACY_PICKLES = {
    "KorailConfirmationSession": (
        "gASVUQAAAAAAAACML3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9jb25maXJtYXRpb25f"
        "cmVhZGVylIwZS29yYWlsQ29uZmlybWF0aW9uU2Vzc2lvbpSTlC4="
    ),
    "read_korail_same_session_confirmation": (
        "gASVXQAAAAAAAACML3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9jb25maXJtYXRpb25f"
        "cmVhZGVylIwlcmVhZF9rb3JhaWxfc2FtZV9zZXNzaW9uX2NvbmZpcm1hdGlvbpSTlC4="
    ),
    "_parse_korail_payment_deadline": (
        "gASVVgAAAAAAAACML3JhaWxfd2FpdGxpc3Qua29yYWlsX3B5ZG9sbF9jb25maXJtYXRpb25f"
        "cmVhZGVylIweX3BhcnNlX2tvcmFpbF9wYXltZW50X2RlYWRsaW5llJOULg=="
    ),
}


def _target() -> ReservationConfirmationTarget:
    return ReservationConfirmationTarget(
        attempt_id="attempt-fixture",
        candidate_id="candidate-fixture",
        provider=Provider.KORAIL,
        train_number="43",
        origin="서울",
        destination="부산",
        departure_at=datetime(2026, 8, 3, 15, 45, tzinfo=KOREA),
        arrival_at=datetime(2026, 8, 3, 18, 12, tzinfo=KOREA),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        credential_version=7,
    )


def _exact_detail_snapshot() -> PydollPageSnapshot:
    return PydollPageSnapshot(
        body_text=(
            "예약 상세 서울역 → 부산역 2026-08-03 KTX 0043 15:45 일반실 "
            "18:12 총 1명 예약취소 장바구니 결제하기"
        ),
        rows=(),
        url="https://www.korail.com/ticket/reservation/detail",
    )


@dataclass
class _ConfirmationSession:
    snapshot: PydollPageSnapshot

    async def _snapshot(self) -> PydollPageSnapshot:
        return self.snapshot

    async def _probe_official_authenticated_session(self) -> bool:
        return False

    async def _has_authenticated_header(self) -> bool:
        return False


class _SessionContext(AbstractAsyncContextManager[_ConfirmationSession]):
    def __init__(self, session: _ConfirmationSession) -> None:
        self.session = session

    async def __aenter__(self) -> _ConfirmationSession:
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_confirmation_reader_uses_only_the_narrow_snapshot_protocol() -> None:
    session: KorailConfirmationSession = _ConfirmationSession(_exact_detail_snapshot())

    evidence = await read_korail_same_session_confirmation(
        session=session,
        target=_target(),
        credential_version=7,
    )

    assert evidence.exact_identity_matched is True
    assert evidence.payment_pending_markers_present is True


def test_legacy_confirmation_reader_is_an_assignment_only_exact_facade() -> None:
    module_path = SOURCE_ROOT / "korail_pydoll_confirmation_reader.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
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
    assert imports == {("korail_sidecar.pydoll", 1, "confirmation_reader", "_owner")}
    assert set(assignments) == PUBLIC_SYMBOLS | PRIVATE_SYMBOLS
    assert {name for name in vars(legacy_reader) if not name.startswith("_")} == PUBLIC_SYMBOLS
    for symbol, value in assignments.items():
        assert isinstance(value, ast.Attribute)
        assert isinstance(value.value, ast.Name)
        assert value.value.id == "_owner"
        assert value.attr == symbol
        assert getattr(legacy_reader, symbol) is getattr(owner, symbol)


def test_confirmation_reader_owner_does_not_reverse_depend_on_browser_facade() -> None:
    module_path = SOURCE_ROOT / "korail_sidecar" / "pydoll" / "confirmation_reader.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "korail_pydoll_browser" not in imported_modules


def test_confirmation_reader_definitions_have_one_canonical_owner() -> None:
    for symbol in OWNER_DEFINITIONS:
        value = getattr(owner, symbol)
        assert value.__module__ == owner.__name__
        assert getattr(legacy_reader, symbol) is value

    assert browser_module.read_korail_same_session_confirmation is (
        owner.read_korail_same_session_confirmation
    )
    assert browser_module._parse_korail_payment_deadline is owner._parse_korail_payment_deadline


@pytest.mark.parametrize(("symbol", "payload"), LEGACY_PICKLES.items())
def test_pre_move_pickle_globals_restore_to_the_canonical_owner(
    symbol: str,
    payload: str,
) -> None:
    assert pickle.loads(base64.b64decode(payload)) is getattr(owner, symbol)


@pytest.mark.parametrize("first_import", ["canonical", "legacy", "browser"])
def test_confirmation_reader_import_orders_keep_one_owner(first_import: str) -> None:
    script = r"""
import importlib
import json
import sys

modules = {
    "canonical": "rail_waitlist.korail_sidecar.pydoll.confirmation_reader",
    "legacy": "rail_waitlist.korail_pydoll_confirmation_reader",
    "browser": "rail_waitlist.korail_pydoll_browser",
}
importlib.import_module(modules[sys.argv[1]])
legacy_loaded_before = "rail_waitlist.korail_pydoll_confirmation_reader" in sys.modules
from rail_waitlist import korail_pydoll_browser as browser
from rail_waitlist import korail_pydoll_confirmation_reader as legacy
from rail_waitlist.korail_sidecar.pydoll import confirmation_reader as owner

print(json.dumps({
    "identity": (
        browser.read_korail_same_session_confirmation
        is legacy.read_korail_same_session_confirmation
        is owner.read_korail_same_session_confirmation
    ),
    "legacy_loaded_before": legacy_loaded_before,
    "module": owner.read_korail_same_session_confirmation.__module__,
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
        "module": "rail_waitlist.korail_sidecar.pydoll.confirmation_reader",
    }


def test_browser_is_the_only_direct_canonical_confirmation_reader_consumer() -> None:
    canonical_consumers: set[str] = set()

    for module_path in SOURCE_ROOT.rglob("*.py"):
        relative_path = module_path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "korail_sidecar.pydoll.confirmation_reader":
                canonical_consumers.add(relative_path)

    assert canonical_consumers == {"korail_pydoll_browser.py"}


@pytest.mark.asyncio
async def test_browser_confirmation_facade_delegates_with_active_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ConfirmationSession(_exact_detail_snapshot())
    client = PydollKorailBrowserClient(
        session_factory=lambda *_: _SessionContext(session),
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )
    client._active_session = browser_module._ActivePydollSession(
        context=_SessionContext(session),
        session=session,
        created_at=0,
        last_used_at=0,
        authenticated_credential_version="7",
    )
    expected = KorailSameSessionDetailEvidence(
        observed_at=datetime(2026, 8, 3, tzinfo=UTC),
        credential_version=7,
        exact_identity_matched=False,
        payment_pending_markers_present=False,
    )
    reader = AsyncMock(return_value=expected)
    monkeypatch.setattr(browser_module, "read_korail_same_session_confirmation", reader)

    assert await client.read_reservation_detail(_target()) is expected
    reader.assert_awaited_once_with(
        session=session,
        target=_target(),
        credential_version=7,
        payment_deadline_parser=browser_module._parse_korail_payment_deadline,
    )
    assert not client._session_lock.locked()
