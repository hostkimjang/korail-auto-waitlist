from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import pytest
from SRT import SeatType, SRTNotLoggedInError
from SRT.errors import SRTNetFunnelError
from SRT.reservation import SRTReservation

from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationTarget,
)
from rail_waitlist.schemas import ReservationRequest
from rail_waitlist.srt_reservation import (
    SrtReservationExecutor,
    SrtSessionActorState,
    verify_srt_credentials_once,
)

KOREA = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Credentials:
    login_id: str = "1234567890"
    password: str = "not-a-real-password"
    credential_version: int = 1
    login_method: Literal["membership_number", "email", "phone"] = "membership_number"


@dataclass
class FakeTrain:
    train_number: str = "00329"
    dep_date: str = "20260801"
    dep_time: str = "130900"
    dep_station_name: str = "대전"
    arr_station_name: str = "부산"
    general_available: bool = True
    special_available: bool = True

    def general_seat_available(self) -> bool:
        return self.general_available

    def special_seat_available(self) -> bool:
        return self.special_available


@dataclass
class FakeTicket:
    seat_type_code: str = "1"


@dataclass
class FakeReservation:
    train_number: str = "00329"
    dep_date: str = "20260801"
    dep_time: str = "130900"
    dep_station_name: str = "대전"
    arr_station_name: str = "부산"
    payment_date: str = "20991231"
    payment_time: str = "235900"
    paid: bool = False
    seat_count: int = 1
    tickets: list[FakeTicket] = field(default_factory=lambda: [FakeTicket()])


class FakeClient:
    def __init__(
        self,
        *,
        trains: list[FakeTrain] | None = None,
        expire_search_once: bool = False,
        authenticated: bool = True,
    ) -> None:
        self.is_login = authenticated
        self.trains = trains if trains is not None else [FakeTrain()]
        self.expire_search_once = expire_search_once
        self.search_calls = 0
        self.reserve_calls = 0
        self.seat_types: list[SeatType] = []

    def search_train(self, *_args, **_kwargs):
        self.search_calls += 1
        if self.expire_search_once:
            self.expire_search_once = False
            raise SRTNotLoggedInError()
        return self.trains

    def reserve(self, _train, *, passengers, special_seat, window_seat=None):
        self.reserve_calls += 1
        self.seat_types.append(special_seat)
        assert passengers[0].count == 1
        assert window_seat is None
        return FakeReservation(
            tickets=[
                FakeTicket(seat_type_code=("2" if special_seat is SeatType.SPECIAL_ONLY else "1"))
            ]
        )

    def get_reservations(self, paid_only=False):
        reservation = FakeReservation()
        return [] if paid_only and not reservation.paid else [reservation]


def test_login_verification_only_creates_authenticated_client():
    client = FakeClient(trains=[])
    factory_inputs: list[tuple[str, str]] = []

    def factory(login_id: str, password: str):
        factory_inputs.append((login_id, password))
        return client

    authenticated = verify_srt_credentials_once(
        Credentials(login_id="01012345678", login_method="phone"),
        factory,
    )

    assert authenticated is True
    assert factory_inputs == [("010-1234-5678", "not-a-real-password")]
    assert client.search_calls == 0
    assert client.reserve_calls == 0


def request(seat_class: SeatClass = SeatClass.STANDARD) -> ReservationRequest:
    return ReservationRequest(
        provider=Provider.SRT,
        origin_node_id="0010",
        destination_node_id="0020",
        origin="대전",
        destination="부산",
        train_number="329",
        departure_at=datetime(2026, 8, 1, 13, 9, tzinfo=KOREA),
        seat_class=seat_class,
        passenger_count=1,
        candidate_id="candidate-1",
        idempotency_key="reserve:candidate-1",
    )


async def test_exact_available_train_is_reserved_once_and_returns_real_deadline():
    client = FakeClient()
    factory_calls = 0

    def factory(_login_id: str, _password: str):
        nonlocal factory_calls
        factory_calls += 1
        return client

    executor = SrtReservationExecutor(factory)
    result = await executor.reserve_once(request(), Credentials())
    second = await executor.reserve_once(request(SeatClass.FIRST), Credentials())

    assert result.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert result.payment_deadline == datetime(2099, 12, 31, 23, 59, tzinfo=KOREA)
    assert str(result.official_handoff_url).startswith("https://etk.srail.kr/")
    assert second.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert factory_calls == 1
    assert client.reserve_calls == 2
    assert client.seat_types == [SeatType.GENERAL_ONLY, SeatType.SPECIAL_ONLY]


@pytest.mark.parametrize(
    "reservation",
    [
        FakeReservation(tickets=[FakeTicket(seat_type_code="2")]),
        FakeReservation(tickets=[]),
        FakeReservation(seat_count=2),
        FakeReservation(seat_count=0),
    ],
)
async def test_direct_reserve_result_requires_exact_seat_class_and_passenger_count(
    reservation: FakeReservation,
) -> None:
    class ResultClient(FakeClient):
        def reserve(self, _train, *, passengers, special_seat, window_seat=None):
            self.reserve_calls += 1
            return reservation

    client = ResultClient()
    executor = SrtReservationExecutor(lambda _login_id, _password: client)

    result = await executor.reserve_once(request(), Credentials())

    assert result.outcome is ReservationOutcome.UNKNOWN
    assert result.payment_deadline is None
    assert result.official_handoff_url is None
    assert client.reserve_calls == 1


async def test_read_only_srt_list_confirms_class_passengers_and_deadline() -> None:
    client = FakeClient()
    executor = SrtReservationExecutor(lambda _login_id, _password: client)
    departure = request().departure_at
    confirmation = await executor.confirm_reservation(
        ReservationConfirmationTarget(
            attempt_id="attempt-1",
            candidate_id="candidate-1",
            provider=Provider.SRT,
            train_number="329",
            origin="대전",
            destination="부산",
            departure_at=departure,
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            credential_version=1,
        ),
        Credentials(),
    )

    assert confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert confirmation.payment_deadline == datetime(2099, 12, 31, 23, 59, tzinfo=KOREA)


async def test_read_only_list_recovers_deadline_from_srtrain_official_fields() -> None:
    """Lock the SRTrain 2.6.7 ``iseLmtDt``/``iseLmtTm`` mapping used in production.

    ``get_reservations`` constructs this public record from the official SRT ticket-list
    response.  Exercising that real dependency model prevents a fixture-only field rename
    from silently degrading an existing unpaid reservation to ``deadline unavailable``.
    """

    reservation = SRTReservation(
        {
            "pnrNo": "redacted-test-reservation",
            "rcvdAmt": "32600",
            "tkSpecNum": "1",
        },
        {
            "stlbTrnClsfCd": "17",
            "trnNo": "00329",
            "dptDt": "20260801",
            "dptTm": "130900",
            "dptRsStnCd": "0010",
            "arvTm": "153400",
            "arvRsStnCd": "0020",
            "iseLmtDt": "20260801",
            "iseLmtTm": "134800",
            "stlFlg": "N",
        },
        [FakeTicket(seat_type_code="1")],
    )

    class OfficialRecordClient(FakeClient):
        def get_reservations(self, paid_only=False):
            return [] if paid_only else [reservation]

    executor = SrtReservationExecutor(lambda _login_id, _password: OfficialRecordClient())
    confirmation = await executor.confirm_reservation(
        ReservationConfirmationTarget(
            attempt_id="attempt-1",
            candidate_id="candidate-1",
            provider=Provider.SRT,
            train_number="329",
            origin="대전",
            destination="부산",
            departure_at=request().departure_at,
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            credential_version=1,
        ),
        Credentials(),
    )

    assert confirmation.outcome is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    assert confirmation.payment_deadline == datetime(2026, 8, 1, 13, 48, tzinfo=KOREA)


async def test_expired_session_reinitializes_only_the_read_only_search():
    clients = [FakeClient(expire_search_once=True), FakeClient()]

    def factory(_login_id: str, _password: str):
        return clients.pop(0)

    executor = SrtReservationExecutor(factory)
    result = await executor.reserve_once(request(), Credentials())

    assert result.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert len(clients) == 0
    assert executor.session_snapshot().state is SrtSessionActorState.READY


async def test_session_actor_snapshot_distinguishes_local_reuse_from_last_verification():
    clients = [FakeClient(), FakeClient()]
    now = [10.0]
    executor = SrtReservationExecutor(
        lambda _login_id, _password: clients.pop(0),
        session_reuse_ttl_seconds=60,
        monotonic=lambda: now[0],
    )

    cold = executor.session_snapshot()
    assert cold.state is SrtSessionActorState.COLD
    assert cold.credential_generation is None
    assert cold.locally_reusable is False

    assert await executor.prewarm_credentials(Credentials(credential_version=4)) is True
    ready = executor.session_snapshot()
    assert ready.state is SrtSessionActorState.READY
    assert ready.credential_generation == 4
    assert ready.created_at_monotonic == 10.0
    assert ready.last_verified_at_monotonic == 10.0
    assert ready.last_used_at_monotonic == 10.0
    assert ready.local_reuse_until_monotonic == 70.0
    assert ready.locally_reusable is True
    assert "credential_fingerprint" not in repr(executor._active_session)

    now[0] = 20.0
    assert await executor.prewarm_credentials(Credentials(credential_version=4)) is True
    reused = executor.session_snapshot()
    assert reused.last_verified_at_monotonic == 10.0
    assert reused.last_used_at_monotonic == 20.0
    assert len(clients) == 1

    now[0] = 81.0
    stale = executor.session_snapshot()
    assert stale.state is SrtSessionActorState.STALE
    assert stale.locally_reusable is False
    assert await executor.prewarm_credentials(Credentials(credential_version=4)) is True
    refreshed = executor.session_snapshot()
    assert refreshed.state is SrtSessionActorState.READY
    assert refreshed.last_verified_at_monotonic == 81.0
    assert len(clients) == 0


async def test_same_generation_different_srt_credentials_force_a_new_login():
    clients = [FakeClient(), FakeClient()]
    factory_calls: list[str] = []

    def factory(login_id: str, _password: str):
        factory_calls.append(login_id)
        return clients.pop(0)

    executor = SrtReservationExecutor(factory)
    first = Credentials(credential_version=7)
    second = replace(
        first,
        login_method="email",
        login_id="other-fixture@example.test",
        password="other-fixture-password",
    )

    await executor.reserve_once(request(), first)
    await executor.reserve_once(request(SeatClass.FIRST), second)

    assert factory_calls == ["1234567890", "other-fixture@example.test"]
    assert len(clients) == 0


async def test_srt_prewarm_auth_failure_is_reported_without_a_retry():
    factory_calls = 0

    def factory(_login_id: str, _password: str):
        nonlocal factory_calls
        factory_calls += 1
        return FakeClient(authenticated=False)

    executor = SrtReservationExecutor(factory)

    assert await executor.prewarm_credentials(Credentials()) is False
    snapshot = executor.session_snapshot()
    assert snapshot.state is SrtSessionActorState.AUTH_REQUIRED
    assert snapshot.locally_reusable is False
    assert factory_calls == 1


async def test_srt_prewarm_protection_marks_the_actor_blocked_without_retry():
    factory_calls = 0

    def factory(_login_id: str, _password: str):
        nonlocal factory_calls
        factory_calls += 1
        raise SRTNetFunnelError("redacted fixture failure")

    executor = SrtReservationExecutor(factory)

    with pytest.raises(SRTNetFunnelError):
        await executor.prewarm_credentials(Credentials())

    assert executor.session_snapshot().state is SrtSessionActorState.BLOCKED
    assert factory_calls == 1


async def test_ambiguous_exact_match_fails_closed_without_reservation():
    client = FakeClient(trains=[FakeTrain(), FakeTrain()])
    executor = SrtReservationExecutor(lambda _login_id, _password: client)

    result = await executor.reserve_once(request(), Credentials())

    assert result.outcome is ReservationOutcome.UNKNOWN
    assert client.reserve_calls == 0


async def test_unavailable_requested_seat_does_not_call_reserve():
    client = FakeClient(trains=[FakeTrain(general_available=False)])
    executor = SrtReservationExecutor(lambda _login_id, _password: client)

    result = await executor.reserve_once(request(), Credentials())

    assert result.outcome is ReservationOutcome.NOT_AVAILABLE
    assert client.reserve_calls == 0


async def test_credential_version_change_discards_the_authenticated_session():
    clients = [FakeClient(), FakeClient()]
    created: list[FakeClient] = []

    def factory(_login_id: str, _password: str):
        client = clients.pop(0)
        created.append(client)
        return client

    executor = SrtReservationExecutor(factory)
    await executor.reserve_once(request(), Credentials(credential_version=1))
    await executor.reserve_once(request(), Credentials(credential_version=2))

    assert len(created) == 2
    assert [client.reserve_calls for client in created] == [1, 1]


async def test_session_ttl_uses_last_activity_before_creating_a_new_srt_client():
    clients = [FakeClient(), FakeClient()]
    now = [0.0]

    executor = SrtReservationExecutor(
        lambda _login_id, _password: clients.pop(0),
        session_reuse_ttl_seconds=60,
        monotonic=lambda: now[0],
    )

    await executor.reserve_once(request(), Credentials())
    now[0] = 59.0
    await executor.reserve_once(request(), Credentials())
    active_after_second_request = executor._active_session
    assert active_after_second_request is not None
    assert active_after_second_request.last_used_at == 59.0
    now[0] = 119.0
    await executor.reserve_once(request(), Credentials())

    assert len(clients) == 0


async def test_concurrent_srt_reservations_are_serialized_by_the_process_session_owner():
    class BlockingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
            self._guard = threading.Lock()
            self.active_searches = 0
            self.max_active_searches = 0

        def search_train(self, *_args, **_kwargs):
            with self._guard:
                self.active_searches += 1
                self.max_active_searches = max(self.max_active_searches, self.active_searches)
            self.entered.set()
            assert self.release.wait(timeout=2)
            with self._guard:
                self.active_searches -= 1
            return super().search_train(*_args, **_kwargs)

    client = BlockingClient()
    executor = SrtReservationExecutor(lambda _login_id, _password: client)
    first = asyncio.create_task(executor.reserve_once(request(), Credentials()))
    assert await asyncio.to_thread(client.entered.wait, 1)
    second = asyncio.create_task(executor.reserve_once(request(SeatClass.FIRST), Credentials()))
    await asyncio.sleep(0)
    assert client.max_active_searches == 1
    client.release.set()

    results = await asyncio.gather(first, second)

    assert [result.outcome for result in results] == [
        ReservationOutcome.PAYMENT_REQUIRED,
        ReservationOutcome.PAYMENT_REQUIRED,
    ]
    assert client.max_active_searches == 1


async def test_worker_restart_creates_a_fresh_srt_session_from_the_existing_credentials():
    clients = [FakeClient(), FakeClient()]
    factory_calls = 0

    def factory(_login_id: str, _password: str):
        nonlocal factory_calls
        factory_calls += 1
        return clients.pop(0)

    await SrtReservationExecutor(factory).reserve_once(request(), Credentials())
    await SrtReservationExecutor(factory).reserve_once(request(), Credentials())

    assert factory_calls == 2


@pytest.mark.parametrize(
    ("login_method", "login_id", "expected_login_id"),
    [
        ("membership_number", "1234567890", "1234567890"),
        ("email", "  member@example.com  ", "member@example.com"),
        ("phone", "01012345678", "010-1234-5678"),
    ],
)
async def test_client_factory_receives_identifier_canonicalized_for_login_method(
    login_method: Literal["membership_number", "email", "phone"],
    login_id: str,
    expected_login_id: str,
):
    client = FakeClient()
    factory_inputs: list[tuple[str, str]] = []

    def factory(canonical_login_id: str, password: str):
        factory_inputs.append((canonical_login_id, password))
        return client

    executor = SrtReservationExecutor(factory)
    result = await executor.reserve_once(
        request(),
        Credentials(login_id=login_id, login_method=login_method),
    )

    assert result.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert factory_inputs == [(expected_login_id, "not-a-real-password")]


@pytest.mark.parametrize(
    ("login_method", "login_id"),
    [
        ("membership_number", "member"),
        ("email", "not-an-email"),
        ("phone", "0101234567"),
        ("phone", "01112345678"),
        ("phone", "010-1234-5678"),
    ],
)
async def test_invalid_login_identifier_fails_closed_before_client_creation(
    login_method: Literal["membership_number", "email", "phone"],
    login_id: str,
):
    factory_calls = 0

    def factory(_login_id: str, _password: str):
        nonlocal factory_calls
        factory_calls += 1
        return FakeClient()

    executor = SrtReservationExecutor(factory)
    result = await executor.reserve_once(
        request(),
        Credentials(login_id=login_id, login_method=login_method),
    )

    assert result.outcome is ReservationOutcome.FAILED
    assert factory_calls == 0
