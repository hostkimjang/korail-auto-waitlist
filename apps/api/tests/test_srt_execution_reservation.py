from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from rail_waitlist.config import Settings
from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.provider_accounts import ProviderCredentials
from rail_waitlist.providers import SrtLiveExecutionAdapter
from rail_waitlist.schemas import ReservationRequest, ReservationResult

KOREA = ZoneInfo("Asia/Seoul")


class UnusedSeatSource:
    async def observation_deferred_until(self):
        return None

    async def observe(self, *_args, **_kwargs):
        raise AssertionError("reservation test must not perform a second seat observation")

    async def drain_pending_calls(self):
        return None


@dataclass
class RecordingReservationExecutor:
    calls: int = 0

    async def reserve_once(self, request, credentials):
        self.calls += 1
        assert request.train_number == "329"
        assert credentials.credential_version == 3
        now = datetime.now(KOREA)
        return ReservationResult(
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            source="test-srt-reservation",
            observed_at=now,
            payment_deadline=now + timedelta(minutes=15),
            official_handoff_url=(
                "https://etk.srail.kr/hpg/hra/02/"
                "selectReservationList.do?pageId=TK0102010000"
            ),
        )


def enabled_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "EXPERIMENTAL_RAIL_ENABLED": True,
        "srt_seat_status_enabled": True,
        "srt_seat_monitoring_enabled": True,
        "srt_reservation_once_enabled": True,
        **overrides,
    }
    return Settings(**values)


def reservation_request() -> ReservationRequest:
    return ReservationRequest(
        provider=Provider.SRT,
        origin_node_id="0010",
        destination_node_id="0020",
        origin="대전",
        destination="부산",
        train_number="329",
        departure_at=datetime(2026, 8, 1, 13, 9, tzinfo=KOREA),
        seat_class=SeatClass.STANDARD,
        passenger_count=1,
        candidate_id="candidate-1",
        idempotency_key="reserve:candidate-1",
    )


def test_srt_reservation_capability_requires_the_fourth_explicit_gate():
    disabled = SrtLiveExecutionAdapter(
        enabled_settings(srt_reservation_once_enabled=False),
        UnusedSeatSource(),
    )
    enabled = SrtLiveExecutionAdapter(enabled_settings(), UnusedSeatSource())

    assert disabled.capabilities().seat_monitoring is True
    assert disabled.capabilities().reservation_once is False
    assert enabled.capabilities().reservation_once is True


async def test_srt_reservation_requires_an_enabled_stored_account():
    executor = RecordingReservationExecutor()

    async def no_credentials(_provider):
        return None

    adapter = SrtLiveExecutionAdapter(
        enabled_settings(),
        UnusedSeatSource(),
        credential_loader=no_credentials,
        reservation_executor=executor,
    )

    result = await adapter.reserve_once(reservation_request())

    assert result.outcome is ReservationOutcome.AUTH_REQUIRED
    assert result.credential_version is None
    assert executor.calls == 0


async def test_srt_reservation_passes_decrypted_credentials_only_in_process():
    executor = RecordingReservationExecutor()

    async def credentials(provider):
        assert provider is Provider.SRT
        return ProviderCredentials("masked-at-api", "never-logged", 3)

    adapter = SrtLiveExecutionAdapter(
        enabled_settings(),
        UnusedSeatSource(),
        credential_loader=credentials,
        reservation_executor=executor,
    )

    result = await adapter.reserve_once(reservation_request())

    assert result.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert result.credential_version == 3
    assert executor.calls == 1
