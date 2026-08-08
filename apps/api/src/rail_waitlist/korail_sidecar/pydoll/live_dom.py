"""Read live Pydoll controls without owning browser lifecycle or polling."""

from __future__ import annotations

import re
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any

from .reservation_driver import ReservationControlState
from .search_driver import SearchControlState
from .search_hour_policy import has_disabled_class

__all__ = (
    "PydollControlState",
    "read_control_state",
    "sanitized_class_tokens",
    "visible_elements",
)


@dataclass(frozen=True)
class PydollControlState(ReservationControlState, SearchControlState):
    enabled: bool
    aria_disabled: str
    disabled_attribute: bool
    classes: tuple[str, ...]
    container_classes: tuple[str, ...]
    slide_classes: tuple[str, ...]
    read_error: bool = False


def sanitized_class_tokens(value: object) -> tuple[str, ...]:
    """Keep bounded CSS token metadata without persisting page text or request values."""

    return tuple(
        token for token in str(value).split()[:8] if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", token)
    )


async def visible_elements(root: Any, selector: str) -> list[Any]:
    elements = await root.query(selector, find_all=True, raise_exc=False)
    if elements is None:
        return []
    if isinstance(elements, AsyncIterable):
        candidates = [element async for element in elements]
    else:
        candidates = list(elements)
    visible: list[Any] = []
    for element in candidates:
        try:
            if await element.is_visible():
                visible.append(element)
        except Exception:  # noqa: BLE001, S112 -- detached React nodes are skipped.
            continue
    return visible


async def read_control_state(element: Any) -> PydollControlState:
    """Read dynamic control attributes from the live DOM instead of Pydoll's cache."""

    try:
        response = await element.execute_script(
            """
            function() {
              const container = this.closest('td, li');
              const slide = this.closest('.slick-slide');
              return {
                ariaDisabled: (this.getAttribute('aria-disabled') || '').toLowerCase(),
                disabledAttribute: this.hasAttribute('disabled') || Boolean(this.disabled),
                className: typeof this.className === 'string' ? this.className : '',
                containerClassName: container && typeof container.className === 'string'
                  ? container.className : '',
                slideClassName: slide && typeof slide.className === 'string'
                  ? slide.className : '',
              };
            }
            """,
            return_by_value=True,
        )
        value = response.get("result", {}).get("result", {}).get("value", {})
        if not isinstance(value, dict):
            raise TypeError("control state is not an object")
        aria_disabled = str(value.get("ariaDisabled", "")).lower()
        disabled_attribute = bool(value.get("disabledAttribute", False))
        classes = sanitized_class_tokens(value.get("className", ""))
        container_classes = sanitized_class_tokens(value.get("containerClassName", ""))
        slide_classes = sanitized_class_tokens(value.get("slideClassName", ""))
        class_disabled = (
            has_disabled_class(classes)
            or has_disabled_class(container_classes)
            or has_disabled_class(slide_classes)
        )
        return PydollControlState(
            enabled=not disabled_attribute and aria_disabled != "true" and not class_disabled,
            aria_disabled=aria_disabled if aria_disabled in {"", "true", "false"} else "other",
            disabled_attribute=disabled_attribute,
            classes=classes,
            container_classes=container_classes,
            slide_classes=slide_classes,
        )
    except Exception:  # noqa: BLE001 -- optional backend response shapes are not stable.
        return PydollControlState(
            enabled=False,
            aria_disabled="read_error",
            disabled_attribute=False,
            classes=(),
            container_classes=(),
            slide_classes=(),
            read_error=True,
        )
