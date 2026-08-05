from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import rail_waitlist.notification_management.watch_transition_application as application_module
import rail_waitlist.services as services_module
from rail_waitlist.domain import NotificationKind, Provider, WatchStatus
from rail_waitlist.models import (
    NotificationChannel,
    OutboxEvent,
    Watch,
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
