from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import Self
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import rail_waitlist.korail_pydoll_browser as pydoll_module
import rail_waitlist.korail_sidecar.http as korail_http_module
from rail_waitlist.domain import Provider, SeatClass
from rail_waitlist.korail_browser_adapter_service import create_adapter_app
from rail_waitlist.korail_browser_automation import BrowserSeatSearchRequest
from rail_waitlist.korail_browser_seat_source import KorailBrowserSeatSource, _AdapterFailure
from rail_waitlist.korail_pydoll_browser import (
    KorailCredentialInput,
    PydollKorailBrowserClient,
    PydollPageSnapshot,
    PydollSeatBox,
    PydollTrainRow,
)
from rail_waitlist.korail_reservation_confirmation import (
    KorailSameSessionDetailEvidence,
    normalize_korail_same_session_detail,
)
from rail_waitlist.korail_reservation_contract import KorailReservationConfirmationResult
from rail_waitlist.korail_sidecar.browser_contracts import (
    BrowserProtectionDetected,
    BrowserRateLimited,
    BrowserSourceUnavailable,
)
from rail_waitlist.korail_sidecar.pydoll.page_contracts import (
    PydollIssuedTicketListSnapshot,
    PydollIssuedTicketSummary,
    PydollReservationListSnapshot,
)
from rail_waitlist.korail_sidecar.pydoll.search_driver import (
    PydollSearchDomDriver,
    _issued_ticket_from_value,
    _issued_ticket_snapshot_from_script_response,
    _reservation_list_snapshot_from_script_response,
)
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationDiagnosticCode,
    ReservationConfirmationOutcome,
    ReservationConfirmationPurpose,
    ReservationConfirmationSeat,
    ReservationConfirmationTarget,
)

KOREA = ZoneInfo("Asia/Seoul")


def confirmation_target(
    *,
    credential_version: int = 7,
    purpose: ReservationConfirmationPurpose = ReservationConfirmationPurpose.INITIAL,
    reserved_seats: tuple[ReservationConfirmationSeat, ...] = (),
    confirmation_correlation_seats: tuple[ReservationConfirmationSeat, ...] = (),
) -> ReservationConfirmationTarget:
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
        credential_version=credential_version,
        purpose=purpose,
        reserved_seats=reserved_seats,
        confirmation_correlation_seats=confirmation_correlation_seats,
    )


def confirmation_payload() -> dict[str, object]:
    target = confirmation_target()
    return {
        "attempt_id": target.attempt_id,
        "candidate_id": target.candidate_id,
        "train_number": target.train_number,
        "origin": target.origin,
        "destination": target.destination,
        "departure_at": target.departure_at.isoformat(),
        "arrival_at": target.arrival_at.isoformat(),
        "seat_class": target.seat_class.value,
        "passenger_count": target.passenger_count,
        "credential_version": target.credential_version,
        "purpose": target.purpose.value,
        "reserved_seats": [],
        "confirmation_correlation_seats": [],
    }


class ReadOnlyDetailSession:
    def __init__(
        self,
        snapshot: PydollPageSnapshot,
        *,
        reservation_list_snapshot: PydollReservationListSnapshot | None = None,
        issued_ticket_snapshot: PydollIssuedTicketListSnapshot | None = None,
        snapshot_error: Exception | None = None,
        reservation_list_error: Exception | None = None,
        issued_ticket_error: Exception | None = None,
        official_session_error: Exception | None = None,
        officially_authenticated: bool = False,
        header_authenticated: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.reservation_list_snapshot = reservation_list_snapshot
        self.issued_ticket_snapshot = issued_ticket_snapshot
        self.snapshot_error = snapshot_error
        self.reservation_list_error = reservation_list_error
        self.issued_ticket_error = issued_ticket_error
        self.official_session_error = official_session_error
        self.officially_authenticated = officially_authenticated
        self.header_authenticated = header_authenticated
        self.events: list[str] = []
        self.snapshot_started = asyncio.Event()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.events.append("close")

    async def open(self) -> PydollPageSnapshot:
        self.events.append("open")
        return PydollPageSnapshot(body_text="KORAIL", rows=())

    async def ensure_authenticated(self, credential: object) -> bool:
        self.events.append("authenticate")
        return True

    async def _snapshot(self) -> PydollPageSnapshot:
        self.snapshot_started.set()
        self.events.append("snapshot")
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.snapshot

    async def _probe_official_authenticated_session(self) -> bool:
        self.events.append("login_check")
        if self.official_session_error is not None:
            raise self.official_session_error
        return self.officially_authenticated

    async def _has_authenticated_header(self) -> bool:
        self.events.append("auth_header")
        return self.header_authenticated

    async def read_reservation_list(self) -> PydollReservationListSnapshot:
        self.events.append("reservation_list")
        if self.reservation_list_error is not None:
            raise self.reservation_list_error
        if self.reservation_list_snapshot is None:
            # Most detail-focused tests intentionally leave the fallback surface
            # incomplete. A generic exception is reserved for tests that verify
            # the production source-unavailable stage wrapping.
            return PydollReservationListSnapshot(url="https://www.korail.com/")
        return self.reservation_list_snapshot

    async def read_issued_ticket_list(self) -> PydollIssuedTicketListSnapshot:
        self.events.append("issued_ticket_list")
        if self.issued_ticket_error is not None:
            raise self.issued_ticket_error
        if self.issued_ticket_snapshot is None:
            raise RuntimeError("issued ticket fixture not configured")
        return self.issued_ticket_snapshot


class BlockingTimetableSession:
    def __init__(self) -> None:
        self.search_started = asyncio.Event()
        self.release_search = asyncio.Event()
        self.closed = 0
        self.stations = {"departure": "", "arrival": ""}
        self.schedule = (date(2026, 8, 3), 15)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed += 1

    async def open(self) -> PydollPageSnapshot:
        return PydollPageSnapshot(body_text="KORAIL", rows=())

    async def choose_station(self, kind: str, station: str) -> None:
        self.search_started.set()
        await self.release_search.wait()
        self.stations[kind] = station

    async def choose_schedule(self, travel_date: date, departure_hour: int) -> None:
        self.schedule = (travel_date, departure_hour)

    async def current_station(self, kind: str) -> str:
        return self.stations[kind]

    async def current_schedule(self) -> tuple[date, int]:
        return self.schedule

    async def current_passenger(self) -> str:
        return "총 1명"

    async def submit_once(self) -> None:
        return None

    async def wait_for_result(self) -> PydollPageSnapshot:
        return PydollPageSnapshot(
            body_text="KORAIL 열차 조회 결과",
            rows=(
                PydollTrainRow(
                    kind_text="KTX 0043",
                    train_number="0043",
                    route_text="서울 → 부산(15:45 ~ 18:12) 소요시간: 2시간 27분",
                    seats=(
                        PydollSeatBox("일반실 59,800원", frozenset()),
                        PydollSeatBox("특실 83,700원", frozenset()),
                    ),
                ),
            ),
        )

    async def expand_results(
        self,
        snapshot: PydollPageSnapshot,
        max_actions: int,
    ) -> PydollPageSnapshot:
        return snapshot


async def detail_client(
    snapshot: PydollPageSnapshot,
    *,
    reservation_list_snapshot: PydollReservationListSnapshot | None = None,
    issued_ticket_snapshot: PydollIssuedTicketListSnapshot | None = None,
    snapshot_error: Exception | None = None,
    reservation_list_error: Exception | None = None,
    issued_ticket_error: Exception | None = None,
    official_session_error: Exception | None = None,
    officially_authenticated: bool = False,
    header_authenticated: bool = False,
) -> tuple[PydollKorailBrowserClient, ReadOnlyDetailSession]:
    session = ReadOnlyDetailSession(
        snapshot,
        reservation_list_snapshot=reservation_list_snapshot,
        issued_ticket_snapshot=issued_ticket_snapshot,
        snapshot_error=snapshot_error,
        reservation_list_error=reservation_list_error,
        issued_ticket_error=issued_ticket_error,
        official_session_error=official_session_error,
        officially_authenticated=officially_authenticated,
        header_authenticated=header_authenticated,
    )
    client = PydollKorailBrowserClient(
        session_factory=lambda *_: session,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )
    assert await client.prewarm_credentials(
        KorailCredentialInput(login_id="fixture", password="fixture", version="7")
    )
    session.events.clear()
    return client, session


def exact_detail_snapshot(
    *,
    url: str = "https://www.korail.com/ticket/reservation/detail",
) -> PydollPageSnapshot:
    return PydollPageSnapshot(
        body_text=(
            "예약 상세 서울역 → 부산역 2026-08-03 KTX 0043 15:45 일반실 "
            "18:12 총 1명 예약취소 장바구니 결제하기"
        ),
        rows=(),
        url=url,
    )


@pytest.mark.asyncio
async def test_pydoll_confirmation_reads_only_current_session_and_exact_identity() -> None:
    client, session = await detail_client(exact_detail_snapshot())

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.credential_version == 7
    assert evidence.exact_identity_matched is True
    assert evidence.seat_class_matched is True
    assert evidence.passenger_count_matched is True
    assert evidence.payment_pending_markers_present is True
    assert session.events == ["snapshot"]


@pytest.mark.asyncio
async def test_blocked_read_only_search_does_not_block_same_session_confirmation() -> None:
    authenticated = ReadOnlyDetailSession(exact_detail_snapshot())
    search = BlockingTimetableSession()
    sessions = iter((authenticated, search))
    client = PydollKorailBrowserClient(
        session_factory=lambda *_: next(sessions),
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )
    credential = KorailCredentialInput(login_id="fixture", password="fixture", version="7")
    assert await client.prewarm_credentials(credential)
    authenticated_active = client._active_session
    authenticated.events.clear()

    search_task = asyncio.create_task(
        client.search(
            BrowserSeatSearchRequest(
                origin="서울",
                destination="부산",
                travel_date=date(2026, 8, 3),
                departure_from=time(15),
                departure_to=time(18),
                passenger_count=1,
            )
        )
    )
    await search.search_started.wait()

    confirmation_task = asyncio.create_task(client.read_reservation_detail(confirmation_target()))
    await asyncio.wait_for(authenticated.snapshot_started.wait(), timeout=1)
    evidence = await confirmation_task

    assert evidence.exact_identity_matched is True
    assert client._active_session is authenticated_active
    assert authenticated.events == ["snapshot"]

    search.release_search.set()
    await search_task
    assert client._active_session is authenticated_active
    assert authenticated.events == ["snapshot"]
    await client.close()


@pytest.mark.asyncio
async def test_pydoll_confirmation_without_active_session_is_source_unavailable() -> None:
    def unexpected_session_factory(*_args: object) -> ReadOnlyDetailSession:
        raise AssertionError("confirmation unexpectedly created a browser context")

    client = PydollKorailBrowserClient(
        session_factory=unexpected_session_factory,
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await client.read_reservation_detail(confirmation_target())

    assert captured.value.stage == "confirmation_session_unavailable"
    assert client._active_session is None
    assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.COLD


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        exact_detail_snapshot().body_text.replace("서울역", "서울숲"),
        exact_detail_snapshot().body_text.replace("부산역", "부산진"),
        exact_detail_snapshot().body_text.replace("0043", "0047"),
        exact_detail_snapshot().body_text.replace("2026-08-03", "2026-08-04"),
        exact_detail_snapshot().body_text.replace("15:45", "15:55"),
        exact_detail_snapshot().body_text.replace("일반실", "특실"),
    ],
)
async def test_pydoll_confirmation_rejects_every_inexact_identity(body: str) -> None:
    client, _ = await detail_client(
        PydollPageSnapshot(
            body_text=body,
            rows=(),
            url="https://www.korail.com/ticket/reservation/detail",
        )
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.exact_identity_matched is False


@pytest.mark.asyncio
async def test_pydoll_confirmation_classifies_login_and_protection_without_actions() -> None:
    login_client, login_session = await detail_client(
        exact_detail_snapshot(url="https://www.korail.com/ticket/login")
    )
    blocked_client, blocked_session = await detail_client(
        PydollPageSnapshot(body_text="CODE -8003", rows=(), url="https://www.korail.com/")
    )

    login = await login_client.read_reservation_detail(confirmation_target())
    blocked = await blocked_client.read_reservation_detail(confirmation_target())

    assert login.auth_required is True
    assert blocked.provider_blocked is True
    assert login_session.events == ["snapshot", "login_check", "auth_header", "close"]
    assert blocked_session.events == ["snapshot", "close"]
    assert login_client._active_session is None
    assert blocked_client._active_session is None
    assert (
        login_client.session_snapshot().state is pydoll_module.KorailSessionActorState.AUTH_REQUIRED
    )
    assert blocked_client.session_snapshot().state is pydoll_module.KorailSessionActorState.BLOCKED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("officially_authenticated", "header_authenticated"),
    [(True, False), (False, True)],
)
async def test_pydoll_confirmation_does_not_trust_transient_login_route_alone(
    officially_authenticated: bool,
    header_authenticated: bool,
) -> None:
    client, session = await detail_client(
        exact_detail_snapshot(url="https://www.korail.com/ticket/login"),
        officially_authenticated=officially_authenticated,
        header_authenticated=header_authenticated,
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.auth_required is False
    assert evidence.exact_identity_matched is False
    expected_auth_events = ["login_check"]
    if not officially_authenticated:
        expected_auth_events.append("auth_header")
    assert session.events == ["snapshot", *expected_auth_events, "reservation_list"]


@pytest.mark.asyncio
async def test_pydoll_confirmation_uses_header_after_unavailable_official_probe() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(url="https://www.korail.com/ticket/login"),
        official_session_error=BrowserSourceUnavailable("session_keepalive"),
        header_authenticated=True,
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.auth_required is False
    assert session.events == [
        "snapshot",
        "login_check",
        "auth_header",
        "reservation_list",
    ]


@pytest.mark.asyncio
async def test_pydoll_confirmation_preserves_uncertainty_when_header_is_absent() -> None:
    uncertainty = BrowserSourceUnavailable("session_keepalive")
    client, session = await detail_client(
        exact_detail_snapshot(url="https://www.korail.com/ticket/login"),
        official_session_error=uncertainty,
        header_authenticated=False,
    )

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await client.read_reservation_detail(confirmation_target())

    assert captured.value is uncertainty
    assert session.events == [
        "snapshot",
        "login_check",
        "auth_header",
        "reservation_list",
        "close",
    ]
    assert client._active_session is None
    assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.STALE


def reservation_list_snapshot(
    *rows: str,
    url: str = "https://www.korail.com/ticket/reservation/list",
    page_marker_visible: bool = True,
    explicit_empty_visible: bool = False,
    loading_visible: bool = False,
    stable_observation: bool = True,
    malformed_card_count: int = 0,
    protection_detected: bool = False,
) -> PydollReservationListSnapshot:
    return PydollReservationListSnapshot(
        url=url,
        reservation_rows=rows,
        rendered_card_count=len(rows) + malformed_card_count,
        malformed_card_count=malformed_card_count,
        page_marker_visible=page_marker_visible,
        explicit_empty_visible=explicit_empty_visible,
        loading_visible=loading_visible,
        stable_observation=stable_observation,
        protection_detected=protection_detected,
    )


def exact_reservation_list_snapshot(
    *,
    row: str | None = None,
    url: str = "https://www.korail.com/ticket/reservation/list",
) -> PydollReservationListSnapshot:
    exact_row = row or (
        "승차권 예약 KTX 0043 서울역 → 부산역 "
        "2026년08월03일(월) 15:45 → 18:12 1매 "
        "예약취소 예약변경 결제/발권 결제기한: 2026. 08. 03 16:30"
    )
    return reservation_list_snapshot(exact_row, url=url)


def test_reservation_list_script_boundary_tracks_completeness_without_raw_card_data() -> None:
    redacted = (
        "KTX 0043 서울역 → 부산역 2026년08월03일 15:45 → 18:12 1매 "
        "예약취소 예약변경 결제/발권 결제기한: 2026. 08. 03 16:30"
    )
    response = {
        "result": {
            "result": {
                "value": {
                    "url": "https://www.korail.com/ticket/reservation/list",
                    "cardCount": 2,
                    "rows": [redacted, None],
                    "pageMarkerVisible": True,
                    "explicitEmptyVisible": False,
                    "loadingVisible": False,
                    "protectionDetected": False,
                }
            }
        }
    }

    snapshot = _reservation_list_snapshot_from_script_response(response, network_responses=())

    assert snapshot.reservation_rows == (redacted,)
    assert snapshot.rendered_card_count == 2
    assert snapshot.malformed_card_count == 1
    assert snapshot.page_ready is True
    assert snapshot.official_read_completed is False

    unsafe = {
        **response,
        "result": {
            "result": {
                "value": {
                    **response["result"]["result"]["value"],
                    "cardCount": 1,
                    "rows": [f"{redacted} forbidden-transport-identifier"],
                }
            }
        },
    }
    rejected = _reservation_list_snapshot_from_script_response(unsafe, network_responses=())
    assert rejected.reservation_rows == ()
    assert rejected.malformed_card_count == 1
    assert "forbidden-transport-identifier" not in repr(rejected)


def test_reservation_list_dom_contract_collects_identity_without_action_seed() -> None:
    source = inspect.getsource(PydollSearchDomDriver.reservation_list_snapshot)

    assert "allIdentityElements" in source
    assert "identityBearing" in source
    assert "containedSeedCount !== 1" in source
    assert "(?:명|매)" not in source.split("const redact", maxsplit=1)[0]
    assert "normalized(item.innerText) === '결제/발권'" not in source
    assert "rows: cards.map(redact)" in source
    assert "loadingVisible" in source
    assert "explicitEmptyVisible" in source


@pytest.mark.asyncio
async def test_pydoll_confirmation_falls_back_to_exact_official_reservation_list() -> None:
    client, session = await detail_client(
        PydollPageSnapshot(
            body_text="다른 화면",
            rows=(),
            url="https://www.korail.com/",
        ),
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.exact_identity_matched is True
    assert evidence.seat_class_matched is False
    assert evidence.seat_class_match_required is False
    assert evidence.passenger_count_matched is True
    assert evidence.payment_pending_markers_present is True
    assert evidence.payment_deadline == datetime(2026, 8, 3, 16, 30, tzinfo=KOREA)
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        exact_reservation_list_snapshot().reservation_rows[0].replace("0043", "0047"),
        exact_reservation_list_snapshot()
        .reservation_rows[0]
        .replace("2026년08월03일", "2026년08월04일", 1),
        exact_reservation_list_snapshot().reservation_rows[0].replace("15:45", "15:55"),
        exact_reservation_list_snapshot().reservation_rows[0].replace("18:12", "18:22"),
        exact_reservation_list_snapshot().reservation_rows[0].replace("서울역", "서울숲"),
        exact_reservation_list_snapshot().reservation_rows[0].replace("부산역", "부산진"),
        exact_reservation_list_snapshot().reservation_rows[0].replace("1매", "2매"),
    ],
)
async def test_pydoll_reservation_list_rejects_every_inexact_identity(row: str) -> None:
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=exact_reservation_list_snapshot(row=row),
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.official_list_read_completed is True
    assert evidence.official_list_target_absent is True
    assert evidence.exact_identity_matched is False
    assert evidence.payment_pending_markers_present is False


@pytest.mark.asyncio
async def test_pydoll_completed_empty_reservation_list_normalizes_to_not_found() -> None:
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(explicit_empty_visible=True),
    )

    evidence = await client.read_reservation_detail(confirmation_target())
    result = normalize_korail_same_session_detail(confirmation_target(), evidence)

    assert evidence.source == "korail-reservation-list"
    assert evidence.official_list_read_completed is True
    assert evidence.official_list_target_absent is True
    assert result.outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert not result.permits_automatic_reservation_retry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reservation_snapshot",
    [
        reservation_list_snapshot(explicit_empty_visible=True, loading_visible=True),
        reservation_list_snapshot(malformed_card_count=1),
        reservation_list_snapshot(),
        reservation_list_snapshot(
            explicit_empty_visible=True,
            url="https://www.korail.com/ticket/search/general",
        ),
    ],
)
async def test_reservation_list_race_or_incomplete_render_never_proves_absence(
    reservation_snapshot: PydollReservationListSnapshot,
) -> None:
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_snapshot,
    )

    evidence = await client.read_reservation_detail(confirmation_target())
    result = normalize_korail_same_session_detail(confirmation_target(), evidence)

    assert evidence.official_list_read_completed is False
    assert evidence.official_list_target_absent is False
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_two_card_render_with_one_missing_identity_field_never_proves_absence() -> None:
    other_row = exact_reservation_list_snapshot().reservation_rows[0].replace("0043", "0047")
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(
            other_row,
            malformed_card_count=1,
        ),
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.official_list_read_completed is False
    assert evidence.official_list_target_absent is False


@pytest.mark.asyncio
async def test_exact_reservation_row_without_pending_controls_is_not_absent() -> None:
    exact_without_pending = (
        exact_reservation_list_snapshot()
        .reservation_rows[0]
        .replace(
            "예약취소 예약변경 결제/발권",
            "승차권 확인",
        )
    )
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(exact_without_pending),
    )

    evidence = await client.read_reservation_detail(confirmation_target())
    result = normalize_korail_same_session_detail(confirmation_target(), evidence)

    assert evidence.official_list_read_completed is True
    assert evidence.official_list_target_absent is False
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_pydoll_duplicate_exact_reservation_rows_remain_inconclusive() -> None:
    exact = exact_reservation_list_snapshot()
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(
            exact.reservation_rows[0], exact.reservation_rows[0]
        ),
    )

    evidence = await client.read_reservation_detail(confirmation_target())
    result = normalize_korail_same_session_detail(confirmation_target(), evidence)

    assert evidence.official_list_read_completed is True
    assert evidence.official_list_target_absent is False
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_pydoll_reservation_list_auth_and_protection_fail_closed() -> None:
    login_client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=exact_reservation_list_snapshot(
            url="https://www.korail.com/ticket/login"
        ),
    )
    blocked_client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(protection_detected=True),
    )

    login = await login_client.read_reservation_detail(confirmation_target())
    blocked = await blocked_client.read_reservation_detail(confirmation_target())

    assert login.auth_required is True
    assert blocked.provider_blocked is True


def paid_follow_up_target(
    *,
    car_number: str = "4",
    seat_number: str = "8A",
) -> ReservationConfirmationTarget:
    return confirmation_target(
        purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP,
        reserved_seats=(
            ReservationConfirmationSeat(
                car_number=car_number,
                seat_number=seat_number,
            ),
        ),
    )


def unknown_follow_up_target(
    *,
    car_number: str = "4",
    seat_number: str = "8A",
) -> ReservationConfirmationTarget:
    return confirmation_target(
        purpose=ReservationConfirmationPurpose.UNKNOWN_RESULT_FOLLOW_UP,
        confirmation_correlation_seats=(
            ReservationConfirmationSeat(
                car_number=car_number,
                seat_number=seat_number,
            ),
        ),
    )


def exact_issued_ticket(
    **overrides: object,
) -> PydollIssuedTicketSummary:
    values: dict[str, object] = {
        "service_date": date(2026, 8, 3),
        "train_number": "0043",
        "origin": "서울",
        "destination": "부산",
        "departure_time": time(15, 45),
        "arrival_time": time(18, 12),
        "seat_class": "standard",
        "passenger_count": 1,
        "car_number": "4",
        "seat_number": "8A",
    }
    values.update(overrides)
    return PydollIssuedTicketSummary(**values)


def issued_ticket_list_snapshot(
    *tickets: PydollIssuedTicketSummary,
    url: str = "https://www.korail.com/ticket/myticket/list",
    malformed_card_count: int = 0,
    empty_state_visible: bool = False,
    protection_detected: bool = False,
) -> PydollIssuedTicketListSnapshot:
    return PydollIssuedTicketListSnapshot(
        url=url,
        tickets=tickets,
        rendered_card_count=len(tickets) + malformed_card_count,
        malformed_card_count=malformed_card_count,
        empty_state_visible=empty_state_visible,
        protection_detected=protection_detected,
    )


def test_issued_ticket_script_boundary_redacts_sensitive_transport_fields() -> None:
    sensitive = "must-not-cross-issued-ticket-boundary"
    payload = {
        "serviceDate": "2026-08-03",
        "trainNumber": "0043",
        "origin": "서울",
        "destination": "부산",
        "departureTime": "15:45",
        "arrivalTime": "18:12",
        "seatClass": "standard",
        "passengerCount": 1,
        "carNumber": "4",
        "seatNumber": "8A",
        "returned": False,
        "operationStopped": False,
        "transferred": False,
    }
    response = {
        "result": {
            "result": {
                "value": {
                    "url": "https://www.korail.com/ticket/myticket/list",
                    "cardCount": 1,
                    "emptyStateVisible": False,
                    "protectionDetected": False,
                    "tickets": [payload],
                }
            }
        }
    }

    snapshot = _issued_ticket_snapshot_from_script_response(
        response,
        network_responses=(),
    )

    assert snapshot.tickets == (exact_issued_ticket(),)
    assert sensitive not in repr(snapshot)
    for forbidden in ("ticketNumber", "pnr", "qr", "rawText"):
        invalid = {
            **response,
            "result": {
                "result": {
                    "value": {
                        **response["result"]["result"]["value"],
                        "tickets": [{**payload, forbidden: sensitive}],
                    }
                }
            },
        }
        rejected = _issued_ticket_snapshot_from_script_response(
            invalid,
            network_responses=(),
        )
        assert rejected.tickets == ()
        assert rejected.malformed_card_count == 1
        assert sensitive not in repr(rejected)


def test_issued_ticket_dom_contract_uses_official_count_before_fallback_and_blocks_gifts() -> None:
    source = inspect.getsource(PydollSearchDomDriver.issued_ticket_snapshot)
    official_count_selector = ".my-ticket__trn-ticket-ticket-num .data"
    group_fallback_selector = ".tck_group-count"

    assert source.index(official_count_selector) < source.index(group_fallback_selector)
    assert "(?:\\s*(?:명|매|석))?" in source
    assert "groupText.match(/(\\d{1,2})\\s*(?:명|매|석)/)" in source
    assert "card.querySelector('.tit_wrap .gift')" in source
    assert r"/받은\s*승차권/" in source


@pytest.mark.parametrize("passenger_count", [None, True, 0, 10, "1"])
def test_issued_ticket_parser_rejects_invalid_passenger_count(passenger_count: object) -> None:
    payload = {
        "serviceDate": "2026-08-03",
        "trainNumber": "0043",
        "origin": "서울",
        "destination": "부산",
        "departureTime": "15:45",
        "arrivalTime": "18:12",
        "seatClass": "standard",
        "passengerCount": passenger_count,
        "carNumber": "4",
        "seatNumber": "8A",
        "returned": False,
        "operationStopped": False,
        "transferred": False,
    }

    assert _issued_ticket_from_value(payload) is None


@pytest.mark.asyncio
async def test_payment_follow_up_exact_issued_ticket_overrides_stale_pending_detail() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )
    target = paid_follow_up_target()

    evidence = await client.read_reservation_detail(target)
    result = normalize_korail_same_session_detail(target, evidence)

    assert evidence.source == "korail-issued-ticket-list"
    assert evidence.issued_ticket_exact_match is True
    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert result.payment_deadline is None
    assert result.official_handoff_url is None
    assert session.events == ["snapshot", "issued_ticket_list"]


@pytest.mark.asyncio
async def test_unknown_follow_up_confirms_only_exact_issued_ticket_as_paid() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )
    target = unknown_follow_up_target()

    evidence = await client.read_reservation_detail(target)
    result = normalize_korail_same_session_detail(target, evidence)

    assert evidence.issued_ticket_exact_match is True
    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAID
    assert session.events == ["snapshot", "issued_ticket_list"]


@pytest.mark.asyncio
async def test_unknown_follow_up_never_confirms_itinerary_only_unpaid_hold() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(empty_state_visible=True),
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )
    target = unknown_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
async def test_unknown_follow_up_complete_issued_and_unpaid_absence_is_not_found() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(empty_state_visible=True),
        reservation_list_snapshot=reservation_list_snapshot(explicit_empty_visible=True),
    )
    target = unknown_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
async def test_unknown_follow_up_incomplete_issued_read_never_proves_absence() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(malformed_card_count=1),
        reservation_list_snapshot=reservation_list_snapshot(explicit_empty_visible=True),
    )
    target = unknown_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
async def test_unknown_follow_up_wrong_issued_seat_and_unpaid_row_stays_inconclusive() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(
            exact_issued_ticket(car_number="5", seat_number="9B")
        ),
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )
    target = unknown_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("issued_snapshot", "expected"),
    [
        (
            issued_ticket_list_snapshot(exact_issued_ticket()),
            ReservationConfirmationOutcome.CONFIRMED_PAID,
        ),
        (
            issued_ticket_list_snapshot(url="https://www.korail.com/ticket/login"),
            ReservationConfirmationOutcome.AUTH_REQUIRED,
        ),
        (
            issued_ticket_list_snapshot(protection_detected=True),
            ReservationConfirmationOutcome.PROVIDER_BLOCKED,
        ),
        (
            issued_ticket_list_snapshot(empty_state_visible=True),
            None,
        ),
    ],
)
async def test_payment_follow_up_issued_probe_survives_detail_snapshot_failure(
    issued_snapshot: PydollIssuedTicketListSnapshot,
    expected: ReservationConfirmationOutcome | None,
) -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_snapshot,
        snapshot_error=RuntimeError("synthetic opaque detail read failure"),
    )
    target = paid_follow_up_target()

    if expected is None:
        with pytest.raises(BrowserSourceUnavailable) as captured:
            await client.read_reservation_detail(target)
        assert captured.value.stage == "confirmation_detail_snapshot"
        assert session.events == [
            "snapshot",
            "issued_ticket_list",
            "reservation_list",
            "close",
        ]
        assert client._active_session is None
        assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.STALE
        return

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )
    assert result.outcome is expected
    if expected is ReservationConfirmationOutcome.AUTH_REQUIRED:
        assert session.events == [
            "snapshot",
            "issued_ticket_list",
            "login_check",
            "auth_header",
            "close",
        ]
        assert client._active_session is None
        assert (
            client.session_snapshot().state is pydoll_module.KorailSessionActorState.AUTH_REQUIRED
        )
    elif expected is ReservationConfirmationOutcome.PROVIDER_BLOCKED:
        assert session.events == ["snapshot", "issued_ticket_list", "close"]
        assert client._active_session is None
        assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.BLOCKED
    else:
        assert session.events == ["snapshot", "issued_ticket_list"]
        assert client._active_session is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reservation_snapshot", "expected"),
    [
        (
            exact_reservation_list_snapshot(),
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
        ),
        (
            reservation_list_snapshot(explicit_empty_visible=True),
            ReservationConfirmationOutcome.NOT_FOUND,
        ),
    ],
)
async def test_payment_follow_up_uses_fresh_unpaid_list_after_no_issued_match(
    reservation_snapshot: PydollReservationListSnapshot,
    expected: ReservationConfirmationOutcome,
) -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        reservation_list_snapshot=reservation_snapshot,
        issued_ticket_snapshot=issued_ticket_list_snapshot(empty_state_visible=True),
    )
    target = paid_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is expected
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issued_snapshot",
    [
        issued_ticket_list_snapshot(malformed_card_count=1),
        issued_ticket_list_snapshot(exact_issued_ticket(), exact_issued_ticket()),
        issued_ticket_list_snapshot(exact_issued_ticket(train_number="0047")),
        issued_ticket_list_snapshot(
            url="https://www.korail.com/ticket/reservation/list",
        ),
    ],
)
async def test_payment_follow_up_does_not_accept_unpaid_absence_after_unsafe_issued_probe(
    issued_snapshot: PydollIssuedTicketListSnapshot,
) -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        reservation_list_snapshot=reservation_list_snapshot(explicit_empty_visible=True),
        issued_ticket_snapshot=issued_snapshot,
    )
    target = paid_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    duplicate_exact = len(issued_snapshot.tickets) > 1
    assert result.diagnostic_code is (
        ReservationConfirmationDiagnosticCode.OFFICIAL_RECORD_AMBIGUOUS
        if duplicate_exact
        else ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    assert session.events == [
        "snapshot",
        "issued_ticket_list",
        *([] if duplicate_exact else ["reservation_list"]),
    ]


@pytest.mark.asyncio
async def test_payment_follow_up_snapshot_failure_still_uses_fresh_unpaid_list() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        reservation_list_snapshot=exact_reservation_list_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(empty_state_visible=True),
        snapshot_error=RuntimeError("synthetic opaque detail read failure"),
    )
    target = paid_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        BrowserProtectionDetected(stage="confirmation_read"),
        BrowserRateLimited(),
    ],
    ids=("protected", "rate_limited"),
)
@pytest.mark.parametrize(
    "surface",
    ["detail", "reservation_list", "issued_ticket_list"],
)
async def test_confirmation_reader_propagates_explicit_provider_failures(
    error: Exception,
    surface: str,
) -> None:
    target = confirmation_target()
    options: dict[str, object] = {}
    if surface == "detail":
        options["snapshot_error"] = error
    elif surface == "reservation_list":
        options["reservation_list_error"] = error
    else:
        target = paid_follow_up_target()
        options["issued_ticket_error"] = error
    client, session = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        **options,  # type: ignore[arg-type]
    )

    with pytest.raises(type(error)) as captured:
        await client.read_reservation_detail(target)

    assert captured.value is error
    assert (
        session.events
        == {
            "detail": ["snapshot", "close"],
            "reservation_list": ["snapshot", "reservation_list", "close"],
            "issued_ticket_list": ["snapshot", "issued_ticket_list", "close"],
        }[surface]
    )
    assert client._active_session is None
    assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.BLOCKED


@pytest.mark.asyncio
async def test_confirmation_reader_uses_list_after_detail_source_unavailable() -> None:
    source_error = BrowserSourceUnavailable("detail_snapshot")
    client, session = await detail_client(
        exact_detail_snapshot(),
        snapshot_error=source_error,
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.payment_pending_markers_present is True
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
async def test_confirmation_reader_uses_list_after_generic_detail_snapshot_failure() -> None:
    secret = "generic-detail-secret-must-not-escape"
    client, session = await detail_client(
        exact_detail_snapshot(),
        snapshot_error=RuntimeError(secret),
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )

    evidence = await client.read_reservation_detail(confirmation_target())
    result = normalize_korail_same_session_detail(confirmation_target(), evidence)

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert result.diagnostic_code is None
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
async def test_complete_empty_list_without_arrival_stays_evidence_insufficient() -> None:
    client, session = await detail_client(
        PydollPageSnapshot(
            body_text="예약 상세를 판독하지 못했습니다",
            rows=(),
            url="https://www.korail.com/ticket/reservation/detail",
        ),
        reservation_list_snapshot=reservation_list_snapshot(explicit_empty_visible=True),
    )
    target = replace(confirmation_target(), arrival_at=None)

    evidence = await client.read_reservation_detail(target)
    result = normalize_korail_same_session_detail(target, evidence)

    assert evidence.official_list_read_completed is True
    assert evidence.official_list_target_absent is False
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert (
        result.diagnostic_code
        is ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
async def test_confirmation_reader_uses_list_after_login_attestation_is_unavailable() -> None:
    source_error = BrowserSourceUnavailable("session_keepalive")
    client, session = await detail_client(
        exact_detail_snapshot(url="https://www.korail.com/ticket/login"),
        official_session_error=source_error,
        header_authenticated=False,
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.payment_pending_markers_present is True
    assert session.events == [
        "snapshot",
        "login_check",
        "auth_header",
        "reservation_list",
    ]


@pytest.mark.asyncio
async def test_confirmation_reader_reraises_source_error_without_conclusive_list() -> None:
    source_error = BrowserSourceUnavailable("detail_snapshot")
    client, session = await detail_client(
        exact_detail_snapshot(),
        snapshot_error=source_error,
        reservation_list_snapshot=reservation_list_snapshot(loading_visible=True),
    )

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await client.read_reservation_detail(confirmation_target())

    assert captured.value is source_error
    assert session.events == ["snapshot", "reservation_list", "close"]
    assert client._active_session is None
    assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.STALE


@pytest.mark.asyncio
async def test_confirmation_reader_wraps_generic_list_failure_with_closed_stage() -> None:
    secret = "generic-reservation-list-secret-must-not-escape"
    client, session = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_error=RuntimeError(secret),
    )

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await client.read_reservation_detail(confirmation_target())

    assert captured.value.stage == "confirmation_reservation_list"
    assert secret not in str(captured.value)
    assert session.events == ["snapshot", "reservation_list", "close"]
    assert client._active_session is None
    assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.STALE


@pytest.mark.asyncio
async def test_payment_follow_up_uses_issued_ticket_after_detail_source_unavailable() -> None:
    source_error = BrowserSourceUnavailable("detail_snapshot")
    client, session = await detail_client(
        exact_detail_snapshot(),
        snapshot_error=source_error,
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )
    target = paid_follow_up_target()

    evidence = await client.read_reservation_detail(target)

    assert evidence.issued_ticket_exact_match is True
    assert session.events == ["snapshot", "issued_ticket_list"]


@pytest.mark.asyncio
async def test_payment_follow_up_uses_list_after_issued_source_unavailable() -> None:
    source_error = BrowserSourceUnavailable("issued_ticket_read")
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_error=source_error,
        reservation_list_snapshot=exact_reservation_list_snapshot(),
    )
    target = paid_follow_up_target()

    evidence = await client.read_reservation_detail(target)

    assert evidence.source == "korail-reservation-list"
    assert evidence.payment_pending_markers_present is True
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list"]


@pytest.mark.asyncio
async def test_payment_follow_up_reraises_issued_source_error_without_conclusive_list() -> None:
    source_error = BrowserSourceUnavailable("issued_ticket_read")
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_error=source_error,
        reservation_list_snapshot=reservation_list_snapshot(loading_visible=True),
    )
    target = paid_follow_up_target()

    with pytest.raises(BrowserSourceUnavailable) as captured:
        await client.read_reservation_detail(target)

    assert captured.value is source_error
    assert session.events == ["snapshot", "issued_ticket_list", "reservation_list", "close"]
    assert client._active_session is None
    assert client.session_snapshot().state is pydoll_module.KorailSessionActorState.STALE


@pytest.mark.asyncio
async def test_initial_confirmation_never_reads_issued_ticket_list() -> None:
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.source == "korail-same-session-detail"
    assert evidence.payment_pending_markers_present is True
    assert session.events == ["snapshot"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ticket",
    [
        exact_issued_ticket(service_date=date(2026, 8, 4)),
        exact_issued_ticket(train_number="0047"),
        exact_issued_ticket(origin="서울숲"),
        exact_issued_ticket(destination="부산진"),
        exact_issued_ticket(departure_time=time(15, 55)),
        exact_issued_ticket(arrival_time=time(18, 22)),
        exact_issued_ticket(seat_class="first"),
        exact_issued_ticket(passenger_count=2),
        exact_issued_ticket(car_number="5"),
        exact_issued_ticket(seat_number="8B"),
        exact_issued_ticket(returned=True),
        exact_issued_ticket(operation_stopped=True),
        exact_issued_ticket(transferred=True),
    ],
)
async def test_payment_follow_up_rejects_every_inexact_or_invalidated_ticket(
    ticket: PydollIssuedTicketSummary,
) -> None:
    client, _ = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(ticket),
    )
    target = paid_follow_up_target()

    evidence = await client.read_reservation_detail(target)
    result = normalize_korail_same_session_detail(target, evidence)

    assert evidence.source != "korail-issued-ticket-list"
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        issued_ticket_list_snapshot(exact_issued_ticket(), exact_issued_ticket()),
        issued_ticket_list_snapshot(malformed_card_count=1),
        issued_ticket_list_snapshot(empty_state_visible=True),
        issued_ticket_list_snapshot(
            exact_issued_ticket(),
            url="https://www.korail.com/ticket/reservation/list",
        ),
    ],
)
async def test_payment_follow_up_duplicate_malformed_empty_or_wrong_route_is_not_paid(
    snapshot: PydollIssuedTicketListSnapshot,
) -> None:
    client, _ = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=snapshot,
    )
    target = paid_follow_up_target()

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_payment_follow_up_issued_login_and_protection_fail_closed() -> None:
    login_client, _ = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(
            url="https://www.korail.com/ticket/login"
        ),
    )
    blocked_client, _ = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(
            protection_detected=True,
        ),
    )
    target = paid_follow_up_target()

    login = normalize_korail_same_session_detail(
        target,
        await login_client.read_reservation_detail(target),
    )
    blocked = normalize_korail_same_session_detail(
        target,
        await blocked_client.read_reservation_detail(target),
    )

    assert login.outcome is ReservationConfirmationOutcome.AUTH_REQUIRED
    assert blocked.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED


@pytest.mark.asyncio
async def test_no_seat_follow_up_keeps_issued_and_unpaid_absence_inconclusive() -> None:
    target = confirmation_target(purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP)
    client, session = await detail_client(
        exact_detail_snapshot(),
        reservation_list_snapshot=reservation_list_snapshot(explicit_empty_visible=True),
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
async def test_follow_up_without_seat_keeps_fresh_unpaid_hold_payment_required() -> None:
    target = confirmation_target(purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP)
    client, session = await detail_client(
        exact_detail_snapshot(),
        reservation_list_snapshot=exact_reservation_list_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
async def test_follow_up_without_seat_does_not_combine_issued_core_with_loading_empty() -> None:
    target = confirmation_target(purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP)
    client, session = await detail_client(
        exact_detail_snapshot(),
        reservation_list_snapshot=reservation_list_snapshot(
            explicit_empty_visible=True,
            loading_visible=True,
        ),
        issued_ticket_snapshot=issued_ticket_list_snapshot(exact_issued_ticket()),
    )

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert session.events == ["snapshot", "reservation_list"]


@pytest.mark.asyncio
async def test_follow_up_without_seat_does_not_probe_issued_cards() -> None:
    target = confirmation_target(purpose=ReservationConfirmationPurpose.PAYMENT_FOLLOW_UP)
    client, session = await detail_client(
        exact_detail_snapshot(),
        issued_ticket_snapshot=issued_ticket_list_snapshot(
            exact_issued_ticket(),
            exact_issued_ticket(car_number="5", seat_number="9B"),
        ),
    )

    result = normalize_korail_same_session_detail(
        target,
        await client.read_reservation_detail(target),
    )

    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert session.events == ["snapshot", "reservation_list"]


def official_screenshot_target() -> ReservationConfirmationTarget:
    return ReservationConfirmationTarget(
        attempt_id="attempt-official-list",
        candidate_id="candidate-official-list",
        provider=Provider.KORAIL,
        train_number="020",
        origin="대전",
        destination="서울",
        departure_at=datetime(2026, 8, 4, 10, 36, tzinfo=KOREA),
        arrival_at=datetime(2026, 8, 4, 11, 49, tzinfo=KOREA),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        credential_version=7,
    )


def official_screenshot_row() -> str:
    return (
        "KTX 020 대전 → 서울 2026년08월04일(화) 10:36 → 11:49 "
        "1매 결제기한: 2026. 08. 04 17:35 "
        "예약취소 예약변경 결제/발권"
    )


@pytest.mark.parametrize(
    "deadline_text",
    [
        "결제기한: 2026.08.03.01:26",
        "결제기한: 2026. 08. 03. 01:26",
        "결제기한: 2026. 08. 03 01:26",
    ],
)
def test_korail_payment_deadline_accepts_official_dot_separators(
    deadline_text: str,
) -> None:
    assert pydoll_module._parse_korail_payment_deadline(deadline_text) == datetime(
        2026, 8, 3, 1, 26, tzinfo=KOREA
    )


@pytest.mark.asyncio
async def test_pydoll_reservation_list_accepts_real_pending_action_triplet() -> None:
    row = official_screenshot_row()
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(row),
    )

    evidence = await client.read_reservation_detail(official_screenshot_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.exact_identity_matched is True
    assert evidence.seat_class_matched is False
    assert evidence.seat_class_match_required is False
    assert evidence.passenger_count_matched is True
    assert evidence.payment_pending_markers_present is True
    assert evidence.payment_deadline == datetime(2026, 8, 4, 17, 35, tzinfo=KOREA)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        (official_screenshot_row().replace("결제/발권", ""),),
        (official_screenshot_row().replace("1매", "2매"),),
        (official_screenshot_row(), official_screenshot_row()),
    ],
)
async def test_pydoll_reservation_list_rejects_missing_action_or_ambiguous_rows(
    rows: tuple[str, ...],
) -> None:
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=reservation_list_snapshot(*rows),
    )

    evidence = await client.read_reservation_detail(official_screenshot_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.exact_identity_matched is False
    assert evidence.payment_pending_markers_present is False


class FakeAutomation:
    async def close(self) -> None:
        return None


class FakeReservationClient:
    def __init__(self, evidence: KorailSameSessionDetailEvidence | Exception) -> None:
        self.evidence = evidence
        self.targets: list[ReservationConfirmationTarget] = []

    async def read_reservation_detail(
        self, target: ReservationConfirmationTarget
    ) -> KorailSameSessionDetailEvidence:
        self.targets.append(target)
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence


def test_confirmation_endpoint_requires_bearer_redacts_validation_and_sets_no_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "reservation-number-must-not-echo"
    reservation_client = FakeReservationClient(
        KorailSameSessionDetailEvidence(
            observed_at=datetime(2026, 8, 3, 7, tzinfo=UTC),
            credential_version=7,
            exact_identity_matched=True,
            payment_pending_markers_present=True,
            seat_class_matched=True,
            passenger_count_matched=True,
            source="korail-reservation-list",
        )
    )
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )
    invalid = {**confirmation_payload(), "reservation_number": secret}

    with caplog.at_level(logging.INFO), TestClient(app) as http:
        unauthorized = http.post("/v1/confirm-reservation", json=confirmation_payload())
        rejected = http.post(
            "/v1/confirm-reservation",
            json=invalid,
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )
        accepted = http.post(
            "/v1/confirm-reservation",
            json=confirmation_payload(),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert unauthorized.status_code == 401
    assert rejected.status_code == 422
    assert rejected.json() == {"detail": "request_validation_failed"}
    assert accepted.status_code == 200
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.json()["outcome"] == "confirmed_payment_required"
    assert accepted.json()["source"] == "korail-reservation-list"
    assert (
        "KORAIL reservation confirmation completed purpose=initial "
        "outcome=confirmed_payment_required diagnostic_code=none "
        "source=korail-reservation-list phase=completed"
    ) in caplog.text
    assert secret not in rejected.text
    assert secret not in caplog.text
    assert reservation_client.targets == [confirmation_target()]


class _SecretBearingSourceUnavailable(BrowserSourceUnavailable):
    def __init__(self, stage: str, secret: str) -> None:
        super().__init__(stage)
        self.secret = secret

    def __str__(self) -> str:
        return f"raw provider exception containing {self.secret}"


@pytest.mark.parametrize(
    ("stage", "expected_stage"),
    [
        ("session_keepalive", "session_keepalive"),
        ("confirmation_reservation_list", "confirmation_reservation_list"),
        ("credential=confirmation-secret-must-not-appear", "unspecified"),
    ],
    ids=("safe_stage", "reservation_list_stage", "unsafe_stage"),
)
def test_confirmation_endpoint_reports_source_unavailable_without_raw_exception_log(
    caplog: pytest.LogCaptureFixture,
    stage: str,
    expected_stage: str,
) -> None:
    secret = "confirmation-secret-must-not-appear"
    reservation_client = FakeReservationClient(_SecretBearingSourceUnavailable(stage, secret))
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )

    with caplog.at_level(logging.INFO), TestClient(app) as http:
        response = http.post(
            "/v1/confirm-reservation",
            json=confirmation_payload(),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "inconclusive"
    assert response.json()["diagnostic_code"] == "official_read_unavailable"
    assert response.json()["source"] == "korail-same-session-detail"
    failure_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("KORAIL reservation confirmation failed")
    ]
    assert len(failure_records) == 1
    assert re.fullmatch(
        "KORAIL reservation confirmation failed "
        "failure=source_unavailable diagnostic_code=official_read_unavailable "
        f"phase=official_read stage={expected_stage} "
        r"operation=confirm_reservation request_id=[0-9a-f]{32}",
        failure_records[0].getMessage(),
    )
    assert failure_records[0].levelno == logging.ERROR
    assert secret not in caplog.text
    assert "raw provider exception" not in caplog.text
    assert reservation_client.targets == [confirmation_target()]


@pytest.mark.parametrize(
    ("phase", "expected_diagnostic"),
    [
        ("official_read", "official_read_unavailable"),
        ("evidence_normalization", "official_evidence_insufficient"),
    ],
)
def test_confirmation_endpoint_logs_closed_phase_for_redacted_generic_errors(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expected_diagnostic: str,
) -> None:
    secret = "generic-confirmation-secret-must-not-appear"
    evidence = KorailSameSessionDetailEvidence(
        observed_at=datetime(2026, 8, 3, 7, tzinfo=UTC),
        credential_version=7,
        exact_identity_matched=False,
        payment_pending_markers_present=False,
    )
    reservation_client = FakeReservationClient(
        RuntimeError(secret) if phase == "official_read" else evidence
    )
    if phase == "evidence_normalization":

        def fail_normalization(*_args: object) -> object:
            raise RuntimeError(secret)

        monkeypatch.setattr(
            korail_http_module,
            "normalize_korail_same_session_detail",
            fail_normalization,
        )
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )

    with caplog.at_level(logging.INFO), TestClient(app) as http:
        response = http.post(
            "/v1/confirm-reservation",
            json=confirmation_payload(),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "inconclusive"
    assert response.json()["diagnostic_code"] == expected_diagnostic
    assert f"diagnostic_code={expected_diagnostic} phase={phase}" in caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        BrowserProtectionDetected(stage="session_keepalive"),
        BrowserRateLimited(),
    ],
    ids=("protected", "rate_limited"),
)
def test_confirmation_endpoint_maps_explicit_provider_failures_to_blocked(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    reservation_client = FakeReservationClient(error)
    app = create_adapter_app(
        automation=FakeAutomation(),
        reservation_client=reservation_client,
        token="t" * 32,
        readiness_probe=AsyncMock(return_value=None),
    )

    with caplog.at_level(logging.INFO), TestClient(app) as http:
        response = http.post(
            "/v1/confirm-reservation",
            json=confirmation_payload(),
            headers={"Authorization": f"Bearer {'t' * 32}"},
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "provider_blocked"
    assert response.json()["diagnostic_code"] is None
    assert response.json()["source"] == "korail-same-session-detail"
    failure_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith("KORAIL reservation confirmation failed")
    ]
    assert len(failure_records) == 1
    assert re.fullmatch(
        "KORAIL reservation confirmation failed failure=provider_blocked "
        "diagnostic_code=none phase=official_read "
        r"operation=confirm_reservation request_id=[0-9a-f]{32}",
        failure_records[0].getMessage(),
    )
    assert failure_records[0].levelno == logging.WARNING
    assert reservation_client.targets == [confirmation_target()]


def test_confirmation_dto_rejects_extra_and_inconsistent_fields() -> None:
    base = {
        "outcome": "inconclusive",
        "diagnostic_code": "official_evidence_insufficient",
        "source": "korail-same-session-detail",
        "observed_at": datetime(2026, 8, 3, 7, tzinfo=UTC),
    }

    with pytest.raises(ValidationError):
        KorailReservationConfirmationResult.model_validate({**base, "raw_dom": "private"})
    with pytest.raises(ValidationError):
        KorailReservationConfirmationResult.model_validate(
            {**base, "official_handoff_url": "https://www.korail.com/ticket/mypage/mykorail"}
        )
    with pytest.raises(ValidationError, match="requires a diagnostic code"):
        KorailReservationConfirmationResult.model_validate(
            {key: value for key, value in base.items() if key != "diagnostic_code"}
        )
    with pytest.raises(ValidationError, match="requires a diagnostic code"):
        KorailReservationConfirmationResult.model_validate(
            {
                **base,
                "outcome": "not_found",
            }
        )


class FakeConfirmationTransport:
    def __init__(self, result: KorailReservationConfirmationResult | Exception) -> None:
        self.result = result
        self.requests: list[object] = []

    async def confirm_reservation(self, request: object) -> KorailReservationConfirmationResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def confirmation_source(transport: FakeConfirmationTransport) -> KorailBrowserSeatSource:
    return KorailBrowserSeatSource(
        enabled=True,
        adapter_url="http://adapter.invalid",
        cache_ttl_seconds=30,
        timeout_seconds=35,
        rate_limit_cooldown_seconds=300,
        protection_cooldown_seconds=300,
        transport=transport,
    )


@pytest.mark.asyncio
async def test_source_preserves_exact_request_generation_and_protection_outcome() -> None:
    transport = FakeConfirmationTransport(
        KorailReservationConfirmationResult(
            outcome="inconclusive",
            diagnostic_code="official_evidence_insufficient",
            source="korail-same-session-detail",
            observed_at=datetime(2026, 8, 3, 7, tzinfo=UTC),
        )
    )
    result = await confirmation_source(transport).confirm_reservation(confirmation_target())
    blocked = await confirmation_source(
        FakeConfirmationTransport(_AdapterFailure("provider_access_restricted", protection=True))
    ).confirm_reservation(confirmation_target())

    request = transport.requests[0]
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert (
        result.diagnostic_code
        is ReservationConfirmationDiagnosticCode.OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    assert request.credential_version == 7
    assert request.train_number == "43"
    assert request.departure_at == confirmation_target().departure_at
    assert blocked.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED
