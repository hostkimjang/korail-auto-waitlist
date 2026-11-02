"""Own temporary CDP listeners for the normal UI's single login submission."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol
from urllib.parse import urlsplit

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
        callback: Callable[[dict[str, Any]], object],
    ) -> int: ...

    async def remove_callback(self, callback_id: int) -> object: ...

    async def _execute_command(self, command: dict[str, object]) -> object: ...


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


def _command_succeeded(response: object) -> bool:
    return isinstance(response, Mapping) and isinstance(response.get("result"), Mapping)


async def _require_command(
    tab: LoginSubmissionObservationTab,
    method: str,
    params: dict[str, object] | None = None,
) -> None:
    command: dict[str, object] = {"method": method}
    if params is not None:
        command["params"] = params
    if not _command_succeeded(await tab._execute_command(command)):
        raise RuntimeError("login observation command failed")


def _stack_trace_id(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping) or not set(value) <= {"id", "debuggerId"}:
        return None
    identifier = value.get("id")
    debugger_id = value.get("debuggerId")
    if (
        not isinstance(identifier, str)
        or not 0 < len(identifier) <= 256
        or any(ord(char) < 33 or ord(char) > 126 for char in identifier)
        or (
            debugger_id is not None
            and (
                not isinstance(debugger_id, str)
                or not 0 < len(debugger_id) <= 256
                or any(ord(char) < 33 or ord(char) > 126 for char in debugger_id)
            )
        )
    ):
        return None
    result = {"id": identifier}
    if isinstance(debugger_id, str):
        result["debuggerId"] = debugger_id
    return result


def _has_async_parent(stack: object) -> bool:
    seen: set[int] = set()
    for _ in range(8):
        if not isinstance(stack, Mapping) or id(stack) in seen:
            return False
        seen.add(id(stack))
        if stack.get("parentId") is not None:
            return True
        stack = stack.get("parent")
        if stack is None:
            return False
    return False


def _official_request_stack(event: object) -> Mapping[str, object] | None:
    if not isinstance(event, Mapping):
        return None
    params = event.get("params")
    if not isinstance(params, Mapping) or params.get("type") not in {"XHR", "Fetch"}:
        return None
    request = params.get("request")
    initiator = params.get("initiator")
    if not isinstance(request, Mapping) or not isinstance(initiator, Mapping):
        return None
    if request.get("method") not in {"GET", "POST"}:
        return None
    url = request.get("url")
    if not isinstance(url, str) or len(url) > 16384 or any(ord(char) < 33 for char in url):
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.korail.com"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
    except ValueError:
        return None
    stack = initiator.get("stack")
    return stack if isinstance(stack, Mapping) and _has_async_parent(stack) else None


async def _materialize_async_stack(
    tab: LoginSubmissionObservationTab,
    stack: Mapping[str, object],
) -> dict[str, object] | None:
    """Resolve opaque async parents without retaining IDs or request material."""

    levels: list[list[object]] = []
    current: object = stack
    seen: set[int] = set()
    for _ in range(8):
        if not isinstance(current, Mapping) or id(current) in seen:
            return None
        seen.add(id(current))
        frames = current.get("callFrames")
        if not isinstance(frames, list):
            return None
        levels.append(frames)
        parent = current.get("parent")
        parent_id = current.get("parentId")
        if parent is not None and parent_id is not None:
            return None
        if parent is not None:
            current = parent
            continue
        if parent_id is None:
            materialized: dict[str, object] | None = None
            for level in reversed(levels):
                node: dict[str, object] = {"callFrames": level}
                if materialized is not None:
                    node["parent"] = materialized
                materialized = node
            return materialized
        identifier = _stack_trace_id(parent_id)
        if identifier is None:
            return None
        response = await tab._execute_command(
            {"method": "Debugger.getStackTrace", "params": {"stackTraceId": identifier}}
        )
        result = response.get("result") if isinstance(response, Mapping) else None
        current = result.get("stackTrace") if isinstance(result, Mapping) else None
    return None


async def _resolve_async_initiator(
    tab: LoginSubmissionObservationTab,
    event: dict[str, Any],
    event_logger: LoginSubmissionWarningLogger,
) -> dict[str, Any]:
    stack = _official_request_stack(event)
    if stack is None:
        return event
    try:
        materialized = await _materialize_async_stack(tab, stack)
    except Exception:  # noqa: BLE001 -- diagnostic enrichment remains fail-closed.
        materialized = None
    if materialized is None:
        event_logger.warning("KORAIL login observation async stack resolution unavailable")
        return event
    params = event.get("params")
    assert isinstance(params, Mapping)
    initiator = params.get("initiator")
    assert isinstance(initiator, Mapping)
    resolved_initiator = dict(initiator)
    resolved_initiator["stack"] = materialized
    resolved_params = dict(params)
    resolved_params["initiator"] = resolved_initiator
    resolved_event = dict(event)
    resolved_event["params"] = resolved_params
    return resolved_event


async def _cleanup_observation(
    tab: LoginSubmissionObservationTab,
    owner: PydollLoginSubmission,
    callback_ids: list[int],
    network_events_owned: bool,
    async_stack_owned: bool,
    debugger_owned: bool,
    event_logger: LoginSubmissionWarningLogger,
) -> None:
    owner.close()
    # The original tab remains the resource owner even if the browser rotates.
    for callback_id in callback_ids:
        try:
            await tab.remove_callback(callback_id)
        except Exception:  # noqa: BLE001 -- never expose browser error details.
            event_logger.warning("KORAIL login observation callback cleanup failed")
    if async_stack_owned:
        try:
            await _require_command(
                tab,
                "Debugger.setAsyncCallStackDepth",
                {"maxDepth": 0},
            )
        except Exception:  # noqa: BLE001 -- never expose browser error details.
            event_logger.warning("KORAIL login observation async stack cleanup failed")
    if debugger_owned:
        try:
            await _require_command(tab, "Debugger.disable")
        except Exception:  # noqa: BLE001 -- preserve cleanup of every owned resource.
            event_logger.warning("KORAIL login observation debugger cleanup failed")
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
    debugger_owned = False
    async_stack_owned = False
    try:
        try:
            events = _submission_events()
            if not tab.network_events_enabled:
                # Own possible partial enablement too; the prior disabled state is known.
                network_events_owned = True
                await tab.enable_network_events()
            # CDP exposes Network.Initiator.stack only with the Debugger domain
            # enabled. Async parents preserve the public callApi site through the
            # official protection wrapper without reading request values.
            debugger_owned = True
            await _require_command(tab, "Debugger.enable")
            async_stack_owned = True
            await _require_command(
                tab,
                "Debugger.setAsyncCallStackDepth",
                {"maxDepth": 16},
            )

            async def request_callback(event: dict[str, Any]) -> None:
                owner.on_request_will_be_sent(
                    await _resolve_async_initiator(tab, event, event_logger)
                )

            callbacks = (
                request_callback,
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
            _cleanup_observation(
                tab,
                owner,
                callback_ids,
                network_events_owned,
                async_stack_owned,
                debugger_owned,
                event_logger,
            )
        )
