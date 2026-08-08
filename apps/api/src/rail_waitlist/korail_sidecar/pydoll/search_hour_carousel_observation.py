"""Observe and stabilize the official Pydoll hour carousel without owning input order."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from logging import Logger
from typing import Any, Protocol

from ..browser_contracts import BrowserSourceUnavailable
from .search_driver import SearchControlState, SearchHourCandidate

__all__ = (
    "PydollHourCarouselObservationPort",
    "find_hour_navigation_control",
    "hour_carousel_control_metadata",
    "log_hour_window_navigation_failure",
    "read_hour_candidates",
    "wait_for_hour_animation",
    "wait_for_hour_window_change",
)


class PydollHourCarouselObservationPort(Protocol):
    async def _visible_elements(self, selector: str, *, scope: Any = None) -> list[Any]: ...

    async def _read_control_state(self, element: Any) -> SearchControlState: ...

    async def _read_hour_candidates(
        self,
        selector: str,
        *,
        scope: Any,
        visible_only: bool = True,
    ) -> list[SearchHourCandidate]: ...

    def _current_hour_window(
        self,
        candidates: list[SearchHourCandidate],
    ) -> list[SearchHourCandidate]: ...

    async def _wait_for_hour_animation(
        self,
        dialog: Any,
        expected_hours: tuple[int, ...],
    ) -> None: ...

    async def _hour_carousel_control_metadata(self, dialog: Any) -> tuple[object, ...]: ...


async def read_hour_candidates(
    port: PydollHourCarouselObservationPort,
    selector: str,
    *,
    scope: Any,
    visible_only: bool = True,
) -> list[SearchHourCandidate]:
    candidates: list[SearchHourCandidate] = []
    if visible_only:
        elements = await port._visible_elements(selector, scope=scope)
    else:
        elements = await scope.query(selector, find_all=True, raise_exc=False) or []
    for element in elements:
        label = (await element.text).strip()
        if re.fullmatch(r"\d{2}시", label) is None:
            continue
        candidates.append(
            SearchHourCandidate(
                element=element,
                hour=int(label.removesuffix("시")),
                state=await port._read_control_state(element),
            )
        )
    return candidates


async def wait_for_hour_window_change(
    port: PydollHourCarouselObservationPort,
    dialog: Any,
    before: tuple[int, ...],
    direction: str,
    *,
    timeout_seconds: float | None,
    default_timeout_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
) -> bool:
    timeout = min(default_timeout_seconds, 3) if timeout_seconds is None else timeout_seconds
    deadline = monotonic() + max(0.05, timeout)
    stable_progress: tuple[int, ...] = ()
    stable_reads = 0
    while monotonic() < deadline:
        candidates = await port._read_hour_candidates(
            ".slideWrap .slick-slide.slick-active a", scope=dialog
        )
        after = tuple(candidate.hour for candidate in port._current_hour_window(candidates))
        progressed = bool(after) and (
            (direction == ".slick-next" and after[0] > before[0])
            or (direction == ".slick-prev" and after[0] < before[0])
        )
        if progressed:
            if after == stable_progress:
                stable_reads += 1
            else:
                stable_progress = after
                stable_reads = 1
            if stable_reads >= 2:
                await port._wait_for_hour_animation(dialog, after)
                return True
        else:
            stable_progress = ()
            stable_reads = 0
        await sleep(0.05)
    return False


async def log_hour_window_navigation_failure(
    port: PydollHourCarouselObservationPort,
    dialog: Any,
    before: tuple[int, ...],
    *,
    event_logger: Logger,
) -> None:
    candidates = await port._read_hour_candidates(
        ".slideWrap .slick-slide.slick-active a", scope=dialog
    )
    after = tuple(candidate.hour for candidate in port._current_hour_window(candidates))
    event_logger.warning(
        "KORAIL Pydoll hour window did not change stage=departure_hour_navigate "
        "before=%s after=%s controls=%s",
        before,
        after,
        await port._hour_carousel_control_metadata(dialog),
    )


async def wait_for_hour_animation(
    port: PydollHourCarouselObservationPort,
    dialog: Any,
    expected_hours: tuple[int, ...],
    *,
    sleep: Callable[[float], Awaitable[object]],
) -> None:
    try:
        response = await dialog.execute_script(
            """
            function() {
              const track = this.querySelector('.slideWrap .slick-track');
              if (!track) return null;
              const style = getComputedStyle(track);
              const milliseconds = (value) => value.split(',').map((part) => {
                const token = part.trim();
                if (token.endsWith('ms')) return Number.parseFloat(token);
                if (token.endsWith('s')) return Number.parseFloat(token) * 1000;
                return 0;
              });
              const duration = Math.max(0, ...milliseconds(style.transitionDuration));
              const delay = Math.max(0, ...milliseconds(style.transitionDelay));
              return Math.min(1500, duration + delay);
            }
            """,
            return_by_value=True,
        )
        value = response.get("result", {}).get("result", {}).get("value")
        if not isinstance(value, (int, float)) or not 0 <= value <= 1500:
            raise ValueError("invalid hour transition duration")
        await sleep((value / 1000) + 0.05)
        candidates = await port._read_hour_candidates(
            ".slideWrap .slick-slide.slick-active a",
            scope=dialog,
        )
        settled_hours = tuple(candidate.hour for candidate in port._current_hour_window(candidates))
        if settled_hours != expected_hours:
            raise ValueError("hour transition did not settle")
    except BrowserSourceUnavailable:
        raise
    except Exception as error:
        raise BrowserSourceUnavailable("departure_hour_navigate") from error


async def hour_carousel_control_metadata(
    dialog: Any,
    *,
    sanitize_class_tokens: Callable[[object], tuple[str, ...]],
) -> tuple[object, ...]:
    """Return bounded structural metadata only; never page text, URLs, or request values."""

    try:
        response = await dialog.execute_script(
            """
            function() {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const wrap = this.querySelector('.slideWrap');
              if (!wrap) return [];
              const root = wrap.parentElement?.parentElement || wrap.parentElement || wrap;
              return Array.from(root.querySelectorAll('button, a'))
                .filter(visible)
                .slice(0, 24)
                .map((element) => {
                  const relation = wrap.contains(element)
                    ? 'inside'
                    : (wrap.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
                      ? 'after'
                      : 'before';
                  return {
                    tag: element.tagName.toLowerCase(),
                    classes: Array.from(element.classList).slice(0, 8),
                    relation,
                    parentClasses: Array.from(
                      element.parentElement?.classList || []
                    ).slice(0, 8),
                  };
                });
            }
            """,
            return_by_value=True,
        )
        value = response.get("result", {}).get("result", {}).get("value", [])
        if not isinstance(value, list):
            return ()
        return tuple(
            (
                str(item.get("tag", ""))[:16],
                sanitize_class_tokens(" ".join(item.get("classes", []))),
                str(item.get("relation", ""))[:8],
                sanitize_class_tokens(" ".join(item.get("parentClasses", []))),
            )
            for item in value
            if isinstance(item, dict)
        )
    except Exception:  # noqa: BLE001 -- diagnostic metadata must not mask the failure.
        return ()


async def find_hour_navigation_control(
    port: PydollHourCarouselObservationPort,
    direction: str,
    *,
    scope: Any,
) -> Any | None:
    """Resolve one enabled time-carousel arrow owned by ``.slideWrap`` only."""

    visible = await port._visible_elements(
        f".slideWrap :is(button, a){direction}:not(.slick-disabled)", scope=scope
    )
    states = [(element, await port._read_control_state(element)) for element in visible]
    enabled = [element for element, state in states if state.enabled]
    if len(enabled) == 1:
        return enabled[0]
    return None
