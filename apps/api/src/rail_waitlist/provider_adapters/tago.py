from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..provider_contracts import ProviderUnavailable


@dataclass(frozen=True)
class TagoPage:
    items: list[dict[str, Any]]
    total_count: int
    page_no: int
    num_rows: int


def response_page(
    payload: dict[str, Any],
    requested_page: int = 1,
    requested_num_rows: int = 100,
    *,
    allow_unpaginated: bool = False,
) -> TagoPage:
    if not isinstance(payload, dict) or "response" not in payload:
        raise ProviderUnavailable("TAGO returned an invalid response envelope")
    response = payload["response"]
    if not isinstance(response, dict):
        raise ProviderUnavailable("TAGO returned an invalid response object")
    header = response.get("header", {})
    if not isinstance(header, dict):
        raise ProviderUnavailable("TAGO returned an invalid response header")
    if "resultCode" not in header:
        raise ProviderUnavailable("TAGO response header is missing resultCode")
    if str(header["resultCode"]) not in {"00", "0"}:
        raise ProviderUnavailable("TAGO returned an unsuccessful result")
    if "body" not in response:
        raise ProviderUnavailable("TAGO response is missing body")
    body = response["body"]
    if not isinstance(body, dict):
        raise ProviderUnavailable("TAGO returned an invalid response body")
    required_metadata = {"totalCount", "pageNo", "numOfRows"}
    has_pagination = required_metadata.issubset(body)
    if not has_pagination and not allow_unpaginated:
        raise ProviderUnavailable("TAGO response is missing pagination metadata")
    if "items" not in body:
        raise ProviderUnavailable("TAGO response is missing items")
    item_container = body["items"]
    if item_container is None or item_container == "":
        items = []
    elif isinstance(item_container, dict):
        items = item_container.get("item", [])
    else:
        raise ProviderUnavailable("TAGO returned invalid items")
    if isinstance(items, dict):
        items = [items]
    elif not isinstance(items, list):
        items = []

    def positive_int(field: str, fallback: int) -> int:
        raw = body.get(field, fallback)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ProviderUnavailable(f"TAGO returned invalid {field}") from None
        if value < 0 or (field != "totalCount" and value < 1):
            raise ProviderUnavailable(f"TAGO returned invalid {field}")
        return value

    return TagoPage(
        items=items,
        total_count=positive_int("totalCount", len(items)),
        page_no=positive_int("pageNo", requested_page),
        num_rows=positive_int("numOfRows", requested_num_rows),
    )
