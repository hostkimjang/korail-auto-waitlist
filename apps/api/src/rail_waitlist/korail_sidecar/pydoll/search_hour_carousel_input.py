"""Own low-level Pydoll hour-carousel input without owning search orchestration."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from ..browser_contracts import BrowserSourceUnavailable

__all__ = (
    "PydollHourCarouselInputPort",
    "dispatch_mouse_event",
    "navigate_hour_carousel_by_keyboard",
    "swipe_hour_carousel",
)


class PydollHourCarouselInputPort(Protocol):
    _tab: Any

    async def _visible_elements(self, selector: str, *, scope: Any = None) -> list[Any]: ...

    async def _dispatch_mouse_event(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        buttons: int,
        button: str | None = None,
        click_count: int | None = None,
    ) -> None: ...


async def swipe_hour_carousel(
    port: PydollHourCarouselInputPort,
    dialog: Any,
    direction: str,
) -> None:
    viewports = await port._visible_elements(".slideWrap .slick-list", scope=dialog)
    if len(viewports) != 1:
        raise BrowserSourceUnavailable("departure_hour_navigate")
    try:
        await viewports[0].scroll_into_view()
        bounds = await viewports[0].get_bounds_using_js()
        x = float(bounds["x"])
        y = float(bounds["y"])
        width = float(bounds["width"])
        height = float(bounds["height"])
        if width < 40 or height <= 0:
            raise ValueError("invalid hour carousel bounds")
        leading_x = x + width * 0.75
        trailing_x = x + width * 0.25
        if direction == ".slick-prev":
            leading_x, trailing_x = trailing_x, leading_x
        pointer_y = y + height * 0.5
        await port._dispatch_mouse_event(
            "mouseMoved",
            leading_x,
            pointer_y,
            buttons=0,
        )
        pressed = False
        try:
            await port._dispatch_mouse_event(
                "mousePressed",
                leading_x,
                pointer_y,
                button="left",
                buttons=1,
                click_count=1,
            )
            pressed = True
            for step in range(1, 11):
                progress = step / 10
                await port._dispatch_mouse_event(
                    "mouseMoved",
                    leading_x + (trailing_x - leading_x) * progress,
                    pointer_y,
                    button="left",
                    buttons=1,
                )
                await asyncio.sleep(0.025)
        finally:
            if pressed:
                await asyncio.shield(
                    port._dispatch_mouse_event(
                        "mouseReleased",
                        trailing_x,
                        pointer_y,
                        button="left",
                        buttons=0,
                        click_count=1,
                    )
                )
    except BrowserSourceUnavailable:
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("departure_hour_navigate") from error


async def navigate_hour_carousel_by_keyboard(
    port: PydollHourCarouselInputPort,
    dialog: Any,
    direction: str,
) -> bool:
    """Use the picker viewport's documented keyboard-style navigation when focusable."""

    try:
        response = await dialog.execute_script(
            """
            function() {
              const viewports = this.querySelectorAll('.slideWrap .slick-list');
              if (viewports.length !== 1) return false;
              const viewport = viewports[0];
              viewport.focus({preventScroll: true});
              return document.activeElement === viewport
                || viewport.contains(document.activeElement);
            }
            """,
            return_by_value=True,
        )
        focused = response.get("result", {}).get("result", {}).get("value")
        if focused is not True:
            return False
        key, code, virtual_key_code = (
            ("ArrowLeft", "ArrowLeft", 37)
            if direction == ".slick-prev"
            else ("ArrowRight", "ArrowRight", 39)
        )
        await port._tab._execute_command(
            {
                "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": "rawKeyDown",
                    "key": key,
                    "code": code,
                    "windowsVirtualKeyCode": virtual_key_code,
                    "nativeVirtualKeyCode": virtual_key_code,
                },
            }
        )
        await port._tab._execute_command(
            {
                "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": "keyUp",
                    "key": key,
                    "code": code,
                    "windowsVirtualKeyCode": virtual_key_code,
                    "nativeVirtualKeyCode": virtual_key_code,
                },
            }
        )
        return True
    except Exception:  # noqa: BLE001 -- unsupported browser input remains fail-closed.
        return False


async def dispatch_mouse_event(
    port: PydollHourCarouselInputPort,
    event_type: str,
    x: float,
    y: float,
    *,
    buttons: int,
    button: str | None = None,
    click_count: int | None = None,
) -> None:
    params: dict[str, object] = {
        "type": event_type,
        "x": round(x),
        "y": round(y),
        "buttons": buttons,
    }
    if button is not None:
        params["button"] = button
    if click_count is not None:
        params["clickCount"] = click_count
    # Pydoll's public mouse helper omits the CDP ``buttons`` bitmask on move.
    await port._tab._execute_command({"method": "Input.dispatchMouseEvent", "params": params})
