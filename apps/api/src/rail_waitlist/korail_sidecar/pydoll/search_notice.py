"""Dismiss the observed public booking-window notice without accepting other dialogs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Literal, Protocol

from ..browser_contracts import BrowserSourceUnavailable

logger = logging.getLogger("rail_waitlist.korail_pydoll_browser")
NoticeState = Literal["absent", "booking_window_expansion", "unrecognized"]
CLOSE_SELECTOR = '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
OBSERVE_NOTICE_SCRIPT = r"""
(() => {
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  };
  const dialogs = Array.from(document.querySelectorAll(
    '[role="dialog"][aria-modal="true"]'
  )).filter(visible);
  if (dialogs.length === 0) return 'absent';
  if (dialogs.length !== 1) return 'unrecognized';
  const dialog = dialogs[0];
  const layers = dialog.querySelectorAll('.layerWrap.emer_pop');
  const images = Array.from(dialog.querySelectorAll('.pop_content img')).filter(visible);
  const buttons = Array.from(dialog.querySelectorAll('button')).filter(visible);
  const inputs = Array.from(dialog.querySelectorAll('input')).filter(visible);
  const links = Array.from(dialog.querySelectorAll('a')).filter(visible);
  if (!dialog.classList.contains('ReactModal__Content') || layers.length !== 1
      || images.length !== 1 || buttons.length !== 1 || links.length !== 0
      || inputs.length > 1 || inputs.some(input => input.type !== 'checkbox')) {
    return 'unrecognized';
  }
  const close = buttons[0];
  const notice = (images[0].getAttribute('alt') || '').replace(/\s+/g, '');
  if (!notice.includes('10월1일부터승차권예매기간확대')
      || !notice.includes('한국철도공사')
      || !layers[0].contains(close) || close.innerText.trim() !== '창닫기'
      || close.disabled || close.getAttribute('aria-disabled') === 'true') {
    return 'unrecognized';
  }
  return 'booking_window_expansion';
})()
"""


class NoticeCloseControl(Protocol):
    async def click(self) -> None: ...


class NoticeScriptExecutor(Protocol):
    def __call__(self, script: str, *, return_by_value: bool) -> Awaitable[object]: ...


async def _observe_notice(execute_script: NoticeScriptExecutor) -> NoticeState:
    response = await execute_script(OBSERVE_NOTICE_SCRIPT, return_by_value=True)
    if isinstance(response, Mapping):
        result = response.get("result")
        if isinstance(result, Mapping):
            remote_result = result.get("result")
            if isinstance(remote_result, Mapping):
                value = remote_result.get("value")
                if value == "absent":
                    return "absent"
                if value == "booking_window_expansion":
                    return "booking_window_expansion"
    return "unrecognized"


async def dismiss_booking_window_notice(
    *,
    execute_script: NoticeScriptExecutor,
    find_controls: Callable[[str], Awaitable[Sequence[NoticeCloseControl]]],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    timeout_seconds: float,
) -> bool:
    state = await _observe_notice(execute_script)
    if state == "absent":
        return False
    if state != "booking_window_expansion":
        raise BrowserSourceUnavailable("search_notice_unrecognized")
    controls = await find_controls(CLOSE_SELECTOR)
    if len(controls) != 1:
        raise BrowserSourceUnavailable("search_notice_unrecognized")
    # This closes one verified public notice. It never changes the suppression
    # checkbox, accepts consent, or retries an uncertain click.
    await controls[0].click()
    deadline = monotonic() + min(5.0, timeout_seconds)
    while await _observe_notice(execute_script) != "absent":
        if monotonic() >= deadline:
            raise BrowserSourceUnavailable("search_notice_persisted")
        await sleep(0.1)
    logger.info(
        "KORAIL 공식 조회 안내를 닫았습니다 "
        "event=search_notice_dismissed kind=booking_window_expansion"
    )
    return True
