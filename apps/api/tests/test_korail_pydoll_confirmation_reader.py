from __future__ import annotations

import ast
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import rail_waitlist.korail_pydoll_browser as browser_module
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.korail_pydoll_browser import (
    PydollKorailBrowserClient,
    PydollPageSnapshot,
)
from rail_waitlist.korail_pydoll_confirmation_reader import (
    KorailConfirmationSession,
    read_korail_same_session_confirmation,
)
from rail_waitlist.korail_reservation_confirmation import KorailSameSessionDetailEvidence
from rail_waitlist.reservation_confirmation import ReservationConfirmationTarget

KOREA = ZoneInfo("Asia/Seoul")


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


def test_confirmation_reader_does_not_reverse_depend_on_browser_facade() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_pydoll_confirmation_reader.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "korail_pydoll_browser" not in imported_modules


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
