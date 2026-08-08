from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeGuard

from ..provider_contracts import ProviderUnavailable


@dataclass(frozen=True)
class TagoPage:
    items: list[dict[str, object]]
    total_count: int
    page_no: int
    num_rows: int


def _json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def response_page(
    payload: object,
    requested_page: int = 1,
    requested_num_rows: int = 100,
    *,
    allow_unpaginated: bool = False,
) -> TagoPage:
    if not _json_object(payload) or "response" not in payload:
        raise ProviderUnavailable("TAGO returned an invalid response envelope")
    response = payload["response"]
    if not _json_object(response):
        raise ProviderUnavailable("TAGO returned an invalid response object")
    header = response.get("header", {})
    if not _json_object(header):
        raise ProviderUnavailable("TAGO returned an invalid response header")
    if "resultCode" not in header:
        raise ProviderUnavailable("TAGO response header is missing resultCode")
    if str(header["resultCode"]) not in {"00", "0"}:
        raise ProviderUnavailable("TAGO returned an unsuccessful result")
    if "body" not in response:
        raise ProviderUnavailable("TAGO response is missing body")
    body = response["body"]
    if not _json_object(body):
        raise ProviderUnavailable("TAGO returned an invalid response body")
    required_metadata = {"totalCount", "pageNo", "numOfRows"}
    has_pagination = required_metadata.issubset(body)
    if not has_pagination and not allow_unpaginated:
        raise ProviderUnavailable("TAGO response is missing pagination metadata")
    if "items" not in body:
        raise ProviderUnavailable("TAGO response is missing items")
    item_container = body["items"]
    if item_container is None or item_container == "":
        raw_items: object = []
    elif _json_object(item_container):
        raw_items = item_container.get("item", [])
    else:
        raise ProviderUnavailable("TAGO returned invalid items")
    if _json_object(raw_items):
        items = [raw_items]
    elif isinstance(raw_items, list):
        items = []
        for item in raw_items:
            if not _json_object(item):
                # A partial page would make totalCount and pagination completeness unverifiable.
                # Reject it before downstream projections call row.get(...).
                raise ProviderUnavailable("TAGO returned an invalid item")
            items.append(item)
    else:
        raise ProviderUnavailable("TAGO returned invalid items")

    def positive_int(field: str, fallback: int) -> int:
        raw = body.get(field, fallback)
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ProviderUnavailable(f"TAGO returned invalid {field}")
        if isinstance(raw, float) and (not math.isfinite(raw) or not raw.is_integer()):
            raise ProviderUnavailable(f"TAGO returned invalid {field}")
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
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
