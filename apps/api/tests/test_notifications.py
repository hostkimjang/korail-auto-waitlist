from __future__ import annotations

import base64
import socket

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid, VapidException
from pywebpush import WebPushException
from requests import Response

from rail_waitlist.domain import NotificationKind
from rail_waitlist.notifications import (
    NotificationDeliveryError,
    deliver_notification,
    normalize_webpush_vapid_private_key,
    validate_webhook_destination,
    validate_webhook_url_syntax,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/hook",
        "https://user:password@example.com/hook",
    ],
)
def test_webhook_url_syntax_blocks_unsafe_destinations(url):
    with pytest.raises(ValueError):
        validate_webhook_url_syntax(url)


async def test_webhook_dns_resolution_blocks_private_destination(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 443))
        ],
    )
    with pytest.raises(NotificationDeliveryError, match="private_destination"):
        await validate_webhook_destination("https://webhook.example/hook")


async def test_webhook_redirect_is_not_followed():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotificationDeliveryError, match="redirect"):
            await deliver_notification(
                NotificationKind.GENERIC_WEBHOOK,
                {"url": "https://8.8.8.8/hook"},
                {"message": "test"},
                client,
            )
    assert len(requests) == 1
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].headers["host"] == "8.8.8.8"


async def test_malformed_web_push_subscription_becomes_delivery_error():
    with pytest.raises(NotificationDeliveryError, match="^webpush_subscription_invalid$"):
        await deliver_notification(
            NotificationKind.WEB_PUSH,
            {"subscription_info": "not-json"},
            {"message": "test"},
        )


async def test_web_push_delivery_normalizes_in_memory_pem_before_sender(monkeypatch):
    from rail_waitlist.config import get_settings

    private_key = ec.generate_private_key(ec.SECP256R1())
    subscription_private_key = ec.generate_private_key(ec.SECP256R1())
    subscription_public_key = subscription_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    captured: dict[str, object] = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)

    settings = get_settings()
    previous = settings.webpush_vapid_private_key
    settings.webpush_vapid_private_key = pem
    monkeypatch.setattr("rail_waitlist.notifications.webpush", fake_webpush)
    try:
        await deliver_notification(
            NotificationKind.WEB_PUSH,
            {
                "subscription_info": {
                    "endpoint": "https://push.example/subscription",
                    "keys": {
                        "p256dh": base64.urlsafe_b64encode(subscription_public_key)
                        .rstrip(b"=")
                        .decode(),
                        "auth": base64.urlsafe_b64encode(b"A" * 16).rstrip(b"=").decode(),
                    },
                }
            },
            {"message": "test"},
        )
    finally:
        settings.webpush_vapid_private_key = previous

    normalized = captured["vapid_private_key"]
    assert isinstance(normalized, str)
    assert "PRIVATE KEY" not in normalized
    assert captured["subscription_info"] == {
        "endpoint": "https://push.example/subscription",
        "keys": {
            "p256dh": base64.urlsafe_b64encode(subscription_public_key).rstrip(b"=").decode(),
            "auth": base64.urlsafe_b64encode(b"A" * 16).rstrip(b"=").decode(),
        },
    }


@pytest.mark.parametrize(
    ("status_code", "category"),
    [
        (410, "webpush_subscription_expired"),
        (401, "webpush_vapid_auth_failed"),
        (400, "webpush_subscription_rejected"),
        (503, "webpush_provider_error"),
    ],
)
async def test_web_push_provider_failure_is_safely_classified(monkeypatch, status_code, category):
    from rail_waitlist.config import get_settings

    subscription_private_key = ec.generate_private_key(ec.SECP256R1())
    subscription_public_key = subscription_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    response = Response()
    response.status_code = status_code

    def fake_webpush(**kwargs):
        raise WebPushException("provider response omitted", response=response)

    settings = get_settings()
    previous = settings.webpush_vapid_private_key
    settings.webpush_vapid_private_key = base64.urlsafe_b64encode(b"B" * 32).rstrip(b"=").decode()
    monkeypatch.setattr("rail_waitlist.notifications.webpush", fake_webpush)
    try:
        with pytest.raises(NotificationDeliveryError, match=f"^{category}$"):
            await deliver_notification(
                NotificationKind.WEB_PUSH,
                {
                    "subscription_info": {
                        "endpoint": "https://push.example/subscription",
                        "keys": {
                            "p256dh": base64.urlsafe_b64encode(subscription_public_key)
                            .rstrip(b"=")
                            .decode(),
                            "auth": base64.urlsafe_b64encode(b"A" * 16).rstrip(b"=").decode(),
                        },
                    }
                },
                {"message": "test"},
            )
    finally:
        settings.webpush_vapid_private_key = previous


async def test_invalid_web_push_subscription_does_not_call_sender(monkeypatch):
    called = False

    def fake_webpush(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("rail_waitlist.notifications.webpush", fake_webpush)
    with pytest.raises(NotificationDeliveryError, match="^webpush_subscription_invalid$"):
        await deliver_notification(
            NotificationKind.WEB_PUSH,
            {
                "subscription_info": {
                    "endpoint": "https://push.example/subscription",
                    "keys": {"p256dh": "not-a-key", "auth": "not-an-auth-secret"},
                }
            },
            {"message": "test"},
        )
    assert called is False


def test_pem_webpush_vapid_key_is_converted_to_base64url_der():
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    normalized = normalize_webpush_vapid_private_key(pem)

    padded = normalized + "=" * (-len(normalized) % 4)
    decoded = base64.urlsafe_b64decode(padded)
    restored = serialization.load_der_private_key(decoded, password=None)
    parsed_by_sender = Vapid.from_string(normalized)
    assert restored.private_numbers() == private_key.private_numbers()
    assert parsed_by_sender.private_key.private_numbers() == private_key.private_numbers()
    assert "PRIVATE KEY" not in normalized


async def test_invalid_vapid_subject_is_classified_as_server_configuration(monkeypatch):
    from rail_waitlist.config import get_settings

    subscription_private_key = ec.generate_private_key(ec.SECP256R1())
    subscription_public_key = subscription_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    def fake_webpush(**kwargs):
        raise VapidException("details omitted")

    settings = get_settings()
    previous = settings.webpush_vapid_private_key
    settings.webpush_vapid_private_key = base64.urlsafe_b64encode(b"B" * 32).rstrip(b"=").decode()
    monkeypatch.setattr("rail_waitlist.notifications.webpush", fake_webpush)
    try:
        with pytest.raises(
            NotificationDeliveryError,
            match="^webpush_vapid_configuration_invalid$",
        ):
            await deliver_notification(
                NotificationKind.WEB_PUSH,
                {
                    "subscription_info": {
                        "endpoint": "https://push.example/subscription",
                        "keys": {
                            "p256dh": base64.urlsafe_b64encode(subscription_public_key)
                            .rstrip(b"=")
                            .decode(),
                            "auth": base64.urlsafe_b64encode(b"A" * 16).rstrip(b"=").decode(),
                        },
                    }
                },
                {"message": "test"},
            )
    finally:
        settings.webpush_vapid_private_key = previous


def test_base64url_webpush_vapid_key_is_left_unchanged():
    key = base64.urlsafe_b64encode(b"B" * 32).rstrip(b"=").decode()
    assert normalize_webpush_vapid_private_key(key) == key


def test_invalid_base64url_webpush_vapid_key_has_sanitized_error():
    with pytest.raises(NotificationDeliveryError, match="^webpush_vapid_key_invalid$"):
        normalize_webpush_vapid_private_key("not-a-private-key")


def test_invalid_pem_webpush_vapid_key_has_sanitized_error():
    with pytest.raises(NotificationDeliveryError, match="webpush_vapid_key_invalid"):
        normalize_webpush_vapid_private_key(
            "-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----"
        )
