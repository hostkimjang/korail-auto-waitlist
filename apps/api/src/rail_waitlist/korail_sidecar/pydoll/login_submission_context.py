"""Own temporary CDP listeners for the normal UI's single login submission."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

from ..browser_contracts import BrowserSourceUnavailable
from .chromium_lifecycle import finish_owned_cleanup
from .login_submission import PydollLoginSubmission

logger = logging.getLogger(__name__)


class LoginSubmissionObservationTab(Protocol):
    @property
    def network_events_enabled(self) -> bool: ...

    async def enable_network_events(self) -> object: ...

    async def disable_network_events(self) -> object: ...

    async def on(
        self,
        event: object,
        callback: Callable[[dict[str, Any]], None],
    ) -> int: ...

    async def remove_callback(self, callback_id: int) -> object: ...


class LoginSubmissionWarningLogger(Protocol):
    def warning(self, message: str) -> None: ...


def _submission_events() -> tuple[object, object, object, object]:
    # Importing the API or inspecting its contracts must not require Pydoll.
    from pydoll.protocol.network.events import NetworkEvent

    return (
        NetworkEvent.REQUEST_WILL_BE_SENT,
        NetworkEvent.RESPONSE_RECEIVED,
        NetworkEvent.LOADING_FINISHED,
        NetworkEvent.LOADING_FAILED,
    )


async def _cleanup_observation(
    tab: LoginSubmissionObservationTab,
    owner: PydollLoginSubmission,
    callback_ids: list[int],
    network_events_owned: bool,
    event_logger: LoginSubmissionWarningLogger,
) -> None:
    owner.close()
    # The original tab remains the resource owner even if the browser rotates.
    for callback_id in callback_ids:
        try:
            await tab.remove_callback(callback_id)
        except Exception:  # noqa: BLE001 -- never expose browser error details.
            event_logger.warning("KORAIL login observation callback cleanup failed")
    if network_events_owned:
        try:
            await tab.disable_network_events()
        except Exception:  # noqa: BLE001 -- preserve cleanup of every registered listener.
            event_logger.warning("KORAIL login observation network cleanup failed")


@asynccontextmanager
async def observe_login_submission(
    tab: LoginSubmissionObservationTab,
    timeout_seconds: float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    event_logger: LoginSubmissionWarningLogger = logger,
) -> AsyncIterator[PydollLoginSubmission]:
    """Attach an unarmed observer and release its listeners on every exit path."""

    owner = PydollLoginSubmission(timeout_seconds, monotonic=monotonic)
    callback_ids: list[int] = []
    network_events_owned = False
    try:
        try:
            events = _submission_events()
            if not tab.network_events_enabled:
                # Own possible partial enablement too; the prior disabled state is known.
                network_events_owned = True
                await tab.enable_network_events()
            callbacks = (
                owner.on_request_will_be_sent,
                owner.on_response_received,
                owner.on_loading_finished,
                owner.on_loading_failed,
            )
            for event, callback in zip(events, callbacks, strict=True):
                callback_ids.append(await tab.on(event, callback))
        except Exception:
            raise BrowserSourceUnavailable("login_response") from None
        yield owner
    finally:
        await finish_owned_cleanup(
            _cleanup_observation(tab, owner, callback_ids, network_events_owned, event_logger)
        )
