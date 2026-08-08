from __future__ import annotations

import ast
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import rail_waitlist.korail_pydoll_browser as browser_module
import rail_waitlist.korail_pydoll_reservation_actor as reservation_actor_module
import rail_waitlist.korail_pydoll_reservation_contracts as reservation_contracts_module
import rail_waitlist.korail_pydoll_reservation_driver as legacy_reservation_driver_module
import rail_waitlist.korail_sidecar.pydoll.reservation_driver as reservation_driver_module
from rail_waitlist.korail_pydoll_auth_contracts import KorailCredentialInput
from rail_waitlist.korail_pydoll_browser import _PydollSession


class _ClickControl:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self.clicks = 0

    @property
    def text(self):  # type: ignore[no-untyped-def]
        async def read() -> str:
            return self._text

        return read()

    async def click(self) -> None:
        self.clicks += 1


def _request() -> reservation_contracts_module.KorailReservationRequest:
    return reservation_contracts_module.KorailReservationRequest(
        origin="대전",
        destination="서울",
        travel_date=date(2026, 8, 2),
        train_number="118",
        train_type="KTX",
        departure_time=time(6, 35),
        arrival_time=time(7, 49),
        seat_class=reservation_contracts_module.KorailReservationSeatClass.SPECIAL,
        credential=KorailCredentialInput(
            login_id="fixture-login",
            password="fixture-password",
            version="credential-v1",
        ),
    )


@pytest.mark.asyncio
async def test_reservation_driver_resolves_session_monkeypatch_seams_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    row = object()
    seat = _ClickControl()
    reservation = _ClickControl("예매")

    async def visible_elements(selector: str, *, scope: object = None) -> list[object]:
        del scope
        if selector == "li.tckList":
            return [row]
        if selector == "button.reservbtn":
            return [reservation]
        return []

    row_matches = AsyncMock(return_value=True)
    actionable_controls = AsyncMock(return_value=[seat])
    terminal_probe = AsyncMock(
        side_effect=[
            None,
            reservation_contracts_module.KorailReservationResult(
                reservation_contracts_module.KorailReservationOutcome.PAYMENT_REQUIRED,
                "reservation_pending_payment",
            ),
        ]
    )
    monkeypatch.setattr(session, "_visible_elements", visible_elements)
    monkeypatch.setattr(session, "_row_matches_reservation", row_matches)
    monkeypatch.setattr(session, "_actionable_seat_controls", actionable_controls)
    monkeypatch.setattr(session, "_probe_reservation_terminal", terminal_probe)
    monkeypatch.setattr(
        session,
        "_read_control_state",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True,
                aria_disabled="false",
                disabled_attribute=False,
                read_error=False,
            )
        ),
    )

    result = await session.reserve_once(_request())

    assert result.outcome is reservation_contracts_module.KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert seat.clicks == 1
    assert reservation.clicks == 1
    row_matches.assert_awaited_once()
    actionable_controls.assert_awaited_once_with(
        row,
        reservation_contracts_module.KorailReservationSeatClass.SPECIAL.label,
    )
    assert terminal_probe.await_count == 2


def test_reservation_contract_identity_remains_compatible_across_facades() -> None:
    names = (
        "KorailReservationOutcome",
        "KorailReservationRequest",
        "KorailReservationResult",
        "KorailReservationSeatClass",
    )
    for name in names:
        contract = getattr(reservation_contracts_module, name)
        assert getattr(browser_module, name) is contract
        assert getattr(reservation_actor_module, name) is contract
    assert (
        browser_module._ReservationAttemptState is reservation_driver_module.ReservationAttemptState
    )
    assert (
        legacy_reservation_driver_module.ReservationAttemptState
        is reservation_driver_module.ReservationAttemptState
    )


def test_reservation_driver_keeps_css_metadata_bounded_like_browser_controls() -> None:
    tokens = reservation_driver_module._sanitized_class_tokens(
        "one two three four five six seven " + ("x" * 41) + " ignored-after-eight"
    )

    assert tokens == ("one", "two", "three", "four", "five", "six", "seven")


def test_reservation_driver_has_no_browser_or_actor_dependencies() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rail_waitlist"
        / "korail_sidecar"
        / "pydoll"
        / "reservation_driver.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules.isdisjoint(
        {
            "korail_pydoll_auth_actor",
            "korail_pydoll_browser",
            "korail_pydoll_confirmation_reader",
            "korail_pydoll_http_replay",
            "korail_pydoll_login_driver",
            "korail_pydoll_page_safety",
            "korail_pydoll_reservation_actor",
            "korail_pydoll_search_actor",
        }
    )
