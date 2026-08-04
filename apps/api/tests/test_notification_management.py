from rail_waitlist.config import get_settings
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
from rail_waitlist.schemas import (
    NotificationChannelCreate as CompatibilityNotificationChannelCreate,
)
from rail_waitlist.schemas import NotificationChannelRead as CompatibilityNotificationChannelRead
from rail_waitlist.schemas import (
    NotificationChannelUpdate as CompatibilityNotificationChannelUpdate,
)
from rail_waitlist.schemas import QueuedResponse as CompatibilityQueuedResponse


def test_notification_management_schemas_keep_compatibility_exports() -> None:
    assert CompatibilityNotificationChannelCreate is FeatureNotificationChannelCreate
    assert CompatibilityNotificationChannelUpdate is FeatureNotificationChannelUpdate
    assert CompatibilityNotificationChannelRead is FeatureNotificationChannelRead
    assert CompatibilityQueuedResponse is FeatureQueuedResponse


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
