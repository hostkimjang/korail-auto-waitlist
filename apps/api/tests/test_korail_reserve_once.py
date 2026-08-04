from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import rail_waitlist.korail_pydoll_browser as pydoll_module
from rail_waitlist.korail_browser_adapter_service import create_adapter_app
from rail_waitlist.korail_browser_automation import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSeatSearchRequest,
    BrowserSeatSearchResult,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    KorailLoginMethod,
    KorailReservationOutcome,
    KorailReservationRequest,
    KorailReservationResult,
    KorailReservationSeatClass,
    KorailSessionActorSnapshot,
    KorailSessionActorState,
    PydollKorailBrowserClient,
    PydollPageSnapshot,
    PydollSeatBox,
    PydollTrainRow,
    _PydollSession,
    _ReservationAttemptState,
)
from rail_waitlist.korail_search_bootstrap import KorailStationIdentity


class FakeElement:
    def __init__(
        self,
        text: str,
        *,
        children: dict[str, FakeElement] | None = None,
        price_box_text: str = "",
        price_box_classes: tuple[str, ...] = (),
    ) -> None:
        self._text = text
        self.children = children or {}
        self.price_box_text = price_box_text
        self.price_box_classes = price_box_classes
        self.clicks = 0
        self.typed_values: list[str] = []

    @property
    def text(self):  # type: ignore[no-untyped-def]
        async def read() -> str:
            return self._text

        return read()

    async def query(
        self,
        selector: str,
        *,
        find_all: bool = False,
        raise_exc: bool = True,
    ) -> object:
        if find_all:
            return []
        value = self.children.get(selector)
        if value is None and raise_exc:
            raise LookupError(selector)
        return value

    async def click(self) -> None:
        self.clicks += 1

    async def clear(self) -> None:
        self.typed_values.clear()

    async def type_text(self, value: str) -> None:
        self.typed_values.append(value)

    async def is_visible(self) -> bool:
        return True

    async def execute_script(self, script: str, *, return_by_value: bool) -> dict[str, object]:
        return {
            "result": {
                "result": {
                    "value": {
                        "ariaDisabled": "false",
                        "disabledAttribute": False,
                        "className": "",
                        "containerClassName": "",
                        "slideClassName": "",
                        "text": self.price_box_text,
                        "classes": list(self.price_box_classes),
                    }
                }
            }
        }


def reservation_request(
    version: str = "credential-v1",
    login_method: KorailLoginMethod = KorailLoginMethod.MEMBERSHIP_NUMBER,
) -> KorailReservationRequest:
    return KorailReservationRequest(
        origin="대전",
        destination="서울",
        travel_date=date(2026, 8, 2),
        train_number="118",
        train_type="KTX",
        departure_time=time(6, 35),
        arrival_time=time(7, 49),
        seat_class=KorailReservationSeatClass.SPECIAL,
        credential=KorailCredentialInput(
            login_id="fixture-login",
            password="fixture-password",
            version=version,
            login_method=login_method,
        ),
    )


def seat_search_request() -> BrowserSeatSearchRequest:
    return BrowserSeatSearchRequest(
        origin="대전",
        destination="서울",
        travel_date=date(2026, 8, 2),
        departure_from=time(6),
        departure_to=time(8),
        passenger_count=1,
    )


def reservation_snapshot(*rows: PydollTrainRow) -> PydollPageSnapshot:
    return PydollPageSnapshot(body_text="KORAIL 열차 조회 결과", rows=rows)


def exact_reservation_row() -> PydollTrainRow:
    return PydollTrainRow(
        kind_text="KTX 118",
        train_number="118",
        route_text="대전 → 서울(06:35 ~ 07:49) 소요시간: 1시간 14분",
        seats=(),
    )


def test_initial_snapshot_fast_path_requires_one_exact_reservation_target() -> None:
    request = reservation_request()

    assert pydoll_module._snapshot_has_unique_reservation_target(
        reservation_snapshot(exact_reservation_row()), request
    )
    assert not pydoll_module._snapshot_has_unique_reservation_target(
        reservation_snapshot(exact_reservation_row(), exact_reservation_row()), request
    )
    assert not pydoll_module._snapshot_has_unique_reservation_target(
        reservation_snapshot(
            replace(
                exact_reservation_row(),
                route_text="대전 → 서울(06:45 ~ 07:59) 소요시간: 1시간 14분",
            )
        ),
        request,
    )


def booking_row() -> FakeElement:
    return FakeElement(
        "KTX 118 대전 서울 06:35 07:49",
        children={
            ".tck_inner .tit_box": FakeElement("KTX 118"),
            ".tck_inner .tit_box .num": FakeElement("118"),
            ".tck_inner .data_box.right": FakeElement(
                "대전 → 서울(06:35 ~ 07:49) 소요시간: 1시간 14분"
            ),
        },
    )


@pytest.mark.asyncio
async def test_session_clicks_exact_seat_and_reservation_once_then_stops_at_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")
    forbidden = [FakeElement("결제하기"), FakeElement("장바구니"), FakeElement("예약취소")]

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        if selector == "[role='dialog'], dialog[open], [aria-modal='true']":
            return []
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot("로그인", (), url="https://www.korail.com/ticket/login"),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))
    official_session_probe = AsyncMock(return_value=False)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert seat.clicks == 1
    assert reserve.clicks == 1
    assert [control.clicks for control in forbidden] == [0, 0, 0]
    official_session_probe.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_session_ignores_same_class_unavailable_anchor_when_price_action_is_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    unavailable = FakeElement("특실\n매진")
    actionable = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [unavailable, actionable]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
                "예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.reason == "reservation_pending_payment"
    assert unavailable.clicks == 0
    assert actionable.clicks == 1
    assert reserve.clicks == 1


@pytest.mark.asyncio
async def test_session_clicks_price_only_anchor_owned_by_exact_seat_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement(
        "33,200원",
        price_box_text="특실 33,200원",
        price_box_classes=("price_box", "spe"),
    )
    reserve = FakeElement("예매")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
                "예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.seat_clicked is True
    assert seat.clicks == 1
    assert reserve.clicks == 1


@pytest.mark.asyncio
async def test_session_treats_sold_out_soon_price_control_as_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement(
        "특실 33,200원",
        price_box_text="특실 매진임박 33,200원",
        price_box_classes=("price_box", "spe", "sold_out_soon"),
    )

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)

    controls = await session._actionable_seat_controls(row, "특실")

    assert controls == [seat]


@pytest.mark.asyncio
async def test_session_collapses_equivalent_responsive_seat_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    desktop_action = FakeElement("특실\n33,200원")
    mobile_action = FakeElement("특실 33,200원")
    reserve = FakeElement("예매")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [desktop_action, mobile_action]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
                "예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert desktop_action.clicks == 1
    assert mobile_action.clicks == 0
    assert reserve.clicks == 1


@pytest.mark.asyncio
async def test_session_keeps_differently_priced_seat_actions_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    first_action = FakeElement("특실\n33,200원")
    second_action = FakeElement("특실\n34,100원")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [first_action, second_action]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.UNAVAILABLE
    assert result.reason == "seat_control_not_unique"
    assert first_action.clicks == second_action.clicks == 0


@pytest.mark.asyncio
async def test_session_ignores_transient_login_route_with_official_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")
    retained_login_shell = FakeElement("로그인")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        if selector == "[role='dialog'], dialog[open], [aria-modal='true']":
            return [retained_login_shell] if reserve.clicks == 0 else []
        return []

    snapshots = iter(
        (
            PydollPageSnapshot(
                "열차 목록",
                (),
                url="https://www.korail.com/ticket/login",
            ),
            PydollPageSnapshot(
                "열차 목록",
                (),
                url="https://www.korail.com/ticket/login",
            ),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))
    official_session_probe = AsyncMock(return_value=True)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert result.target_rechecked_at is not None
    assert result.seat_selected_at is not None
    assert result.reservation_requested_at is not None
    assert (
        result.target_rechecked_at
        <= result.seat_selected_at
        <= result.reservation_requested_at
    )
    assert seat.clicks == reserve.clicks == 1
    official_session_probe.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_session_recognizes_exact_payment_required_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
                "예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))

    result = await session.reserve_once(
        replace(reservation_request(), train_number="00118")
    )

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert seat.clicks == reserve.clicks == 1


@pytest.mark.asyncio
async def test_session_logs_in_place_after_seat_then_reserves_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")
    payment = FakeElement("결제하기")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        if selector == "button.payment":
            return [payment]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("로그인", (), url="https://www.korail.com/ticket/login"),
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
                "예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    authenticate_in_place = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_authenticate_in_place", authenticate_in_place)
    preserved_state = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_has_exact_preserved_booking_state", preserved_state)

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert seat.clicks == 1
    assert reserve.clicks == 1
    assert payment.clicks == 0
    authenticate_in_place.assert_awaited_once()
    assert authenticate_in_place.await_args.args[0] == reservation_request().credential
    preserved_state.assert_awaited_once_with(reservation_request())


@pytest.mark.asyncio
async def test_session_logs_in_place_after_reserve_then_only_observes_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")
    payment = FakeElement("결제하기")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        if selector == "button.payment":
            return [payment]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
            PydollPageSnapshot("로그인", (), url="https://www.korail.com/ticket/login"),
            PydollPageSnapshot(
                "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
                "예약취소 장바구니 결제하기",
                (),
                url="https://www.korail.com/ticket/reservation/detail",
            ),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    authenticate_in_place = AsyncMock(return_value=True)
    monkeypatch.setattr(session, "_authenticate_in_place", authenticate_in_place)

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert seat.clicks == 1
    assert reserve.clicks == 1
    assert payment.clicks == 0
    authenticate_in_place.assert_awaited_once()
    assert authenticate_in_place.await_args.args[0] == reservation_request().credential


@pytest.mark.asyncio
async def test_session_failed_in_place_login_returns_auth_required_without_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인",
                (),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    authenticate_in_place = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_authenticate_in_place", authenticate_in_place)

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert result.seat_clicked is True
    assert result.reservation_clicked is False
    assert seat.clicks == 1
    assert reserve.clicks == 0
    authenticate_in_place.assert_awaited_once()
    assert authenticate_in_place.await_args.args[0] == reservation_request().credential


@pytest.mark.asyncio
async def test_session_fails_closed_when_in_place_login_loses_exact_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    snapshots = iter(
        (
            PydollPageSnapshot("로그인", (), url="https://www.korail.com/ticket/login"),
            PydollPageSnapshot("열차 목록", (), url="https://www.korail.com/ticket/search/list"),
        )
    )
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_snapshot", AsyncMock(side_effect=lambda: next(snapshots)))
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    monkeypatch.setattr(session, "_authenticate_in_place", AsyncMock(return_value=True))
    preserved_state = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_has_exact_preserved_booking_state", preserved_state)

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.FAILED
    assert result.reason == "reservation_selection_not_preserved"
    assert result.seat_clicked is True
    assert result.reservation_clicked is False
    assert seat.clicks == 1
    assert reserve.clicks == 0
    preserved_state.assert_awaited_once_with(reservation_request())


@pytest.mark.asyncio
async def test_session_preserves_reservation_click_latch_when_click_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    reserve = FakeElement("예매")

    async def failing_click() -> None:
        reserve.clicks += 1
        raise RuntimeError("opaque")

    reserve.click = failing_click  # type: ignore[method-assign]

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        if selector == "button.reservbtn":
            return [reserve]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "열차 목록", (), url="https://www.korail.com/ticket/search/list"
            )
        ),
    )

    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.FAILED
    assert result.reason.startswith("reservation_result_unknown")
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert seat.clicks == reserve.clicks == 1


@pytest.mark.asyncio
async def test_session_fails_closed_on_ambiguous_exact_train(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    rows = [booking_row(), booking_row()]

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        return rows if selector == "li.tckList" else []

    monkeypatch.setattr(session, "_visible_elements", visible)
    result = await session.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.UNAVAILABLE
    assert result.reason == "target_not_unique"
    assert result.seat_clicked is False
    assert result.reservation_clicked is False


@pytest.mark.asyncio
async def test_exact_match_allows_missing_optional_train_type() -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    request = replace(reservation_request(), train_type=None)

    assert await session._row_matches_reservation(booking_row(), request) is True


@pytest.mark.parametrize(
    ("login_method", "tab_selector", "identity_selector"),
    [
        (
            KorailLoginMethod.MEMBERSHIP_NUMBER,
            "button#memberNo[type='button']",
            "input#id[name='id'][type='text'][title='회원번호'][maxlength='10']",
        ),
        (
            KorailLoginMethod.EMAIL,
            "button#email[type='button']",
            "input#id[name='id'][type='email'][title='이메일 주소']",
        ),
        (
            KorailLoginMethod.PHONE,
            "button#phone[type='button']",
            "input#id[name='id'][type='text'][title='휴대폰 번호'][maxlength='11']",
        ),
    ],
)
@pytest.mark.asyncio
async def test_session_selects_login_method_and_uses_one_scoped_login_button(
    monkeypatch: pytest.MonkeyPatch,
    login_method: KorailLoginMethod,
    tab_selector: str,
    identity_selector: str,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    method_tab = FakeElement(login_method.value)
    login_id = FakeElement("")
    password = FakeElement("")
    active_panel = FakeElement("회원 로그인")
    scoped_login = FakeElement("로그인")
    duplicate_outside_form = FakeElement("로그인")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == tab_selector:
            return [method_tab]
        if selector == ".tabPage.active[role='tabpanel']":
            return [active_panel]
        if selector == identity_selector and scope is active_panel:
            return [login_id]
        if (
            selector == "input#password[name='password'][type='password']"
            and scope is active_panel
        ):
            return [password]
        if selector == "button,[role='button']" and scope is active_panel:
            return [scoped_login]
        if selector == "button,[role='button']":
            return [scoped_login, duplicate_outside_form]
        return []

    session._tab = SimpleNamespace(go_to=AsyncMock())
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_has_exact_visible",
        AsyncMock(side_effect=[False, True, False, False, True]),
    )
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 성공",
                (),
                # The official React route may lag behind the authenticated header.
                url="https://www.korail.com/ticket/login",
            )
        ),
    )
    monkeypatch.setattr(session, "_wait_for_exact_text", AsyncMock())
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )

    authenticated = await session.ensure_authenticated(
        reservation_request(login_method=login_method).credential
    )

    assert authenticated is True
    assert method_tab.clicks == 1
    assert login_id.typed_values == ["fixture-login"]
    assert password.typed_values == ["fixture-password"]
    assert scoped_login.clicks == 1
    assert duplicate_outside_form.clicks == 0


@pytest.mark.asyncio
async def test_login_submit_waits_for_method_tab_to_render_then_clicks_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    method_tab = FakeElement("membership_number")
    login_id = FakeElement("")
    password = FakeElement("")
    submit = FakeElement("로그인")
    tab_results = iter(([], [method_tab]))
    tab_queries = 0

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        nonlocal tab_queries
        del scope
        if selector == KorailLoginMethod.MEMBERSHIP_NUMBER.tab_selector:
            tab_queries += 1
            return next(tab_results)
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    login_controls = AsyncMock(return_value=(login_id, password, submit))
    monkeypatch.setattr(session, "_wait_for_login_controls", login_controls)

    submitted = await session._submit_login_form(reservation_request().credential)

    assert submitted is True
    assert tab_queries == 2
    assert method_tab.clicks == 1
    assert submit.clicks == 1
    assert login_id.typed_values == ["fixture-login"]
    assert password.typed_values == ["fixture-password"]
    login_controls.assert_awaited_once_with(KorailLoginMethod.MEMBERSHIP_NUMBER)


@pytest.mark.asyncio
@pytest.mark.parametrize("tab_count", [0, 2])
async def test_login_submit_fails_closed_when_method_tab_never_becomes_unique(
    monkeypatch: pytest.MonkeyPatch,
    tab_count: int,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 50, True)
    method_tabs = [FakeElement("membership_number") for _ in range(tab_count)]

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        del scope
        if selector == KorailLoginMethod.MEMBERSHIP_NUMBER.tab_selector:
            return method_tabs
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    login_controls = AsyncMock()
    monkeypatch.setattr(session, "_wait_for_login_controls", login_controls)

    submitted = await session._submit_login_form(reservation_request().credential)

    assert submitted is False
    assert all(tab.clicks == 0 for tab in method_tabs)
    login_controls.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_in_place_login_submits_once_without_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    method_tab = FakeElement("membership_number")
    login_id = FakeElement("")
    password = FakeElement("")
    active_panel = FakeElement("회원 로그인")
    submit = FakeElement("로그인")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == KorailLoginMethod.MEMBERSHIP_NUMBER.tab_selector:
            return [method_tab]
        if selector == ".tabPage.active[role='tabpanel']":
            return [active_panel]
        if (
            selector == KorailLoginMethod.MEMBERSHIP_NUMBER.identity_selector
            and scope is active_panel
        ):
            return [login_id]
        if (
            selector == "input#password[name='password'][type='password']"
            and scope is active_panel
        ):
            return [password]
        if selector == "button,[role='button']" and scope is active_panel:
            return [submit]
        return []

    session._tab = SimpleNamespace(go_to=AsyncMock())
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_has_authenticated_header",
        AsyncMock(side_effect=(False, True)),
    )
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리",
                (),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )

    authenticated = await session._authenticate_in_place(reservation_request().credential)

    assert authenticated is True
    assert session._tab.go_to.await_count == 0
    assert method_tab.clicks == 1
    assert login_id.typed_values == ["fixture-login"]
    assert password.typed_values == ["fixture-password"]
    assert submit.clicks == 1


@pytest.mark.asyncio
async def test_session_rejects_transient_logout_that_does_not_persist_on_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    method_tab = FakeElement("phone")
    login_id = FakeElement("")
    password = FakeElement("")
    submit = FakeElement("로그인")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        del scope
        if selector == KorailLoginMethod.PHONE.tab_selector:
            return [method_tab]
        return []

    session._tab = SimpleNamespace(go_to=AsyncMock())
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_wait_for_login_controls",
        AsyncMock(return_value=(login_id, password, submit)),
    )
    header_states = iter((False, True))

    async def has_authenticated_header(*_args: object, **_kwargs: object) -> bool:
        return next(header_states, False)

    monkeypatch.setattr(session, "_has_exact_visible", has_authenticated_header)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리",
                (),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )
    monkeypatch.setattr(session, "_wait_for_exact_text", AsyncMock())
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        AsyncMock(return_value=False),
    )

    authenticated = await session.ensure_authenticated(
        reservation_request(login_method=KorailLoginMethod.PHONE).credential
    )

    assert authenticated is False
    assert method_tab.clicks == 1
    assert submit.clicks == 1
    assert session._tab.go_to.await_count == 2


@pytest.mark.asyncio
async def test_session_accepts_official_session_before_login_header_hydrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    method_tab = FakeElement("phone")
    login_id = FakeElement("")
    password = FakeElement("")
    submit = FakeElement("로그인")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        del scope
        if selector == KorailLoginMethod.PHONE.tab_selector:
            return [method_tab]
        return []

    session._tab = SimpleNamespace(go_to=AsyncMock())
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_wait_for_login_controls",
        AsyncMock(return_value=(login_id, password, submit)),
    )
    monkeypatch.setattr(session, "_has_exact_visible", AsyncMock(return_value=False))
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리",
                (),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )
    monkeypatch.setattr(session, "_wait_for_exact_text", AsyncMock())
    official_session_probe = AsyncMock(return_value=True)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    authenticated = await session.ensure_authenticated(
        reservation_request(login_method=KorailLoginMethod.PHONE).credential
    )

    assert authenticated is True
    assert submit.clicks == 1
    official_session_probe.assert_awaited_once_with()
    assert session._tab.go_to.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", False), (None, False)],
)
async def test_official_session_probe_returns_only_strict_boolean(
    value: object,
    expected: bool,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    execute_script = AsyncMock(
        return_value={"result": {"result": {"value": value}}}
    )
    session._tab = SimpleNamespace(execute_script=execute_script)

    authenticated = await session._probe_official_authenticated_session()

    assert authenticated is expected
    assert execute_script.await_args.kwargs == {
        "return_by_value": True,
        "await_promise": True,
        "timeout": 1_000,
    }


@pytest.mark.asyncio
async def test_preserved_booking_state_returns_only_boolean_and_reidentifies_exact_dom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    row = booking_row()
    seat = FakeElement("특실\n33,200원")
    execute_script = AsyncMock(return_value={"result": {"result": {"value": True}}})
    session._tab = SimpleNamespace(execute_script=execute_script)

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "li.tckList":
            return [row]
        if selector == "a" and scope is row:
            return [seat]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "current_schedule",
        AsyncMock(return_value=(date(2026, 8, 2), 6)),
    )

    assert await session._has_exact_preserved_booking_state(
        replace(reservation_request(), train_number="00118")
    ) is True
    script = execute_script.await_args.args[0]
    assert "redirectUrl" in script
    assert "reservedTrainList" in script
    assert "reserveParams" in script
    assert execute_script.await_args.kwargs == {
        "return_by_value": True,
        "await_promise": False,
        "timeout": 1_000,
    }


@pytest.mark.asyncio
async def test_preserved_booking_state_fails_closed_on_non_boolean_script_result() -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    session._tab = SimpleNamespace(
        execute_script=AsyncMock(return_value={"result": {"result": {"value": "true"}}})
    )

    assert await session._has_exact_preserved_booking_state(reservation_request()) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("network_response", "expected_exception"),
    [
        ((429, "fetch"), BrowserRateLimited),
        ((403, "document"), BrowserProtectionDetected),
    ],
)
async def test_login_wait_classifies_network_protection_before_polling(
    monkeypatch: pytest.MonkeyPatch,
    network_response: tuple[int, str],
    expected_exception: type[Exception],
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리",
                (),
                network_responses=(network_response,),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )

    with pytest.raises(expected_exception):
        await session._wait_for_login_authentication()


@pytest.mark.asyncio
async def test_login_wait_calls_official_session_check_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 350, True)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리", (), url="https://www.korail.com/ticket/login"
            )
        ),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    official_session_probe = AsyncMock(return_value=False)
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    assert await session._wait_for_login_authentication() is False
    official_session_probe.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reservation_uses_distinct_pre_route_and_post_submit_session_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    attempt = _ReservationAttemptState()
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리", (), url="https://www.korail.com/ticket/login"
            )
        ),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    official_session_probe = AsyncMock(side_effect=(False, True))
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    terminal = await session._probe_reservation_terminal(reservation_request(), attempt)
    authenticated = await session._wait_for_login_authentication(attempt)

    assert terminal is not None
    assert terminal.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert authenticated is True
    assert attempt.pre_login_route_check_attempted is True
    assert attempt.pre_login_route_authenticated is False
    assert attempt.post_submit_check_attempted is True
    assert attempt.post_submit_authenticated is True
    assert official_session_probe.await_count == 2


@pytest.mark.asyncio
async def test_post_submit_auth_keeps_observing_login_url_until_exact_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    attempt = _ReservationAttemptState()
    login_snapshot = PydollPageSnapshot(
        "로그인 처리", (), url="https://www.korail.com/ticket/login"
    )
    snapshot = AsyncMock(return_value=login_snapshot)
    monkeypatch.setattr(session, "_snapshot", snapshot)
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[]))
    official_session_probe = AsyncMock(side_effect=(False, True))
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    before_submit = await session._probe_reservation_terminal(reservation_request(), attempt)
    assert before_submit is not None
    assert before_submit.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert await session._wait_for_login_authentication(attempt) is True

    while_login_url = await session._probe_reservation_terminal(reservation_request(), attempt)
    assert while_login_url is None
    assert official_session_probe.await_count == 2

    snapshot.return_value = PydollPageSnapshot(
        "승차권 예약 2026-08-02 KTX 118 06:35 07:49 특실 "
        "예약취소 장바구니 결제하기",
        (),
        url="https://www.korail.com/ticket/reservation/detail",
    )
    detail = await session._probe_reservation_terminal(reservation_request(), attempt)

    assert detail is not None
    assert detail.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert official_session_probe.await_count == 2


@pytest.mark.asyncio
async def test_post_submit_auth_failure_keeps_login_route_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 350, True)
    attempt = _ReservationAttemptState()
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인 처리", (), url="https://www.korail.com/ticket/login"
            )
        ),
    )
    monkeypatch.setattr(session, "_has_authenticated_header", AsyncMock(return_value=False))
    monkeypatch.setattr(session, "_visible_elements", AsyncMock(return_value=[]))
    official_session_probe = AsyncMock(side_effect=(False, False))
    monkeypatch.setattr(
        session,
        "_probe_official_authenticated_session",
        official_session_probe,
    )

    before_submit = await session._probe_reservation_terminal(reservation_request(), attempt)
    assert before_submit is not None
    assert before_submit.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert await session._wait_for_login_authentication(attempt) is False

    after_submit = await session._probe_reservation_terminal(reservation_request(), attempt)

    assert after_submit is not None
    assert after_submit.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert official_session_probe.await_count == 2


@pytest.mark.parametrize(
    ("login_method", "tab_selector", "mismatched_identity_selector"),
    [
        (
            KorailLoginMethod.MEMBERSHIP_NUMBER,
            "button#memberNo[type='button']",
            "input#id[name='id'][type='text'][title='휴대폰 번호'][maxlength='11']",
        ),
        (
            KorailLoginMethod.EMAIL,
            "button#email[type='button']",
            "input#id[name='id'][type='text'][title='회원번호'][maxlength='10']",
        ),
        (
            KorailLoginMethod.PHONE,
            "button#phone[type='button']",
            "input#id[name='id'][type='email'][title='이메일 주소']",
        ),
    ],
)
@pytest.mark.asyncio
async def test_session_fails_closed_when_selected_login_form_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
    login_method: KorailLoginMethod,
    tab_selector: str,
    mismatched_identity_selector: str,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    method_tab = FakeElement(login_method.value)
    mismatched_id = FakeElement("")
    password = FakeElement("")
    active_panel = FakeElement("잘못된 로그인 폼")
    scoped_login = FakeElement("로그인")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == tab_selector:
            return [method_tab]
        if selector == ".tabPage.active[role='tabpanel']":
            return [active_panel]
        if selector == mismatched_identity_selector and scope is active_panel:
            return [mismatched_id]
        if (
            selector == "input#password[name='password'][type='password']"
            and scope is active_panel
        ):
            return [password]
        if selector == "button,[role='button']" and scope is active_panel:
            return [scoped_login]
        return []

    session._tab = SimpleNamespace(go_to=AsyncMock())
    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(session, "_has_exact_visible", AsyncMock(return_value=False))

    authenticated = await session.ensure_authenticated(
        reservation_request(login_method=login_method).credential
    )

    assert authenticated is False
    assert method_tab.clicks == 1
    assert mismatched_id.typed_values == []
    assert password.typed_values == []
    assert scoped_login.clicks == 0


@pytest.mark.asyncio
async def test_session_maps_login_browser_errors_to_a_safe_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    session._tab = SimpleNamespace(go_to=AsyncMock(side_effect=RuntimeError("opaque")))
    monkeypatch.setattr(session, "_has_exact_visible", AsyncMock(return_value=False))

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await session.ensure_authenticated(reservation_request().credential)

    assert captured.value.stage == "login_page_navigate"


@pytest.mark.asyncio
async def test_visible_elements_accepts_pydoll_async_generator_results() -> None:
    first = FakeElement("첫 번째")
    second = FakeElement("두 번째")

    class AsyncGeneratorRoot:
        async def query(
            self,
            selector: str,
            *,
            find_all: bool,
            raise_exc: bool,
        ) -> object:
            assert (selector, find_all, raise_exc) == ("button", True, False)

            async def generate():  # type: ignore[no-untyped-def]
                yield first
                yield second

            return generate()

    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    session._tab = AsyncGeneratorRoot()

    assert await session._visible_elements("button") == [first, second]


@pytest.mark.asyncio
async def test_has_exact_visible_awaits_live_element_text_without_async_generator() -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    session._visible_elements = AsyncMock(  # type: ignore[method-assign]
        return_value=[FakeElement("로그인"), FakeElement("로그아웃")]
    )

    assert await session._has_exact_visible("a,button", "로그아웃") is True


@pytest.mark.asyncio
async def test_terminal_probe_stops_at_delay_consent_without_clicking_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    dialog = FakeElement("지연승낙 안내 계속 진행하시겠습니까")
    decline = FakeElement("아니오")
    proceed = FakeElement("네")

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "[role='dialog'], dialog[open], [aria-modal='true']":
            return [dialog]
        if selector == "button,a" and scope is dialog:
            return [decline, proceed]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "지연승낙 안내",
                (),
                url="https://www.korail.com/ticket/search/list",
            )
        ),
    )

    result = await session._probe_reservation_terminal(reservation_request())

    assert result is not None
    assert result.outcome is KorailReservationOutcome.CONSENT_REQUIRED
    assert decline.clicks == proceed.clicks == 0


@pytest.mark.asyncio
async def test_terminal_probe_ignores_authenticated_login_shell_after_reservation_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    retained_login_shell = FakeElement("로그인")
    attempt = _ReservationAttemptState(
        login_attempted=True,
        post_submit_check_attempted=True,
        post_submit_authenticated=True,
        reservation_clicked=True,
    )

    async def visible(selector: str, *, scope: object = None) -> list[FakeElement]:
        if selector == "[role='dialog'], dialog[open], [aria-modal='true']":
            return [retained_login_shell]
        return []

    monkeypatch.setattr(session, "_visible_elements", visible)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "로그인",
                (),
                url="https://www.korail.com/ticket/login",
            )
        ),
    )

    terminal = await session._probe_reservation_terminal(reservation_request(), attempt)

    assert terminal is None


@pytest.mark.asyncio
async def test_terminal_probe_maps_protection_to_provider_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PydollSession("https://www.korail.com/ticket/search/general", 1_000, True)
    monkeypatch.setattr(
        session,
        "_snapshot",
        AsyncMock(
            return_value=PydollPageSnapshot(
                "CODE -8003",
                (),
                url="https://www.korail.com/ticket/search/list",
            )
        ),
    )

    result = await session._probe_reservation_terminal(reservation_request())

    assert result is not None
    assert result.outcome is KorailReservationOutcome.PROVIDER_BLOCKED


class ReservationFixtureSession:
    def __init__(self, *, authenticated: bool = True) -> None:
        self.closed = 0
        self.open_calls = 0
        self.auth_calls = 0
        self.choose_station_calls = 0
        self.choose_schedule_calls = 0
        self.submit_calls = 0
        self.navigate_calls = 0
        self.navigate_fresh_calls = 0
        self.reserve_calls = 0
        self.authenticated = authenticated
        self.authenticated_login_methods: list[KorailLoginMethod] = []
        self.stations = {"departure": "", "arrival": ""}
        self.schedule = (date(2026, 8, 2), 6)

    async def open(self) -> PydollPageSnapshot:
        self.open_calls += 1
        return PydollPageSnapshot("열차 조회", ())

    async def navigate(self, url: str) -> PydollPageSnapshot:
        self.navigate_calls += 1
        self.stations = {"departure": "대전", "arrival": "서울"}
        self.schedule = (date(2026, 8, 2), 6)
        return PydollPageSnapshot("열차 조회 결과", ())

    async def navigate_fresh(self, url: str) -> PydollPageSnapshot:
        self.navigate_fresh_calls += 1
        return await self.navigate(url)

    async def choose_station(self, kind: str, station: str) -> None:
        self.choose_station_calls += 1
        self.stations[kind] = station

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        self.choose_schedule_calls += 1
        self.schedule = (travel_date, departure_hour)

    async def current_station(self, kind: str) -> str:
        return self.stations[kind]

    async def current_schedule(self) -> tuple[date, int]:
        return self.schedule

    async def current_passenger(self) -> str:
        return "총 1명"

    async def ensure_authenticated(self, credential: KorailCredentialInput) -> bool:
        self.auth_calls += 1
        self.authenticated_login_methods.append(credential.login_method)
        return self.authenticated

    async def submit_once(self) -> None:
        self.submit_calls += 1

    async def wait_for_result(self) -> PydollPageSnapshot:
        return PydollPageSnapshot("결과", ())

    async def expand_results(
        self, snapshot: PydollPageSnapshot, max_actions: int
    ) -> PydollPageSnapshot:
        return snapshot

    async def reserve_once(self, request: KorailReservationRequest) -> KorailReservationResult:
        self.reserve_calls += 1
        return KorailReservationResult(
            KorailReservationOutcome.PAYMENT_REQUIRED,
            "reservation_pending_payment",
            seat_clicked=True,
            reservation_clicked=True,
        )


class ReservationFixtureContext(AbstractAsyncContextManager[ReservationFixtureSession]):
    def __init__(self, session: ReservationFixtureSession) -> None:
        self.session = session

    async def __aenter__(self) -> ReservationFixtureSession:
        return self.session

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.session.closed += 1


class ReservationFixtureFactory:
    def __init__(self, *, authenticated: bool = True) -> None:
        self.sessions: list[ReservationFixtureSession] = []
        self.authenticated = authenticated

    def __call__(
        self, page_url: str, timeout_ms: int, headless: bool
    ) -> ReservationFixtureContext:
        session = ReservationFixtureSession(authenticated=self.authenticated)
        self.sessions.append(session)
        return ReservationFixtureContext(session)


class SequenceReservationFixtureFactory:
    def __init__(self, *sessions: ReservationFixtureSession) -> None:
        self.sessions = list(sessions)
        self.calls = 0

    def __call__(
        self, page_url: str, timeout_ms: int, headless: bool
    ) -> ReservationFixtureContext:
        session = self.sessions[self.calls]
        self.calls += 1
        return ReservationFixtureContext(session)


class StaticReservationStationResolver:
    async def resolve_pair(self, origin: str, destination: str):
        assert (origin, destination) == ("대전", "서울")
        return KorailStationIdentity("0010", "대전"), KorailStationIdentity("0001", "서울")


class ProtectedDirectReservationSession(ReservationFixtureSession):
    async def navigate(self, url: str) -> PydollPageSnapshot:
        await super().navigate(url)
        return PydollPageSnapshot("CODE -8003", ())


class ProtectedFreshDirectReservationSession(ReservationFixtureSession):
    async def navigate_fresh(self, url: str) -> PydollPageSnapshot:
        await super().navigate_fresh(url)
        return PydollPageSnapshot("CODE -8003", ())


@pytest.mark.asyncio
async def test_reserve_once_direct_bootstrap_skips_picker_and_submit() -> None:
    session = ReservationFixtureSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session),
        station_identity_resolver=StaticReservationStationResolver(),  # type: ignore[arg-type]
    )

    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert session.open_calls == 1
    assert session.auth_calls == 1
    assert session.navigate_calls == 1
    assert session.navigate_fresh_calls == 0
    assert session.choose_station_calls == 0
    assert session.choose_schedule_calls == 0
    assert session.submit_calls == 0
    assert session.reserve_calls == 1


@pytest.mark.asyncio
async def test_reserve_once_direct_protection_does_not_retry_ui_search() -> None:
    session = ProtectedDirectReservationSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session),
        station_identity_resolver=StaticReservationStationResolver(),  # type: ignore[arg-type]
    )

    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PROVIDER_BLOCKED
    assert session.navigate_calls == 1
    assert session.choose_station_calls == 0
    assert session.choose_schedule_calls == 0
    assert session.submit_calls == 0
    assert session.reserve_calls == 0


@pytest.mark.asyncio
async def test_warm_authenticated_direct_bootstrap_skips_public_page() -> None:
    session = ReservationFixtureSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session),
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
        station_identity_resolver=StaticReservationStationResolver(),  # type: ignore[arg-type]
    )
    credential = reservation_request().credential

    assert await client.verify_credentials(credential) is True
    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert result.session_ready_at is not None
    assert session.open_calls == 1
    assert session.auth_calls == 1
    assert session.navigate_fresh_calls == 1
    assert session.navigate_calls == 1
    assert session.choose_station_calls == 0
    assert session.choose_schedule_calls == 0
    assert session.submit_calls == 0
    assert session.reserve_calls == 1


@pytest.mark.asyncio
async def test_warm_direct_protection_keeps_existing_fail_closed_result() -> None:
    session = ProtectedFreshDirectReservationSession()
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session),
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
        station_identity_resolver=StaticReservationStationResolver(),  # type: ignore[arg-type]
    )

    assert await client.verify_credentials(reservation_request().credential) is True
    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PROVIDER_BLOCKED
    assert result.session_ready_at is not None
    assert session.open_calls == 1
    assert session.navigate_fresh_calls == 1
    assert session.choose_station_calls == 0
    assert session.submit_calls == 0
    assert session.reserve_calls == 0


@pytest.mark.asyncio
async def test_changed_credential_generation_cannot_use_warm_direct_path() -> None:
    first = ReservationFixtureSession()
    replacement = ReservationFixtureSession()
    factory = SequenceReservationFixtureFactory(first, replacement)
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
        station_identity_resolver=StaticReservationStationResolver(),  # type: ignore[arg-type]
    )

    assert await client.verify_credentials(reservation_request("credential-v1").credential)
    result = await client.reserve_once(reservation_request("credential-v2"))

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert first.closed == 1
    assert first.navigate_fresh_calls == 0
    assert replacement.open_calls == 1
    assert replacement.auth_calls == 1
    assert replacement.navigate_fresh_calls == 0
    assert replacement.navigate_calls == 1


@pytest.mark.asyncio
async def test_expired_authenticated_session_cannot_use_warm_direct_path() -> None:
    first = ReservationFixtureSession()
    replacement = ReservationFixtureSession()
    factory = SequenceReservationFixtureFactory(first, replacement)
    now = [0.0]
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=20,
        station_identity_resolver=StaticReservationStationResolver(),  # type: ignore[arg-type]
        monotonic=lambda: now[0],
    )

    assert await client.verify_credentials(reservation_request().credential)
    now[0] = 61.0
    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert first.closed == 1
    assert first.navigate_fresh_calls == 0
    assert replacement.open_calls == 1
    assert replacement.auth_calls == 1
    assert replacement.navigate_fresh_calls == 0
    assert replacement.navigate_calls == 1


@pytest.mark.asyncio
async def test_real_session_fresh_navigation_replaces_tab_before_direct_url() -> None:
    session = object.__new__(_PydollSession)
    session._opened_once = True
    session._replace_tab = AsyncMock()  # type: ignore[method-assign]
    expected = PydollPageSnapshot("열차 조회 결과", ())
    session.navigate = AsyncMock(return_value=expected)  # type: ignore[method-assign]
    valid_url = pydoll_module.build_korail_general_search_url(
        origin=KorailStationIdentity("0010", "대전"),
        destination=KorailStationIdentity("0001", "서울"),
        travel_date=date(2026, 8, 2),
        departure_time=time(6, 35),
    )

    result = await session.navigate_fresh(valid_url)

    assert result is expected
    session._replace_tab.assert_awaited_once_with()
    session.navigate.assert_awaited_once_with(valid_url)
    assert "txtGoStartCode=0010" in valid_url


class ReplayCaptureSearchSession(ReservationFixtureSession):
    def __init__(self) -> None:
        super().__init__()
        self.capture_started = 0
        self.capture_exported = 0

    async def begin_http_replay_capture(self) -> None:
        self.capture_started += 1

    async def export_http_replay_plan(
        self,
        *,
        origin: str,
        destination: str,
        captured_date: date,
    ) -> object:
        assert (origin, destination, captured_date) == ("대전", "서울", date(2026, 8, 2))
        self.capture_exported += 1
        return SimpleNamespace(captured_request_count=1)

    async def wait_for_result(self) -> PydollPageSnapshot:
        return PydollPageSnapshot(
            "KORAIL 열차 조회 결과",
            (
                PydollTrainRow(
                    kind_text="KTX",
                    train_number="118",
                    route_text="대전 → 서울(06:35 ~ 07:49) 소요시간: 1시간 14분",
                    seats=(
                        PydollSeatBox("일반실 23,700원", frozenset({"price_box"})),
                        PydollSeatBox("특실 33,200원", frozenset({"price_box"})),
                    ),
                ),
            ),
        )


class FailingSearchSession(ReservationFixtureSession):
    async def choose_station(self, kind: str, station: str) -> None:
        raise BrowserSourceUnavailable("station_search_input")


class SignalingReservationSession(ReservationFixtureSession):
    def __init__(self) -> None:
        super().__init__()
        self.reservation_started = asyncio.Event()

    async def reserve_once(self, request: KorailReservationRequest) -> KorailReservationResult:
        self.reservation_started.set()
        return await super().reserve_once(request)


class ReplayFixtureClient:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class BlockingReplayFixtureClient(ReplayFixtureClient):
    def __init__(self) -> None:
        super().__init__()
        self.search_started = asyncio.Event()
        self.release_search = asyncio.Event()

    async def search(self, request: BrowserSeatSearchRequest) -> BrowserSeatSearchResult:
        self.search_started.set()
        await self.release_search.wait()
        snapshot = PydollPageSnapshot(
            "KORAIL 열차 조회 결과",
            (
                PydollTrainRow(
                    kind_text="KTX",
                    train_number="118",
                    route_text="대전 → 서울(06:35 ~ 07:49) 소요시간: 1시간 14분",
                    seats=(
                        PydollSeatBox("일반실 23,700원", frozenset({"price_box"})),
                        PydollSeatBox("특실 33,200원", frozenset({"price_box"})),
                    ),
                ),
            ),
        )
        return PydollKorailBrowserClient._read_result(snapshot, request)


@pytest.mark.asyncio
async def test_credential_version_change_replaces_persistent_session() -> None:
    factory = ReservationFixtureFactory()
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    first = await client.reserve_once(reservation_request("credential-v1"))
    second = await client.reserve_once(reservation_request("credential-v1"))
    third = await client.reserve_once(reservation_request("credential-v2"))
    await client.close()

    assert [first.outcome, second.outcome, third.outcome] == [
        KorailReservationOutcome.PAYMENT_REQUIRED,
        KorailReservationOutcome.PAYMENT_REQUIRED,
        KorailReservationOutcome.PAYMENT_REQUIRED,
    ]
    assert len(factory.sessions) == 2
    assert [session.auth_calls for session in factory.sessions] == [1, 1]
    assert factory.sessions[0].reserve_calls == 2
    assert factory.sessions[0].closed == 1
    assert factory.sessions[1].reserve_calls == 1
    assert factory.sessions[1].closed == 1


@pytest.mark.asyncio
async def test_same_version_credentials_never_share_an_authenticated_session() -> None:
    factory = ReservationFixtureFactory()
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    first = reservation_request().credential
    second = replace(
        first,
        login_method=KorailLoginMethod.EMAIL,
        login_id="other-fixture@example.test",
        password="other-fixture-password",
    )

    assert await asyncio.gather(
        client.verify_credentials(first),
        client.verify_credentials(second),
    ) == [True, True]
    assert len(factory.sessions) == 2
    assert [session.auth_calls for session in factory.sessions] == [1, 1]
    assert "authenticated_credential_fingerprint" not in repr(client._active_session)

    last_verified_method = factory.sessions[-1].authenticated_login_methods[-1]
    credential_to_reserve = second if last_verified_method is first.login_method else first
    request = replace(reservation_request(), credential=credential_to_reserve)

    first_reservation = await client.reserve_once(request)
    second_reservation = await client.reserve_once(request)
    await client.close()

    assert first_reservation.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert second_reservation.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert len(factory.sessions) == 3
    assert [session.auth_calls for session in factory.sessions] == [1, 1, 1]
    assert factory.sessions[-1].reserve_calls == 2
    assert [session.closed for session in factory.sessions] == [1, 1, 1]


@pytest.mark.asyncio
async def test_client_reserve_once_authenticates_once_before_reusing_a_persistent_session() -> None:
    factory = ReservationFixtureFactory(authenticated=True)
    client = PydollKorailBrowserClient(session_factory=factory)

    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert len(factory.sessions) == 1
    session = factory.sessions[0]
    assert session.open_calls == 1
    assert session.auth_calls == 1
    assert session.choose_station_calls == 2
    assert session.choose_schedule_calls == 1
    assert session.submit_calls == 1
    assert session.reserve_calls == 1


@pytest.mark.asyncio
async def test_authenticated_persistent_session_reuses_last_active_browser_until_ttl() -> None:
    factory = ReservationFixtureFactory()
    now = [0.0]
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=20,
        monotonic=lambda: now[0],
    )

    await client.reserve_once(reservation_request())
    now[0] = 59.0
    await client.reserve_once(reservation_request())
    active_after_second_request = client._active_session
    assert active_after_second_request is not None
    assert active_after_second_request.last_used_at == 59.0
    now[0] = 118.0
    await client.reserve_once(reservation_request())
    active_after_third_request = client._active_session
    assert active_after_third_request is active_after_second_request
    assert active_after_third_request.last_used_at == 118.0
    now[0] = 179.0
    await client.reserve_once(reservation_request())
    await client.close()

    assert len(factory.sessions) == 2
    assert [session.auth_calls for session in factory.sessions] == [1, 1]
    assert [session.reserve_calls for session in factory.sessions] == [3, 1]
    assert [session.closed for session in factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_korail_session_actor_snapshot_separates_local_reuse_from_verification() -> None:
    factory = ReservationFixtureFactory()
    now = [10.0]
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=20,
        monotonic=lambda: now[0],
    )

    cold = client.session_snapshot()
    assert cold.state is KorailSessionActorState.COLD
    assert cold.credential_generation is None
    assert cold.locally_reusable is False

    assert await client.verify_credentials(reservation_request("credential-v4").credential)
    ready = client.session_snapshot()
    assert ready.state is KorailSessionActorState.READY
    assert ready.credential_generation == "credential-v4"
    assert ready.created_at_monotonic == 10.0
    assert ready.last_verified_at_monotonic == 10.0
    assert ready.last_used_at_monotonic == 10.0
    assert ready.local_reuse_until_monotonic == 70.0
    assert ready.locally_reusable is True

    now[0] = 20.0
    assert await client.prewarm_credentials(reservation_request("credential-v4").credential)
    reused = client.session_snapshot()
    assert reused.last_verified_at_monotonic == 10.0
    assert reused.last_used_at_monotonic == 20.0
    assert len(factory.sessions) == 1

    now[0] = 81.0
    stale = client.session_snapshot()
    assert stale.state is KorailSessionActorState.STALE
    assert stale.locally_reusable is False

    assert await client.prewarm_credentials(reservation_request("credential-v4").credential)
    refreshed = client.session_snapshot()
    assert refreshed.state is KorailSessionActorState.READY
    assert refreshed.last_verified_at_monotonic == 81.0
    assert len(factory.sessions) == 2
    await client.close()


@pytest.mark.asyncio
async def test_korail_session_actor_reports_authenticating_while_login_is_in_flight() -> None:
    session = ReservationFixtureSession()
    started = asyncio.Event()
    release = asyncio.Event()

    async def authenticate(_credential: KorailCredentialInput) -> bool:
        started.set()
        await release.wait()
        return True

    session.ensure_authenticated = authenticate  # type: ignore[method-assign]
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session),
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    verification = asyncio.create_task(
        client.verify_credentials(reservation_request("credential-v7").credential)
    )
    await started.wait()

    in_flight = client.session_snapshot()
    assert in_flight.state is KorailSessionActorState.AUTHENTICATING
    assert in_flight.credential_generation == "credential-v7"
    assert in_flight.last_verified_at_monotonic is None
    assert in_flight.locally_reusable is False

    release.set()
    assert await verification is True
    assert client.session_snapshot().state is KorailSessionActorState.READY
    await client.close()


@pytest.mark.asyncio
async def test_korail_auth_failure_updates_the_non_secret_actor_state() -> None:
    factory = ReservationFixtureFactory(authenticated=False)
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    assert await client.verify_credentials(reservation_request("credential-v5").credential) is False

    snapshot = client.session_snapshot()
    assert snapshot.state is KorailSessionActorState.AUTH_REQUIRED
    assert snapshot.credential_generation == "credential-v5"
    assert snapshot.locally_reusable is False


@pytest.mark.asyncio
async def test_korail_protection_updates_the_non_secret_actor_state() -> None:
    session = ReservationFixtureSession()
    session.ensure_authenticated = AsyncMock(  # type: ignore[method-assign]
        side_effect=BrowserProtectionDetected(stage="login_submit")
    )
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session),
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    with pytest.raises(BrowserProtectionDetected):
        await client.verify_credentials(reservation_request("credential-v6").credential)

    snapshot = client.session_snapshot()
    assert snapshot.state is KorailSessionActorState.BLOCKED
    assert snapshot.credential_generation == "credential-v6"
    assert snapshot.locally_reusable is False


@pytest.mark.asyncio
async def test_authenticated_session_isolated_from_search_and_http_replay_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticated = ReservationFixtureSession()
    search = ReplayCaptureSearchSession()
    factory = SequenceReservationFixtureFactory(authenticated, search)
    replay = ReplayFixtureClient()
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: replay,
    )
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    assert await client.verify_credentials(reservation_request().credential) is True
    authenticated_active = client._active_session
    assert authenticated_active is not None

    result = await client.search(seat_search_request())

    assert [train.train_number for train in result.trains] == ["118"]
    assert client._active_session is authenticated_active
    assert authenticated.closed == 0
    assert authenticated.choose_station_calls == 0
    assert authenticated.submit_calls == 0
    assert search.auth_calls == 0
    assert search.capture_started == 1
    assert search.capture_exported == 1
    assert search.closed == 1
    assert len(client._active_http_replays) == 1

    reservation = await client.reserve_once(reservation_request())
    await client.close()

    assert reservation.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert factory.calls == 2
    assert authenticated.auth_calls == 1
    assert authenticated.reserve_calls == 1
    assert authenticated.closed == 1
    assert replay.closed == 1


@pytest.mark.asyncio
async def test_failed_background_search_preserves_authenticated_active_session() -> None:
    authenticated = ReservationFixtureSession()
    search = FailingSearchSession()
    factory = SequenceReservationFixtureFactory(authenticated, search)
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    assert await client.verify_credentials(reservation_request().credential) is True
    authenticated_active = client._active_session
    assert authenticated_active is not None

    with pytest.raises(BrowserSourceUnavailable):
        await client.search(seat_search_request())

    assert client._active_session is authenticated_active
    assert authenticated.closed == 0
    assert authenticated.choose_station_calls == 0
    assert search.closed == 1

    reservation = await client.reserve_once(reservation_request())
    await client.close()

    assert reservation.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert factory.calls == 2
    assert authenticated.auth_calls == 1
    assert authenticated.reserve_calls == 1
    assert authenticated.closed == 1


@pytest.mark.asyncio
async def test_blocked_http_replay_search_does_not_block_authenticated_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticated = SignalingReservationSession()
    search = ReplayCaptureSearchSession()
    factory = SequenceReservationFixtureFactory(authenticated, search)
    replay = BlockingReplayFixtureClient()
    monkeypatch.setattr(
        pydoll_module,
        "KorailHttpReplayClient",
        lambda plan, timeout_seconds, lease_is_current: replay,
    )
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    assert await client.verify_credentials(reservation_request().credential) is True
    authenticated_active = client._active_session
    await client.search(seat_search_request())
    assert search.closed == 1
    assert client._active_session is authenticated_active

    search_task = asyncio.create_task(client.search(seat_search_request()))
    await replay.search_started.wait()

    reservation_task = asyncio.create_task(client.reserve_once(reservation_request()))
    await asyncio.wait_for(authenticated.reservation_started.wait(), timeout=1)
    reservation = await reservation_task

    assert reservation.outcome is KorailReservationOutcome.PAYMENT_REQUIRED
    assert client._active_session is authenticated_active
    assert authenticated.closed == 0
    assert authenticated.reserve_calls == 1

    replay.release_search.set()
    search_result = await search_task
    assert [train.train_number for train in search_result.trains] == ["118"]
    assert client._active_session is authenticated_active
    assert authenticated.closed == 0
    await client.close()
    assert replay.closed == 1


@pytest.mark.asyncio
async def test_failed_authenticated_session_is_discarded_without_a_second_login_attempt() -> None:
    factory = ReservationFixtureFactory(authenticated=False)
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.AUTH_REQUIRED
    assert len(factory.sessions) == 1
    assert factory.sessions[0].auth_calls == 1
    assert factory.sessions[0].submit_calls == 0
    assert factory.sessions[0].reserve_calls == 0
    assert factory.sessions[0].closed == 1


@pytest.mark.asyncio
async def test_client_preserves_uncertain_reservation_click_result() -> None:
    session = ReservationFixtureSession()
    session.reserve_once = AsyncMock(  # type: ignore[method-assign]
        return_value=KorailReservationResult(
            KorailReservationOutcome.FAILED,
            "reservation_result_unknown:reservation_click_error",
            seat_clicked=True,
            reservation_clicked=True,
        )
    )
    client = PydollKorailBrowserClient(
        session_factory=lambda *_args: ReservationFixtureContext(session)
    )

    result = await client.reserve_once(reservation_request())

    assert result.outcome is KorailReservationOutcome.FAILED
    assert result.reason.startswith("reservation_result_unknown")
    assert result.seat_clicked is True
    assert result.reservation_clicked is True
    assert result.session_ready_at is not None


@pytest.mark.asyncio
async def test_verify_credentials_only_opens_and_authenticates_once() -> None:
    factory = ReservationFixtureFactory()
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    authenticated = await client.verify_credentials(reservation_request().credential)
    await client.close()

    assert authenticated is True
    assert len(factory.sessions) == 1
    session = factory.sessions[0]
    assert session.open_calls == 1
    assert session.auth_calls == 1
    assert session.choose_station_calls == 0
    assert session.choose_schedule_calls == 0
    assert session.submit_calls == 0
    assert session.reserve_calls == 0
    assert session.closed == 1


@pytest.mark.asyncio
async def test_verify_credentials_does_not_retry_failed_login() -> None:
    factory = ReservationFixtureFactory(authenticated=False)
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )

    authenticated = await client.verify_credentials(
        reservation_request(login_method=KorailLoginMethod.PHONE).credential
    )

    assert authenticated is False
    assert len(factory.sessions) == 1
    session = factory.sessions[0]
    assert session.open_calls == 1
    assert session.auth_calls == 1
    assert session.submit_calls == 0
    assert session.reserve_calls == 0
    assert session.closed == 1


@pytest.mark.asyncio
async def test_verify_credentials_never_accepts_a_previous_authenticated_session() -> None:
    factory = ReservationFixtureFactory(authenticated=True)
    client = PydollKorailBrowserClient(
        session_factory=factory,
        session_reuse_ttl_seconds=300,
        session_reuse_max_searches=20,
    )
    first = reservation_request().credential

    assert await client.verify_credentials(first) is True
    factory.authenticated = False
    changed = KorailCredentialInput(
        login_method=first.login_method,
        login_id="different-member",
        password="different-password",
        version=first.version,
    )
    assert await client.verify_credentials(changed) is False

    assert len(factory.sessions) == 2
    assert [session.auth_calls for session in factory.sessions] == [1, 1]
    assert [session.closed for session in factory.sessions] == [1, 1]


class FakeAutomation:
    async def close(self) -> None:
        return None


class FakeReservationClient:
    def __init__(self, result: KorailReservationResult | None = None) -> None:
        self.request: KorailReservationRequest | None = None
        self.result = result or KorailReservationResult(
            KorailReservationOutcome.PAYMENT_REQUIRED,
            "reservation_pending_payment",
            seat_clicked=True,
            reservation_clicked=True,
        )

    async def reserve_once(self, request: KorailReservationRequest) -> KorailReservationResult:
        self.request = request
        return self.result

    async def verify_credentials(self, credential: KorailCredentialInput) -> bool:
        self.verified_credential = credential
        return True


class UnavailableLoginReservationClient(FakeReservationClient):
    async def verify_credentials(self, credential: KorailCredentialInput) -> bool:
        self.verified_credential = credential
        raise BrowserSourceUnavailable("login_panel")


class StatefulFakeReservationClient(FakeReservationClient):
    def session_snapshot(self) -> KorailSessionActorSnapshot:
        return KorailSessionActorSnapshot(
            state=KorailSessionActorState.READY,
            credential_generation="credential-v8",
            created_at_monotonic=10.0,
            last_verified_at_monotonic=11.0,
            last_used_at_monotonic=12.0,
            local_reuse_until_monotonic=72.0,
            locally_reusable=True,
        )


def internal_payload() -> dict[str, object]:
    return {
        "origin": "대전",
        "destination": "서울",
        "travel_date": "2026-08-02",
        "train_number": "118",
        "train_type": "KTX",
        "departure_time": "06:35:00",
        "arrival_time": "07:49:00",
        "seat_class": "special",
        "credential": {
            "login_method": "membership_number",
            "login_id": "fixture-login",
            "password": "fixture-password",
            "version": "credential-v1",
        },
    }


def test_internal_reserve_endpoint_is_bearer_protected_and_returns_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "pydoll")
    caplog.set_level(logging.INFO, logger="rail_waitlist.korail_browser_adapter_service")
    reservation_client = FakeReservationClient()
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )

    with TestClient(app) as client:
        unauthorized = client.post("/v1/reserve-once", json=internal_payload())
        response = client.post(
            "/v1/reserve-once",
            json=internal_payload(),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "outcome": "payment_required",
        "reason": "reservation_pending_payment",
        "seat_clicked": True,
        "reservation_clicked": True,
    }
    assert "fixture-login" not in response.text
    assert "fixture-password" not in response.text
    assert (
        "KORAIL reserve-once completed outcome=payment_required "
        "reason=reservation_pending_payment seat_clicked=true reservation_clicked=true"
    ) in caplog.text
    assert "fixture-login" not in caplog.text
    assert "fixture-password" not in caplog.text
    assert reservation_client.request is not None
    assert (
        reservation_client.request.credential.login_method
        is KorailLoginMethod.MEMBERSHIP_NUMBER
    )
    assert reservation_client.request.credential.version == "credential-v1"


def test_internal_session_state_is_bearer_protected_and_contains_no_secret_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "pydoll")
    monkeypatch.setattr(
        "rail_waitlist.korail_browser_adapter_service.time.monotonic",
        lambda: 50.0,
    )
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=StatefulFakeReservationClient(),
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )

    with TestClient(app) as client:
        unauthorized = client.get("/v1/session-state")
        response = client.get(
            "/v1/session-state",
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "state": "ready",
        "credential_generation": "credential-v8",
        "created_age_seconds": 40.0,
        "last_verified_age_seconds": 39.0,
        "last_used_age_seconds": 38.0,
        "local_reuse_remaining_seconds": 22.0,
        "locally_reusable": True,
    }
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "cookie" not in serialized
    assert "token" not in serialized
    assert "fingerprint" not in serialized


def test_internal_login_verification_is_bearer_protected_and_has_no_booking_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "pydoll")
    reservation_client = FakeReservationClient()
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )
    payload = {"credential": internal_payload()["credential"]}

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        unauthorized = client.post("/v1/verify-login", json=payload)
        response = client.post(
            "/v1/verify-login",
            json=payload,
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"outcome": "authenticated"}
    assert "fixture-login" not in response.text
    assert "fixture-password" not in response.text
    assert "outcome=authenticated" in caplog.text
    assert "fixture-login" not in caplog.text
    assert "fixture-password" not in caplog.text
    assert reservation_client.request is None
    assert (
        reservation_client.verified_credential.login_method
        is KorailLoginMethod.MEMBERSHIP_NUMBER
    )


def test_internal_login_verification_logs_only_the_safe_failure_stage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "pydoll")
    reservation_client = UnavailableLoginReservationClient()
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )
    payload = {"credential": internal_payload()["credential"]}

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.post(
            "/v1/verify-login",
            json=payload,
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 200
    assert response.json() == {"outcome": "failed"}
    assert "stage=login_panel" in caplog.text
    assert "fixture-login" not in caplog.text
    assert "fixture-password" not in caplog.text


@pytest.mark.parametrize("path", ["/v1/verify-login", "/v1/reserve-once"])
def test_internal_credential_validation_never_reflects_the_password(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("KORAIL_BROWSER_ENGINE", "pydoll")
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=FakeReservationClient(),
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )
    oversized_password = "sensitive-fixture-" + ("x" * 256)
    payload = internal_payload()
    credential = payload["credential"]
    assert isinstance(credential, dict)
    credential["password"] = oversized_password
    body = {"credential": credential} if path.endswith("verify-login") else payload

    with TestClient(app) as client:
        response = client.post(
            path,
            json=body,
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "request_validation_failed"}
    assert oversized_password not in response.text
