from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist import worker as worker_module
from rail_waitlist.domain import NotificationKind, OutboxStatus
from rail_waitlist.models import NotificationChannel, OutboxEvent
from rail_waitlist.notification_management import delivery as delivery_module
from rail_waitlist.notifications import NotificationDeliveryError
from rail_waitlist.security import secret_box


class DeliveryCounter:
    def __init__(self) -> None:
        self.results: list[str] = []

    def labels(self, result: str) -> DeliveryCounter:
        self.results.append(result)
        return self

    def inc(self) -> None:
        return None


class PendingGauge:
    def __init__(self) -> None:
        self.values: list[int] = []

    def set(self, value: int) -> None:
        self.values.append(value)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _event(
    aggregate_id: str,
    dedupe_key: str,
    *,
    message: str,
    created_at: datetime,
    available_at: datetime | None = None,
    event_type: str = "notification.test_requested",
    attempts: int = 0,
) -> OutboxEvent:
    return OutboxEvent(
        aggregate_type="notification_channel",
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={"message": message},
        dedupe_key=dedupe_key,
        attempts=attempts,
        available_at=available_at or created_at,
        created_at=created_at,
    )


def _configure_owner(app, monkeypatch: pytest.MonkeyPatch) -> tuple[DeliveryCounter, PendingGauge]:
    counter = DeliveryCounter()
    gauge = PendingGauge()
    monkeypatch.setattr(delivery_module, "SessionFactory", app.state.test_session_factory)
    monkeypatch.setattr(delivery_module, "OUTBOX_DELIVERIES", counter)
    monkeypatch.setattr(delivery_module, "OUTBOX_PENDING", gauge)
    return counter, gauge


async def test_delivery_isolates_decrypt_failure_and_sends_following_event(
    app, db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter, gauge = _configure_owner(app, monkeypatch)
    now = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        poison_channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="poison",
            config_ciphertext="not-a-fernet-token",
        )
        normal_channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="normal",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
        )
        session.add_all([poison_channel, normal_channel])
        await session.flush()
        poison = _event(
            poison_channel.id,
            "poison-event",
            message="poison",
            created_at=now - timedelta(seconds=1),
            attempts=4,
        )
        normal = _event(normal_channel.id, "normal-event", message="normal", created_at=now)
        session.add_all([poison, normal])
        await session.commit()
        poison_id, normal_id = poison.id, normal.id

    delivered_messages: list[str] = []

    async def fake_deliver(_kind, _config, payload) -> None:
        delivered_messages.append(payload["message"])

    monkeypatch.setattr(delivery_module, "deliver_notification", fake_deliver)

    assert await delivery_module.deliver_pending_notifications() == 1

    async with factory() as session:
        poison = await session.get(OutboxEvent, poison_id)
        normal = await session.get(OutboxEvent, normal_id)
        assert poison is not None and normal is not None
        assert poison.status is OutboxStatus.FAILED
        assert poison.attempts == 5
        assert poison.last_error == "config_decrypt_failed"
        assert poison.processed_at is not None
        assert normal.status is OutboxStatus.SENT
        assert normal.attempts == 1
        assert normal.last_error is None
        assert normal.processed_at is not None
    assert delivered_messages == ["normal"]
    assert counter.results == ["failed", "sent"]
    assert gauge.values == [0]


async def test_delivery_marks_missing_and_disabled_channels_terminal(
    app, db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter, gauge = _configure_owner(app, monkeypatch)
    now = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        disabled = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="disabled",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
            enabled=False,
        )
        session.add(disabled)
        await session.flush()
        missing = _event(
            "missing-channel", "missing-channel-event", message="missing", created_at=now
        )
        disabled_event = _event(
            disabled.id,
            "disabled-channel-event",
            message="disabled",
            created_at=now + timedelta(microseconds=1),
        )
        session.add_all([missing, disabled_event])
        await session.commit()
        event_ids = [missing.id, disabled_event.id]

    async def fail_if_called(*_args) -> None:
        raise AssertionError("disabled or missing channels must not invoke delivery")

    monkeypatch.setattr(delivery_module, "deliver_notification", fail_if_called)
    assert await delivery_module.deliver_pending_notifications() == 0

    async with factory() as session:
        events = [await session.get(OutboxEvent, event_id) for event_id in event_ids]
        assert all(event is not None for event in events)
        assert all(event.status is OutboxStatus.FAILED for event in events if event is not None)
        assert all(event.attempts == 0 for event in events if event is not None)
        assert all(
            event.last_error == "channel_missing_or_disabled"
            for event in events
            if event is not None
        )
        assert all(event.processed_at is not None for event in events if event is not None)
    assert counter.results == ["failed", "failed"]
    assert gauge.values == [0]


async def test_expired_web_push_disables_only_that_device(
    app, db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter, gauge = _configure_owner(app, monkeypatch)
    now = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        expired = NotificationChannel(
            kind=NotificationKind.WEB_PUSH,
            name="expired phone",
            config_ciphertext=secret_box.encrypt_dict({"device": "expired"}),
        )
        active = NotificationChannel(
            kind=NotificationKind.WEB_PUSH,
            name="active desktop",
            config_ciphertext=secret_box.encrypt_dict({"device": "active"}),
        )
        session.add_all([expired, active])
        await session.flush()
        expired_event = _event(
            expired.id,
            "expired-web-push",
            message="expired",
            created_at=now,
        )
        active_event = _event(
            active.id,
            "active-web-push",
            message="active",
            created_at=now + timedelta(microseconds=1),
        )
        session.add_all([expired_event, active_event])
        await session.commit()
        expired_id, active_id = expired.id, active.id
        expired_event_id, active_event_id = expired_event.id, active_event.id

    async def deliver_by_device(_kind, config, _payload) -> None:
        if config["device"] == "expired":
            raise NotificationDeliveryError(
                "webpush_subscription_expired",
                permanent=True,
                disable_channel=True,
            )

    monkeypatch.setattr(delivery_module, "deliver_notification", deliver_by_device)
    assert await delivery_module.deliver_pending_notifications() == 1

    async with factory() as session:
        expired = await session.get(NotificationChannel, expired_id)
        active = await session.get(NotificationChannel, active_id)
        expired_event = await session.get(OutboxEvent, expired_event_id)
        active_event = await session.get(OutboxEvent, active_event_id)

    assert expired is not None and expired.enabled is False
    assert active is not None and active.enabled is True
    assert expired_event is not None and expired_event.status is OutboxStatus.FAILED
    assert active_event is not None and active_event.status is OutboxStatus.SENT
    assert counter.results == ["failed", "sent"]
    assert gauge.values == [0]


@pytest.mark.parametrize(
    ("initial_attempts", "expected_delay_seconds"),
    [(0, 30), (1, 60), (2, 120), (3, 240)],
)
async def test_delivery_retries_first_four_failures_with_bounded_backoff(
    app,
    db_engine,
    monkeypatch: pytest.MonkeyPatch,
    initial_attempts: int,
    expected_delay_seconds: int,
) -> None:
    counter, gauge = _configure_owner(app, monkeypatch)
    before = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name=f"retry-{initial_attempts}",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
        )
        session.add(channel)
        await session.flush()
        event = _event(
            channel.id,
            f"retry-event-{initial_attempts}",
            message="retry",
            created_at=before,
            attempts=initial_attempts,
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    error_category = "safe_category_" + ("x" * 100)

    async def fail_delivery(*_args) -> None:
        raise NotificationDeliveryError(error_category)

    monkeypatch.setattr(delivery_module, "deliver_notification", fail_delivery)
    assert await delivery_module.deliver_pending_notifications() == 0
    after = datetime.now(UTC)

    async with factory() as session:
        event = await session.get(OutboxEvent, event_id)
        assert event is not None
        assert event.status is OutboxStatus.PENDING
        assert event.attempts == initial_attempts + 1
        assert event.processed_at is None
        assert event.last_error == error_category[:80]
        available_at = _as_utc(event.available_at)
        assert before + timedelta(seconds=expected_delay_seconds) <= available_at
        assert available_at <= after + timedelta(seconds=expected_delay_seconds)
    assert counter.results == []
    assert gauge.values == [1]


async def test_delivery_marks_fifth_safe_failure_terminal(
    app, db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter, gauge = _configure_owner(app, monkeypatch)
    now = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="terminal",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
        )
        session.add(channel)
        await session.flush()
        event = _event(
            channel.id,
            "terminal-event",
            message="terminal",
            created_at=now,
            attempts=4,
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    async def fail_delivery(*_args) -> None:
        raise NotificationDeliveryError("delivery_category_only")

    monkeypatch.setattr(delivery_module, "deliver_notification", fail_delivery)
    assert await delivery_module.deliver_pending_notifications() == 0

    async with factory() as session:
        event = await session.get(OutboxEvent, event_id)
        assert event is not None
        assert event.status is OutboxStatus.FAILED
        assert event.attempts == 5
        assert event.last_error == "delivery_category_only"
        assert event.processed_at is not None
    assert counter.results == ["failed"]
    assert gauge.values == [0]


async def test_delivery_filters_orders_and_limits_each_locked_batch(
    app, db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_owner(app, monkeypatch)
    now = datetime.now(UTC)
    base = now - timedelta(minutes=10)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="batch",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
        )
        session.add(channel)
        await session.flush()
        eligible = [
            _event(
                channel.id,
                f"batch-{index}",
                message=str(index),
                created_at=base + timedelta(microseconds=index),
            )
            for index in range(52)
        ]
        future = _event(
            channel.id,
            "future-event",
            message="future",
            created_at=base - timedelta(seconds=2),
            available_at=now + timedelta(hours=1),
        )
        unrelated = _event(
            channel.id,
            "unrelated-event",
            message="unrelated",
            created_at=base - timedelta(seconds=1),
            event_type="watch.status_changed",
        )
        session.add_all([*eligible, future, unrelated])
        await session.commit()
        eligible_ids = [event.id for event in eligible]
        filtered_ids = [future.id, unrelated.id]

    delivered_messages: list[str] = []

    async def capture_delivery(_kind, _config, payload) -> None:
        delivered_messages.append(payload["message"])

    monkeypatch.setattr(delivery_module, "deliver_notification", capture_delivery)
    assert await delivery_module.deliver_pending_notifications() == 50
    assert delivered_messages == [str(index) for index in range(50)]

    async with factory() as session:
        delivered = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.id.in_(eligible_ids[:50]))
                )
            ).all()
        )
        pending = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.id.in_([*eligible_ids[50:], *filtered_ids])
                    )
                )
            ).all()
        )
        assert all(event.status is OutboxStatus.SENT for event in delivered)
        assert all(event.status is OutboxStatus.PENDING for event in pending)

    compiled = str(
        delivery_module._pending_notification_events(now).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ORDER BY outbox_events.created_at" in compiled
    assert "LIMIT 50" in compiled
    assert "FOR UPDATE SKIP LOCKED" in compiled


async def test_unexpected_delivery_failure_rolls_back_the_whole_batch(
    app, db_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_owner(app, monkeypatch)
    now = datetime.now(UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        channel = NotificationChannel(
            kind=NotificationKind.TELEGRAM,
            name="rollback",
            config_ciphertext=secret_box.encrypt_dict({"bot_token": "token", "chat_id": "1"}),
        )
        session.add(channel)
        await session.flush()
        first = _event(channel.id, "rollback-first", message="first", created_at=now)
        second = _event(
            channel.id,
            "rollback-second",
            message="second",
            created_at=now + timedelta(microseconds=1),
        )
        session.add_all([first, second])
        await session.commit()
        event_ids = [first.id, second.id]

    async def fail_second(_kind, _config, payload) -> None:
        if payload["message"] == "second":
            raise RuntimeError("unexpected transport failure")

    monkeypatch.setattr(delivery_module, "deliver_notification", fail_second)
    with pytest.raises(RuntimeError, match="unexpected transport failure"):
        await delivery_module.deliver_pending_notifications()

    async with factory() as session:
        events = [await session.get(OutboxEvent, event_id) for event_id in event_ids]
        assert all(event is not None for event in events)
        assert all(event.status is OutboxStatus.PENDING for event in events if event is not None)
        assert all(event.attempts == 0 for event in events if event is not None)
        assert all(event.processed_at is None for event in events if event is not None)


def test_worker_keeps_existing_celery_delivery_task_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_delivery() -> int:
        return 7

    monkeypatch.setattr(worker_module, "deliver_pending_notifications", fake_delivery)

    assert worker_module.deliver_outbox.name == "rail_waitlist.worker.deliver_outbox"
    assert worker_module.deliver_outbox.run() == 7
