from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import rail_waitlist.services as services_module
from rail_waitlist.domain import (
    NotificationKind,
    Provider,
    ReservationOutcome,
    ReservationPolicy,
    SeatClass,
    SeatObservationStatus,
    WatchStatus,
)
from rail_waitlist.models import (
    NotificationChannel,
    OutboxEvent,
    ReservationAttempt,
    SeatObservation,
    Watch,
    WatchCandidate,
    WatchTransitionHistory,
)
from rail_waitlist.reservation_confirmation import (
    ReservationConfirmationOutcome,
    ReservationConfirmationResult,
)
from rail_waitlist.reservations.attempt_policy import payment_hold_retry_episode_key
from rail_waitlist.schemas import (
    ProviderCapabilities,
    ReservationProgressStage,
    ReservationResult,
    SeatObservationResult,
)
from rail_waitlist.services import (
    add_outbox_event,
    apply_reservation_reconciliation,
    apply_watch_transition,
    begin_reservation_attempt,
    complete_reservation_attempt,
    record_seat_observation,
    reservation_attempt_result_policy,
)


class CapabilityAdapter:
    def __init__(self, provider: Provider, *, seat_monitoring: bool) -> None:
        self.provider = provider
        self.seat_monitoring = seat_monitoring

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.provider,
            timetable=False,
            official_booking_link=False,
            official_waitlist_link=False,
            seat_monitoring=self.seat_monitoring,
            reservation_once=False,
        )


@pytest.mark.parametrize(
    "outcome",
    [ReservationOutcome.AUTH_REQUIRED, ReservationOutcome.PROVIDER_BLOCKED],
)
def test_auth_bound_reservation_results_expose_the_reverification_retry_condition(
    outcome: ReservationOutcome,
) -> None:
    policy = reservation_attempt_result_policy(outcome)

    assert policy.retryable is False
    assert policy.retry_condition == "provider_account_reverified"


def make_watch(
    *,
    provider: Provider = Provider.SRT,
    status: WatchStatus = WatchStatus.DRAFT,
    notification_channel_ids: list[str] | None = None,
) -> Watch:
    return Watch(
        provider=provider,
        origin="수서",
        origin_node_id="N-SUSEO",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2026, 8, 1),
        time_from=time(12),
        time_to=time(18),
        train_numbers=["SRT-301"],
        notification_channel_ids=notification_channel_ids or [],
        mode="official",
        status=status,
        dedupe_key=f"service-state-{provider.value}-{status.value}",
    )


async def test_outbox_dedupe_key_is_bounded_and_stable(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    long_key = "notification:" + "same-transition:" * 20

    async with factory() as session:
        first = await add_outbox_event(
            session,
            aggregate_type="notification_channel",
            aggregate_id="notification-channel",
            event_type="notification.dispatch_requested",
            payload={"status": "seat_found"},
            dedupe_key=long_key,
        )
        second = await add_outbox_event(
            session,
            aggregate_type="notification_channel",
            aggregate_id="notification-channel",
            event_type="notification.dispatch_requested",
            payload={"status": "seat_found"},
            dedupe_key=long_key,
        )

        assert first.id == second.id
        assert len(first.dedupe_key) == 128
        assert first.dedupe_key.startswith("notification:")


@pytest.mark.parametrize(
    ("seat_monitoring", "expects_due_time"),
    [(True, True), (False, False)],
)
async def test_scheduled_watch_is_due_only_when_execution_adapter_can_monitor(
    db_engine, monkeypatch, seat_monitoring, expects_due_time
):
    adapter = CapabilityAdapter(Provider.SRT, seat_monitoring=seat_monitoring)
    monkeypatch.setattr(services_module, "get_execution_provider", lambda provider: adapter)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch()
        session.add(watch)
        await session.flush()
        before_transition = datetime.now(UTC)

        await apply_watch_transition(session, watch, WatchStatus.SCHEDULED)

        assert watch.status is WatchStatus.SCHEDULED
        assert (watch.next_check_at is not None) is expects_due_time
        if watch.next_check_at is not None:
            assert watch.next_check_at >= before_transition


@pytest.mark.parametrize(
    "status",
    [
        SeatObservationStatus.AVAILABLE,
        SeatObservationStatus.LIMITED,
        SeatObservationStatus.STANDING_PLUS_SEAT,
    ],
)
async def test_actionable_inventory_transitions_watching_to_seat_found(db_engine, status):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.WATCHING)
        candidate = WatchCandidate(
            train_number="SRT-301",
            departure_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        observation = await record_seat_observation(
            session,
            watch,
            candidate,
            SeatObservationResult(
                seat_class=SeatClass.STANDARD,
                status=status,
                source="authorized-test-source",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(seconds=30),
            ),
        )

        assert watch.status is WatchStatus.SEAT_FOUND
        assert candidate.state == "seat_found"
        assert observation.status is status


async def test_waitlist_observation_transitions_and_notifies_as_official_waitlist(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)

    async with factory() as session:
        channel = NotificationChannel(
            id="notification-channel",
            kind=NotificationKind.TELEGRAM,
            name="service-state-test",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        watch = make_watch(
            status=WatchStatus.WATCHING,
            notification_channel_ids=["notification-channel"],
        )
        candidate = WatchCandidate(
            train_number="SRT-301",
            departure_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
        )
        watch.candidates.append(candidate)
        session.add_all([channel, watch])
        await session.flush()

        observation = await record_seat_observation(
            session,
            watch,
            candidate,
            SeatObservationResult(
                seat_class=SeatClass.STANDARD,
                status=SeatObservationStatus.WAITLIST_AVAILABLE,
                source="authorized-test-source",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(seconds=30),
            ),
        )
        await session.flush()
        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.aggregate_id == "notification-channel",
            )
        )
        persisted_observation = await session.get(SeatObservation, observation.id)

        assert watch.status is WatchStatus.OFFICIAL_WAITLIST
        assert candidate.state == "seat_found"
        assert persisted_observation.status is SeatObservationStatus.WAITLIST_AVAILABLE
        assert notification is not None
        assert notification.payload["status"] == WatchStatus.OFFICIAL_WAITLIST.value


async def test_state_notification_uses_channel_enabled_after_active_watch_creation(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.WATCHING)
        session.add(watch)
        await session.commit()

        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="connected-after-watch",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        session.add(channel)
        await session.flush()

        await apply_watch_transition(session, watch, WatchStatus.SEAT_FOUND)
        await session.flush()

        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.aggregate_id == channel.id,
            )
        )

        assert watch.notification_channel_ids == []
        assert notification is not None
        assert notification.payload["watch_id"] == watch.id
        assert notification.payload["status"] == WatchStatus.SEAT_FOUND.value


async def test_reserving_notification_describes_the_current_availability_episode(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.SEAT_FOUND)
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="reservation-progress",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        session.add_all([watch, channel])
        await session.flush()

        await apply_watch_transition(session, watch, WatchStatus.RESERVING)
        await session.flush()

        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.aggregate_id == channel.id,
                OutboxEvent.payload["watch_id"].as_string() == watch.id,
                OutboxEvent.payload["status"].as_string() == WatchStatus.RESERVING.value,
            )
        )

        assert notification is not None
        assert "이번 좌석 가용성에 대한 예매" in notification.payload["message"]
        assert "1회 예매" not in notification.payload["message"]


@pytest.mark.parametrize(
    "reason",
    [
        "reservation_unknown",
        "reservation_result_deadline_already_elapsed",
        "stale_reservation_attempt_requires_manual_check",
    ],
)
async def test_manual_check_monitoring_resume_notifies_external_channels(
    db_engine,
    reason: str,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.RESERVING)
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name=f"manual-check-{reason}",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        session.add_all([watch, channel])
        await session.flush()

        await apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason=reason,
        )
        await session.flush()

        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.aggregate_id == channel.id,
                OutboxEvent.payload["watch_id"].as_string() == watch.id,
                OutboxEvent.payload["status"].as_string() == WatchStatus.WATCHING.value,
            )
        )

        assert notification is not None
        assert "예매 결과를 확정하지 못했습니다" in notification.payload["message"]
        assert "공식 플랫폼의 예약 내역을 확인해 주세요" in notification.payload["message"]
        assert (
            "같은 좌석 가용 상태에서는 자동 예매를 다시 시도하지 않습니다"
            in notification.payload["message"]
        )


async def test_not_available_monitoring_resume_explains_the_required_retry_edge(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.RESERVING)
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="not-available-retry-edge",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        session.add_all([watch, channel])
        await session.flush()

        await apply_watch_transition(
            session,
            watch,
            WatchStatus.WATCHING,
            reason="reservation_not_available",
        )
        await session.flush()

        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.aggregate_id == channel.id,
                OutboxEvent.payload["watch_id"].as_string() == watch.id,
                OutboxEvent.payload["status"].as_string() == WatchStatus.WATCHING.value,
            )
        )

        assert notification is not None
        assert "예매 시점에 좌석을 확보하지 못해" in notification.payload["message"]
        assert (
            "판매 불가 상태를 확인한 뒤 좌석이 다시 가용해지는 경우에만"
            in notification.payload["message"]
        )


async def test_state_notification_skips_disabled_global_channel_even_if_watch_snapshotted_it(
    db_engine,
):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="disabled-after-watch",
            config_ciphertext="encrypted-test-placeholder",
            enabled=False,
        )
        session.add(channel)
        await session.flush()
        watch = make_watch(
            status=WatchStatus.WATCHING,
            notification_channel_ids=[channel.id],
        )
        session.add(watch)
        await session.flush()

        await apply_watch_transition(session, watch, WatchStatus.SEAT_FOUND)
        await session.flush()

        notifications = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notification.dispatch_requested"
                    )
                )
            ).all()
        )

        assert watch.notification_channel_ids == [channel.id]
        assert notifications == []


async def test_non_actionable_observation_keeps_watch_watching(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)

    async with factory() as session:
        watch = make_watch(status=WatchStatus.WATCHING)
        candidate = WatchCandidate(
            train_number="SRT-301",
            departure_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await record_seat_observation(
            session,
            watch,
            candidate,
            SeatObservationResult(
                seat_class=SeatClass.STANDARD,
                status=SeatObservationStatus.SOLD_OUT,
                source="authorized-test-source",
                observed_at=observed_at,
                fresh_until=observed_at + timedelta(seconds=30),
            ),
        )

        assert watch.status is WatchStatus.WATCHING
        assert candidate.state == "observed"


async def test_successful_reservation_updates_watch_handoff_url(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    handoff_url = "https://example.invalid/reservation-handoff"

    async with factory() as session:
        watch = make_watch(provider=Provider.MOCK, status=WatchStatus.SEAT_FOUND)
        watch.official_booking_url = "https://example.invalid/old-booking-page"
        candidate = WatchCandidate(
            train_number="MOCK-001",
            departure_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reservation-handoff-test",
        )
        assert created is True
        assert isinstance(attempt, ReservationAttempt)
        await complete_reservation_attempt(
            session,
            watch,
            candidate,
            attempt,
            ReservationResult(
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                source="mock",
                observed_at=observed_at,
                payment_deadline=observed_at + timedelta(minutes=10),
                official_handoff_url=handoff_url,
            ),
        )
        await session.flush()

        assert attempt.official_handoff_url == handoff_url
        assert watch.official_booking_url == handoff_url
        assert watch.status is WatchStatus.PAYMENT_REQUIRED


@pytest.mark.parametrize(
    "confirmation_outcome",
    [
        ReservationConfirmationOutcome.NOT_FOUND,
        ReservationConfirmationOutcome.INCONCLUSIVE,
    ],
)
async def test_reconciliation_negative_evidence_requires_exact_retry_episode(
    db_engine,
    confirmation_outcome: ReservationConfirmationOutcome,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.SRT, status=WatchStatus.WATCHING)
        candidate = WatchCandidate(
            train_number="301",
            departure_at=datetime(2026, 8, 3, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:reconciliation-negative",
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=7,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.SRT,
                outcome=confirmation_outcome,
                source="srtrain-reservation-list",
                observed_at=observed_at,
            ),
            reconciled_at=observed_at + timedelta(seconds=1),
        )
        repeated, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:must-not-retry",
            episode_key="availability:later",
            retry_authorized=True,
        )

        assert repeated.id == attempt.id
        assert created is False
        assert attempt.outcome is ReservationOutcome.UNKNOWN
        assert attempt.confirmation_outcome is confirmation_outcome
        assert attempt.confirmation_source == "srtrain-reservation-list"
        assert attempt.confirmation_observed_at == observed_at
        assert attempt.last_reconciled_at == observed_at + timedelta(seconds=1)
        assert attempt.reconciliation_attempt_count == 1
        assert attempt.next_reconcile_at == (
            None
            if confirmation_outcome is ReservationConfirmationOutcome.NOT_FOUND
            else observed_at + timedelta(seconds=31)
        )
        assert watch.status is WatchStatus.WATCHING
        assert candidate.state == "observed"


async def test_confirmed_absent_unknown_remains_fenced_after_later_actionable_observation(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.SRT, status=WatchStatus.SEAT_FOUND)
        candidate = WatchCandidate(
            train_number="335",
            departure_at=confirmed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="seat_found",
        )
        first_attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:confirmed-absent:first",
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=7,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=confirmed_at,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        session.add(
            SeatObservation(
                candidate=candidate,
                status=SeatObservationStatus.AVAILABLE,
                source="authorized-provider",
                observed_at=confirmed_at + timedelta(seconds=1),
                fresh_until=confirmed_at + timedelta(minutes=1),
            )
        )
        await session.flush()

        replayed_attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:confirmed-absent:second",
            episode_key=f"confirmed-absent-retry:{first_attempt.id}",
            retry_authorized=True,
        )

        assert replayed_attempt.id == first_attempt.id
        assert created is False


async def test_legacy_confirmed_absent_payment_hold_rearms_exactly_once(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.SEAT_FOUND)
        candidate = WatchCandidate(
            train_number="238",
            departure_at=confirmed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="seat_found",
        )
        legacy_attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:legacy-confirmed-absent:first",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=None,
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=confirmed_at,
            post_deadline_reconciled_at=None,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        session.add(
            SeatObservation(
                candidate=candidate,
                status=SeatObservationStatus.LIMITED,
                source="authorized-provider",
                observed_at=confirmed_at + timedelta(seconds=1),
                fresh_until=confirmed_at + timedelta(minutes=1),
            )
        )
        await session.flush()

        retry, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:legacy-confirmed-absent:second",
            episode_key=f"confirmed-absent-retry:{legacy_attempt.id}",
            retry_authorized=True,
        )

        assert created is True
        assert retry.attempt_sequence == 2
        retry.outcome = ReservationOutcome.PAYMENT_REQUIRED
        retry.confirmation_outcome = ReservationConfirmationOutcome.NOT_FOUND
        retry.confirmation_source = "korail-reservation-list"
        retry.confirmation_observed_at = confirmed_at + timedelta(seconds=2)
        candidate.state = "seat_found"
        watch.status = WatchStatus.SEAT_FOUND
        session.add(
            SeatObservation(
                candidate=candidate,
                status=SeatObservationStatus.AVAILABLE,
                source="authorized-provider",
                observed_at=confirmed_at + timedelta(seconds=3),
                fresh_until=confirmed_at + timedelta(minutes=1),
            )
        )
        await session.flush()

        repeated, repeated_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:legacy-confirmed-absent:third",
            episode_key=f"confirmed-absent-retry:{retry.id}",
            retry_authorized=True,
        )

        assert repeated.id == retry.id
        assert repeated_created is False


async def test_confirmed_absent_payment_hold_with_deadline_stays_fenced(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    confirmed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.SEAT_FOUND)
        candidate = WatchCandidate(
            train_number="238",
            departure_at=confirmed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="seat_found",
        )
        hold_attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:confirmed-hold:first",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            payment_deadline=confirmed_at + timedelta(minutes=10),
            confirmation_outcome=ReservationConfirmationOutcome.NOT_FOUND,
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=confirmed_at,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()
        session.add(
            SeatObservation(
                candidate=candidate,
                status=SeatObservationStatus.AVAILABLE,
                source="authorized-provider",
                observed_at=confirmed_at + timedelta(seconds=1),
                fresh_until=confirmed_at + timedelta(minutes=1),
            )
        )
        await session.flush()

        repeated, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:confirmed-hold:must-not-retry",
            episode_key=f"confirmed-absent-retry:{hold_attempt.id}",
            retry_authorized=True,
        )

        assert repeated.id == hold_attempt.id
        assert created is False


@pytest.mark.parametrize(
    "confirmation_outcome",
    [
        ReservationConfirmationOutcome.AUTH_REQUIRED,
        ReservationConfirmationOutcome.PROVIDER_BLOCKED,
    ],
)
async def test_reconciliation_auth_or_protection_signal_stops_bounded_reads(
    db_engine,
    confirmation_outcome: ReservationConfirmationOutcome,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.SRT, status=WatchStatus.WATCHING)
        candidate = WatchCandidate(
            train_number="305",
            departure_at=datetime(2026, 8, 3, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:auth-terminal",
            idempotency_key=f"reserve:reconciliation-{confirmation_outcome.value}",
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=7,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.SRT,
                outcome=confirmation_outcome,
                source="srtrain-reservation-list",
                observed_at=observed_at,
            ),
            reconciled_at=observed_at + timedelta(seconds=1),
        )

        assert attempt.reconciliation_attempt_count == 1
        assert attempt.next_reconcile_at is None
        assert attempt.outcome is ReservationOutcome.UNKNOWN


async def test_reconciliation_positive_match_restores_payment_handoff(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    deadline = observed_at + timedelta(minutes=10)
    async with factory() as session:
        watch = make_watch(provider=Provider.SRT, status=WatchStatus.WATCHING)
        candidate = WatchCandidate(
            train_number="301",
            departure_at=datetime(2026, 8, 3, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
            state="observed",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:reconciliation-positive",
            outcome=ReservationOutcome.UNKNOWN,
            credential_version=4,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.SRT,
                outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                source="srtrain-reservation-list",
                observed_at=observed_at,
                payment_deadline=deadline,
                official_handoff_url="https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
            ),
            reconciled_at=observed_at + timedelta(seconds=1),
        )
        await session.flush()

        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempt.payment_deadline == deadline
        assert attempt.confirmation_outcome is (
            ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED
        )
        assert watch.status is WatchStatus.PAYMENT_REQUIRED
        assert candidate.state == "payment_required"
        assert watch.official_booking_url == attempt.official_handoff_url


async def test_confirmed_hold_without_deadline_keeps_bounded_deadline_recovery_due(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.PAYMENT_REQUIRED)
        watch.official_booking_url = "https://www.korail.com/ticket/reservation/list"
        candidate = WatchCandidate(
            train_number="238",
            departure_at=observed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:missing-deadline-refresh",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            credential_version=3,
            reconciliation_attempt_count=1,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                source="korail-reservation-list",
                observed_at=observed_at,
                official_handoff_url="https://www.korail.com/ticket/reservation/list",
            ),
            reconciled_at=observed_at + timedelta(seconds=1),
        )

        assert watch.status is WatchStatus.PAYMENT_REQUIRED
        assert watch.payment_deadline is None
        assert attempt.reconciliation_attempt_count == 2
        assert attempt.next_reconcile_at == observed_at + timedelta(seconds=31)


async def test_final_not_found_for_confirmed_hold_resumes_monitoring_without_retry(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.PAYMENT_REQUIRED)
        watch.reservation_policy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        watch.payment_deadline = observed_at - timedelta(minutes=1)
        watch.official_booking_url = "https://www.korail.com/ticket/reservation/list"
        watch.next_check_at = None
        candidate = WatchCandidate(
            train_number="238",
            departure_at=observed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        suppressed = WatchCandidate(
            train_number="240",
            departure_at=observed_at + timedelta(days=1, minutes=10),
            seat_class="standard",
            priority=2,
            state="suppressed_by_priority",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:expired-hold",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            credential_version=3,
            payment_deadline=observed_at - timedelta(minutes=1),
            confirmation_outcome=(ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=observed_at - timedelta(minutes=2),
            reconciliation_attempt_count=2,
        )
        watch.candidates.extend([candidate, suppressed])
        session.add(watch)
        await session.flush()
        suppressed.suppressed_by_candidate_id = candidate.id

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                source="korail-reservation-list",
                observed_at=observed_at,
                payment_deadline=observed_at - timedelta(minutes=1),
                official_handoff_url="https://www.korail.com/ticket/reservation/list",
            ),
            reconciled_at=observed_at,
        )
        assert attempt.reconciliation_attempt_count == 3
        assert attempt.post_deadline_reconciled_at is None
        assert attempt.next_reconcile_at == observed_at + timedelta(seconds=30)

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.NOT_FOUND,
                source="korail-reservation-list",
                observed_at=observed_at,
            ),
            reconciled_at=observed_at + timedelta(seconds=31),
        )
        repeated, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:must-stay-fenced",
            episode_key="availability:later",
            retry_authorized=True,
        )
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch.id,
                OutboxEvent.event_type == "watch.payment_hold_ended_monitoring_resumed",
            )
        )

        assert watch.status is WatchStatus.WATCHING
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None
        assert watch.next_check_at == observed_at + timedelta(seconds=31)
        assert candidate.state == "observed"
        assert suppressed.state == "observed"
        assert suppressed.suppressed_by_candidate_id is None
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempt.post_deadline_reconciled_at == observed_at + timedelta(seconds=31)
        assert attempt.next_reconcile_at is None
        assert repeated.id == attempt.id
        assert created is False
        assert event is not None
        assert event.payload["automatic_reservation_retry"] is True

        unavailable = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.SOLD_OUT,
            source="authorized-provider",
            observed_at=observed_at + timedelta(seconds=32),
            fresh_until=observed_at + timedelta(seconds=62),
        )
        rediscovered = SeatObservation(
            candidate_id=candidate.id,
            status=SeatObservationStatus.AVAILABLE,
            source="authorized-provider",
            observed_at=observed_at + timedelta(seconds=33),
            fresh_until=observed_at + timedelta(seconds=63),
        )
        session.add_all([unavailable, rediscovered])
        await session.flush()
        retried, retry_created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:new-availability-edge",
            episode_key=payment_hold_retry_episode_key(attempt.id, unavailable.id),
            retry_authorized=True,
        )
        assert retry_created is True
        assert retried.attempt_sequence == 2


async def test_final_expired_confirmed_hold_resumes_monitoring_without_payment_wait(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    deadline = observed_at - timedelta(minutes=1)
    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="expired-srt-payment-hold",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        watch = make_watch(provider=Provider.SRT, status=WatchStatus.PAYMENT_REQUIRED)
        watch.reservation_policy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        watch.payment_deadline = deadline
        watch.official_booking_url = "https://etk.srail.kr/hpg/hra/02/selectReservationList.do"
        candidate = WatchCandidate(
            train_number="370",
            departure_at=observed_at + timedelta(hours=12),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:expired-confirmed-hold",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            credential_version=3,
            payment_deadline=deadline,
            confirmation_outcome=(ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
            confirmation_source="srtrain-reservation-list",
            confirmation_observed_at=deadline - timedelta(seconds=30),
            reconciliation_attempt_count=3,
        )
        watch.candidates.append(candidate)
        session.add_all([channel, watch])
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.SRT,
                outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                source="srtrain-reservation-list",
                observed_at=observed_at,
                payment_deadline=deadline,
                official_handoff_url=("https://etk.srail.kr/hpg/hra/02/selectReservationList.do"),
            ),
            reconciled_at=observed_at,
        )
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch.id,
                OutboxEvent.event_type == "watch.payment_hold_ended_monitoring_resumed",
            )
        )
        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == channel.id,
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.payload["watch_id"].as_string() == watch.id,
                OutboxEvent.payload["status"].as_string() == WatchStatus.WATCHING.value,
            )
        )
        repeated, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reserve:must-remain-fenced-after-expired-confirmed-hold",
            episode_key="availability:same-hold",
            retry_authorized=True,
        )

        assert watch.status is WatchStatus.WATCHING
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None
        assert watch.next_check_at == observed_at
        assert candidate.state == "observed"
        assert attempt.outcome is ReservationOutcome.PAYMENT_REQUIRED
        assert attempt.post_deadline_reconciled_at == observed_at
        assert services_module.is_payment_hold_ended(attempt) is True
        assert repeated.id == attempt.id
        assert created is False
        assert event is not None
        assert event.payload == {
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "terminal": True,
            "status": "watching",
            "from": "payment_required",
            "to": "watching",
            "reason": "confirmed_payment_deadline_elapsed",
            "message": "임시 예약이 결제기한 안에 결제되지 않아 취소되었습니다.",
            "payment_deadline": deadline.isoformat(),
            "automatic_reservation_retry": True,
            "retry_condition": "new_availability_episode",
        }
        assert notification is not None
        message_lines = notification.payload["message"].splitlines()
        assert len(message_lines) == 2
        assert message_lines[0].startswith("SRT · 370 · 2026년 8월 1일 (토) · 수서 ")
        assert message_lines[0].endswith("→ 부산 도착시각 미확인 · 일반실 · 1명")
        assert message_lines[1] == (
            "임시 예약이 결제기한 안에 결제되지 않아 취소되었습니다. "
            "좌석 감시를 다시 시작합니다. 같은 가용성 구간에서는 바로 다시 예매하지 않습니다."
        )


async def test_third_pre_deadline_not_found_keeps_confirmed_hold_fail_closed(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.PAYMENT_REQUIRED)
        watch.reservation_policy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        watch.payment_deadline = observed_at + timedelta(minutes=10)
        candidate = WatchCandidate(
            train_number="238",
            departure_at=observed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:pre-deadline-not-found",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            reconciliation_attempt_count=2,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.NOT_FOUND,
                source="korail-reservation-list",
                observed_at=observed_at,
            ),
            reconciled_at=observed_at,
        )

        assert attempt.reconciliation_attempt_count == 3
        assert attempt.post_deadline_reconciled_at is None
        assert watch.status is WatchStatus.PAYMENT_REQUIRED
        assert candidate.state == "payment_required"


async def test_post_deadline_confirmation_with_extended_deadline_remains_recheckable(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    extended_deadline = observed_at + timedelta(minutes=15)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.PAYMENT_REQUIRED)
        watch.reservation_policy = ReservationPolicy.RESERVE_ONCE_BEFORE_PAYMENT
        watch.payment_deadline = observed_at - timedelta(minutes=1)
        candidate = WatchCandidate(
            train_number="238",
            departure_at=observed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:extended-hold",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            reconciliation_attempt_count=3,
        )
        watch.candidates.append(candidate)
        session.add(watch)
        await session.flush()

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED,
                source="korail-reservation-list",
                observed_at=observed_at,
                payment_deadline=extended_deadline,
                official_handoff_url="https://www.korail.com/ticket/reservation/list",
            ),
            reconciled_at=observed_at,
        )

        assert attempt.reconciliation_attempt_count == 3
        assert attempt.post_deadline_reconciled_at is None
        assert attempt.payment_deadline == extended_deadline
        assert watch.payment_deadline == extended_deadline
        assert watch.status is WatchStatus.PAYMENT_REQUIRED


async def test_final_not_found_expires_notify_only_one_off_watch(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)
    async with factory() as session:
        watch = make_watch(provider=Provider.KORAIL, status=WatchStatus.PAYMENT_REQUIRED)
        watch.reservation_policy = ReservationPolicy.NOTIFY_ONLY
        watch.payment_deadline = observed_at - timedelta(minutes=1)
        watch.official_booking_url = "https://www.korail.com/ticket/reservation/list"
        candidate = WatchCandidate(
            train_number="246",
            departure_at=observed_at + timedelta(days=1),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        suppressed = WatchCandidate(
            train_number="248",
            departure_at=observed_at + timedelta(days=1, minutes=10),
            seat_class="standard",
            priority=2,
            state="suppressed_by_priority",
        )
        attempt = ReservationAttempt(
            candidate=candidate,
            attempt_sequence=1,
            episode_key="availability:first",
            idempotency_key="reserve:one-off-expired-hold",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            credential_version=3,
            payment_deadline=observed_at - timedelta(minutes=1),
            confirmation_outcome=(ReservationConfirmationOutcome.CONFIRMED_PAYMENT_REQUIRED),
            confirmation_source="korail-reservation-list",
            confirmation_observed_at=observed_at - timedelta(minutes=1),
            reconciliation_attempt_count=3,
        )
        watch.candidates.extend([candidate, suppressed])
        session.add(watch)
        await session.flush()
        suppressed.suppressed_by_candidate_id = candidate.id

        await apply_reservation_reconciliation(
            session,
            watch,
            candidate,
            attempt,
            ReservationConfirmationResult(
                provider=Provider.KORAIL,
                outcome=ReservationConfirmationOutcome.NOT_FOUND,
                source="korail-reservation-list",
                observed_at=observed_at,
            ),
            reconciled_at=observed_at,
        )

        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch.id,
                OutboxEvent.event_type == "watch.payment_hold_ended_one_off_expired",
            )
        )
        assert watch.status is WatchStatus.EXPIRED
        assert candidate.state == "expired"
        assert suppressed.state == "expired"
        assert suppressed.suppressed_by_candidate_id is None
        assert watch.payment_deadline is None
        assert watch.official_booking_url is None
        assert attempt.post_deadline_reconciled_at == observed_at
        assert event is not None
        assert event.payload["automatic_reservation_retry"] is False


async def test_failed_reservation_persists_failure_and_resumes_monitoring(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    observed_at = datetime.now(UTC)

    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="reservation-failure-monitoring",
            config_ciphertext="encrypted-test-placeholder",
            enabled=True,
        )
        watch = make_watch(provider=Provider.MOCK, status=WatchStatus.SEAT_FOUND)
        candidate = WatchCandidate(
            train_number="MOCK-FAILED-001",
            departure_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            seat_class="standard",
            priority=1,
            state="seat_found",
        )
        watch.candidates.append(candidate)
        session.add_all([channel, watch])
        await session.flush()

        attempt, created = await begin_reservation_attempt(
            session,
            watch,
            candidate,
            "reservation-failed-monitoring-test",
        )
        assert created is True

        await complete_reservation_attempt(
            session,
            watch,
            candidate,
            attempt,
            ReservationResult(
                outcome=ReservationOutcome.FAILED,
                source="mock",
                observed_at=observed_at,
                progress_stages=(
                    ReservationProgressStage(
                        stage="authenticated_session_ready",
                        occurred_at=observed_at,
                    ),
                ),
            ),
        )
        await session.flush()

        transition = await session.scalar(
            select(WatchTransitionHistory)
            .where(
                WatchTransitionHistory.watch_id == watch.id,
                WatchTransitionHistory.to_status == WatchStatus.WATCHING,
            )
            .order_by(WatchTransitionHistory.created_at.desc())
        )
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch.id,
                OutboxEvent.event_type == "watch.reservation_failed_monitoring_resumed",
            )
        )
        result_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == watch.id,
                OutboxEvent.event_type == "watch.reservation_result",
            )
        )
        notification = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == channel.id,
                OutboxEvent.event_type == "notification.dispatch_requested",
                OutboxEvent.payload["watch_id"].as_string() == watch.id,
                OutboxEvent.payload["status"].as_string() == WatchStatus.WATCHING.value,
            )
        )

        assert attempt.outcome is ReservationOutcome.FAILED
        assert attempt.finished_at is not None
        assert attempt.finished_at >= observed_at
        assert candidate.state == "observed"
        assert watch.status is WatchStatus.WATCHING
        assert watch.reservation_attempted is True
        assert transition is not None
        assert transition.reason == "reservation_failed_monitoring_resumed"
        assert event is not None
        assert event.payload == {
            "watch_id": watch.id,
            "candidate_id": candidate.id,
            "outcome": "failed",
            "reason": "reservation_failed_monitoring_resumed",
            "monitoring_resumed": True,
        }
        assert result_event is not None
        assert result_event.payload["attempt_started_at"] == attempt.started_at.isoformat()
        assert result_event.payload["attempt_finished_at"] == attempt.finished_at.isoformat()
        assert result_event.payload["progress_stages"] == [
            {
                "stage": "authenticated_session_ready",
                "occurred_at": observed_at.isoformat(),
            }
        ]
        assert notification is not None
        assert "예매 결과를 확정하지 못해" in notification.payload["message"]
        assert "같은 가용성 구간에서는 다시 예매하지 않습니다" in notification.payload["message"]
