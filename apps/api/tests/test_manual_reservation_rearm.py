from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from rail_waitlist.config import get_settings
from rail_waitlist.domain import (
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    ReservationResultReasonCode,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.observations.group_application import retryable_reservation_episode_key
from rail_waitlist.outbox import add_outbox_event
from rail_waitlist.outbox_management.models import OutboxEvent
from rail_waitlist.provider_account_management.models import RailProviderAccount
from rail_waitlist.reservations.attempt_policy import (
    is_confirmed_absent_retry_source,
    is_unresolved_unknown_manual_rearm_source,
    manual_payment_hold_rearm_episode_key,
    manual_unknown_rearm_episode_key,
)
from rail_waitlist.reservations.attempt_runtime import begin_reservation_attempt
from rail_waitlist.reservations.manual_rearm_application import (
    ManualReservationRearmDependencies,
    ManualReservationRearmRejected,
    authorize_manual_reservation_rearm,
)
from rail_waitlist.reservations.manual_rearm_contracts import ManualReservationRearmReason
from rail_waitlist.reservations.payment_hold_application import is_payment_hold_ended
from rail_waitlist.reservations.provider_confirmation.contracts import (
    ReservationConfirmationOutcome,
)
from rail_waitlist.reservations.reconciliation_policy import (
    ReservationReconciliationResolution,
)
from rail_waitlist.watch_management import http as watch_http
from rail_waitlist.watch_management.models import (
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
)
from rail_waitlist.watch_management.read_model import watch_read


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


async def _persist_unresolved_unknown(
    session_factory,
    *,
    reconciliation_count: int = 3,
    confirmation_outcome: ReservationConfirmationOutcome = (
        ReservationConfirmationOutcome.INCONCLUSIVE
    ),
    reconciliation_resolution: ReservationReconciliationResolution | None = None,
    credential_version: int = 1,
) -> tuple[str, str, str, datetime]:
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
            train_numbers=["223"],
            reservation_policy=ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT,
            status=WatchStatus.WATCHING,
            dedupe_key=f"manual-unknown-rearm-{now.timestamp()}",
            next_check_at=now + timedelta(minutes=1),
        )
        candidate = WatchCandidate(
            train_number="223",
            departure_at=now + timedelta(days=1),
            scheduled_departure_at=now + timedelta(days=1),
            seat_class=SeatClass.STANDARD,
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:unknown-original",
            idempotency_key=f"reserve-unknown-{now.timestamp()}",
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=19),
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=credential_version,
            confirmation_outcome=confirmation_outcome,
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=now - timedelta(seconds=5),
            last_reconciled_at=now - timedelta(seconds=5),
            reconciliation_attempt_count=reconciliation_count,
            reconciliation_resolution=reconciliation_resolution,
            next_reconcile_at=(
                None if reconciliation_resolution is not None else now + timedelta(minutes=15)
            ),
        )
        watch.candidates.append(candidate)
        session.add(watch)
        session.add(attempt)
        await session.commit()
        return watch.id, candidate.id, attempt.id, now


def _dependencies(now: datetime) -> ManualReservationRearmDependencies:
    async def dispatch_credential_version(_session, _watch) -> int:
        return 1

    return ManualReservationRearmDependencies(
        reservation_dispatch_credential_version=dispatch_credential_version,
        is_payment_hold_ended=is_payment_hold_ended,
        is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
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


async def test_same_attempt_new_hold_end_generation_persists_a_new_authorization_event(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_ended_hold(session_factory)

    async with session_factory() as session:
        first = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now),
        )
    assert first.created is True

    renewed_hold_ended_at = now + timedelta(seconds=2)
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        attempt.confirmation_observed_at = renewed_hold_ended_at
        attempt.last_reconciled_at = renewed_hold_ended_at
        attempt.post_deadline_reconciled_at = renewed_hold_ended_at
        await session.commit()

    async with session_factory() as session:
        second = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(now + timedelta(seconds=3)),
        )
    assert second.created is True

    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "watch.manual_reservation_rearmed"
                    )
                )
            ).all()
        )

    assert candidate is not None
    assert candidate.manual_rearm_authorized_at == (now + timedelta(seconds=3)).replace(tzinfo=None)
    assert len(events) == 2
    assert all(event.payload["authorization_kind"] == "payment_hold_ended" for event in events)
    assert len({event.dedupe_key for event in events}) == 2


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


async def test_unresolved_unknown_rearm_requires_ack_and_keeps_reconciliation_due(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_unresolved_unknown(session_factory)

    async with session_factory() as session:
        with pytest.raises(ManualReservationRearmRejected, match="공식 앱"):
            await authorize_manual_reservation_rearm(
                session,
                watch_id,
                reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
                dependencies=_dependencies(now),
            )
        await session.rollback()
    async with session_factory() as session:
        result = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
            official_reservation_state_confirmed=True,
            dependencies=_dependencies(now),
        )
    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        attempt = await session.get(ReservationAttempt, attempt_id)
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "watch.manual_reservation_rearmed")
        )

    assert result.created is True
    assert candidate is not None and attempt is not None and event is not None
    assert candidate.manual_rearm_source_attempt_id == attempt.id
    assert attempt.reconciliation_attempt_count == 3
    assert attempt.next_reconcile_at == (now + timedelta(minutes=15)).replace(tzinfo=None)
    assert event.payload["authorization_kind"] == "unknown_result_unresolved"


async def test_unknown_authorization_cannot_be_reused_after_same_attempt_becomes_ended_hold(
    app,
) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_unresolved_unknown(session_factory)

    async with session_factory() as session:
        unknown_rearm = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
            official_reservation_state_confirmed=True,
            dependencies=_dependencies(now),
        )
    assert unknown_rearm.created is True

    hold_ended_at = now + timedelta(seconds=2)
    async with session_factory() as session:
        attempt = await session.get(ReservationAttempt, attempt_id)
        assert attempt is not None
        attempt.outcome = ReservationOutcome.PAYMENT_REQUIRED
        attempt.result_reason_code = ReservationResultReasonCode.PAYMENT_HOLD_CREATED
        attempt.confirmation_outcome = ReservationConfirmationOutcome.NOT_FOUND
        attempt.confirmation_source = "korail-reservation-list"
        attempt.confirmation_observed_at = hold_ended_at
        attempt.last_reconciled_at = hold_ended_at
        attempt.post_deadline_reconciled_at = hold_ended_at
        attempt.next_reconcile_at = None
        attempt.reconciliation_resolution = None
        await session.commit()

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        read = await watch_read(
            session,
            watch,
            manual_rearm_account_versions={Provider.KORAIL: 1},
            read_at=hold_ended_at + timedelta(milliseconds=1),
        )
        projected = read.candidates[0].latest_reservation_attempt
        assert projected is not None
        assert projected.manual_rearm_available is True
        assert projected.manual_rearm_reason is ManualReservationRearmReason.PAYMENT_HOLD_ENDED

    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        assert watch is not None and candidate is not None
        stale_observation = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.AVAILABLE,
            source="korail-official-page-browser",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(seconds=5),
        )
        session.add(stale_observation)
        await session.flush()
        assert (
            await retryable_reservation_episode_key(
                session,
                candidate,
                stale_observation,
                Provider.KORAIL,
                is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
                is_unresolved_unknown_manual_rearm_source=(
                    is_unresolved_unknown_manual_rearm_source
                ),
                is_payment_hold_ended=is_payment_hold_ended,
            )
            is None
        )
        watch.status = WatchStatus.SEAT_FOUND
        blocked, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "stale-unknown-marker-payment-claim",
            episode_key=manual_payment_hold_rearm_episode_key(
                attempt_id,
                candidate.id,
                stale_observation.id,
            ),
            retry_authorized=True,
            credential_version=1,
        )
        assert created is False
        assert blocked.id == attempt_id
        await session.rollback()

    payment_authorized_at = now + timedelta(seconds=3)
    async with session_factory() as session:
        payment_rearm = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            dependencies=_dependencies(payment_authorized_at),
        )
    assert payment_rearm.created is True

    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        watch = await session.get(Watch, watch_id)
        assert candidate is not None and watch is not None
        assert candidate.manual_rearm_authorized_at == payment_authorized_at.replace(tzinfo=None)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "watch.manual_reservation_rearmed"
                    )
                )
            ).all()
        )
        assert {event.payload["authorization_kind"] for event in events} == {
            "unknown_result_unresolved",
            "payment_hold_ended",
        }
        hold_generation = str(int(hold_ended_at.timestamp() * 1_000_000))
        assert {event.dedupe_key for event in events} == {
            f"manual-reservation-rearm:unknown_result_unresolved:{attempt_id}:current",
            f"manual-reservation-rearm:payment_hold_ended:{attempt_id}:{hold_generation}",
        }
        read = await watch_read(
            session,
            watch,
            manual_rearm_account_versions={Provider.KORAIL: 1},
            read_at=payment_authorized_at,
        )
        projected = read.candidates[0].latest_reservation_attempt
        assert projected is not None
        assert projected.manual_rearm_available is False
        assert projected.manual_rearm_reason is None

        fresh_observation = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.AVAILABLE,
            source="korail-official-page-browser",
            observed_at=now + timedelta(seconds=4),
            fresh_until=now + timedelta(seconds=8),
        )
        session.add(fresh_observation)
        await session.flush()
        episode_key = await retryable_reservation_episode_key(
            session,
            candidate,
            fresh_observation,
            Provider.KORAIL,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )
        assert episode_key == manual_payment_hold_rearm_episode_key(
            attempt_id,
            candidate.id,
            fresh_observation.id,
        )
        watch.status = WatchStatus.SEAT_FOUND
        retried, retry_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "fresh-payment-marker-claim",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=1,
        )
        assert retry_created is True
        assert retried.attempt_sequence == 2


async def test_exhausted_final_not_found_can_be_rearmed_but_changed_credentials_cannot(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, _candidate_id, _attempt_id, now = await _persist_unresolved_unknown(
        session_factory,
        reconciliation_count=6,
        confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
        reconciliation_resolution=(ReservationReconciliationResolution.EXHAUSTED_UNRESOLVED),
    )
    async with session_factory() as session:
        result = await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
            official_reservation_state_confirmed=True,
            dependencies=_dependencies(now),
        )
    assert result.created is True

    changed_watch_id, _candidate_id, _attempt_id, changed_now = await _persist_unresolved_unknown(
        session_factory, credential_version=2
    )
    async with session_factory() as session:
        with pytest.raises(ManualReservationRearmRejected, match="계정이 변경"):
            await authorize_manual_reservation_rearm(
                session,
                changed_watch_id,
                reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
                official_reservation_state_confirmed=True,
                dependencies=_dependencies(changed_now),
            )


async def test_unknown_manual_rearm_route_requires_explicit_reason_and_ack(
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
    watch_id, _candidate_id, _attempt_id, _now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext="not-read-by-unknown-rearm-gate",
                credential_version=1,
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

    before = await client.get(f"/api/v1/watches/{watch_id}")
    missing_ack = await client.post(
        f"/api/v1/watches/{watch_id}/reservation-rearm",
        json={"reason": "unknown_result_unresolved"},
    )
    accepted = await client.post(
        f"/api/v1/watches/{watch_id}/reservation-rearm",
        json={
            "reason": "unknown_result_unresolved",
            "official_reservation_state_confirmed": True,
        },
    )

    before_attempt = before.json()["candidates"][0]["latest_reservation_attempt"]
    accepted_attempt = accepted.json()["candidates"][0]["latest_reservation_attempt"]
    assert before.status_code == 200
    assert before_attempt["manual_rearm_available"] is True
    assert before_attempt["manual_rearm_reason"] == "unknown_result_unresolved"
    assert missing_ack.status_code == 409
    assert accepted.status_code == 200
    assert accepted_attempt["manual_rearm_available"] is False
    assert accepted_attempt["manual_rearm_reason"] is None
    assert enqueued == [watch_id]


async def test_unknown_manual_rearm_uses_one_fresh_official_observation_and_rechecks_claim(
    app,
) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext="not-read-by-unknown-rearm-observation",
                credential_version=1,
                last_auth_status="authenticated",
            )
        )
        await session.commit()
    async with session_factory() as session:
        await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
            official_reservation_state_confirmed=True,
            dependencies=_dependencies(now),
        )
    async with session_factory() as session:
        candidate = await session.get(WatchCandidate, candidate_id)
        assert candidate is not None
        observation = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.AVAILABLE,
            source="korail-official-page-browser",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=1),
        )
        session.add(observation)
        await session.flush()
        episode_key = await retryable_reservation_episode_key(
            session,
            candidate,
            observation,
            Provider.KORAIL,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )
        assert episode_key == manual_unknown_rearm_episode_key(
            attempt_id,
            candidate_id,
            observation.id,
        )
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        watch.status = WatchStatus.SEAT_FOUND
        created_attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-unknown-claim",
            episode_key=episode_key,
            retry_authorized=True,
            credential_version=1,
        )
        await session.commit()

    assert created is True
    assert created_attempt.attempt_sequence == 2
    assert created_attempt.episode_key.startswith("manual-unknown:")


@pytest.mark.parametrize(
    ("observed_offset", "source", "status", "current_credential_version"),
    [
        (-1, "korail-official-page-browser", SeatObservationStatus.AVAILABLE, 1),
        (0, "korail-official-page-browser", SeatObservationStatus.AVAILABLE, 1),
        (1, "companion-overlay", SeatObservationStatus.AVAILABLE, 1),
        (1, "korail-official-page-browser", SeatObservationStatus.SOLD_OUT, 1),
        (1, "korail-official-page-browser", SeatObservationStatus.STANDING_PLUS_SEAT, 1),
        (1, "korail-official-page-browser", SeatObservationStatus.WAITLIST_AVAILABLE, 1),
        (1, "korail-official-page-browser", SeatObservationStatus.STANDING_ONLY, 1),
        (1, "korail-official-page-browser", SeatObservationStatus.AVAILABLE, 2),
    ],
)
async def test_unknown_manual_rearm_rejects_nonfresh_nonexact_or_changed_account_observation(
    app,
    observed_offset: int,
    source: str,
    status: SeatObservationStatus,
    current_credential_version: int,
) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
            official_reservation_state_confirmed=True,
            dependencies=_dependencies(now),
        )
    async with session_factory() as session:
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext="not-read-by-negative-rearm-observation",
                credential_version=current_credential_version,
                last_auth_status="authenticated",
            )
        )
        candidate = await session.get(WatchCandidate, candidate_id)
        watch = await session.get(Watch, watch_id)
        assert candidate is not None and watch is not None
        observation = SeatObservation(
            candidate_id=candidate.id,
            status=status,
            source=source,
            observed_at=now + timedelta(seconds=observed_offset),
            fresh_until=now + timedelta(minutes=1),
        )
        session.add(observation)
        await session.flush()
        episode_key = await retryable_reservation_episode_key(
            session,
            candidate,
            observation,
            Provider.KORAIL,
            is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
            is_unresolved_unknown_manual_rearm_source=(is_unresolved_unknown_manual_rearm_source),
            is_payment_hold_ended=is_payment_hold_ended,
        )
        assert episode_key is None
        watch.status = WatchStatus.SEAT_FOUND
        blocked, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-unknown-negative-claim",
            episode_key=manual_unknown_rearm_episode_key(
                attempt_id,
                candidate_id,
                observation.id,
            ),
            retry_authorized=True,
            credential_version=current_credential_version,
        )

    assert created is False
    assert blocked.id == attempt_id


@pytest.mark.parametrize("source_kind", ["manual-unknown", "manual-after-hold"])
async def test_manual_recovery_source_attempt_cannot_recursively_rearm(
    app,
    source_kind: str,
) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        source_attempt = await session.get(ReservationAttempt, attempt_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        assert source_attempt is not None and candidate is not None
        source_attempt.episode_key = (
            manual_unknown_rearm_episode_key(
                "earlier-attempt",
                candidate.id,
                "earlier-observation",
            )
            if source_kind == "manual-unknown"
            else manual_payment_hold_rearm_episode_key(
                "earlier-attempt",
                candidate.id,
                "earlier-observation",
            )
        )
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        read = await watch_read(
            session,
            watch,
            manual_rearm_account_versions={Provider.KORAIL: 1},
            read_at=now,
        )
        projected = read.candidates[0].latest_reservation_attempt
        assert projected is not None
        assert projected.manual_rearm_available is False
        assert projected.manual_rearm_reason is None
        candidate.manual_rearm_source_attempt_id = source_attempt.id
        candidate.manual_rearm_authorized_at = now
        session.add(
            RailProviderAccount(
                provider=Provider.KORAIL,
                credentials_ciphertext="not-read-by-recursive-rearm-gate",
                credential_version=1,
                last_auth_status="authenticated",
            )
        )
        observation = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.AVAILABLE,
            source="korail-official-page-browser",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=1),
        )
        session.add(observation)
        await session.flush()
        assert (
            await retryable_reservation_episode_key(
                session,
                candidate,
                observation,
                Provider.KORAIL,
                is_confirmed_absent_retry_source=is_confirmed_absent_retry_source,
                is_unresolved_unknown_manual_rearm_source=(
                    is_unresolved_unknown_manual_rearm_source
                ),
                is_payment_hold_ended=is_payment_hold_ended,
            )
            is None
        )
        watch.status = WatchStatus.SEAT_FOUND
        blocked, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-unknown-recursive-claim",
            episode_key=manual_unknown_rearm_episode_key(
                attempt_id,
                candidate_id,
                observation.id,
            ),
            retry_authorized=True,
            credential_version=1,
        )

    assert created is False
    assert blocked.id == attempt_id


async def test_unknown_manual_claim_closes_if_confirmation_becomes_conclusive(app) -> None:
    session_factory = app.state.test_session_factory
    watch_id, candidate_id, attempt_id, now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        await authorize_manual_reservation_rearm(
            session,
            watch_id,
            reason=ManualReservationRearmReason.UNKNOWN_RESULT_UNRESOLVED,
            official_reservation_state_confirmed=True,
            dependencies=_dependencies(now),
        )
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        candidate = await session.get(WatchCandidate, candidate_id)
        source_attempt = await session.get(ReservationAttempt, attempt_id)
        assert watch is not None and candidate is not None and source_attempt is not None
        observation = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.LIMITED,
            source="korail-official-page-browser",
            observed_at=now + timedelta(seconds=1),
            fresh_until=now + timedelta(minutes=1),
        )
        session.add(observation)
        await session.flush()
        source_attempt.confirmation_outcome = (
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        )
        source_attempt.reconciliation_resolution = None
        watch.status = WatchStatus.SEAT_FOUND
        blocked, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "manual-unknown-race-blocked",
            episode_key=manual_unknown_rearm_episode_key(
                attempt_id,
                candidate_id,
                observation.id,
            ),
            retry_authorized=True,
            credential_version=1,
        )

    assert created is False
    assert blocked.id == attempt_id


async def test_projection_and_command_use_unique_unresolved_source_before_later_safe_attempt(
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
    watch_id, _candidate_id, _attempt_id, now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        newer_candidate = WatchCandidate(
            watch_id=watch.id,
            train_number="225",
            departure_at=now + timedelta(days=1, minutes=10),
            scheduled_departure_at=now + timedelta(days=1, minutes=10),
            seat_class=SeatClass.STANDARD,
            priority=2,
            state="observed",
        )
        session.add(newer_candidate)
        await session.flush()
        session.add_all(
            [
                ReservationAttempt(
                    candidate_id=newer_candidate.id,
                    attempt_sequence=1,
                    episode_key="availability:newer-candidate",
                    idempotency_key="newer-candidate-attempt",
                    started_at=now - timedelta(minutes=1),
                    finished_at=now - timedelta(seconds=30),
                    outcome=ReservationOutcome.NOT_AVAILABLE,
                    credential_version=1,
                ),
                RailProviderAccount(
                    provider=Provider.KORAIL,
                    credentials_ciphertext="not-read-by-global-latest-gate",
                    credential_version=1,
                    last_auth_status="authenticated",
                ),
            ]
        )
        await session.commit()
    enqueued: list[str] = []
    monkeypatch.setattr(
        watch_http,
        "enqueue_immediate_watch_processing",
        lambda queued_watch_id: enqueued.append(queued_watch_id) or True,
    )

    projected = await client.get(f"/api/v1/watches/{watch_id}")
    accepted = await client.post(
        f"/api/v1/watches/{watch_id}/reservation-rearm",
        json={
            "reason": "unknown_result_unresolved",
            "official_reservation_state_confirmed": True,
        },
    )

    attempts = [
        candidate["latest_reservation_attempt"] for candidate in projected.json()["candidates"]
    ]
    assert projected.status_code == 200
    unresolved_attempt = next(attempt for attempt in attempts if attempt["outcome"] == "unknown")
    later_attempt = next(attempt for attempt in attempts if attempt["outcome"] == "not_available")
    assert unresolved_attempt["manual_rearm_available"] is True
    assert unresolved_attempt["manual_rearm_reason"] == "unknown_result_unresolved"
    assert later_attempt["manual_rearm_available"] is False
    assert later_attempt["manual_rearm_reason"] is None
    assert accepted.status_code == 200
    assert enqueued == [watch_id]


@pytest.mark.parametrize("watch_fence", ["second-unresolved", "exact-paid"])
async def test_projection_and_command_close_for_ambiguous_or_paid_watch_wide_fence(
    app,
    client,
    watch_fence: str,
) -> None:
    settings = get_settings()
    settings.experimental_rail_enabled = True
    settings.korail_browser_adapter_enabled = True
    settings.korail_seat_monitoring_enabled = True
    settings.korail_reservation_once_enabled = True
    session_factory = app.state.test_session_factory
    watch_id, _candidate_id, _attempt_id, now = await _persist_unresolved_unknown(session_factory)
    async with session_factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        other = WatchCandidate(
            watch_id=watch.id,
            train_number="225",
            departure_at=now + timedelta(days=1, minutes=10),
            scheduled_departure_at=now + timedelta(days=1, minutes=10),
            seat_class=SeatClass.STANDARD,
            priority=2,
            state="observed",
        )
        other_attempt = ReservationAttempt(
            candidate=other,
            attempt_sequence=1,
            episode_key="availability:watch-wide-fence",
            idempotency_key=f"watch-wide-fence-{watch_fence}",
            started_at=now - timedelta(minutes=1),
            finished_at=now - timedelta(seconds=30),
            outcome=(
                ReservationOutcome.UNKNOWN
                if watch_fence == "second-unresolved"
                else ReservationOutcome.RESERVED
            ),
            credential_version=1,
            confirmation_outcome=(
                ReservationConfirmationOutcome.INCONCLUSIVE
                if watch_fence == "second-unresolved"
                else ReservationConfirmationOutcome.CONFIRMED_PAID
            ),
            confirmation_source="official-reservation-list",
            confirmation_observed_at=now - timedelta(seconds=30),
            last_reconciled_at=now - timedelta(seconds=30),
            reconciliation_attempt_count=(3 if watch_fence == "second-unresolved" else 1),
            next_reconcile_at=(
                now + timedelta(minutes=15) if watch_fence == "second-unresolved" else None
            ),
        )
        session.add_all(
            [
                other_attempt,
                RailProviderAccount(
                    provider=Provider.KORAIL,
                    credentials_ciphertext="not-read-by-watch-wide-fence",
                    credential_version=1,
                    last_auth_status="authenticated",
                ),
            ]
        )
        await session.commit()

    projected = await client.get(f"/api/v1/watches/{watch_id}")
    rejected = await client.post(
        f"/api/v1/watches/{watch_id}/reservation-rearm",
        json={
            "reason": "unknown_result_unresolved",
            "official_reservation_state_confirmed": True,
        },
    )

    assert projected.status_code == 200
    attempts = [
        candidate["latest_reservation_attempt"] for candidate in projected.json()["candidates"]
    ]
    assert all(attempt["manual_rearm_available"] is False for attempt in attempts)
    assert all(attempt["manual_rearm_reason"] is None for attempt in attempts)
    assert rejected.status_code == 409
