from __future__ import annotations

import ast
from datetime import UTC, date, datetime, time
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


@pytest.mark.asyncio
async def test_click_attempt_captures_empty_baseline_before_seat_and_exact_state_after_dispatch(
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
    exact_seat = reservation_contracts_module.KorailReservedSeat(
        car_number="4",
        seat_number="2C",
    )

    async def visible_elements(selector: str, *, scope: object = None) -> list[object]:
        del scope
        if selector == "li.tckList":
            return [row]
        if selector == "button.reservbtn":
            return [reservation]
        return []

    read_order: list[tuple[int, int]] = []

    async def read_reserved_state(_request_value):  # type: ignore[no-untyped-def]
        read_order.append((seat.clicks, reservation.clicks))
        return () if len(read_order) == 1 else (exact_seat,)

    monkeypatch.setattr(session, "_visible_elements", visible_elements)
    monkeypatch.setattr(session, "_row_matches_reservation", AsyncMock(return_value=True))
    monkeypatch.setattr(session, "_actionable_seat_controls", AsyncMock(return_value=[seat]))
    monkeypatch.setattr(
        session,
        "_probe_reservation_terminal",
        AsyncMock(
            side_effect=[
                None,
                reservation_contracts_module.KorailReservationResult(
                    reservation_contracts_module.KorailReservationOutcome.FAILED,
                    "reservation_result_unknown",
                ),
            ]
        ),
    )
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
    monkeypatch.setattr(
        session._reservation_driver,
        "_read_reserved_seats_from_preserved_state",
        read_reserved_state,
    )

    result = await session.reserve_once(_request())

    assert result.outcome is reservation_contracts_module.KorailReservationOutcome.FAILED
    assert result.reservation_clicked is True
    assert result.confirmation_correlation_seats == (exact_seat,)
    assert read_order == [(0, 0), (1, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline_kind", ["unavailable", "unchanged"])
async def test_correlation_requires_a_trusted_changed_pre_click_baseline(
    monkeypatch: pytest.MonkeyPatch,
    baseline_kind: str,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    exact_seat = reservation_contracts_module.KorailReservedSeat(
        car_number="4",
        seat_number="2C",
    )
    session._reservation_driver._confirmation_correlation_baseline = (
        None if baseline_kind == "unavailable" else (exact_seat,)
    )
    monkeypatch.setattr(
        session._reservation_driver,
        "_read_reserved_seats_from_preserved_state",
        AsyncMock(return_value=(exact_seat,)),
    )

    correlation = await session._reservation_driver.confirmation_correlation_seats_from_fresh_state(
        _request()
    )

    assert correlation == ()


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


@pytest.mark.parametrize("aria_disabled", ["true", "mixed", "other", "read_error"])
def test_dialog_action_rejects_disabled_or_unknown_aria_state(aria_disabled: str) -> None:
    state = SimpleNamespace(
        enabled=True,
        aria_disabled=aria_disabled,
        disabled_attribute=False,
        read_error=False,
    )

    assert not reservation_driver_module._control_state_allows_dialog_action(state)


@pytest.mark.parametrize("aria_disabled", ["", "false"])
def test_dialog_action_accepts_only_known_enabled_aria_state(aria_disabled: str) -> None:
    state = SimpleNamespace(
        enabled=True,
        aria_disabled=aria_disabled,
        disabled_attribute=False,
        read_error=False,
    )

    assert reservation_driver_module._control_state_allows_dialog_action(state)


@pytest.mark.parametrize(
    "body",
    (
        "좌석 정보 없음",
        "4호차 8A 5호차 9B",
        "4호차 좌석 미정",
        "안내번호 4호차8A",
    ),
)
def test_payment_detail_seat_parser_fails_closed_for_uncertain_text(body: str) -> None:
    assert reservation_driver_module._single_reserved_seat(body) == ()


def test_payment_detail_seat_parser_accepts_one_explicit_car_and_seat() -> None:
    seats = reservation_driver_module._single_reserved_seat("배정 좌석 4호차 8a 결제하기")

    assert [(seat.car_number, seat.seat_number) for seat in seats] == [("4", "8A")]


def _official_reservation_history_state() -> dict[str, object]:
    return {
        "journeyCount": 1,
        "trainNumber": "00118",
        "departureDate": "20260802",
        "departureTime": "063500",
        "arrivalTime": "074900",
        "origin": "대전역",
        "destination": "서울역",
        "seats": [
            {
                "seatClass": "특실",
                "carNumber": "0004",
                "seatNumber": "002c",
            }
        ],
    }


def test_payment_detail_reads_exact_official_history_seat_fields() -> None:
    seats = reservation_driver_module._reserved_seats_from_history_state(
        _official_reservation_history_state(),
        _request(),
    )

    assert [(seat.car_number, seat.seat_number) for seat in seats] == [("4", "2C")]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("trainNumber", "119"),
        ("departureDate", "20260803"),
        ("departureTime", "064000"),
        ("arrivalTime", "075000"),
        ("origin", "서울"),
        ("destination", "대전"),
        ("seats", []),
        (
            "seats",
            [{"seatClass": "일반실", "carNumber": "0004", "seatNumber": "002C"}],
        ),
        (
            "seats",
            [{"seatClass": "특실", "carNumber": "0004", "seatNumber": "입석"}],
        ),
        (
            "seats",
            [
                {"seatClass": "특실", "carNumber": "0004", "seatNumber": "002C"},
                {"seatClass": "특실", "carNumber": "0004", "seatNumber": "003A"},
            ],
        ),
        ("journeyCount", 0),
        ("journeyCount", 2),
        ("journeyCount", True),
        ("trainNumber", 118),
        ("departureDate", 20260802),
        ("origin", ["대전역"]),
        (
            "seats",
            [{"seatClass": "특실", "carNumber": 4, "seatNumber": "002C"}],
        ),
        (
            "seats",
            [{"seatClass": "특실", "carNumber": "0004", "seatNumber": 2}],
        ),
        ("seats", [["특실", "0004", "002C"]]),
    ),
)
def test_payment_detail_history_seat_fields_fail_closed_on_target_mismatch(
    field: str,
    value: object,
) -> None:
    state = _official_reservation_history_state()
    state[field] = value

    assert reservation_driver_module._reserved_seats_from_history_state(state, _request()) == ()


@pytest.mark.asyncio
async def test_payment_detail_reads_seat_from_preserved_history_state_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession(
        "https://www.korail.com/ticket/search/general",
        1_000,
        True,
    )
    execute_script = AsyncMock(
        return_value={
            "result": {
                "result": {
                    "value": _official_reservation_history_state(),
                }
            }
        }
    )
    monkeypatch.setattr(session._reservation_driver, "_execute_script", execute_script)

    seats = await session._reservation_driver.reserved_seats_from_preserved_state(_request())

    assert [(seat.car_number, seat.seat_number) for seat in seats] == [("4", "2C")]
    execute_script.assert_awaited_once()
    script = execute_script.await_args_list[0].args[0]
    assert isinstance(script, str)
    assert "const reservation = state.reservation" in script
    assert "reservation.jrny_infos.jrny_info" in script
    assert "state.reservedTrainList" not in script
    assert "isPlainRecord(reservation)" in script
    assert "isPlainRecord(train)" in script
    assert "isPlainRecord(seat)" in script
    assert "Array.isArray(value)" in script
    assert "journeyInfo.length !== 1" in script
    assert "seatInfo.length !== 1" in script
    assert "typeof train[name] === 'string'" in script
    assert "typeof seat[name] === 'string'" in script


@pytest.mark.asyncio
async def test_payment_terminal_projects_structured_seat_when_body_has_no_seat_text() -> None:
    port = SimpleNamespace(
        _snapshot=AsyncMock(
            return_value=reservation_driver_module.PydollPageSnapshot(
                body_text=(
                    "KTX 118 2026-08-02 대전 06:35 서울 07:49 특실 예약취소 장바구니 결제하기"
                ),
                rows=(),
                url="https://www.korail.com/ticket/reservation/detail",
            )
        )
    )
    execute_script = AsyncMock(
        return_value={
            "result": {
                "result": {
                    "value": _official_reservation_history_state(),
                }
            }
        }
    )
    driver = reservation_driver_module.PydollReservationDomDriver(
        port=port,
        timeout_ms=1_000,
        timeout_seconds=1.0,
        execute_script=execute_script,
        visible_elements=AsyncMock(return_value=[]),
        current_schedule=AsyncMock(return_value=(date(2026, 8, 2), 6)),
        read_control_state=AsyncMock(),
        monotonic=lambda: 0.0,
        sleep=AsyncMock(),
        utc_now=lambda: datetime(2026, 8, 2, tzinfo=UTC),
        event_logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
    )

    result = await driver.probe_reservation_terminal(_request())

    assert result is not None
    assert result.outcome is reservation_contracts_module.KorailReservationOutcome.PAYMENT_REQUIRED
    assert [(seat.car_number, seat.seat_number) for seat in result.reserved_seats] == [("4", "2C")]


@pytest.mark.asyncio
async def test_payment_terminal_does_not_persist_page_wide_seat_text_without_official_state() -> (
    None
):
    port = SimpleNamespace(
        _snapshot=AsyncMock(
            return_value=reservation_driver_module.PydollPageSnapshot(
                body_text=(
                    "KTX 118 2026-08-02 대전 06:35 서울 07:49 특실 "
                    "예약취소 장바구니 결제하기 배정 좌석 4호차 2C"
                ),
                rows=(),
                url="https://www.korail.com/ticket/reservation/detail",
            )
        )
    )
    driver = reservation_driver_module.PydollReservationDomDriver(
        port=port,
        timeout_ms=1_000,
        timeout_seconds=1.0,
        execute_script=AsyncMock(return_value={"result": {"result": {"value": None}}}),
        visible_elements=AsyncMock(return_value=[]),
        current_schedule=AsyncMock(return_value=(date(2026, 8, 2), 6)),
        read_control_state=AsyncMock(),
        monotonic=lambda: 0.0,
        sleep=AsyncMock(),
        utc_now=lambda: datetime(2026, 8, 2, tzinfo=UTC),
        event_logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
    )

    result = await driver.probe_reservation_terminal(_request())

    assert result is not None
    assert result.outcome is reservation_contracts_module.KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.reserved_seats == ()


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
