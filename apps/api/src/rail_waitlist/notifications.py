from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import VapidException
from pywebpush import WebPushException, webpush

from .config import get_settings
from .domain import NotificationKind


class NotificationDeliveryError(RuntimeError):
    pass


BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata.aws.internal",
}

WEBPUSH_EXPIRED_STATUS_CODES = {404, 410}
WEBPUSH_VAPID_AUTH_STATUS_CODES = {401, 403}


def _decode_base64url(value: str, *, error_category: str) -> bytes:
    try:
        encoded = value.encode("ascii")
        return base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError) as error:
        raise NotificationDeliveryError(error_category) from error


def normalize_webpush_subscription(config: dict[str, Any]) -> dict[str, Any]:
    """Validate browser subscription data before it reaches pywebpush.

    The exception category deliberately contains no endpoint or key material. This
    keeps malformed browser state separate from VAPID server configuration failures.
    """

    try:
        subscription: Any = config["subscription_info"]
        if isinstance(subscription, str):
            subscription = json.loads(subscription)
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise NotificationDeliveryError("webpush_subscription_invalid") from error
    if not isinstance(subscription, dict):
        raise NotificationDeliveryError("webpush_subscription_invalid")

    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys")
    if not isinstance(endpoint, str) or not isinstance(keys, dict):
        raise NotificationDeliveryError("webpush_subscription_invalid")
    try:
        parsed_endpoint = urlsplit(endpoint)
    except (TypeError, ValueError) as error:
        raise NotificationDeliveryError("webpush_subscription_invalid") from error
    if (
        parsed_endpoint.scheme.lower() != "https"
        or not parsed_endpoint.hostname
        or parsed_endpoint.username
        or parsed_endpoint.password
    ):
        raise NotificationDeliveryError("webpush_subscription_invalid")

    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not isinstance(p256dh, str) or not isinstance(auth, str) or not p256dh or not auth:
        raise NotificationDeliveryError("webpush_subscription_invalid")
    public_key = _decode_base64url(p256dh, error_category="webpush_subscription_invalid")
    auth_secret = _decode_base64url(auth, error_category="webpush_subscription_invalid")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    except ValueError as error:
        raise NotificationDeliveryError("webpush_subscription_invalid") from error
    if len(auth_secret) != 16:
        raise NotificationDeliveryError("webpush_subscription_invalid")
    return subscription


def normalize_webpush_vapid_private_key(private_key: str) -> str:
    """Return the in-memory key format accepted by pywebpush.

    pywebpush treats a string as base64url-encoded RAW/DER unless the string is a
    filesystem path. Our deployment intentionally injects the secret directly through
    the environment, so a PEM string must be converted to base64url DER first rather
    than being mistaken for an encoded key.
    """

    try:
        if private_key.lstrip().startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(private_key.encode(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                key.curve, ec.SECP256R1  # gitleaks:allow -- 공개 P-256 곡선 상수
            ):
                raise ValueError("Web Push VAPID requires a P-256 private key")
            der = key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            return base64.urlsafe_b64encode(der).rstrip(b"=").decode("ascii")

        decoded = _decode_base64url(private_key, error_category="webpush_vapid_key_invalid")
        if len(decoded) == 32:
            ec.derive_private_key(int.from_bytes(decoded, "big"), ec.SECP256R1())
        else:
            key = serialization.load_der_private_key(decoded, password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                key.curve, ec.SECP256R1  # gitleaks:allow -- 공개 P-256 곡선 상수
            ):
                raise ValueError("Web Push VAPID requires a P-256 private key")
    except NotificationDeliveryError:
        raise
    except (TypeError, ValueError) as error:
        raise NotificationDeliveryError("webpush_vapid_key_invalid") from error
    return private_key


def _classify_webpush_provider_error(error: WebPushException) -> str:
    response = error.response
    status_code = getattr(response, "status_code", None)
    if status_code in WEBPUSH_EXPIRED_STATUS_CODES:
        return "webpush_subscription_expired"
    if status_code in WEBPUSH_VAPID_AUTH_STATUS_CODES:
        return "webpush_vapid_auth_failed"
    if status_code == 400:
        return "webpush_subscription_rejected"
    return "webpush_provider_error"


def validate_webhook_url_syntax(url: str, *, allow_http: bool = False) -> tuple[str, int]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("webhook URL is invalid") from error
    allowed_schemes = {"https"} | ({"http"} if allow_http else set())
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError("webhook URL must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("webhook URL host is invalid")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        raise ValueError("webhook URL cannot target a local or metadata host")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("webhook URL must target a public IP address")
    return hostname, port or (443 if parsed.scheme.lower() == "https" else 80)


async def validated_webhook_target(url: str) -> tuple[str, str, str]:
    hostname, port = validate_webhook_url_syntax(url)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            resolved = await asyncio.to_thread(
                socket.getaddrinfo, hostname, port, 0, socket.SOCK_STREAM
            )
        except socket.gaierror as error:
            raise NotificationDeliveryError("webhook_dns_resolution_failed") from error
        addresses = {item[4][0] for item in resolved}
    else:
        addresses = {str(literal)}
    if not addresses:
        raise NotificationDeliveryError("webhook_dns_resolution_failed")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value.split("%", 1)[0])
        except ValueError as error:
            raise NotificationDeliveryError("webhook_dns_result_invalid") from error
        if not address.is_global:
            raise NotificationDeliveryError("webhook_private_destination_blocked")
    selected = sorted(addresses)[0].split("%", 1)[0]
    parsed = urlsplit(url)
    ip_literal = f"[{selected}]" if ":" in selected else selected
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    netloc = ip_literal if port == default_port else f"{ip_literal}:{port}"
    pinned_url = urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))
    host_header = hostname if port == default_port else f"{hostname}:{port}"
    return pinned_url, host_header, hostname


async def validate_webhook_destination(url: str) -> None:
    await validated_webhook_target(url)


async def deliver_notification(
    kind: NotificationKind,
    config: dict[str, Any],
    message: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> None:
    if kind == NotificationKind.WEB_PUSH:
        try:
            subscription = normalize_webpush_subscription(config)
            settings = get_settings()
            vapid_private_key = settings.webpush_private_key()
            if not vapid_private_key:
                raise NotificationDeliveryError("webpush_vapid_key_not_configured")
            normalized_vapid_private_key = normalize_webpush_vapid_private_key(vapid_private_key)
            await asyncio.to_thread(
                webpush,
                subscription_info=subscription,
                data=json.dumps(message, ensure_ascii=False),
                vapid_private_key=normalized_vapid_private_key,
                vapid_claims={"sub": settings.webpush_vapid_subject},
            )
        except NotificationDeliveryError:
            raise
        except WebPushException as error:
            raise NotificationDeliveryError(_classify_webpush_provider_error(error)) from error
        except (VapidException, ValueError) as error:
            raise NotificationDeliveryError("webpush_vapid_configuration_invalid") from error
        except Exception as error:
            raise NotificationDeliveryError(type(error).__name__) from error
        return

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    try:
        if kind == NotificationKind.TELEGRAM:
            response = await client.post(
                f"https://api.telegram.org/bot{config['bot_token']}/sendMessage",
                json={"chat_id": config["chat_id"], "text": message.get("message", "알림 테스트")},
            )
        elif kind == NotificationKind.DISCORD_WEBHOOK:
            pinned_url, host_header, sni_hostname = await validated_webhook_target(config["url"])
            response = await client.post(
                pinned_url,
                json={"content": message.get("message", "알림 테스트")},
                headers={"Host": host_header},
                follow_redirects=False,
                extensions={"sni_hostname": sni_hostname},
            )
        else:
            pinned_url, host_header, sni_hostname = await validated_webhook_target(config["url"])
            headers = {"Content-Type": "application/json", "Host": host_header}
            if config.get("authorization"):
                headers["Authorization"] = config["authorization"]
            response = await client.post(
                pinned_url,
                json=message,
                headers=headers,
                follow_redirects=False,
                extensions={"sni_hostname": sni_hostname},
            )
        if response.is_redirect:
            raise NotificationDeliveryError("webhook_redirect_blocked")
        response.raise_for_status()
    except NotificationDeliveryError:
        raise
    except (httpx.HTTPError, KeyError, ValueError) as error:
        raise NotificationDeliveryError(type(error).__name__) from error
    finally:
        if owns_client:
            await client.aclose()
