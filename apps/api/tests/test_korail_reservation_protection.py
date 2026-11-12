from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rail_waitlist.korail_sidecar.pydoll.auth_contracts import KorailCredentialInput
from rail_waitlist.korail_sidecar.pydoll.page_contracts import PydollPageSnapshot
from rail_waitlist.korail_sidecar.pydoll.reservation_contracts import (
    KorailReservationRequest,
    KorailReservationSeatClass,
)
from rail_waitlist.korail_sidecar.pydoll.reservation_dialog_policy import ReservationDialogPhase
from rail_waitlist.korail_sidecar.pydoll.reservation_protection import (
    classify_reservation_protection,
    log_reservation_protection,
)
from rail_waitlist.korail_sidecar.pydoll.search_response_protection import (
    SEARCH_RESPONSE_MACRO_PROTECTION,
)


@pytest.mark.parametrize(
    ("network", "body", "surfaces", "source", "trigger"),
    [
        (
            ((200, SEARCH_RESPONSE_MACRO_PROTECTION),),
            "조회 결과가 없습니다",
            (),
            "business_response",
            "marker_macro_err1",
        ),
        (((429, "xhr"),), "", (), "network", "rate_limited"),
        (((403, "document"),), "", (), "network", "http_403_main"),
        (((403, "fixture-private-resource"),), "", (), "network", "http_403_subresource"),
        ((), "macro_err1 fixture-private-body", (), "page_body", "marker_macro_err1"),
        ((), "정상 본문", ("captcha fixture-private-dialog",), "page_surface", "marker_captcha"),
        ((), "미허가 도구", (), "page_body", "marker_unauthorized_tool"),
        ((), "비정상 접근", (), "page_body", "marker_abnormal_access"),
        ((), "code: 8002", (), "page_body", "marker_code_8002"),
        ((), "code: 8003", (), "page_body", "marker_code_8003"),
        ((), "code: 1405", (), "page_body", "marker_code_1405"),
        ((), "NetFunnel", (), "page_body", "marker_netfunnel"),
    ],
)
def test_protection_retains_closed_source_and_never_logs_provider_material(
    network: tuple[tuple[int, str], ...],
    body: str,
    surfaces: tuple[str, ...],
    source: str,
    trigger: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot = PydollPageSnapshot(
        body_text=body,
        rows=(),
        network_responses=network,
        protection_texts=surfaces,
        url="https://www.korail.com/ticket/search/list?fixture-private-url",
    )
    evidence = classify_reservation_protection(snapshot)
    assert evidence is not None
    assert (evidence.source, evidence.trigger) == (source, trigger)
    with caplog.at_level(logging.WARNING):
        log_reservation_protection(
            evidence,
            snapshot,
            ReservationDialogPhase.POST_REQUEST,
            event_logger=logging.getLogger("test.korail.protection"),
        )
    assert f"source={source}" in caplog.text
    assert f"trigger={trigger}" in caplog.text
    assert "phase=post_request" in caplog.text
    assert "fixture-private" not in caplog.text


def test_successful_http_without_protection_is_not_blocked() -> None:
    assert (
        classify_reservation_protection(
            PydollPageSnapshot("조회 결과가 없습니다", (), network_responses=((200, "fetch"),))
        )
        is None
    )


@pytest.mark.asyncio
async def test_reservation_stops_before_any_controls_on_http_200_macro_verdict() -> None:
    from rail_waitlist.korail_sidecar.pydoll.reservation_driver import (
        PydollReservationDomDriver,
        ReservationAttemptState,
    )

    snapshot = PydollPageSnapshot(
        "조회 결과가 없습니다", (), network_responses=((200, SEARCH_RESPONSE_MACRO_PROTECTION),)
    )
    controls = AsyncMock()
    scripts = AsyncMock()
    driver = PydollReservationDomDriver(
        port=SimpleNamespace(_snapshot=AsyncMock(return_value=snapshot)),
        timeout_ms=1000,
        timeout_seconds=1,
        execute_script=scripts,
        visible_elements=controls,
        current_schedule=AsyncMock(),
        read_control_state=AsyncMock(),
        monotonic=lambda: 0,
        sleep=AsyncMock(),
        utc_now=lambda: datetime.now(UTC),
        event_logger=logging.getLogger("test.korail.protection"),
    )
    # A rejected snapshot must settle before target-specific DOM reads or input.
    request = KorailReservationRequest(
        origin="대전",
        destination="서울",
        travel_date=date(2026, 8, 2),
        train_number="118",
        train_type="KTX",
        departure_time=time(6, 35),
        arrival_time=time(7, 49),
        seat_class=KorailReservationSeatClass.GENERAL,
        credential=KorailCredentialInput(
            login_id="fixture-login", password="fixture-password", version="1"
        ),
    )
    result = await driver.probe_reservation_terminal(request, ReservationAttemptState())
    assert result is not None
    assert result.outcome.value == "provider_blocked"
    assert result.reason == "provider_access_restricted"
    controls.assert_not_awaited()
    scripts.assert_not_awaited()
