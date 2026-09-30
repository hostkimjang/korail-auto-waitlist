"""Classify failed official business responses without retaining provider material."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from ..browser_contracts import ProtectionTrigger
from ..browser_protection import (
    is_rate_limit_response,
    protection_trigger_from_http_response,
    protection_trigger_from_replay_text,
)
from ..browser_service_availability import (
    ProviderUnavailableTrigger,
    decode_provider_page_text,
    provider_unavailable_trigger_from_page,
)

BusinessResourceType = Literal["xhr", "fetch"]
_MAX_CLASSIFIED_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PydollBusinessServerError:
    status: int
    resource_type: BusinessResourceType
    kind: Literal["server_error"] = "server_error"


@dataclass(frozen=True, slots=True)
class PydollBusinessProtection:
    status: int
    resource_type: BusinessResourceType
    trigger: ProtectionTrigger
    kind: Literal["protection"] = "protection"


@dataclass(frozen=True, slots=True)
class PydollBusinessProviderUnavailable:
    status: int
    resource_type: BusinessResourceType
    trigger: ProviderUnavailableTrigger
    kind: Literal["provider_unavailable"] = "provider_unavailable"


type PydollBusinessResponseFailure = (
    PydollBusinessServerError | PydollBusinessProtection | PydollBusinessProviderUnavailable
)


def pydoll_network_response_evidence(event: object) -> tuple[int, str] | None:
    """Normalize the existing protection signals and narrowly scoped business 5xx."""

    if not isinstance(event, Mapping):
        return None
    params = event.get("params")
    if not isinstance(params, Mapping):
        return None
    response = params.get("response")
    if not isinstance(response, Mapping):
        return None
    status_value = response.get("status")
    resource_type = str(params.get("type", "")).strip().lower()
    if not isinstance(status_value, (bytes, float, int, str)):
        return None
    try:
        status = int(status_value)
    except (OverflowError, TypeError, ValueError):
        return None
    if is_rate_limit_response(status, resource_type) or (
        protection_trigger_from_http_response(status, resource_type) == "http_403_main"
    ):
        return status, resource_type
    failure = classify_pydoll_business_response_failure(event)
    if failure is not None:
        return failure.status, f"business_{failure.resource_type}"
    return None


def classify_pydoll_business_response_failure(
    event: object,
    *,
    response_body: bytes | None = None,
) -> PydollBusinessResponseFailure | None:
    """Keep only official business 5xx evidence; 429/403 keep their existing owner.

    An optional bounded body is classified in memory. Neither the URL nor the
    body becomes part of the returned evidence, including its representation.
    A business error is not proof of absent trains and does not authorize retry.
    """

    if not isinstance(event, Mapping):
        return None
    params = event.get("params")
    if not isinstance(params, Mapping):
        return None
    response = params.get("response")
    if not isinstance(response, Mapping):
        return None
    resource_type = params.get("type")
    if resource_type == "XHR":
        resource: BusinessResourceType = "xhr"
    elif resource_type == "Fetch":
        resource = "fetch"
    else:
        return None
    status_value = response.get("status")
    if (
        isinstance(status_value, bool)
        or not isinstance(status_value, (int, float))
        or (isinstance(status_value, float) and not math.isfinite(status_value))
        or not 500 <= status_value <= 599
        or status_value != int(status_value)
    ):
        return None
    status = int(status_value)
    url = response.get("url")
    if not isinstance(url, str) or not url or len(url) > 16384:
        return None
    try:
        parsed = urlsplit(url)
        official_business = (
            parsed.scheme == "https"
            and parsed.hostname == "www.korail.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path.startswith("/web_s/")
            and bool(parsed.path.removeprefix("/web_s/"))
            and not parsed.fragment
        )
    except ValueError:
        return None
    if not official_business:
        return None
    if response_body is not None and len(response_body) <= _MAX_CLASSIFIED_BODY_BYTES:
        body_text = decode_provider_page_text(response_body)
        protection = protection_trigger_from_replay_text(body_text)
        if protection is not None:
            return PydollBusinessProtection(status, resource, protection)
        unavailable = provider_unavailable_trigger_from_page(url, body_text)
        if unavailable is not None:
            return PydollBusinessProviderUnavailable(status, resource, unavailable)
    return PydollBusinessServerError(status, resource)
