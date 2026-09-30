"""Close public search announcements independently of their changing content."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ..browser_contracts import BrowserSourceUnavailable

logger = logging.getLogger("rail_waitlist.korail_pydoll_browser")
NoticeState = Literal["absent", "public_search_notice", "unrecognized"]
MAX_NOTICE_CLOSE_ACTIONS = 5
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
    '[role="dialog"], dialog[open], [aria-modal="true"]'
  )).filter(visible);
  if (dialogs.length === 0) return 'absent';
  if (location.protocol !== 'https:'
      || !['www.korail.com', 'korail.com'].includes(location.hostname)
      || !['/ticket/search/general', '/ticket/search/list'].includes(
        location.pathname.replace(/\/$/, '')
      )) {
    return 'unrecognized';
  }
  const candidates = [];
  for (const dialog of dialogs) {
    const layers = dialog.querySelectorAll('.layerWrap.emer_pop');
    const contents = dialog.querySelectorAll('.pop_content');
    const buttons = Array.from(dialog.querySelectorAll('button'));
    const inputs = Array.from(dialog.querySelectorAll('input'));
    if (!dialog.classList.contains('ReactModal__Content')
        || dialog.getAttribute('role') !== 'dialog'
        || dialog.getAttribute('aria-modal') !== 'true'
        || layers.length !== 1 || contents.length !== 1 || !visible(contents[0])
        || !layers[0].contains(contents[0]) || buttons.length !== 1
        || inputs.length > 1 || inputs.some(input => input.type !== 'checkbox')
        || dialog.querySelector('form, select, textarea, iframe, [contenteditable="true"]')
        || Array.from(dialog.querySelectorAll('a')).some(link => !contents[0].contains(link))) {
      return 'unrecognized';
    }
    const close = buttons[0];
    const label = (close.innerText || '').replace(/\s+/g, '');
    if (!layers[0].contains(close) || !visible(close)
        || !['창닫기', '닫기'].includes(label) || close.disabled
        || close.getAttribute('aria-disabled') === 'true'
        || !(contents[0].innerText.trim() || contents[0].querySelector('img'))) {
      return 'unrecognized';
    }
    candidates.push({dialog, close, content: contents[0]});
  }
  const isActionable = ({close}) => {
    const rect = close.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return hit === close || (hit && close.contains(hit));
  };
  const actionable = candidates.filter(isActionable);
  if (!actionable.length) {
    for (const candidate of [...candidates].reverse()) {
      candidate.close.scrollIntoView({block: 'center'});
      if (isActionable(candidate)) { actionable.push(candidate); break; }
    }
  }
  if (!actionable.length) return 'unrecognized';
  const chosen = actionable[actionable.length - 1];
  // DOM identity distinguishes stacked notices; content detects a new announcement
  // rendered inside the same modal. Neither the content nor its key is logged.
  const store = window.__railWaitlistPublicNoticeIdentity ||= {keys: new WeakMap(), next: 1};
  if (!store.keys.has(chosen.dialog)) store.keys.set(chosen.dialog, store.next++);
  const text = chosen.content.innerHTML;
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  const controls = Array.from(document.querySelectorAll(
    '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
  )).filter(visible);
  return {
    state: 'public_search_notice',
    key: `${store.keys.get(chosen.dialog)}:${(hash >>> 0).toString(16)}`,
    close_index: controls.indexOf(chosen.close),
    control_count: controls.length,
  };
})()
"""


@dataclass(frozen=True, slots=True)
class NoticeObservation:
    state: NoticeState
    key: str = ""
    close_index: int = 0
    control_count: int = 0


class NoticeCloseControl(Protocol):
    async def click(self) -> None: ...


class NoticeScriptExecutor(Protocol):
    def __call__(self, script: str, *, return_by_value: bool) -> Awaitable[object]: ...


async def _observe_notice(execute_script: NoticeScriptExecutor) -> NoticeObservation:
    response = await execute_script(OBSERVE_NOTICE_SCRIPT, return_by_value=True)
    if isinstance(response, Mapping):
        result = response.get("result")
        if isinstance(result, Mapping):
            remote_result = result.get("result")
            if isinstance(remote_result, Mapping):
                value = remote_result.get("value")
                if value == "absent":
                    return NoticeObservation("absent")
                if isinstance(value, Mapping) and value.get("state") == "public_search_notice":
                    key = value.get("key")
                    index = value.get("close_index")
                    count = value.get("control_count")
                    if (
                        isinstance(key, str)
                        and 0 < len(key) <= 64
                        and type(index) is int
                        and type(count) is int
                        and 0 <= index < count <= MAX_NOTICE_CLOSE_ACTIONS
                    ):
                        return NoticeObservation("public_search_notice", key, index, count)
    return NoticeObservation("unrecognized")


async def dismiss_public_search_notices(
    *,
    execute_script: NoticeScriptExecutor,
    find_controls: Callable[[str], Awaitable[Sequence[NoticeCloseControl]]],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    timeout_seconds: float,
) -> bool:
    observed = await _observe_notice(execute_script)
    deadline = monotonic() + min(5.0, timeout_seconds)
    closed_keys: set[str] = set()
    while True:
        if observed.state == "absent":
            return bool(closed_keys)
        if observed.state != "public_search_notice":
            raise BrowserSourceUnavailable("search_notice_unrecognized")
        if observed.key in closed_keys:
            raise BrowserSourceUnavailable("search_notice_persisted")
        if len(closed_keys) >= MAX_NOTICE_CLOSE_ACTIONS or monotonic() >= deadline:
            raise BrowserSourceUnavailable("search_notice_action_limit")
        controls = await find_controls(CLOSE_SELECTOR)
        if len(controls) != observed.control_count:
            raise BrowserSourceUnavailable("search_notice_unrecognized")
        # Revalidate after resolving controls: a replacement dialog must never
        # turn a verified dismissal into accepting an unrelated provider action.
        if await _observe_notice(execute_script) != observed:
            raise BrowserSourceUnavailable("search_notice_changed")
        try:
            await controls[observed.close_index].click()
        except Exception:  # noqa: BLE001 -- uncertain dispatch must never be replayed.
            raise BrowserSourceUnavailable("search_notice_close_unknown") from None
        closed_keys.add(observed.key)
        logger.info(
            "KORAIL 공개 검색 공지를 닫았습니다 "
            "event=search_notice_dismissed kind=public_search_notice count=%d",
            len(closed_keys),
        )
        previous_key = observed.key
        while True:
            observed = await _observe_notice(execute_script)
            if observed.state != "public_search_notice" or observed.key != previous_key:
                break
            if monotonic() >= deadline:
                raise BrowserSourceUnavailable("search_notice_persisted")
            await sleep(0.1)
