from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import rail_waitlist.notification_management.watch_transition_application as application_module
import rail_waitlist.services as services_module
from rail_waitlist.domain import (
    NotificationKind,
    Provider,
    ReservationOutcome,
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
from rail_waitlist.notification_management.watch_transition_application import (
    add_watch_notifications,
)
from rail_waitlist.services import (
    add_watch_notifications as compatibility_add_watch_notifications,
)
from rail_waitlist.services import apply_watch_transition


def make_watch(
    *,
    status: WatchStatus = WatchStatus.WATCHING,
    payment_deadline: datetime | None = None,
) -> Watch:
    return Watch(
        provider=Provider.SRT,
        origin="수서",
        origin_node_id="N-SUSEO",
        destination="부산",
        destination_node_id="N-BUSAN",
        travel_date=date(2026, 8, 5),
        time_from=time(12),
        time_to=time(18),
        train_numbers=["SRT-301"],
        notification_channel_ids=[],
        mode="official",
        status=status,
        dedupe_key=f"watch-transition-notification-{status.value}",
        payment_deadline=payment_deadline,
    )


def make_channel(
    channel_id: str,
    *,
    enabled: bool = True,
    created_at: datetime | None = None,
) -> NotificationChannel:
    return NotificationChannel(
        id=channel_id,
        kind=NotificationKind.TELEGRAM,
        name=channel_id,
        config_ciphertext="encrypted-test-placeholder",
        enabled=enabled,
        created_at=created_at or datetime(2026, 8, 5, tzinfo=UTC),
    )


def test_services_keeps_watch_notification_application_identity() -> None:
    assert compatibility_add_watch_notifications is add_watch_notifications


@pytest.mark.parametrize(
    (
        "target",
        "transition_token",
        "reason",
        "payment_deadline",
        "expected_message",
    ),
    [
        (
            WatchStatus.RESERVING,
            "seat_found:reserving:1",
            None,
            None,
            "이번 좌석 가용성에 대한 예매를 진행하고 있습니다.",
        ),
        (
            WatchStatus.PAYMENT_REQUIRED,
            "reserving:payment_required:1",
            None,
            datetime(2026, 8, 5, 1, tzinfo=UTC),
            "08월 05일 10:00까지 공식 플랫폼에서 결제해 주세요.",
        ),
        (
            WatchStatus.PAYMENT_REQUIRED,
            "reserving:payment_required:2",
            None,
            None,
            "공식 플랫폼에서 결제기한을 확인하고 결제해 주세요.",
        ),
        (
            WatchStatus.AUTH_REQUIRED,
            "watching:auth_required:1",
            None,
            None,
            "로그인 또는 사용자 확인이 필요합니다.",
        ),
        (
            WatchStatus.WATCHING,
            "seat_found:watching:1",
            None,
            None,
            "좌석이 다시 판매 불가 상태로 바뀌어 감시를 계속합니다.",
        ),
        (
            WatchStatus.WATCHING,
            "reserving:watching:1",
            "reservation_failed_monitoring_resumed",
            None,
            "같은 가용성 구간에서는 다시 예매하지 않습니다.",
        ),
        (
            WatchStatus.WATCHING,
            "payment_required:watching:1",
            "confirmed_payment_hold_no_longer_actionable_monitoring_resumed",
            None,
            "좌석 감시를 다시 시작합니다.",
        ),
        (
            WatchStatus.EXPIRED,
            "payment_required:expired:1",
            "confirmed_payment_hold_no_longer_actionable_one_off_expired",
            None,
            "해당 1회성 작업을 종료합니다.",
        ),
        (
            WatchStatus.FAILED,
            "reserving:failed:1",
            None,
            None,
            "작업 상태: failed",
        ),
    ],
)
async def test_watch_transition_notification_message_matrix(
    db_engine,
    target: WatchStatus,
    transition_token: str,
    reason: str | None,
    payment_deadline: datetime | None,
    expected_message: str,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        channel = make_channel("message-channel")
        watch = make_watch(payment_deadline=payment_deadline)
        session.add_all([channel, watch])
        await session.flush()

        await add_watch_notifications(
            session,
            watch,
            target,
            transition_token,
            reason=reason,
        )
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.dispatch_requested")
        )

        assert event is not None
        assert expected_message in event.payload["message"]
        assert event.payload["status"] == target.value
        assert event.payload["payment_deadline"] == (
            payment_deadline.isoformat() if payment_deadline is not None else None
        )
        assert event.payload["provider"] == "srt"
        assert event.payload["travel_date"] == "2026-08-05"
        assert event.payload["origin"] == "수서"
        assert event.payload["destination"] == "부산"
        assert event.payload["seat_class"] == "standard"
        assert event.payload["passenger_count"] == 1
        assert event.payload["candidate_id"] is None
        assert event.payload["train_number"] is None
        assert event.payload["departure_at"] is None
        assert event.payload["arrival_at"] is None
        assert event.payload["attempt_sequence"] is None
        assert event.payload["attempt_started_at"] is None
        assert event.payload["attempt_finished_at"] is None
        assert event.payload["workflow_stage"] is None
        assert event.payload["retry_condition"] is None


async def test_observation_candidate_wins_without_leaking_an_unrelated_attempt(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = make_channel("observation-channel")
        watch = make_watch()
        watch.passenger_count = 2
        unrelated = WatchCandidate(
            train_number="SRT-301",
            departure_at=datetime(2026, 8, 5, 3, 10, tzinfo=UTC),
            arrival_at=datetime(2026, 8, 5, 5, 30, tzinfo=UTC),
            seat_class="standard",
            priority=1,
        )
        observed = WatchCandidate(
            train_number="SRT-305",
            departure_at=datetime(2026, 8, 5, 3, 30, tzinfo=UTC),
            arrival_at=datetime(2026, 8, 5, 5, 55, tzinfo=UTC),
            seat_class="standard",
            priority=2,
        )
        watch.candidates.extend([unrelated, observed])
        session.add_all([channel, watch])
        await session.flush()
        session.add(
            ReservationAttempt(
                candidate_id=unrelated.id,
                attempt_sequence=1,
                episode_key="availability:unrelated",
                idempotency_key="notification-unrelated-attempt",
                outcome=ReservationOutcome.NOT_AVAILABLE,
                started_at=datetime(2026, 8, 5, 2, 59, tzinfo=UTC),
                finished_at=datetime(2026, 8, 5, 3, tzinfo=UTC),
            )
        )
        observation = SeatObservation(
            candidate_id=observed.id,
            status=SeatObservationStatus.AVAILABLE,
            source="srt-owner-test",
            observed_at=datetime(2026, 8, 5, 3, 1, tzinfo=UTC),
            fresh_until=datetime(2026, 8, 5, 3, 2, tzinfo=UTC),
        )
        session.add(observation)
        await session.flush()

        await add_watch_notifications(
            session,
            watch,
            WatchStatus.SEAT_FOUND,
            "watching:seat_found:observation",
            observation=observation,
        )
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.dispatch_requested")
        )

        assert event is not None
        assert event.payload["candidate_id"] == observed.id
        assert event.payload["train_number"] == "SRT-305"
        assert event.payload["departure_at"] == "2026-08-05T03:30:00+00:00"
        assert event.payload["arrival_at"] == "2026-08-05T05:55:00+00:00"
        assert event.payload["attempt_sequence"] is None
        assert event.payload["retry_condition"] is None
        assert event.payload["message"] == (
            "SRT · SRT-305 · 2026년 8월 5일 (수) · 수서 12:30 → 부산 14:55 · 일반실 · 2명\n"
            "예매 가능한 좌석을 확인했습니다. 공식 플랫폼에서 최종 상태를 확인해 주세요."
        )


async def test_latest_attempt_selects_its_candidate_and_preserves_cleared_deadline(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    deadline = datetime(2026, 8, 5, 4, tzinfo=UTC)
    async with factory() as session:
        channel = make_channel("attempt-channel")
        watch = make_watch()
        candidate = WatchCandidate(
            train_number="SRT-309",
            departure_at=datetime(2026, 8, 5, 3, 45, tzinfo=UTC),
            arrival_at=datetime(2026, 8, 5, 6, 5, tzinfo=UTC),
            seat_class="standard",
            priority=1,
            state="payment_required",
        )
        watch.candidates.append(candidate)
        session.add_all([channel, watch])
        await session.flush()
        attempt = ReservationAttempt(
            candidate_id=candidate.id,
            attempt_sequence=2,
            episode_key="availability:payment",
            idempotency_key="notification-payment-attempt",
            outcome=ReservationOutcome.PAYMENT_REQUIRED,
            started_at=datetime(2026, 8, 5, 3, 1, tzinfo=UTC),
            finished_at=datetime(2026, 8, 5, 3, 1, 2, tzinfo=UTC),
            payment_deadline=deadline,
        )
        session.add(attempt)
        await session.flush()
        assert watch.payment_deadline is None

        await add_watch_notifications(
            session,
            watch,
            WatchStatus.PAYMENT_REQUIRED,
            "reserving:payment_required:attempt",
        )
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.dispatch_requested")
        )

        assert event is not None
        assert event.payload["candidate_id"] == candidate.id
        assert event.payload["attempt_sequence"] == 2
        assert event.payload["attempt_started_at"] == "2026-08-05T03:01:00+00:00"
        assert event.payload["attempt_finished_at"] == "2026-08-05T03:01:02+00:00"
        assert event.payload["workflow_stage"] == "payment_required"
        assert event.payload["retry_condition"] is None
        assert event.payload["payment_deadline"] == deadline.isoformat()
        assert "08월 05일 13:00까지 공식 플랫폼에서 결제해 주세요." in event.payload["message"]


async def test_completed_notification_never_reuses_historical_attempt_deadline(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    deadline = datetime(2026, 8, 5, 4, tzinfo=UTC)
    async with factory() as session:
        channel = make_channel("completed-channel")
        watch = make_watch(status=WatchStatus.PAYMENT_REQUIRED)
        candidate = WatchCandidate(
            train_number="SRT-309",
            departure_at=datetime(2026, 8, 5, 3, 45, tzinfo=UTC),
            seat_class="standard",
            priority=1,
            state="expired",
        )
        watch.candidates.append(candidate)
        session.add_all([channel, watch])
        await session.flush()
        session.add(
            ReservationAttempt(
                candidate_id=candidate.id,
                attempt_sequence=2,
                episode_key="availability:paid",
                idempotency_key="notification-completed-attempt",
                outcome=ReservationOutcome.PAYMENT_REQUIRED,
                started_at=datetime(2026, 8, 5, 3, 1, tzinfo=UTC),
                finished_at=datetime(2026, 8, 5, 3, 1, 2, tzinfo=UTC),
                payment_deadline=deadline,
            )
        )
        await session.flush()

        await add_watch_notifications(
            session,
            watch,
            WatchStatus.COMPLETED,
            "payment_required:completed:confirmed-paid",
            reason="reservation_reconciliation_confirmed_paid",
        )
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.dispatch_requested")
        )

        assert event is not None
        assert event.payload["status"] == "completed"
        assert event.payload["payment_deadline"] is None
        assert "결제 안내를 종료합니다" in event.payload["message"]


async def test_latest_not_available_attempt_structures_monitoring_retry(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = make_channel("retry-channel")
        watch = make_watch()
        candidate = WatchCandidate(
            train_number="SRT-311",
            departure_at=datetime(2026, 8, 5, 4, tzinfo=UTC),
            arrival_at=None,
            seat_class="standard",
            priority=1,
            state="observed",
        )
        watch.candidates.append(candidate)
        session.add_all([channel, watch])
        await session.flush()
        session.add(
            ReservationAttempt(
                candidate_id=candidate.id,
                attempt_sequence=1,
                episode_key="availability:not-available",
                idempotency_key="notification-not-available-attempt",
                outcome=ReservationOutcome.NOT_AVAILABLE,
                started_at=datetime(2026, 8, 5, 3, 2, tzinfo=UTC),
                finished_at=datetime(2026, 8, 5, 3, 2, 1, tzinfo=UTC),
            )
        )
        await session.flush()

        await add_watch_notifications(
            session,
            watch,
            WatchStatus.WATCHING,
            "reserving:watching:not-available",
            reason="reservation_not_available",
        )
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "notification.dispatch_requested")
        )

        assert event is not None
        assert event.payload["workflow_stage"] == "monitoring_resumed"
        assert event.payload["retry_condition"] == "new_availability_episode"
        assert event.payload["arrival_at"] is None
        assert "도착시각 미확인" in event.payload["message"]


async def test_non_notifiable_transition_does_not_query_or_enqueue(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch()
        session.add_all([make_channel("unused-channel"), watch])
        await session.flush()

        await add_watch_notifications(
            session,
            watch,
            WatchStatus.PAUSED,
            "watching:paused:1",
        )
        events = list((await session.scalars(select(OutboxEvent))).all())

        assert events == []


async def test_enabled_global_channels_are_sorted_and_disabled_channels_are_skipped(
    db_engine,
    monkeypatch,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    calls: list[str] = []

    async def record_outbox_event(
        _session,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
    ) -> object:
        assert aggregate_type == "notification_channel"
        assert event_type == "notification.dispatch_requested"
        assert payload["channel_id"] == aggregate_id
        assert aggregate_id in dedupe_key
        calls.append(aggregate_id)
        return object()

    monkeypatch.setattr(application_module, "add_outbox_event", record_outbox_event)
    early = datetime(2026, 8, 5, tzinfo=UTC)
    late = datetime(2026, 8, 5, 1, tzinfo=UTC)

    async with factory() as session:
        watch = make_watch()
        session.add_all(
            [
                make_channel("enabled-late", created_at=late),
                make_channel("enabled-early-b", created_at=early),
                make_channel("enabled-early-a", created_at=early),
                make_channel("disabled", enabled=False, created_at=early),
                watch,
            ]
        )
        await session.flush()

        await add_watch_notifications(
            session,
            watch,
            WatchStatus.SEAT_FOUND,
            "watching:seat_found:1",
        )

    assert calls == ["enabled-early-a", "enabled-early-b", "enabled-late"]


async def test_transition_token_deduplicates_notification_outbox(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        watch = make_watch()
        session.add_all([make_channel("dedupe-channel"), watch])
        await session.flush()

        for _ in range(2):
            await add_watch_notifications(
                session,
                watch,
                WatchStatus.SEAT_FOUND,
                "watching:seat_found:same-token",
            )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notification.dispatch_requested"
                    )
                )
            ).all()
        )

        assert len(events) == 1
        assert events[0].dedupe_key.endswith(":watching:seat_found:same-token")


@pytest.mark.parametrize("commit_transition", [True, False])
async def test_status_and_notification_outboxes_share_transition_commit_or_rollback(
    db_engine,
    monkeypatch: pytest.MonkeyPatch,
    commit_transition: bool,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    event_types: list[str] = []
    original_add_outbox_event = application_module.add_outbox_event

    async def record_add_outbox_event(*args: Any, **kwargs: Any) -> OutboxEvent:
        event_types.append(kwargs["event_type"])
        return await original_add_outbox_event(*args, **kwargs)

    monkeypatch.setattr(application_module, "add_outbox_event", record_add_outbox_event)
    monkeypatch.setattr(services_module, "add_outbox_event", record_add_outbox_event)

    async with factory() as session:
        watch = make_watch()
        session.add_all([make_channel("atomic-channel"), watch])
        await session.commit()
        watch_id = watch.id

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        assert watch is not None
        await apply_watch_transition(session, watch, WatchStatus.SEAT_FOUND)
        assert event_types == [
            "watch.status_changed",
            "notification.dispatch_requested",
        ]
        if commit_transition:
            await session.commit()
        else:
            await session.rollback()

    async with factory() as session:
        watch = await session.get(Watch, watch_id)
        histories = list(
            (
                await session.scalars(
                    select(WatchTransitionHistory).where(
                        WatchTransitionHistory.watch_id == watch_id
                    )
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.payload["watch_id"].as_string() == watch_id
                    )
                )
            ).all()
        )

        assert watch is not None
        if commit_transition:
            assert watch.status is WatchStatus.SEAT_FOUND
            assert len(histories) == 1
            assert {event.event_type for event in events} == {
                "watch.status_changed",
                "notification.dispatch_requested",
            }
        else:
            assert watch.status is WatchStatus.WATCHING
            assert histories == []
            assert events == []
