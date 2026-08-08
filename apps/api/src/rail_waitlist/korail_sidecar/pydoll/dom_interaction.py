"""Perform generic Pydoll DOM queries and bounded waits without owning a tab lifecycle."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from logging import Logger
from typing import Any, Protocol

from ..browser_contracts import BrowserSourceUnavailable
from .live_dom import PydollControlState

__all__ = (
    "PydollDomInteractionPort",
    "click_exact_text",
    "evaluate_text",
    "evaluate_value",
    "find_exact_visible",
    "has_exact_visible",
    "wait_for_dialog",
    "wait_for_enabled_exact_text",
    "wait_for_exact_text",
    "wait_for_value",
    "wait_for_visible_elements",
)


class PydollDomInteractionPort(Protocol):
    async def _evaluate_value(self, selector: str) -> object: ...

    async def _find_exact_visible(
        self,
        selector: str,
        text: str,
        *,
        scope: Any = None,
    ) -> Any: ...

    async def _visible_elements(self, selector: str, *, scope: Any = None) -> list[Any]: ...

    async def _read_control_state(self, element: Any) -> PydollControlState: ...

    def _control_state_log_value(self, state: PydollControlState) -> tuple[object, ...]: ...


async def evaluate_value(tab: Any, selector: str) -> object:
    response = await tab.execute_script(
        f"document.querySelector({selector!r})?.value ?? ''",
        return_by_value=True,
    )
    return response["result"]["result"].get("value", "")


async def evaluate_text(tab: Any, selector: str) -> str:
    response = await tab.execute_script(
        f"document.querySelector({selector!r})?.innerText ?? ''",
        return_by_value=True,
    )
    return str(response["result"]["result"].get("value", ""))


async def wait_for_value(
    port: PydollDomInteractionPort,
    selector: str,
    expected: str,
    *,
    contains: bool = False,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> None:
    deadline = monotonic() + timeout_seconds()
    while monotonic() < deadline:
        actual = str(await port._evaluate_value(selector)).strip()
        if (contains and expected in actual) or (not contains and actual == expected):
            return
        await sleep(0.1)
    raise source_unavailable_type("input_readback")


async def click_exact_text(
    port: PydollDomInteractionPort,
    selector: str,
    text: str,
) -> None:
    await (await port._find_exact_visible(selector, text)).click()


async def wait_for_exact_text(
    port: PydollDomInteractionPort,
    selector: str,
    text: str,
    *,
    scope: Any = None,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> Any:
    deadline = monotonic() + timeout_seconds()
    while monotonic() < deadline:
        try:
            return await port._find_exact_visible(selector, text, scope=scope)
        except LookupError:
            await sleep(0.1)
    raise source_unavailable_type("visible_control")


async def wait_for_enabled_exact_text(
    port: PydollDomInteractionPort,
    selector: str,
    text: str,
    *,
    scope: Any = None,
    failure_stage: str = "disabled_control",
    accepted_labels: tuple[str, ...] = (),
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    event_logger: Logger,
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> Any:
    deadline = monotonic() + timeout_seconds()
    last_visible_count = 0
    last_states: list[PydollControlState] = []
    normalized_labels = {" ".join(label.split()) for label in (text, *accepted_labels)}
    while monotonic() < deadline:
        # Slick keeps disabled clones in the rendered tree while moving between ranges.
        # Inspect every exact visible match instead of trusting the first clone.
        visible = await port._visible_elements(selector, scope=scope)
        last_visible_count = len(visible)
        last_states = []
        for element in visible:
            label = " ".join(str(await element.text).split())
            if label not in normalized_labels:
                continue
            state = await port._read_control_state(element)
            last_states.append(state)
            if state.enabled:
                return element
        await sleep(0.1)
    event_logger.warning(
        "KORAIL Pydoll control unavailable stage=%s visible=%d exact=%d states=%s",
        failure_stage,
        last_visible_count,
        len(last_states),
        tuple(port._control_state_log_value(state) for state in last_states),
    )
    raise source_unavailable_type(failure_stage)


async def wait_for_visible_elements(
    port: PydollDomInteractionPort,
    selector: str,
    *,
    scope: Any = None,
    failure_stage: str,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    event_logger: Logger,
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> list[Any]:
    deadline = monotonic() + timeout_seconds()
    while monotonic() < deadline:
        elements = await port._visible_elements(selector, scope=scope)
        if elements:
            return elements
        await sleep(0.1)
    event_logger.warning(
        "KORAIL Pydoll controls unavailable stage=%s visible=0",
        failure_stage,
    )
    raise source_unavailable_type(failure_stage)


async def wait_for_dialog(
    port: PydollDomInteractionPort,
    marker: str,
    *,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> Any:
    deadline = monotonic() + timeout_seconds()
    while monotonic() < deadline:
        for dialog in await port._visible_elements("[role='dialog']"):
            if marker in (await dialog.text):
                return dialog
        await sleep(0.1)
    raise source_unavailable_type("dialog")


async def find_exact_visible(
    port: PydollDomInteractionPort,
    selector: str,
    text: str,
    *,
    scope: Any = None,
) -> Any:
    for element in await port._visible_elements(selector, scope=scope):
        if (await element.text).strip() == text:
            return element
    raise LookupError(text)


async def has_exact_visible(
    port: PydollDomInteractionPort,
    selector: str,
    text: str,
    *,
    scope: Any = None,
) -> bool:
    for element in await port._visible_elements(selector, scope=scope):
        try:
            if " ".join(str(await element.text).split()) == text:
                return True
        except Exception:  # noqa: BLE001, S112 -- skip detached React nodes.
            continue
    return False
