"""Observe official search responses for KORAIL's exact JSON protection verdict.

KORAIL can answer a search lookup with HTTP 200 and ``{"errCode": "macro_err1"}`` while
the page only renders an ordinary empty schedule. Header evidence alone cannot see that
verdict, so this owner keeps a bounded set of official XHR/Fetch request ids for the
current search and classifies their completed bodies in memory.

Only the exact top-level JSON ``errCode`` value is trusted. Free-text markers such as
NetFunnel also appear in ordinary static configuration and must never block a search.
No URL, body, cookie, identifier, or field value leaves this module; the only output is
the code-owned ``(status, SEARCH_RESPONSE_MACRO_PROTECTION)`` evidence tuple.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

SEARCH_RESPONSE_MACRO_PROTECTION = "business_macro_err1"
_MACRO_ERROR_CODE = "macro_err1"
_OFFICIAL_HOST = "www.korail.com"
_BUSINESS_RESOURCE_TYPES = frozenset({"XHR", "Fetch"})

type CdpCommand = Callable[[dict[str, object]], Awaitable[object]]
type _BodyVerdict = Literal["not_ready", "ignored", "macro_protection"]


@dataclass(slots=True)
class _Candidate:
    status: int
    read_attempts: int = 0


class PydollSearchResponseProtectionObserver:
    """Search-scoped, bounded response-body classifier with no retained provider data."""

    # Every official XHR/Fetch response of one lookup is a candidate, including page
    # bootstrap JSON that precedes the schedule lookup. The ceiling bounds memory and
    # CDP reads; it must stay well above a page's bootstrap traffic so the business
    # response that carries the verdict is never crowded out.
    MAX_CANDIDATES_PER_SEARCH = 64
    MAX_READS_PER_DRAIN = 4
    # A body read before loadingFinished fails. Retry on later snapshots only a bounded
    # number of times so a never-finishing request cannot pin the candidate set.
    MAX_READ_ATTEMPTS = 20
    MAX_BODY_BYTES = 256 * 1024
    MAX_REQUEST_ID_LENGTH = 128
    # Pydoll's own CDP command timeout is 60 seconds. A stalled body read must not hold
    # one result snapshot past the lookup's wait deadline, so each read gets a short
    # budget and a timeout counts as "not ready yet".
    BODY_READ_TIMEOUT_SECONDS = 2.0

    def __init__(self) -> None:
        self._armed = False
        self._generation = 0
        self._observed = 0
        self._pending: dict[str, _Candidate] = {}
        self._evidence: tuple[int, str] | None = None
        self._draining = False

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def arm(self) -> None:
        """Start a new search window so earlier tabs or lookups never leak into it."""

        self.reset()
        self._armed = True

    def reset(self) -> None:
        self._armed = False
        self._generation += 1
        self._observed = 0
        self._pending.clear()
        self._evidence = None

    def observe(self, event: object) -> None:
        """Record one candidate request id from a synchronous CDP response event."""

        if not self._armed or self._evidence is not None:
            return
        if self._observed >= self.MAX_CANDIDATES_PER_SEARCH:
            return
        candidate = _official_business_candidate(event, self.MAX_BODY_BYTES)
        if candidate is None:
            return
        request_id, status = candidate
        if len(request_id) > self.MAX_REQUEST_ID_LENGTH or request_id in self._pending:
            return
        self._observed += 1
        self._pending[request_id] = _Candidate(status)

    async def drain(self, execute_command: CdpCommand) -> tuple[int, str] | None:
        """Read a bounded number of pending bodies and return protection evidence once."""

        if self._evidence is not None or not self._armed or not self._pending:
            return self._evidence
        if self._draining:
            return None
        self._draining = True
        generation = self._generation
        try:
            for request_id in tuple(self._pending)[: self.MAX_READS_PER_DRAIN]:
                try:
                    response = await asyncio.wait_for(
                        execute_command(
                            {
                                "method": "Network.getResponseBody",
                                "params": {"requestId": request_id},
                            }
                        ),
                        timeout=self.BODY_READ_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 -- CDP error text may carry provider data.
                    response = None
                if generation != self._generation:
                    # A new tab or lookup started while the read was in flight.
                    return None
                candidate = self._pending.get(request_id)
                if candidate is None:
                    continue
                verdict = _body_verdict(response, self.MAX_BODY_BYTES)
                if verdict == "not_ready":
                    candidate.read_attempts += 1
                    del self._pending[request_id]
                    if candidate.read_attempts < self.MAX_READ_ATTEMPTS:
                        # Requeue behind later candidates so slow or unfinished
                        # requests cannot starve a completed response.
                        self._pending[request_id] = candidate
                    continue
                del self._pending[request_id]
                if verdict == "macro_protection":
                    self._evidence = (candidate.status, SEARCH_RESPONSE_MACRO_PROTECTION)
                    self._pending.clear()
                    return self._evidence
            return None
        finally:
            self._draining = False


def _official_business_candidate(event: object, max_body_bytes: int) -> tuple[str, int] | None:
    if not isinstance(event, Mapping):
        return None
    params = event.get("params")
    if not isinstance(params, Mapping) or params.get("type") not in _BUSINESS_RESOURCE_TYPES:
        return None
    request_id = params.get("requestId")
    response = params.get("response")
    if not isinstance(request_id, str) or not request_id or not isinstance(response, Mapping):
        return None
    status_value = response.get("status")
    if (
        isinstance(status_value, bool)
        or not isinstance(status_value, (int, float))
        or (isinstance(status_value, float) and not math.isfinite(status_value))
        or status_value != int(status_value)
        or not 200 <= status_value <= 299
    ):
        return None
    url = response.get("url")
    if not isinstance(url, str) or not url or len(url) > 16384:
        return None
    try:
        parsed = urlsplit(url)
        official = (
            parsed.scheme == "https"
            and parsed.hostname == _OFFICIAL_HOST
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return None
    if not official or _declared_length(response.get("headers")) > max_body_bytes:
        return None
    return request_id, int(status_value)


def _declared_length(headers: object) -> int:
    if not isinstance(headers, Mapping):
        return 0
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "content-length":
            try:
                return int(str(value).strip())
            except ValueError:
                return 0
    return 0


def _body_verdict(response: object, max_body_bytes: int) -> _BodyVerdict:
    """Classify one CDP body reply without returning or logging its contents."""

    if not isinstance(response, Mapping) or "error" in response:
        return "not_ready"
    result = response.get("result")
    if not isinstance(result, Mapping):
        return "not_ready"
    raw = result.get("body")
    encoded = result.get("base64Encoded")
    if not isinstance(raw, str) or not isinstance(encoded, bool):
        return "ignored"
    if len(raw) > max_body_bytes * 4 // 3 + 4:
        return "ignored"
    try:
        body = base64.b64decode(raw, validate=True) if encoded else raw.encode("utf-8")
    except (binascii.Error, ValueError):
        return "ignored"
    if len(body) > max_body_bytes:
        return "ignored"
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return "ignored"
    if isinstance(payload, Mapping) and payload.get("errCode") == _MACRO_ERROR_CODE:
        return "macro_protection"
    return "ignored"
