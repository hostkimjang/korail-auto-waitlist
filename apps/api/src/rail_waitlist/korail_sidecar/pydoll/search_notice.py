"""Dismiss verified public promotions without accepting unrelated provider actions.

Known announcement layouts and changed layouts with a labelled reminder checkbox
share one safety policy. Unknown dialogs are observed, never guessed with Escape.
Only closed structural categories are persisted; content fingerprints stay in memory.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ...notice_observations import UnrecognizedNoticeReport
from ..browser_contracts import BrowserSourceUnavailable

logger = logging.getLogger("rail_waitlist.korail_pydoll_browser")
NoticeState = Literal["absent", "public_search_notice", "fallback_notice", "unrecognized"]
NoticeCloseMethod = Literal["strict_close_button", "fallback_close_button"]
MAX_NOTICE_CLOSE_ACTIONS = 5
CLOSE_SELECTOR = '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
FALLBACK_CLOSE_SELECTOR = (
    '[role="dialog"][aria-modal="true"] [data-rail-notice-close], '
    "dialog[open] [data-rail-notice-close]"
)

# DOM identity plus content separates sequential promotions in a reused layer.
# Repeated identical content is never clicked twice within one dismissal call.
# Button identity also detects replacement between observation and resolution.
_COMMON_JS = r"""
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden'
      && rect.width > 0 && rect.height > 0;
  };
  const normalize = text => (text || '').replace(/\s+/g, '').toLowerCase();
  const resolvedLabels = element => !(element.getAttribute('aria-labelledby') || '')
    .split(/\s+/).filter(Boolean).some(id => !document.getElementById(id)?.textContent?.trim());
  const labelsOf = element => [element.innerText, element.getAttribute('aria-label'),
    element.getAttribute('title'), ...(element.getAttribute('aria-labelledby') || '')
      .split(/\s+/).filter(Boolean).map(id => document.getElementById(id)?.innerText)]
    .map(normalize).filter(Boolean);
  const closeLabel = element => {
    const labels = labelsOf(element);
    return resolvedLabels(element) && labels.length > 0 && labels.every(label =>
      /^(?:[x×✕✖]|[x×✕✖]?(?:창닫기|닫기|close|closewindow)[x×✕✖]?)$/.test(label));
  };
  const reminderLabel = input => {
    const labels = [...Array.from(input.labels || []).map(label => label.innerText),
      ...labelsOf(input)].map(normalize).filter(Boolean);
    if (!resolvedLabels(input)) return false;
    return input.type === 'checkbox' && ((labels.length === 0
      && /^popClose[0-9]+$/.test(input.id) && input.closest('.layerWrap.emer_pop'))
      || labels.length > 0 && labels.every(label =>
      /^(오늘(?:하루)?|1일간|하루동안)(그만보기|보지않기|다시보지않기|열지않기)$/.test(label)));
  };
  const knownContent = dialog => dialog.querySelector('.layerWrap.emer_pop .pop_content');
  const publicMarker = dialog => Boolean(knownContent(dialog))
    || Array.from(dialog.querySelectorAll('input')).some(reminderLabel);
  const allDialogs = Array.from(document.querySelectorAll(
    '[role="dialog"], dialog[open], [aria-modal="true"]'
  )).filter(visible);
  const dialogs = allDialogs.filter(dialog => typeof noticeScope === 'undefined'
    || noticeScope !== 'reservation' || publicMarker(dialog));
  if (dialogs.length === 0) return 'absent';
  if (location.protocol !== 'https:'
      || !['www.korail.com', 'korail.com'].includes(location.hostname)
      || !['/ticket/search/general', '/ticket/search/list'].includes(
        location.pathname.replace(/\/$/, '')
      )) return 'unrecognized';
  const store = window.__railWaitlistPublicNoticeIdentity ||=
    {keys: new WeakMap(), next: 1};
  const identityOf = node => {
    if (!store.keys.has(node)) store.keys.set(node, store.next++);
    return store.keys.get(node);
  };
  const hashText = text => {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    return (hash >>> 0).toString(16);
  };
  const controlsOf = dialog => Array.from(dialog.querySelectorAll(
    'button, a[href], [role="button"]'
  ));
  const riskyControl = control => labelsOf(control).some(label =>
    /(결제|동의|승낙|예약|예매|취소|인증|로그인|payment|paynow|agree|consent|accept|reserve|booknow|cancel|login)/
      .test(label) || /^(확인|네|예|아니오|아니요|yes|no|ok|confirm)$/.test(label));
  const safeShape = dialog => {
    const inputs = Array.from(dialog.querySelectorAll('input'));
    const controls = controlsOf(dialog);
    const content = knownContent(dialog);
    const publicActionLabels =
      /^(설문(?:조사)?참여(?:하기)?|이벤트보기|프로모션보기|혜택보기|자세히보기)$/;
    const supplementalLabel = /^(새창으로열기|새창열기|opensinnewwindow)$/;
    const publicAction = control => control.tagName === 'A' && content?.contains(control)
      || labelsOf(control).every(label => publicActionLabels.test(label)
        || supplementalLabel.test(label))
        && labelsOf(control).length > 0;
    const actionBody = /(약관|개인정보|동의|승낙|기존예약|결제정보|결제진행|결제확인)/;
    const actionBodyEnglish = /(consent|agreement|termsandconditions|confirmpayment)/;
    return !actionBody.test(normalize(dialog.innerText))
      && !actionBodyEnglish.test(normalize(dialog.innerText))
      && ((dialog.getAttribute('role') === 'dialog'
      && dialog.getAttribute('aria-modal') === 'true') || dialog.tagName === 'DIALOG')
      && !dialog.querySelector('form, select, textarea, iframe, object, embed, '
        + '[contenteditable]:not([contenteditable="false"]), [role="checkbox"], '
        + '[role="radio"], [role="switch"], [role="textbox"], [role="combobox"], '
        + '[role="listbox"], [role="slider"], [role="spinbutton"]')
      && inputs.length <= 1 && inputs.every(reminderLabel)
      && !controls.some(control => control.form || control.closest('form') || riskyControl(control)
        || (control.hasAttribute('href') && /\/(?:payment|reservation|login|auth)(?:[/?#]|$)/i
          .test(control.getAttribute('href')))
        || (!closeLabel(control) && !publicAction(control)))
      && !allDialogs.some(other => other !== dialog
        && (dialog.contains(other) || other.contains(dialog)));
  };
  const safeClose = control => closeLabel(control) && control.tagName !== 'A'
    && !control.disabled && control.getAttribute('aria-disabled') !== 'true'
    && !control.closest('[inert]') && visible(control);
  const actionable = element => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return hit === element || (hit && element.contains(hit));
  };
  // Only closed categories leave the browser. Unknown labels/classes can contain
  // account data, so they are never copied into persistent diagnostic summaries.
  const shapeOf = dialog => {
    const controls = controlsOf(dialog);
    const labels = controls.slice(0, 6).map(control => closeLabel(control) ? 'close'
      : riskyControl(control) ? 'restricted' : control.tagName === 'A' ? 'link' : 'other');
    const risky = ['form', 'input', 'select', 'textarea', 'iframe', '[contenteditable]']
      .filter(tag => Boolean(dialog.querySelector(tag)));
    return [dialog.tagName.toLowerCase(), 'aria-modal=' +
      (dialog.getAttribute('aria-modal') === 'true' ? 'true' : 'other'),
      'public-marker=' + publicMarker(dialog),
      controls.length + ' controls [' + labels.join(' | ') + ']',
      risky.length ? 'risky:' + risky.join(',') : 'no risky tags'].join(' ').slice(0, 200);
  };
"""

_STRICT_NOTICE_BODY = r"""
  const candidates = [];
  for (const dialog of dialogs) {
    const layers = dialog.querySelectorAll('.layerWrap.emer_pop');
    const contents = dialog.querySelectorAll('.pop_content');
    const buttons = Array.from(dialog.querySelectorAll('button'));
    const inputs = Array.from(dialog.querySelectorAll('input'));
    if (!safeShape(dialog) || !dialog.classList.contains('ReactModal__Content')
        || layers.length !== 1 || contents.length !== 1 || !visible(contents[0])
        || !layers[0].contains(contents[0]) || buttons.length < 1
        || Array.from(dialog.querySelectorAll('a')).some(link => !contents[0].contains(link))) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    const closers = controlsOf(dialog).filter(closeLabel);
    if (closers.length !== 1 || (buttons.length > 1 && !(buttons.length === 2
        && inputs.length === 1 && reminderLabel(inputs[0])))) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    const close = closers[0];
    if (close.tagName !== 'BUTTON' || !layers[0].contains(close) || !safeClose(close)
        || !(contents[0].innerText.trim() || contents[0].querySelector('img'))) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    candidates.push({dialog, close, content: contents[0]});
  }
  let usable = candidates.filter(candidate => actionable(candidate.close));
  if (!usable.length) {
    for (const candidate of [...candidates].reverse()) {
      candidate.close.scrollIntoView({block: 'center'});
      if (actionable(candidate.close)) { usable.push(candidate); break; }
    }
  }
  if (!usable.length) return {state: 'unrecognized', shape: shapeOf(dialogs[dialogs.length - 1])};
  const chosen = usable[usable.length - 1];
  const controls = Array.from(document.querySelectorAll(
    '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
  )).filter(visible);
  return {
    state: 'public_search_notice',
    key: 'S:' + identityOf(chosen.dialog) + ':' + hashText(chosen.content.innerHTML),
    target: identityOf(chosen.close),
    close_index: controls.indexOf(chosen.close), control_count: controls.length,
  };
})()"""
OBSERVE_NOTICE_SCRIPT = "(() => {" + _COMMON_JS + _STRICT_NOTICE_BODY
RESERVATION_NOTICE_SCRIPT = (
    "(() => { const noticeScope = 'reservation';" + _COMMON_JS + _STRICT_NOTICE_BODY
)

# A changed container/class can still be a public notice when its reminder
# checkbox is explicitly labelled. Close vocabulary alone is not public evidence.
_FALLBACK_NOTICE_BODY = r"""
  const candidates = [];
  for (const dialog of dialogs) {
    const controls = controlsOf(dialog);
    const closers = controls.filter(closeLabel);
    const inputs = Array.from(dialog.querySelectorAll('input'));
    if (!safeShape(dialog) || !publicMarker(dialog) || closers.length !== 1
        || !safeClose(closers[0]) || controls.length > 6
        || (controls.some(control => control !== closers[0] && control.tagName !== 'A')
          && inputs.length !== 1)) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    candidates.push({dialog, close: closers[0]});
  }
  let usable = candidates.filter(candidate => actionable(candidate.close));
  if (!usable.length) {
    for (const candidate of [...candidates].reverse()) {
      candidate.close.scrollIntoView({block: 'center'});
      if (actionable(candidate.close)) { usable.push(candidate); break; }
    }
  }
  if (!usable.length) return {state: 'unrecognized', shape: shapeOf(dialogs[dialogs.length - 1])};
  document.querySelectorAll('[data-rail-notice-close]')
    .forEach(node => node.removeAttribute('data-rail-notice-close'));
  usable.forEach(candidate => candidate.close.setAttribute('data-rail-notice-close', '1'));
  const chosen = usable[usable.length - 1];
  const marked = Array.from(document.querySelectorAll(
    '[role="dialog"][aria-modal="true"] [data-rail-notice-close], '
    + 'dialog[open] [data-rail-notice-close]'
  )).filter(visible);
  // The whole structural signature catches same-node content/control mutations.
  // The close-node identity separately prevents resolving a stale element handle.
  return {
    state: 'fallback_notice', close_mode: 'control',
    key: 'F:' + identityOf(chosen.dialog) + ':' + hashText(chosen.dialog.innerHTML),
    target: identityOf(chosen.close),
    close_index: marked.indexOf(chosen.close), control_count: marked.length,
  };
})()"""
FALLBACK_OBSERVE_SCRIPT = "(() => {" + _COMMON_JS + _FALLBACK_NOTICE_BODY
RESERVATION_FALLBACK_SCRIPT = (
    "(() => { const noticeScope = 'reservation';" + _COMMON_JS + _FALLBACK_NOTICE_BODY
)


@dataclass(frozen=True, slots=True)
class NoticeObservation:
    state: NoticeState
    key: str = ""
    close_index: int = 0
    control_count: int = 0
    close_mode: str = "control"
    shape: str = ""
    target: int = 0


class NoticeCloseControl(Protocol):
    async def click(self) -> None: ...


class NoticeScriptExecutor(Protocol):
    def __call__(self, script: str, *, return_by_value: bool) -> Awaitable[object]: ...


NoticeReportSink = Callable[[UnrecognizedNoticeReport], None]


def _script_value(response: object) -> object:
    if isinstance(response, Mapping):
        result = response.get("result")
        if isinstance(result, Mapping):
            remote_result = result.get("result")
            if isinstance(remote_result, Mapping):
                return remote_result.get("value")
    return None


def _validated(value: object, *, accepted: NoticeState) -> NoticeObservation:
    if value == "absent":
        return NoticeObservation("absent")
    if not isinstance(value, Mapping):
        return NoticeObservation("unrecognized")
    state = value.get("state")
    shape = value.get("shape")
    if state == "unrecognized":
        if isinstance(shape, str) and 0 < len(shape) <= 200:
            return NoticeObservation("unrecognized", shape=shape)
        return NoticeObservation("unrecognized")
    if state != accepted:
        return NoticeObservation("unrecognized")
    key = value.get("key")
    index = value.get("close_index")
    count = value.get("control_count")
    mode = "control" if accepted == "public_search_notice" else value.get("close_mode", "control")
    if mode != "control":
        return NoticeObservation("unrecognized")
    if not isinstance(key, str) or not 0 < len(key) <= 64:
        return NoticeObservation("unrecognized")
    target = value.get("target")
    if type(target) is not int or target <= 0:
        return NoticeObservation("unrecognized")
    if type(index) is int and type(count) is int and 0 <= index < count <= 30:
        return NoticeObservation(accepted, key, index, count, "control", target=target)
    return NoticeObservation("unrecognized")


async def _observe(
    execute_script: NoticeScriptExecutor, script: str, *, accepted: NoticeState
) -> NoticeObservation:
    response = await execute_script(script, return_by_value=True)
    return _validated(_script_value(response), accepted=accepted)


async def _unresolved_shape(execute_script: NoticeScriptExecutor) -> str:
    script = "(() => {" + _COMMON_JS + "return shapeOf(dialogs[dialogs.length - 1]);})()"
    try:
        response = await execute_script(script, return_by_value=True)
    except Exception:  # noqa: BLE001 -- diagnostics must never change search outcome.
        return "shape summary unavailable"
    value = _script_value(response)
    if isinstance(value, str) and 0 < len(value) <= 200:
        return value
    return "shape summary unavailable"


async def dismiss_public_search_notices(
    *,
    execute_script: NoticeScriptExecutor,
    find_controls: Callable[[str], Awaitable[Sequence[NoticeCloseControl]]],
    monotonic: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    timeout_seconds: float,
    report_unrecognized: NoticeReportSink | None = None,
    context: Literal["search", "reservation"] = "search",
) -> bool:
    deadline = monotonic() + min(5.0, timeout_seconds)
    closed_keys: set[str] = set()
    closed_methods: list[str] = []
    last_shape = ""
    strict_script = RESERVATION_NOTICE_SCRIPT if context == "reservation" else OBSERVE_NOTICE_SCRIPT

    def report(stage: str, method: str, *, shape: str = "") -> None:
        if report_unrecognized is not None:
            report_unrecognized(
                UnrecognizedNoticeReport(
                    stage=stage,
                    close_method=method,
                    attempts=len(closed_keys),
                    shape_summary=shape or last_shape or "no structural summary",
                )
            )

    async def next_observation() -> tuple[NoticeObservation, str, str, str]:
        nonlocal last_shape
        strict = await _observe(execute_script, strict_script, accepted="public_search_notice")
        if strict.state in ("absent", "public_search_notice"):
            return strict, strict_script, "public_search_notice", "strict_close_button"
        if strict.shape:
            last_shape = strict.shape
            fallback_script = (
                RESERVATION_FALLBACK_SCRIPT if context == "reservation" else FALLBACK_OBSERVE_SCRIPT
            )
            fallback = await _observe(execute_script, fallback_script, accepted="fallback_notice")
            if fallback.state == "absent":
                return (
                    fallback,
                    OBSERVE_NOTICE_SCRIPT,
                    "public_search_notice",
                    "strict_close_button",
                )
            method = "fallback_close_button"
            return fallback, fallback_script, "fallback_notice", method
        return strict, OBSERVE_NOTICE_SCRIPT, "public_search_notice", "strict_close_button"

    observed, script, accepted, method = await next_observation()
    while True:
        if observed.state == "absent":
            if closed_keys:
                logger.info(
                    "KORAIL 공개 검색 공지를 닫았습니다 "
                    "event=search_notice_dismissed strict=%d fallback=%d",
                    closed_methods.count("strict"),
                    closed_methods.count("fallback"),
                )
            return bool(closed_keys)
        if observed.state != accepted:
            if observed.shape:
                last_shape = observed.shape
            report("search_notice_unrecognized", method)
            raise BrowserSourceUnavailable("search_notice_unrecognized")
        if observed.key in closed_keys:
            report(
                "search_notice_persisted",
                method,
                shape=await _unresolved_shape(execute_script),
            )
            raise BrowserSourceUnavailable("search_notice_persisted")
        if len(closed_keys) >= MAX_NOTICE_CLOSE_ACTIONS or monotonic() >= deadline:
            report(
                "search_notice_action_limit",
                method,
                shape=await _unresolved_shape(execute_script),
            )
            raise BrowserSourceUnavailable("search_notice_action_limit")
        if observed.close_mode == "control":
            selector = (
                CLOSE_SELECTOR if accepted == "public_search_notice" else FALLBACK_CLOSE_SELECTOR
            )
            controls = await find_controls(selector)
            if len(controls) != observed.control_count:
                report(
                    "search_notice_unrecognized",
                    method,
                    shape=await _unresolved_shape(execute_script),
                )
                raise BrowserSourceUnavailable("search_notice_unrecognized")
            # Revalidate after resolving controls: a replacement dialog must never
            # turn a verified dismissal into accepting an unrelated provider action.
            if await _observe(execute_script, script, accepted=accepted) != observed:
                report(
                    "search_notice_changed", method, shape=await _unresolved_shape(execute_script)
                )
                raise BrowserSourceUnavailable("search_notice_changed")
            try:
                await controls[observed.close_index].click()
            except Exception:  # noqa: BLE001 -- uncertain dispatch must never be replayed.
                report(
                    "search_notice_close_unknown",
                    method,
                    shape=await _unresolved_shape(execute_script),
                )
                raise BrowserSourceUnavailable("search_notice_close_unknown") from None
        closed_keys.add(observed.key)
        closed_methods.append("strict" if accepted == "public_search_notice" else "fallback")
        previous_key = observed.key
        while True:
            observed, script, accepted, method = await next_observation()
            if observed.state != accepted or observed.key != previous_key:
                break
            if monotonic() >= deadline:
                report(
                    "search_notice_persisted",
                    method,
                    shape=await _unresolved_shape(execute_script),
                )
                raise BrowserSourceUnavailable("search_notice_persisted")
            await sleep(0.1)
        if context == "reservation":
            # Re-read the booking surface after each exact public notice dismissal.
            # Its other dialogs belong to the reservation policy and stay untouched.
            logger.info(
                "KORAIL event=search_notice_dismissed strict=%d fallback=%d",
                closed_methods.count("strict"),
                closed_methods.count("fallback"),
            )
            return True
