from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rail_waitlist.domain import Provider, ReservationOutcome, SeatClass
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.schemas import ReservationResult
from rail_waitlist.worker import _CandidateTarget, _confirm_provider_reservation_result


@dataclass
class StubConfirmationAdapter:
    result: ReservationConfirmationResult
    calls: int = 0

    async def confirm_reservation(self, target):
        self.calls += 1
        return self.result


def target() -> _CandidateTarget:
    departure = datetime(2026, 8, 3, 13, 9, tzinfo=UTC)
    return _CandidateTarget(
        watch_id="watch-1",
        candidate_id="candidate-1",
        provider=Provider.SRT,
        origin="대전",
        destination="부산",
        origin_node_id="origin-node",
        destination_node_id="destination-node",
        train_number="329",
        departure_at=departure,
        arrival_at=departure + timedelta(hours=2),
        seat_class=SeatClass.STANDARD.value,
        passenger_count=1,
        priority=1,
        reservation_episode_key="episode-1",
    )


def reservation_result(
    outcome: ReservationOutcome,
    *,
    payment_deadline: datetime | None = None,
) -> ReservationResult:
    return ReservationResult(
        outcome=outcome,
        source="srtrain-2.6.7-reservation",
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        credential_version=3,
        payment_deadline=payment_deadline,
        **(
            {
                "official_handoff_url": (
                    "https://etk.srail.kr/hpg/hra/02/"
                    "selectReservationList.do?pageId=TK0102010000"
                )
            }
            if outcome in {ReservationOutcome.PAYMENT_REQUIRED, ReservationOutcome.RESERVED}
            else {}
        ),
    )


async def test_worker_preserves_exact_srt_reserve_result_without_a_second_list_call() -> None:
    deadline = datetime(2026, 8, 3, 12, 20, tzinfo=UTC)
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
            source="srtrain-reservation-list",
            observed_at=datetime(2026, 8, 3, 12, 1, tzinfo=UTC),
            payment_deadline=deadline,
            official_handoff_url=(
                "https://etk.srail.kr/hpg/hra/02/"
                "selectReservationList.do?pageId=TK0102010000"
            ),
        )
    )

    confirmed = await _confirm_provider_reservation_result(
        adapter,
        target(),
        "attempt-1",
        reservation_result(
            ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=deadline,
        ),
    )

    assert adapter.calls == 0
    assert confirmed.outcome is ReservationOutcome.PAYMENT_REQUIRED
    assert confirmed.source == "srtrain-2.6.7-reservation"
    assert confirmed.payment_deadline == deadline
    assert confirmed.confirmation is not None
    assert (
        confirmed.confirmation.outcome
        is ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
    )


async def test_worker_keeps_not_found_or_inconclusive_as_ambiguous_fence() -> None:
    adapter = StubConfirmationAdapter(
        ReservationConfirmationResult(
            provider=Provider.SRT,
            outcome=ReservationConfirmationOutcome.NOT_FOUND,
            source="srtrain-reservation-list",
            observed_at=datetime(2026, 8, 3, 12, 1, tzinfo=UTC),
        )
    )

    unresolved = await _confirm_provider_reservation_result(
        adapter,
        target(),
        "attempt-1",
        reservation_result(ReservationOutcome.UNKNOWN),
    )

    assert adapter.calls == 1
    assert unresolved.outcome is ReservationOutcome.UNKNOWN
    assert unresolved.official_handoff_url is None
    assert unresolved.payment_deadline is None
