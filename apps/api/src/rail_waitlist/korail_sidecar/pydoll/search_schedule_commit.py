"""Confirm official KORAIL schedule selection after picker input completes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, Protocol

from ..browser_contracts import BrowserSourceUnavailable
from .search_driver import SearchControlState, SearchHourCandidate

__all__ = (
    "PydollScheduleCommitPort",
    "click_hour_and_confirm",
    "wait_for_schedule",
    "wait_for_schedule_date",
)


class PydollScheduleCommitPort(Protocol):
    async def current_schedule(self) -> tuple[date, int]: ...

    async def _read_control_state(self, element: Any) -> SearchControlState: ...


async def wait_for_schedule(
    port: PydollScheduleCommitPort,
    travel_date: date,
    departure_hour: int,
    *,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> None:
    deadline = monotonic() + timeout_seconds()
    while monotonic() < deadline:
        try:
            if await port.current_schedule() == (travel_date, departure_hour):
                return
        except source_unavailable_type:
            pass
        await sleep(0.1)
    raise source_unavailable_type("departure_schedule_readback")


async def wait_for_schedule_date(
    port: PydollScheduleCommitPort,
    travel_date: date,
    *,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    source_unavailable_type: type[BrowserSourceUnavailable] = BrowserSourceUnavailable,
) -> None:
    deadline = monotonic() + timeout_seconds()
    while monotonic() < deadline:
        try:
            selected_date, _ = await port.current_schedule()
            if selected_date == travel_date:
                return
        except source_unavailable_type:
            pass
        await sleep(0.1)
    raise source_unavailable_type("departure_schedule_readback")


async def click_hour_and_confirm(
    port: PydollScheduleCommitPort,
    candidate: SearchHourCandidate,
    *,
    timeout_seconds: Callable[[], float],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
) -> bool:
    await candidate.element.click()
    deadline = monotonic() + min(timeout_seconds(), 1)
    while monotonic() < deadline:
        state = await port._read_control_state(candidate.element)
        if "current" in state.container_classes:
            return True
        await sleep(0.05)
    return False
