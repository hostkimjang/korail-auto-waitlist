"""Carry the caller's KORAIL login-submission pacing policy into the login driver.

The authentication actor knows *why* a login runs (background keepalive, explicit
verification, or a reservation); only the login driver knows *whether* credentials are
about to be submitted. A context-local scope joins the two without widening the
``ensure_authenticated(credential)`` session contract that other actors also implement.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class LoginSubmissionPacer(Protocol):
    async def admit_login_submission(self) -> None: ...

    async def record_login_submission(self) -> None: ...


class LoginSubmissionPurpose(StrEnum):
    """Closed reasons a credential submission may run."""

    # Startup prewarm and keepalive: deferrable, so the shared minimum interval applies.
    BACKGROUND = "background"
    # Reservation or explicit user verification: never deferred, only recorded.
    REQUIRED = "required"


@dataclass(frozen=True)
class _PacingScope:
    pacer: LoginSubmissionPacer
    purpose: LoginSubmissionPurpose


_CURRENT_SCOPE: ContextVar[_PacingScope | None] = ContextVar(
    "korail_login_submission_pacing", default=None
)


@contextmanager
def login_submission_pacing(
    pacer: LoginSubmissionPacer | None,
    purpose: LoginSubmissionPurpose,
) -> Iterator[None]:
    """Bind one purpose for the enclosed login; an outer scope keeps precedence."""

    if pacer is None or _CURRENT_SCOPE.get() is not None:
        yield
        return
    token = _CURRENT_SCOPE.set(_PacingScope(pacer, purpose))
    try:
        yield
    finally:
        _CURRENT_SCOPE.reset(token)


async def admit_login_submission() -> None:
    """Raise ProviderCooldownDeferred when a background login falls inside the interval."""

    scope = _CURRENT_SCOPE.get()
    if scope is not None and scope.purpose is LoginSubmissionPurpose.BACKGROUND:
        await scope.pacer.admit_login_submission()


async def record_login_submission() -> None:
    scope = _CURRENT_SCOPE.get()
    if scope is not None:
        await scope.pacer.record_login_submission()
