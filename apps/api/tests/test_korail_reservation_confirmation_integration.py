from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time
from typing import Self
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import rail_waitlist.korail_pydoll_browser as pydoll_module
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
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationTarget,
)

KOREA = ZoneInfo("Asia/Seoul")


def confirmation_target(*, credential_version: int = 7) -> ReservationConfirmationTarget:
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
    }


class ReadOnlyDetailSession:
    def __init__(
        self,
        snapshot: PydollPageSnapshot,
        *,
        reservation_list_snapshot: PydollPageSnapshot | None = None,
        officially_authenticated: bool = False,
        header_authenticated: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.reservation_list_snapshot = reservation_list_snapshot
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
        return self.snapshot

    async def _probe_official_authenticated_session(self) -> bool:
        self.events.append("login_check")
        return self.officially_authenticated

    async def _has_authenticated_header(self) -> bool:
        self.events.append("auth_header")
        return self.header_authenticated

    async def read_reservation_list(self) -> PydollPageSnapshot:
        self.events.append("reservation_list")
        if self.reservation_list_snapshot is None:
            raise RuntimeError("reservation list fixture not configured")
        return self.reservation_list_snapshot


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
    reservation_list_snapshot: PydollPageSnapshot | None = None,
    officially_authenticated: bool = False,
    header_authenticated: bool = False,
) -> tuple[PydollKorailBrowserClient, ReadOnlyDetailSession]:
    session = ReadOnlyDetailSession(
        snapshot,
        reservation_list_snapshot=reservation_list_snapshot,
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

    confirmation_task = asyncio.create_task(
        client.read_reservation_detail(confirmation_target())
    )
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
async def test_pydoll_confirmation_without_active_session_is_inconclusive() -> None:
    client = PydollKorailBrowserClient(
        session_factory=lambda *_: ReadOnlyDetailSession(exact_detail_snapshot()),
        session_reuse_ttl_seconds=60,
        session_reuse_max_searches=5,
    )

    evidence = await client.read_reservation_detail(confirmation_target())

    assert evidence.credential_version is None
    assert evidence.auth_required is False


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
    assert login_session.events == ["snapshot", "login_check", "auth_header"]
    assert blocked_session.events == ["snapshot"]


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
    assert session.events == [
        "snapshot",
        "login_check",
        "auth_header",
        "reservation_list",
    ]


def exact_reservation_list_snapshot(
    *,
    row: str | None = None,
    url: str = "https://www.korail.com/ticket/reservation/list",
) -> PydollPageSnapshot:
    exact_row = row or (
        "승차권 예약 KTX 0043 서울역 → 부산역 "
        "2026년08월03일(월) 15:45 → 18:12 1매 "
        "예약취소 예약변경 결제/발권 결제기한: 2026. 08. 03 16:30"
    )
    return PydollPageSnapshot(
        body_text=f"예약 승차권 조회 {exact_row}",
        rows=(),
        url=url,
        reservation_rows=(exact_row,),
    )


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
        exact_reservation_list_snapshot().reservation_rows[0].replace(
            "2026년08월03일", "2026년08월04일", 1
        ),
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
        reservation_list_snapshot=PydollPageSnapshot(
            body_text="예약 승차권 조회 결제기한이 지난 목록은 자동 삭제됨",
            rows=(),
            url="https://www.korail.com/ticket/reservation/list",
            reservation_rows=(),
        ),
    )

    evidence = await client.read_reservation_detail(confirmation_target())
    result = normalize_korail_same_session_detail(confirmation_target(), evidence)

    assert evidence.source == "korail-reservation-list"
    assert evidence.official_list_read_completed is True
    assert evidence.official_list_target_absent is True
    assert result.outcome is ReservationConfirmationOutcome.NOT_FOUND
    assert not result.permits_automatic_reservation_retry


@pytest.mark.asyncio
async def test_pydoll_duplicate_exact_reservation_rows_remain_inconclusive() -> None:
    exact = exact_reservation_list_snapshot()
    client, _ = await detail_client(
        PydollPageSnapshot(body_text="다른 화면", rows=(), url="https://www.korail.com/"),
        reservation_list_snapshot=PydollPageSnapshot(
            body_text=exact.body_text,
            rows=(),
            url=exact.url,
            reservation_rows=(exact.reservation_rows[0], exact.reservation_rows[0]),
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
        reservation_list_snapshot=PydollPageSnapshot(
            body_text="CODE -8003",
            rows=(),
            url="https://www.korail.com/ticket/reservation/list",
        ),
    )

    login = await login_client.read_reservation_detail(confirmation_target())
    blocked = await blocked_client.read_reservation_detail(confirmation_target())

    assert login.auth_required is True
    assert blocked.provider_blocked is True


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
        reservation_list_snapshot=PydollPageSnapshot(
            body_text=f"예약 승차권 조회 {row}",
            rows=(),
            url="https://www.korail.com/ticket/reservation/list",
            reservation_rows=(row,),
        ),
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
        reservation_list_snapshot=PydollPageSnapshot(
            body_text="예약 승차권 조회",
            rows=(),
            url="https://www.korail.com/ticket/reservation/list",
            reservation_rows=rows,
        ),
    )

    evidence = await client.read_reservation_detail(official_screenshot_target())

    assert evidence.source == "korail-reservation-list"
    assert evidence.exact_identity_matched is False
    assert evidence.payment_pending_markers_present is False


class FakeAutomation:
    async def close(self) -> None:
        return None


class FakeReservationClient:
    def __init__(self, evidence: KorailSameSessionDetailEvidence) -> None:
        self.evidence = evidence
        self.targets: list[ReservationConfirmationTarget] = []

    async def read_reservation_detail(
        self, target: ReservationConfirmationTarget
    ) -> KorailSameSessionDetailEvidence:
        self.targets.append(target)
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
    assert secret not in rejected.text
    assert secret not in caplog.text
    assert reservation_client.targets == [confirmation_target()]


def test_confirmation_dto_rejects_extra_and_inconsistent_fields() -> None:
    base = {
        "outcome": "inconclusive",
        "source": "korail-same-session-detail",
        "observed_at": datetime(2026, 8, 3, 7, tzinfo=UTC),
    }

    with pytest.raises(ValidationError):
        KorailReservationConfirmationResult.model_validate({**base, "raw_dom": "private"})
    with pytest.raises(ValidationError):
        KorailReservationConfirmationResult.model_validate(
            {**base, "official_handoff_url": "https://www.korail.com/ticket/mypage/mykorail"}
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
            source="korail-same-session-detail",
            observed_at=datetime(2026, 8, 3, 7, tzinfo=UTC),
        )
    )
    result = await confirmation_source(transport).confirm_reservation(confirmation_target())
    blocked = await confirmation_source(
        FakeConfirmationTransport(
            _AdapterFailure("provider_access_restricted", protection=True)
        )
    ).confirm_reservation(confirmation_target())

    request = transport.requests[0]
    assert result.outcome is ReservationConfirmationOutcome.INCONCLUSIVE
    assert request.credential_version == 7
    assert request.train_number == "43"
    assert request.departure_at == confirmation_target().departure_at
    assert blocked.outcome is ReservationConfirmationOutcome.PROVIDER_BLOCKED
