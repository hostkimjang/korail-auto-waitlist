"""Provider-neutral policy for official railway hosts and handoff URLs."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

from ..domain import Provider

OFFICIAL_HOST_ROOTS = {
    Provider.KORAIL: ("korail.com", "letskorail.com"),
    Provider.SRT: ("srail.kr",),
    Provider.MOCK: ("example.invalid",),
}


class _UrlWithHost(Protocol):
    @property
    def host(self) -> str | None: ...


def _is_official_hostname(provider: Provider, hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return any(host == root or host.endswith(f".{root}") for root in OFFICIAL_HOST_ROOTS[provider])


def is_official_provider_host(provider: Provider, value: _UrlWithHost) -> bool:
    return _is_official_hostname(provider, value.host)


def require_official_handoff_url(provider: Provider, value: str) -> str:
    """Require a credential-free HTTPS URL under the selected provider's host roots."""

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
    ):
        raise ValueError("official handoff URL must be a credential-free HTTPS URL")
    if not _is_official_hostname(provider, parsed.hostname):
        raise ValueError("official handoff URL must use the provider allowlist")
    return value
