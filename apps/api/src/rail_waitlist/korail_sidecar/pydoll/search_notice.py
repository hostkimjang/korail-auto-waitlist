"""Close public search announcements independently of their changing content.

Two passes run against the official search page. The strict pass recognizes the
known announcement shape (ReactModal layer with 창닫기, plus
survey-style two-button windows only with the 1일간 그만보기) exactly as before.
When the official page ships a dialog shape that predates this code, the fallback
pass still closes a visible modal dialog that has no form controls and exactly
one close-vocabulary control, and — only when every visible modal dialog has
no interactive control at all — lets the page itself close the dialog
through Escape (native dialogs close directly). Every close is verified by
watching the dialog disappear, and a terminal failure records one structural
summary of the blocking dialog (classes, control labels, risky tags — never body
text) so the settings screen can show what changed on the official page.
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
NoticeCloseMethod = Literal["strict_close_button", "fallback_close_button", "fallback_escape"]
MAX_NOTICE_CLOSE_ACTIONS = 5
CLOSE_SELECTOR = '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
FALLBACK_CLOSE_SELECTOR = (
    '[role="dialog"][aria-modal="true"] [data-rail-notice-close], '
    "dialog[open] [data-rail-notice-close]"
)

# Shared JavaScript prologue: visibility helper, dialog inventory, the
# official-search page guard, a per-dialog identity store shared by every pass,
# and the structural summary used for diagnostics. Identities live in a WeakMap
# keyed by the DOM node, so a dialog that survives its own close can never be
# re-clicked as if it were new. The summary keeps structure only — announcement
# body text never leaves the page.
_COMMON_JS = r"""
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
  const store = window.__railWaitlistPublicNoticeIdentity ||=
    {keys: new WeakMap(), next: 1};
  const identityOf = node => {
    if (!store.keys.has(node)) store.keys.set(node, store.next++);
    return store.keys.get(node);
  };
  const hashText = text => {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {
      hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    }
    return (hash >>> 0).toString(16);
  };
  const closeLabel = element => {
    const text = ((element.innerText || '') + ' ' +
      (element.getAttribute('aria-label') || '') + ' ' +
      (element.getAttribute('title') || '')).replace(/\s+/g, ' ').trim();
    if (/(\ucc3d\ub2eb\uae30|\ub2eb\uae30|\bclose\b)/i.test(text)) return true;
    const glyph = (element.innerText || '').replace(/\s+/g, '');
    return ['x', 'X', '\u2715', '\u2716', '\u00d7'].includes(glyph);
  };
  const actionable = element => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return hit === element || (hit && element.contains(hit));
  };
  const hasControl = node => Boolean(node.closest('button, a[href], [role="button"]'));
  const shapeOf = dialog => {
    const classes = Array.from(dialog.classList).slice(0, 3).join('.');
    const controls = Array.from(dialog.querySelectorAll(
      'button, a[href], [role="button"]'
    ));
    const labels = controls.slice(0, 6).map(control => ((control.innerText
        || control.getAttribute('aria-label') || control.getAttribute('title')
        || '?').replace(/\s+/g, ' ').trim().slice(0, 30)));
    const risky = ['form', 'input', 'select', 'textarea', 'iframe']
      .filter(tag => Boolean(dialog.querySelector(tag)));
    return [
      dialog.tagName.toLowerCase() + (classes ? '.' + classes : ''),
      'aria-modal=' + (dialog.getAttribute('aria-modal') || 'none'),
      controls.length + ' controls [' + labels.join(' | ') + ']',
      risky.length ? 'risky:' + risky.join(',') : 'no risky tags',
    ].join(' ').slice(0, 200);
  };
"""

OBSERVE_NOTICE_SCRIPT = (
    "(() => {"
    + _COMMON_JS
    + r"""
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
        || !layers[0].contains(contents[0]) || buttons.length < 1
        || inputs.length > 1 || inputs.some(input => input.type !== 'checkbox')
        || dialog.querySelector('form, select, textarea, iframe, [contenteditable="true"]')
        || Array.from(dialog.querySelectorAll('a')).some(link => !contents[0].contains(link))) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    const closeLabelSet = ['\ucc3d\ub2eb\uae30', '\ub2eb\uae30'];
    const closers = buttons.filter(
      button => closeLabelSet.includes((button.innerText || '').replace(/\s+/g, ''))
    );
    // A survey-style announcement renders its own call-to-action next to the close
    // control. That two-button shape is only trusted together with the
    // 1\uc77c\uac04 \uadf8\ub9cc\ubcf4\uae30 checkbox KORAIL attaches to public announcements;
    // action dialogs without the marker (e.g. reservation choices) stay unmatched.
    const hasReminderCheckbox = inputs.some(input => input.type === 'checkbox');
    if (closers.length !== 1
        || (buttons.length > 1 && !(buttons.length === 2 && hasReminderCheckbox))) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    const close = closers[0];
    const label = (close.innerText || '').replace(/\s+/g, '');
    if (!layers[0].contains(close) || !visible(close)
        || !['\ucc3d\ub2eb\uae30', '\ub2eb\uae30'].includes(label) || close.disabled
        || close.getAttribute('aria-disabled') === 'true'
        || !(contents[0].innerText.trim() || contents[0].querySelector('img'))) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    candidates.push({dialog, close, content: contents[0]});
  }
  let usable = candidates.filter(candidate => actionable(candidate.close));
  if (!usable.length && candidates.length) {
    for (const candidate of [...candidates].reverse()) {
      candidate.close.scrollIntoView({block: 'center'});
      if (actionable(candidate.close)) { usable.push(candidate); break; }
    }
  }
  if (!usable.length) {
    return {state: 'unrecognized', shape: shapeOf(dialogs[dialogs.length - 1])};
  }
  const chosen = usable[usable.length - 1];
  const identity = identityOf(chosen.dialog);
  const contentKey = hashText(chosen.content.innerHTML);
  const controls = Array.from(document.querySelectorAll(
    '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
  )).filter(visible);
  return {
    state: 'public_search_notice',
    key: 'S:' + identity + ':' + contentKey,
    close_index: controls.indexOf(chosen.close),
    control_count: controls.length,
  };
})()"""
)

# Research-grounded relaxed pass. A visible aria-modal dialog (or native open
# dialog) with no form controls and exactly one close-vocabulary control may be
# closed by that control alone, so stacked or newly designed announcement windows
# still resolve. A dialog whose only dismissal option is Escape gets one Escape
# keydown that the page itself must act on; native dialogs close() directly.
# Anything with form fields, ambiguous close labels, or a non-modal role stays
# unmatched and blocks the search as before.
FALLBACK_OBSERVE_SCRIPT = (
    "(() => {"
    + _COMMON_JS
    + r"""
  const modal = dialog => (dialog.getAttribute('role') === 'dialog'
    && dialog.getAttribute('aria-modal') === 'true') || dialog.tagName === 'DIALOG';
  const riskyDialog = dialogs.find(dialog => !modal(dialog)
    || dialog.querySelector(
      'input:not([type="checkbox"]), select, textarea, form, iframe, [contenteditable="true"]'
    ));
  if (riskyDialog) {
    return {state: 'unrecognized', shape: shapeOf(riskyDialog)};
  }
  const candidates = [];
  for (const dialog of dialogs) {
    const controls = Array.from(dialog.querySelectorAll('button, a[href], [role="button"]'));
    // A control-free modal is a dismissal-only surface (native or Escape-driven
    // dialogs); it never blocks the escape verdict below.
    if (controls.length === 0) continue;
    const closers = controls.filter(control => closeLabel(control)
      && !control.disabled && control.getAttribute('aria-disabled') !== 'true');
    if (closers.length !== 1) {
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
    if (visible(closers[0])) {
      candidates.push({dialog, close: closers[0]});
    } else if (hasControl(closers[0])) {
      // The dialog offers a hidden or embedded close affordance instead of none,
      // so pressing Escape is a guess; leave it unrecognized.
      return {state: 'unrecognized', shape: shapeOf(dialog)};
    }
  }
  let usable = candidates.filter(candidate => actionable(candidate.close));
  if (!usable.length && candidates.length) {
    for (const candidate of [...candidates].reverse()) {
      candidate.close.scrollIntoView({block: 'center'});
      if (actionable(candidate.close)) { usable.push(candidate); break; }
    }
  }
  if (usable.length) {
    document.querySelectorAll('[data-rail-notice-close]')
      .forEach(node => node.removeAttribute('data-rail-notice-close'));
    for (const candidate of usable) {
      candidate.close.setAttribute('data-rail-notice-close', '1');
    }
    const chosen = usable[usable.length - 1];
    const marked = Array.from(document.querySelectorAll(
      '[role="dialog"][aria-modal="true"] [data-rail-notice-close], '
      + 'dialog[open] [data-rail-notice-close]'
    )).filter(visible);
    return {
      state: 'fallback_notice',
      close_mode: 'control',
      key: 'F:' + identityOf(chosen.dialog),
      close_index: marked.indexOf(chosen.close),
      control_count: marked.length,
    };
  }
  if (dialogs.every(dialog =>
    dialog.querySelectorAll('button, a[href], [role="button"]').length === 0)) {
    return {
      state: 'fallback_notice',
      close_mode: 'escape',
      key: 'E:' + identityOf(dialogs[0]),
      close_index: 0,
      control_count: 0,
    };
  }
  return {state: 'unrecognized', shape: shapeOf(dialogs[dialogs.length - 1])};
})()"""
)


def escape_script(identity: str) -> str:
    # The page decides what Escape means; a native dialog additionally defines
    # close() as its programmatic dismissal. Either way the caller must still
    # verify that the dialog disappeared before trusting the dismissal.
    return (
        "(() => {"
        + _COMMON_JS
        + "const target = dialogs.find(dialog => store.keys.get(dialog) === "
        + identity
        + ");"
        "  if (!target) return 'gone';"
        "  if (target.tagName === 'DIALOG') { target.close(); return 'closed'; }"
        "  target.dispatchEvent(new KeyboardEvent('keydown',"
        " {key: 'Escape', code: 'Escape', keyCode: 27, which: 27,"
        " bubbles: true, cancelable: true}));"
        "  return 'dispatched';"
        "})()"
    )


@dataclass(frozen=True, slots=True)
class NoticeObservation:
    state: NoticeState
    key: str = ""
    close_index: int = 0
    control_count: int = 0
    close_mode: str = "control"
    shape: str = ""


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
    if mode not in ("control", "escape"):
        return NoticeObservation("unrecognized")
    if not isinstance(key, str) or not 0 < len(key) <= 64:
        return NoticeObservation("unrecognized")
    if mode == "escape":
        if index == 0 and count == 0:
            return NoticeObservation(accepted, key, 0, 0, "escape")
        return NoticeObservation("unrecognized")
    if type(index) is int and type(count) is int and 0 <= index < count <= MAX_NOTICE_CLOSE_ACTIONS:
        return NoticeObservation(accepted, key, index, count, "control")
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
) -> bool:
    deadline = monotonic() + min(5.0, timeout_seconds)
    closed_keys: set[str] = set()
    closed_methods: list[str] = []
    last_shape = ""

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
        strict = await _observe(
            execute_script, OBSERVE_NOTICE_SCRIPT, accepted="public_search_notice"
        )
        if strict.state in ("absent", "public_search_notice"):
            return strict, OBSERVE_NOTICE_SCRIPT, "public_search_notice", "strict_close_button"
        if strict.shape:
            last_shape = strict.shape
            fallback = await _observe(
                execute_script, FALLBACK_OBSERVE_SCRIPT, accepted="fallback_notice"
            )
            if fallback.state == "absent":
                return (
                    fallback,
                    OBSERVE_NOTICE_SCRIPT,
                    "public_search_notice",
                    "strict_close_button",
                )
            method = (
                "fallback_escape" if fallback.close_mode == "escape" else "fallback_close_button"
            )
            return fallback, FALLBACK_OBSERVE_SCRIPT, "fallback_notice", method
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
                raise BrowserSourceUnavailable("search_notice_changed")
            try:
                await controls[observed.close_index].click()
            except Exception:  # noqa: BLE001 -- uncertain dispatch must never be replayed.
                raise BrowserSourceUnavailable("search_notice_close_unknown") from None
        else:
            identity = observed.key.split(":", 1)[1]
            if not identity.isdigit():
                raise BrowserSourceUnavailable("search_notice_unrecognized")
            try:
                await execute_script(escape_script(identity), return_by_value=True)
            except Exception:  # noqa: BLE001 -- uncertain dispatch must never be replayed.
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
