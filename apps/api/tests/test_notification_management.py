from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.config import get_settings
from rail_waitlist.domain import NotificationKind, OutboxStatus
from rail_waitlist.models import OutboxEvent
from rail_waitlist.notification_management.http import router
from rail_waitlist.notification_management.schemas import (
    NotificationChannelCreate as FeatureNotificationChannelCreate,
)
from rail_waitlist.notification_management.schemas import (
    NotificationChannelRead as FeatureNotificationChannelRead,
)
from rail_waitlist.notification_management.schemas import (
    NotificationChannelUpdate as FeatureNotificationChannelUpdate,
)
from rail_waitlist.notification_management.schemas import QueuedResponse as FeatureQueuedResponse
from rail_waitlist.notification_management.service import (
    TEST_NOTIFICATION_MESSAGE,
    create_notification_channel,
    queue_test_notification,
    update_notification_channel,
)
from rail_waitlist.outbox import add_outbox_event as feature_add_outbox_event
from rail_waitlist.schemas import (
    NotificationChannelCreate as CompatibilityNotificationChannelCreate,
)
from rail_waitlist.schemas import NotificationChannelRead as CompatibilityNotificationChannelRead
from rail_waitlist.schemas import (
    NotificationChannelUpdate as CompatibilityNotificationChannelUpdate,
)
from rail_waitlist.schemas import QueuedResponse as CompatibilityQueuedResponse
from rail_waitlist.security import secret_box
from rail_waitlist.services import add_outbox_event as compatibility_add_outbox_event

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "rail_waitlist"


def test_notification_management_schemas_keep_compatibility_exports() -> None:
    assert CompatibilityNotificationChannelCreate is FeatureNotificationChannelCreate
    assert CompatibilityNotificationChannelUpdate is FeatureNotificationChannelUpdate
    assert CompatibilityNotificationChannelRead is FeatureNotificationChannelRead
    assert CompatibilityQueuedResponse is FeatureQueuedResponse


def test_notification_management_service_owns_policy_without_legacy_service_dependency() -> None:
    http_source = (SOURCE_ROOT / "notification_management" / "http.py").read_text(encoding="utf-8")
    service_source = (SOURCE_ROOT / "notification_management" / "service.py").read_text(
        encoding="utf-8"
    )
    http_imports = {
        node.module
        for node in ast.walk(ast.parse(http_source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    service_tree = ast.parse(service_source)
    service_import_roots = {
        alias.name.partition(".")[0]
        for node in ast.walk(service_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.partition(".")[0]
        for node in ast.walk(service_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "services" not in http_imports
    assert "fastapi" not in service_import_roots
    assert create_notification_channel.__module__.endswith("notification_management.service")
    assert update_notification_channel.__module__.endswith("notification_management.service")
    assert compatibility_add_outbox_event is feature_add_outbox_event


async def test_notification_management_service_preserves_encryption_and_outbox_contract(
    db_engine,
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    requested_at = datetime(2026, 8, 4, 12, 34, 56)

    async with factory() as session:
        channel = await create_notification_channel(
            session,
            FeatureNotificationChannelCreate(
                kind=NotificationKind.TELEGRAM,
                name="application boundary",
                config={"bot_token": "first-secret", "chat_id": "123"},
            ),
        )
        assert "first-secret" not in channel.config_ciphertext
        assert secret_box.decrypt_dict(channel.config_ciphertext) == {
            "bot_token": "first-secret",
            "chat_id": "123",
        }

        channel = await update_notification_channel(
            session,
            channel,
            FeatureNotificationChannelUpdate(
                config={"bot_token": "second-secret", "chat_id": "456"}
            ),
        )
        assert "second-secret" not in channel.config_ciphertext
        assert secret_box.decrypt_dict(channel.config_ciphertext) == {
            "bot_token": "second-secret",
            "chat_id": "456",
        }

        first = await queue_test_notification(session, channel, requested_at=requested_at)
        duplicate = await queue_test_notification(session, channel, requested_at=requested_at)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notification.test_requested"
                    )
                )
            ).all()
        )

    assert first.queued is True
    assert duplicate.event_id == first.event_id
    assert len(events) == 1
    assert events[0].aggregate_type == "notification_channel"
    assert events[0].aggregate_id == channel.id
    assert events[0].payload == {
        "channel_id": channel.id,
        "message": TEST_NOTIFICATION_MESSAGE,
    }
    assert events[0].dedupe_key == f"notification:{channel.id}:test:{requested_at.isoformat()}"
    assert events[0].status is OutboxStatus.PENDING


def test_notification_management_router_owns_existing_routes() -> None:
    route_contracts = {(route.path, frozenset(route.methods or ())) for route in router.routes}
    assert route_contracts == {
        ("/api/v1/notifications/channels", frozenset({"GET"})),
        ("/api/v1/notifications/channels", frozenset({"POST"})),
        ("/api/v1/notifications/channels/{channel_id}", frozenset({"DELETE"})),
        ("/api/v1/notifications/channels/{channel_id}", frozenset({"GET"})),
        ("/api/v1/notifications/channels/{channel_id}", frozenset({"PATCH"})),
        (
            "/api/v1/notifications/channels/{channel_id}/test-send",
            frozenset({"POST"}),
        ),
        ("/api/v1/notifications/web-push/public-key", frozenset({"GET"})),
    }


async def test_notification_management_routes_require_admin_session(public_client) -> None:
    response = await public_client.get("/api/v1/notifications/channels")

    assert response.status_code == 401


async def test_notification_management_routes_keep_error_details(client) -> None:
    missing = await client.get("/api/v1/notifications/channels/missing")
    disabled = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "disabled",
            "config": {"bot_token": "test-token", "chat_id": "123"},
            "enabled": False,
        },
    )
    disabled_send = await client.post(
        f"/api/v1/notifications/channels/{disabled.json()['id']}/test-send"
    )

    assert missing.status_code == 404
    assert missing.json() == {"detail": "notification channel not found"}
    assert disabled_send.status_code == 409
    assert disabled_send.json() == {"detail": "notification channel is disabled"}


async def test_notification_management_invalid_update_rolls_back_and_keeps_422_detail(
    client,
) -> None:
    created = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "before invalid update",
            "config": {"bot_token": "test-token", "chat_id": "123"},
        },
    )
    channel_url = f"/api/v1/notifications/channels/{created.json()['id']}"

    invalid_update = await client.patch(
        channel_url,
        json={"name": "must roll back", "config": {"bot_token": "replacement-token"}},
    )
    fetched = await client.get(channel_url)
    unsafe_webhook = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "generic_webhook",
            "name": "unsafe",
            "config": {"url": "https://127.0.0.1/internal"},
        },
    )

    assert invalid_update.status_code == 422
    assert invalid_update.json() == {"detail": "missing channel fields: chat_id"}
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "before invalid update"
    assert unsafe_webhook.status_code == 422
    assert unsafe_webhook.json() == {"detail": "webhook URL must target a public IP address"}


async def test_notification_management_delete_commits(client) -> None:
    created = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "temporary",
            "config": {"bot_token": "test-token", "chat_id": "123"},
        },
    )
    channel_url = f"/api/v1/notifications/channels/{created.json()['id']}"

    deleted = await client.delete(channel_url)
    fetched_after_delete = await client.get(channel_url)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert fetched_after_delete.status_code == 404
    assert fetched_after_delete.json() == {"detail": "notification channel not found"}


async def test_webpush_public_key_keeps_unconfigured_503_and_cache_headers(client) -> None:
    settings = get_settings()
    previous = settings.webpush_vapid_public_key
    settings.webpush_vapid_public_key = None
    try:
        response = await client.get("/api/v1/notifications/web-push/public-key")
    finally:
        settings.webpush_vapid_public_key = previous

    assert response.status_code == 503
    assert response.json() == {"detail": "Web Push VAPID public key is not configured"}
    assert "cache-control" not in response.headers
