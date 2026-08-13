from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from math import ceil, isfinite
from uuid import RFC_4122, UUID, uuid4

REQUEST_ID_HEADER = "X-Rail-Request-ID"
REQUEST_TIMEOUT_MS_HEADER = "X-Rail-Timeout-Ms"

_request_id: ContextVar[str | None] = ContextVar("rail_request_id", default=None)
_provider_call_id: ContextVar[str | None] = ContextVar("rail_provider_call_id", default=None)
_request_deadline: ContextVar[float | None] = ContextVar("rail_request_deadline", default=None)


def new_log_id() -> str:
    """Return a short-lived opaque identifier that is safe to include in logs."""

    return uuid4().hex


def validated_log_id(value: str | None) -> str | None:
    """Accept only canonical lowercase UUIDv4 hex without logging rejected input."""

    if value is None or len(value) != 32 or value != value.lower():
        return None
    if not value.isascii() or any(character not in "0123456789abcdef" for character in value):
        return None
    try:
        parsed = UUID(hex=value)
    except ValueError:
        return None
    if parsed.version != 4 or parsed.variant != RFC_4122:
        return None
    return value


def current_request_id() -> str | None:
    return _request_id.get()


def current_provider_call_id() -> str | None:
    return _provider_call_id.get()


def current_request_deadline() -> float | None:
    return _request_deadline.get()


def remaining_request_timeout_ms(
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> int | None:
    deadline = current_request_deadline()
    if deadline is None:
        return None
    return max(0, ceil((deadline - monotonic()) * 1000))


@contextmanager
def bind_request_id(value: str | None = None) -> Iterator[str]:
    request_id = validated_log_id(value) or new_log_id()
    token = _request_id.set(request_id)
    try:
        yield request_id
    finally:
        _request_id.reset(token)


@contextmanager
def bind_provider_call_id(value: str) -> Iterator[str]:
    provider_call_id = validated_log_id(value)
    if provider_call_id is None:
        raise ValueError("provider_call_id must be a canonical UUIDv4 hex value")
    token = _provider_call_id.set(provider_call_id)
    try:
        yield provider_call_id
    finally:
        _provider_call_id.reset(token)


@contextmanager
def bind_request_deadline_at(deadline: float) -> Iterator[float]:
    """Bind an absolute monotonic deadline without extending an inherited budget."""

    if not isfinite(deadline) or deadline <= 0:
        raise ValueError("deadline must be a positive finite monotonic timestamp")
    inherited_deadline = current_request_deadline()
    effective_deadline = (
        deadline if inherited_deadline is None else min(deadline, inherited_deadline)
    )
    token = _request_deadline.set(effective_deadline)
    try:
        yield effective_deadline
    finally:
        _request_deadline.reset(token)


@contextmanager
def bind_request_deadline(
    timeout_seconds: float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[float]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    with bind_request_deadline_at(monotonic() + timeout_seconds) as deadline:
        yield deadline
