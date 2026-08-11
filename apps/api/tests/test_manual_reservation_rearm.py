from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from rail_waitlist.config import get_settings
from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.outbox import add_outbox_event
from rail_waitlist.outbox_management.models import OutboxEvent
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.reservations.attempt_policy import (
    manual_payment_hold_rearm_episode_key,
)
from rail_waitlist.reservations.attempt_runtime import begin_reservation_attempt
from rail_waitlist.reservations.manual_rearm_application import (
    ManualReservationRearmDependencies,
    ManualReservationRearmRejected,
    authorize_manual_reservation_rearm,
)
from rail_waitlist.reservations.payment_hold_application import is_payment_hold_ended
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
)
from rail_waitlist.watch_management import http as watch_http
from rail_waitlist.watch_management.models import (
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
)


async def _persist_ended_hold(session_factory) -> tuple[str, str, str, datetime]:
    now = datetime.now(UTC)
    async with session_factory() as session:
        watch = Watch(
            provider=Provider.KORAIL,
            origin="대전",
            origin_node_id="0010",
            destination="서울",
            destination_node_id="0001",
            travel_date=(now + timedelta(days=1)).date(),
            time_from=(now + timedelta(hours=1)).time().replace(tzinfo=None),
            time_to=(now + timedelta(hours=3)).time().replace(tzinfo=None),
            seat_class=SeatClass.STANDARD,
            passenger_count=1,
            train_numbers=["242"],
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key=f"manual-rearm-{now.timestamp()}",
            next_check_at=now + timedelta(minutes=1),
        )
        candidate = WatchCandidate(
            train_number="242",
            departure_at=now + timedelta(days=1),
            scheduled_departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:original",
            idempotency_key=f"reserve-original-{now.timestamp()}",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="korail_browser",
            confirmation_observed_at=now - timedelta(seconds=5),
            post_deadline_reconciled_at=now - timedelta(seconds=5),
        )
        watch.candidates.append(candidate)
        session.add(watch)
        session.add(attempt)
        await session.commit()
        return watch.id, candidate.id, attempt.id, now


def _dependencies(now: datetime) -> ManualReservationRearmDependencies:
    async def dispatch_ready(_session, _watch) -> bool:
        return True

    return ManualReservationRearmDependencies(
        reservation_dispatch_ready=dispatch_ready,
        is_payment_hold_ended=is_payment_hold_ended,
        add_outbox_event=add_outbox_event,
        now=lambda: now,
    )


async def test_manual_rearm_persists_one_marker_and_duplicate_is_idempotent(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_ended_hold(session_factory)

    async with session_factory() as session:
        first = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now),
        )
    async with session_factory() as session:
        second = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now + timedelta(seconds=1)),
        )
        candidate = await session.get(WatchCandidate, candidate_id)
        event_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "watch.manual_reservation_rearmed")
        )

    assert first.created is True
    assert second.created is False
    assert candidate is not None
    assert candidate.manual_rearm_source_attempt_id == attempt_id
    assert candidate.manual_rearm_authorized_at == now.replace(tzinfo=None)
    assert first.watch.next_check_at == now.replace(tzinfo=None)
    assert event_count == 1


async def test_a_later_ended_manual_hold_can_be_rearmed_again(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, first_attempt_id, now = await _persist_ended_hold(session_factory)
    async with session_factory() as session:
        await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now),
        )
    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        assert candidate is not None
        second_attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=2,
            episode_key=manual_payment_hold_rearm_episode_key(
                first_attempt_id,
                candidate.id,
                "first-manual-observation",
            ),
            idempotency_key="second-ended-hold",
            started_at=now + timedelta(seconds=1),
            finished_at=now + timedelta(seconds=2),
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=now + timedelta(seconds=3),
            post_deadline_reconciled_at=now + timedelta(seconds=3),
        )
        session.add(second_attempt)
        await session.commit()
        second_attempt_id = second_attempt.id

    async with session_factory() as session:
        rearmed = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now + timedelta(seconds=4)),
        )
    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)

    assert rearmed.created is True
    assert candidate is not None
    assert candidate.manual_rearm_source_attempt_id == second_attempt_id
    assert candidate.manual_rearm_authorized_at == (now + timedelta(seconds=4)).replace(tzinfo=None)


async def test_manual_rearm_route_commits_then_enqueues_once(
    app,
    client,
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.experimental_rail_enabled = True
    settings.korail_browser_adapter_enabled = True
    settings.korail_seat_monitoring_enabled = True
    settings.korail_reservation_once_enabled = True
    session_factory = app.state.test_session_factory
    watch_id, _candidate_id, _attempt_id, _now = await _persist_ended_hold(session_factory)
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext="not-read-by-rearm-gate",
                last_auth_status="authenticated",
            )
        )
        await session.commit()
    enqueued: list[str] = []
    monkeypatch.setattr(
        watch_http,
        "enqueue_immediate_watch_processing",
        lambda queued_watch_id: enqueued.append(queued_watch_id) or True,
    )

    first = await client.post(f"/api/v1/watches/{watch_id}/reservation-rearm")
    second = await client.post(f"/api/v1/watches/{watch_id}/reservation-rearm")

    assert first.status_code == 200
    assert (
        first.json()["candidates"][0]["latest_reservation_attempt"]["manual_rearm_available"]
        is False
    )
    assert second.status_code == 200
    assert enqueued == [watch_id]


@pytest.mark.parametrize(
    ("status", "policy"),
    [
        (WatchStatus.PAYMENT_REQUIRED, ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT),
        (WatchStatus.WATCHING, ReservationPolicy.NOTIFY_ONLY),
    ],
)
async def test_manual_rearm_rejects_non_monitoring_or_non_automatic_watch(
    app,
    status: WatchStatus,
    policy: ReservationPolicy,
) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, _attempt_id, now = await _persist_ended_hold(session_factory)
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        watch.status = status
        watch.reservation_policy = policy
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(ManualReservationRearmRejected):
            await authorize_manual_reservation_rearm(
                session,
                watch_id,
                dependencies=_dependencies(now),
            )
        await session.rollback()
    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        assert candidate is not None
        assert candidate.manual_rearm_source_attempt_id is None


async def test_manual_rearm_claims_only_once_after_a_later_actionable_observation(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_ended_hold(session_factory)
    async with session_factory() as session:
        await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now),
        )

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        assert watch is not None and candidate is not None
        watch.status = WatchStatus.SEAT_FOUND
        missing_observation_episode_key = manual_payment_hold_rearm_episode_key(
            attempt_id,
            candidate_id,
            "missing-observation",
        )
        blocked, blocked_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-rearm-claim",
            episode_key=missing_observation_episode_key,
            retry_authorized=True,
            credential_version=1,
        )
        assert blocked_created is False
        assert blocked.id == attempt_id
        sold_out_observation = SeatObservation(
            candidate_id=candidate_id,
            status=SeatObservationStatus.SOLD_OUT,
            source="korail-official-page-browser",
            observed_at=now + timedelta(milliseconds=500),
            fresh_until=now + timedelta(seconds=1),
        )
        session.add(sold_out_observation)
        await session.flush()
        sold_out_episode_key = manual_payment_hold_rearm_episode_key(
            attempt_id,
            candidate_id,
            sold_out_observation.id,
        )
        still_blocked, still_blocked_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-rearm-claim",
            episode_key=sold_out_episode_key,
            retry_authorized=True,
            credential_version=1,
        )
        assert still_blocked_created is False
        assert still_blocked.id == attempt_id
        available_observation = SeatObservation(
            candidate_id=candidate_id,
            status=SeatObservationStatus.AVAILABLE,
            source="korail-official-page-browser",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(seconds=2),
        )
        session.add(available_observation)
        await session.flush()
        episode_key = manual_payment_hold_rearm_episode_key(
            attempt_id,
            candidate_id,
            available_observation.id,
        )
        first, first_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-rearm-claim",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=1,
        )
        await session.commit()

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        assert watch is not None and candidate is not None
        duplicate, duplicate_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-rearm-claim",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=1,
        )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
