from __future__ import annotations

import ast
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from rail_waitlist.config import get_settings
from rail_waitlist.domain import NotificationKind, OutboxStatus
from rail_waitlist.models import NotificationChannel, OutboxEvent
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
    TEST_NOTIFICATION_TITLE,
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


def _web_push_config(endpoint: str) -> dict[str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    subscription = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": base64.urlsafe_b64encode(public_key).rstrip(b"=").decode(),
            "auth": base64.urlsafe_b64encode(b"A" * 16).rstrip(b"=").decode(),
        },
    }
    return {"subscription_info": json.dumps(subscription)}


def _expected_device_key(endpoint: str) -> str:
    digest = hashlib.sha256(endpoint.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


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
    requested_at = datetime(2026, 8, 4, 12, 34, 56, tzinfo=UTC)

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
        "body": TEST_NOTIFICATION_MESSAGE,
        "channel_id": channel.id,
        "message": TEST_NOTIFICATION_MESSAGE,
        "status": "seat_found",
        "title": TEST_NOTIFICATION_TITLE,
    }
    assert events[0].dedupe_key == f"notification:{channel.id}:test:{requested_at.isoformat()}"
    assert events[0].status is OutboxStatus.PENDING


async def test_web_push_channels_are_device_scoped_and_idempotent(client) -> None:
    first_endpoint = "https://push.example/subscriptions/chrome"
    second_endpoint = "https://push.example/subscriptions/edge"
    first_payload = {
        "kind": "web_push",
        "name": "Chrome",
        "config": _web_push_config(first_endpoint),
    }

    first = await client.post("/api/v1/notifications/channels", json=first_payload)
    duplicate = await client.post(
        "/api/v1/notifications/channels",
        json={**first_payload, "name": "Chrome 다시 연결"},
    )
    second = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "web_push",
            "name": "Edge",
            "config": _web_push_config(second_endpoint),
        },
    )
    listed = await client.get("/api/v1/notifications/channels")

    assert first.status_code == duplicate.status_code == second.status_code == 201
    assert first.json()["id"] == duplicate.json()["id"]
    assert first.json()["device_key"] == _expected_device_key(first_endpoint)
    assert duplicate.json()["name"] == "Chrome 다시 연결"
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["device_key"] == _expected_device_key(second_endpoint)
    assert second.json()["active_device_count"] == 2
    web_push_rows = [row for row in listed.json() if row["kind"] == "web_push"]
    assert len(web_push_rows) == 2
    assert {row["active_device_count"] for row in web_push_rows} == {2}
    assert all("endpoint" not in json.dumps(row) for row in web_push_rows)


async def test_web_push_create_backfills_matching_legacy_channel(db_engine) -> None:
    endpoint = "https://push.example/subscriptions/legacy"
    config = _web_push_config(endpoint)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        legacy = NotificationChannel(
            kind=NotificationKind.WEB_PUSH,
            name="기존 브라우저",
            config_ciphertext=secret_box.encrypt_dict(config),
        )
        session.add(legacy)
        await session.commit()
        legacy_id = legacy.id

        resolved = await create_notification_channel(
            session,
            FeatureNotificationChannelCreate(
                kind=NotificationKind.WEB_PUSH,
                name="기존 브라우저 재연결",
                config=config,
            ),
        )
        rows = list(
            (
                await session.scalars(
                    select(NotificationChannel).where(
                        NotificationChannel.kind == NotificationKind.WEB_PUSH
                    )
                )
            ).all()
        )

    assert resolved.id == legacy_id
    assert resolved.web_push_device_key == _expected_device_key(endpoint)
    assert len(rows) == 1


async def test_web_push_list_lazy_backfills_and_collapses_legacy_duplicates(
    client, db_engine
) -> None:
    duplicate_endpoint = "https://push.example/subscriptions/duplicate"
    other_endpoint = "https://push.example/subscriptions/other"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                NotificationChannel(
                    kind=NotificationKind.WEB_PUSH,
                    name="canonical",
                    config_ciphertext=secret_box.encrypt_dict(_web_push_config(duplicate_endpoint)),
                    enabled=False,
                    created_at=datetime(2026, 8, 7, 1, tzinfo=UTC),
                ),
                NotificationChannel(
                    kind=NotificationKind.WEB_PUSH,
                    name="duplicate",
                    config_ciphertext=secret_box.encrypt_dict(_web_push_config(duplicate_endpoint)),
                    enabled=True,
                    created_at=datetime(2026, 8, 7, 2, tzinfo=UTC),
                ),
                NotificationChannel(
                    kind=NotificationKind.WEB_PUSH,
                    name="other",
                    config_ciphertext=secret_box.encrypt_dict(_web_push_config(other_endpoint)),
                    enabled=True,
                ),
                NotificationChannel(
                    kind=NotificationKind.WEB_PUSH,
                    name="invalid legacy row",
                    config_ciphertext=secret_box.encrypt_dict({"subscription_info": "invalid"}),
                    enabled=True,
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/v1/notifications/channels")

    assert response.status_code == 200
    web_push_rows = [row for row in response.json() if row["kind"] == "web_push"]
    assert {row["name"] for row in web_push_rows} == {"canonical", "other"}
    assert {row["device_key"] for row in web_push_rows} == {
        _expected_device_key(duplicate_endpoint),
        _expected_device_key(other_endpoint),
    }
    assert {row["active_device_count"] for row in web_push_rows} == {2}

    async with factory() as session:
        persisted = list(
            (
                await session.scalars(
                    select(NotificationChannel)
                    .where(NotificationChannel.kind == NotificationKind.WEB_PUSH)
                    .order_by(NotificationChannel.created_at, NotificationChannel.id)
                )
            ).all()
        )
    persisted_by_name = {channel.name: channel for channel in persisted}
    assert persisted_by_name["canonical"].enabled is True
    assert persisted_by_name["canonical"].web_push_device_key == _expected_device_key(
        duplicate_endpoint
    )
    assert persisted_by_name["duplicate"].enabled is False
    assert persisted_by_name["duplicate"].web_push_device_key is None
    assert persisted_by_name["other"].enabled is True
    assert persisted_by_name["other"].web_push_device_key == _expected_device_key(other_endpoint)
    assert persisted_by_name["invalid legacy row"].enabled is False
    assert persisted_by_name["invalid legacy row"].web_push_device_key is None


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


async def test_retired_native_notification_routes_are_not_exposed(public_client) -> None:
    for method, path in (
        ("post", "/api/v1/notifications/native-pairings"),
        ("post", "/api/v1/notifications/native-pairings/redeem"),
        ("put", "/api/v1/notifications/native-registrations/current"),
        ("delete", "/api/v1/notifications/native-registrations/current"),
        ("put", "/api/v1/notifications/native-devices/android"),
        ("post", "/api/v1/notifications/native-devices/android/unregister"),
    ):
        response = await getattr(public_client, method)(path)
        assert response.status_code == 404


async def test_retired_native_notification_kind_cannot_be_created(client) -> None:
    response = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "android_fcm",
            "name": "retired native channel",
            "config": {"token": "must-not-be-accepted"},
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "native push notification channels are no longer supported"
    }


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


async def test_notification_management_trims_names_and_rejects_blank_required_config(
    client,
) -> None:
    created = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "  운영 알림  ",
            "config": {"bot_token": "  test-token  ", "chat_id": " 123 "},
        },
    )
    blank_name = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "   ",
            "config": {"bot_token": "test-token", "chat_id": "123"},
        },
    )
    blank_token = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "telegram",
            "name": "빈 토큰",
            "config": {"bot_token": "   ", "chat_id": "123"},
        },
    )

    assert created.status_code == 201
    assert created.json()["name"] == "운영 알림"
    assert created.json()["created_at"].endswith(("Z", "+00:00"))
    assert created.json()["updated_at"].endswith(("Z", "+00:00"))
    assert blank_name.status_code == 422
    assert blank_token.status_code == 422
    assert blank_token.json() == {"detail": "empty or invalid channel fields: bot_token"}


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
