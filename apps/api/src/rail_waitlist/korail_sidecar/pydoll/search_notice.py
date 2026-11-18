"""Dismiss verified public promotions without accepting unrelated provider actions.

KORAIL replaces its announcement artwork every few days and its markup every few weeks,
so the popup is located from what the page renders instead of one fixed class layout:
ARIA dialogs, the known announcement layer, and a "do not show today" reminder inside a
layer that covers the page. A candidate still needs public-notice evidence (the known
layer or a reminder control) and only an unambiguous close control is ever clicked.
Labels are read the way a person reads them: the visible text names a control, while
accessibility labels may decorate it but never contradict it. Extra links and buttons
are never pressed, so their wording does not block dismissal. Input fields, forms,
frames outside the notice body, nested dialogs and ambiguous close controls still
reject the dialog. Unknown dialogs are observed with closed rejection reasons and are
never guessed with Escape. Content fingerprints stay in memory.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ...notice_observations import UnrecognizedNoticeReport, known_rejection_reasons
from ..browser_contracts import BrowserSourceUnavailable

logger = logging.getLogger("rail_waitlist.korail_pydoll_browser")
NoticeState = Literal["absent", "public_search_notice", "fallback_notice", "unrecognized"]
NoticeCloseMethod = Literal["strict_close_button", "fallback_close_button"]
MAX_NOTICE_CLOSE_ACTIONS = 5
CLOSE_SELECTOR = '.ReactModal__Content[role="dialog"][aria-modal="true"] .layerWrap.emer_pop button'
# Only the dismissal script sets this mark, and it clears stale marks before setting it.
FALLBACK_CLOSE_SELECTOR = "[data-rail-notice-close]"
# Cheap presence gate for the reservation flow; the script still verifies the evidence.
PUBLIC_NOTICE_PRESENCE_SELECTOR = (
    ".layerWrap.emer_pop, "
    '[role="dialog"] input[type="checkbox"], [aria-modal="true"] input[type="checkbox"], '
    'dialog[open] input[type="checkbox"], input[type="checkbox"][id^="popClose"]'
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
  // Brackets and separators decorate a label without changing what it names.
  const compact = text => normalize(text)
    .replace(/[()[\]{}<>「」『』【】"'\u0060·•ㆍ:;,.!?~_-]/g, '');
  const resolvedLabels = element => !(element.getAttribute('aria-labelledby') || '')
    .split(/\s+/).filter(Boolean).some(id => !document.getElementById(id)?.textContent?.trim());
  const auxiliaryLabels = element => [element.getAttribute('aria-label'),
    element.getAttribute('title'), ...(element.getAttribute('aria-labelledby') || '')
      .split(/\s+/).filter(Boolean).map(id => document.getElementById(id)?.innerText)]
    .map(compact).filter(Boolean);
  const iconLabels = element => Array.from(element.querySelectorAll(
    'img, [aria-label], svg title'
  )).map(node => node.tagName.toLowerCase() === 'title' ? node.textContent
    : node.getAttribute('aria-label') || node.getAttribute('alt')).map(compact).filter(Boolean);
  const CLOSE_NAME = new RegExp('^(?:[x×✕✖╳]|(?:팝업|공지|공지사항|레이어|레이어팝업|알림|안내'
    + '|이벤트|광고|배너|이|현재|본)?창?(?:닫기|닫음)(?:버튼)?'
    + '|close(?:popup|window|layer|modal|dialog)?(?:button|btn)?'
    + '|(?:닫기|close)[x×✕✖╳]|[x×✕✖╳](?:닫기|close))$');
  const CLOSE_HINT = /(?:닫기|닫음|close|[×✕✖╳])/;
  const REMINDER_NAME = new RegExp('^(?:(?:오늘|금일|하루|일주일|한주|한달|[0-9]{1,2}일'
    + '|[0-9]{1,2}시간)(?:하루)?(?:은|간|동안)?)?(?:이|본|해당)?'
    + '(?:창|팝업|팝업창|공지|레이어|알림|이벤트)?(?:을|를|은|는)?(?:다시|더이상|그만)?'
    + '(?:보지않기|보지않음|안보기|열지않기|열지않음|띄우지않기|표시하지않기|노출하지않기'
    + '|그만보기|그만열기)(?:체크박스|체크|선택|설정|하기)*$');
  const REMINDER_HINT = /(?:보지않|열지않|안보기|그만보|그만열|띄우지않|표시하지않|노출하지않)/;
  const RISKY_WORD = new RegExp('(?:결제|동의|승낙|약관|개인정보|예약|예매|발권|구매|환불|취소'
    + '|인증|로그인|회원가입|payment|paynow|agree|consent|accept|reserve|booknow|purchase'
    + '|refund|cancel|login|signin|signup)');
  const RISKY_ANSWER = /^(?:확인|네|예|아니오|아니요|yes|no|ok|confirm)$/;
  const riskyLabel = label => RISKY_WORD.test(label) || RISKY_ANSWER.test(label);
  // In-page anchors and script links act like a button handler; real URLs navigate away.
  const NAVIGATION_FREE_HREF = /^(?:#[a-z0-9_-]*;?|javascript:.*)$/;
  const navigates = control => control.tagName === 'A' && control.hasAttribute('href')
    && !NAVIGATION_FREE_HREF.test((control.getAttribute('href') || '').trim().toLowerCase());
  // close | reminder | restricted | link | other | conflict. Only close is ever clicked.
  const controlKind = control => {
    const own = compact(control.tagName === 'INPUT' ? control.value : control.innerText);
    const names = own ? [own] : iconLabels(control);
    const aux = auxiliaryLabels(control);
    const all = [...names, ...aux];
    if (all.some(name => REMINDER_HINT.test(name))) {
      return !resolvedLabels(control) || all.some(riskyLabel) ? 'conflict' : 'reminder';
    }
    const closeLike = all.some(name => CLOSE_NAME.test(name) || CLOSE_HINT.test(name))
      || (all.length === 0 && Array.from(control.classList).some(token => /close/i.test(token)));
    if (!closeLike) {
      if (all.some(riskyLabel)) return 'restricted';
      return navigates(control) ? 'link' : 'other';
    }
    if (!resolvedLabels(control) || navigates(control) || all.some(riskyLabel)) return 'conflict';
    if (all.length === 0) return 'close';
    const primary = names.length ? names : aux;
    if (!primary.every(name => CLOSE_NAME.test(name))) return 'conflict';
    return aux.every(name => CLOSE_NAME.test(name) || CLOSE_HINT.test(name)) ? 'close' : 'conflict';
  };
  // reminder | conflict. The checkbox is never changed; it only proves a public reminder.
  const checkboxKind = box => {
    const shown = box.tagName === 'INPUT'
      ? Array.from(box.labels || []).map(label => compact(label.innerText))
      : [compact(box.innerText)];
    const all = [...shown.filter(Boolean), ...auxiliaryLabels(box)];
    if (!resolvedLabels(box) || all.some(riskyLabel)) return 'conflict';
    if (all.length === 0) {
      return box.tagName === 'INPUT' && /^popClose[0-9]+$/.test(box.id)
        && box.closest('.layerWrap.emer_pop') ? 'reminder' : 'conflict';
    }
    return all.some(label => REMINDER_NAME.test(label))
      && all.every(label => REMINDER_HINT.test(label)) ? 'reminder' : 'conflict';
  };
  const FIELD_SELECTOR = 'select, textarea, [contenteditable]:not([contenteditable="false"]), '
    + '[role="textbox"], [role="searchbox"], [role="combobox"], [role="listbox"], '
    + '[role="slider"], [role="spinbutton"], [role="radio"], [role="switch"]';
  const BUTTON_INPUT_TYPES = ['button', 'submit', 'reset', 'image'];
  const fieldKinds = root => [
    ...Array.from(root.querySelectorAll('input')).flatMap(input => {
      const type = (input.getAttribute('type') || 'text').toLowerCase();
      if (type === 'hidden' || BUTTON_INPUT_TYPES.includes(type)) return [];
      return [type === 'checkbox' ? checkboxKind(input) : 'field'];
    }),
    ...Array.from(root.querySelectorAll('[role="checkbox"]:not(input)')).map(checkboxKind),
    ...Array.from(root.querySelectorAll(FIELD_SELECTOR)).map(() => 'field'),
  ];
  const controlsOf = root => Array.from(root.querySelectorAll(
    'button, a, [role="button"], input[type="button"], input[type="submit"], '
    + 'input[type="reset"], input[type="image"]'
  ));
  const knownContent = root => root.querySelector('.layerWrap.emer_pop .pop_content');
  const publicMarker = root => Boolean(knownContent(root))
    || fieldKinds(root).includes('reminder')
    || controlsOf(root).some(control => controlKind(control) === 'reminder');
  const DIALOG_SELECTOR = '[role="dialog"], dialog[open], [aria-modal="true"]';
  // A layer that is not announced as a dialog counts only while it covers the page.
  const outermostFixed = node => {
    let found = null;
    for (let current = node.parentElement; current && current !== document.body;
      current = current.parentElement) {
      if (getComputedStyle(current).position === 'fixed') found = current;
    }
    return found;
  };
  const blocksPage = root => [[0.5, 0.5], [0.3, 0.3], [0.7, 0.3], [0.3, 0.7], [0.7, 0.7]]
    .some(([x, y]) => root.contains(document.elementFromPoint(innerWidth * x, innerHeight * y)));
  const evidenceRoots = Array.from(document.querySelectorAll(
    '.layerWrap.emer_pop, input[type="checkbox"], [role="checkbox"]:not(input)'
  )).filter(node => node.matches('.layerWrap.emer_pop') ? visible(node)
    : (visible(node) || Array.from(node.labels || []).some(visible))
      && checkboxKind(node) === 'reminder')
    .map(node => node.closest(DIALOG_SELECTOR) || outermostFixed(node))
    .filter(root => root && (root.matches(DIALOG_SELECTOR) || blocksPage(root)));
  const allDialogs = Array.from(new Set([
    ...Array.from(document.querySelectorAll(DIALOG_SELECTOR)).filter(visible), ...evidenceRoots,
  ])).sort((left, right) =>
    left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
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
  const safeClose = control => !control.disabled
    && control.getAttribute('aria-disabled') !== 'true'
    && !control.closest('[inert]') && visible(control);
  const actionable = element => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return hit === element || (hit && element.contains(hit));
  };
  // Evidence and an unambiguous close are required; extra links and buttons are tolerated
  // because they are never pressed. Every reason is a closed category without page text.
  const judge = root => {
    const reasons = [];
    const controls = controlsOf(root);
    const kinds = controls.map(controlKind);
    const fields = fieldKinds(root);
    const content = knownContent(root);
    if (!content && !fields.includes('reminder') && !kinds.includes('reminder')) {
      reasons.push('no_public_evidence');
    }
    if (fields.includes('conflict')) reasons.push('reminder_label_conflict');
    if (fields.includes('field')) reasons.push('input_field');
    if (root.querySelector('form')
        || controls.some(control => control.form || control.closest('form'))
        || Array.from(root.querySelectorAll('input')).some(input => input.form)) {
      reasons.push('form_submission');
    }
    if (Array.from(root.querySelectorAll('iframe, object, embed'))
      .some(frame => !content || !content.contains(frame))) reasons.push('embedded_frame');
    if (allDialogs.some(other => other !== root
        && (root.contains(other) || other.contains(root)))) reasons.push('nested_dialog');
    if (kinds.includes('conflict')) reasons.push('ambiguous_close_label');
    const closers = controls.filter((control, index) => kinds[index] === 'close');
    const usable = closers.filter(safeClose);
    if (!closers.length) reasons.push('no_close_control');
    else if (!usable.length) reasons.push('close_unavailable');
    return {reasons, closers: usable};
  };
  // The visual panel is the box around rendered content, not the full-screen backdrop.
  const panelOf = root => {
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const node of root.querySelectorAll(
      'img, svg, video, canvas, button, a, input, label, p, li, '
      + 'h1, h2, h3, h4, h5, h6, span, strong'
    )) {
      if (!visible(node)) continue;
      const rect = node.getBoundingClientRect();
      left = Math.min(left, rect.left);
      top = Math.min(top, rect.top);
      right = Math.max(right, rect.right);
      bottom = Math.max(bottom, rect.bottom);
    }
    if (left === Infinity) return 'panel unknown';
    const size = value => Math.round(value / 10) * 10;
    return 'panel ' + size(right - left) + 'x' + size(bottom - top)
      + (blocksPage(root) ? ' blocking' : ' not-blocking');
  };
  // Only closed categories leave the browser. Labels, classes and text can carry
  // account data, so they are never copied into persistent diagnostic summaries.
  const shapeOf = root => {
    const kinds = controlsOf(root).map(controlKind);
    const fields = fieldKinds(root);
    const evidence = [knownContent(root) ? 'layer' : '',
      fields.includes('reminder') || kinds.includes('reminder') ? 'reminder' : '']
      .filter(Boolean).join('+') || 'none';
    return [root.tagName.toLowerCase(),
      'aria-modal=' + (root.getAttribute('aria-modal') === 'true' ? 'true' : 'other'),
      root.matches(DIALOG_SELECTOR) ? 'dialog' : 'layer', 'evidence=' + evidence,
      kinds.length + ' controls [' + kinds.slice(0, 6).join(' | ') + ']',
      'inputs [' + fields.slice(0, 4).join(' | ') + ']', panelOf(root)].join(' ').slice(0, 200);
  };
"""

# The known KORAIL layout keeps its fixed selector so its close never needs a DOM mark.
_STRICT_NOTICE_BODY = r"""
  const candidates = [];
  for (const dialog of dialogs) {
    const layers = dialog.querySelectorAll('.layerWrap.emer_pop');
    const contents = dialog.querySelectorAll('.pop_content');
    const verdict = judge(dialog);
    const close = verdict.closers.length === 1 ? verdict.closers[0] : null;
    if (verdict.reasons.length || !close || close.tagName !== 'BUTTON'
        || !dialog.classList.contains('ReactModal__Content')
        || dialog.getAttribute('role') !== 'dialog'
        || dialog.getAttribute('aria-modal') !== 'true'
        || layers.length !== 1 || contents.length !== 1 || !visible(contents[0])
        || !layers[0].contains(contents[0]) || !layers[0].contains(close)
        || !(contents[0].innerText.trim() || contents[0].querySelector('img'))) {
      return {state: 'unrecognized', shape: shapeOf(dialog), reasons: verdict.reasons};
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
  if (!usable.length) {
    return {state: 'unrecognized', shape: shapeOf(dialogs[dialogs.length - 1]),
      reasons: ['close_unavailable']};
  }
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

# Changed containers, classes, close icons and extra calls to action are handled here.
# An announced dialog that fails the judgement stops the caller; a covering layer that
# was never announced as a dialog is left untouched when it cannot be verified.
_FALLBACK_NOTICE_BODY = r"""
  const candidates = [];
  for (const dialog of dialogs) {
    const verdict = judge(dialog);
    if (!verdict.reasons.length) {
      candidates.push({dialog, closers: verdict.closers});
    } else if (dialog.matches(DIALOG_SELECTOR)) {
      return {state: 'unrecognized', shape: shapeOf(dialog), reasons: verdict.reasons};
    }
  }
  if (!candidates.length) return 'absent';
  // Prefer a close control that is already on top, then one with a visible name.
  const pick = closers => [...closers].sort((left, right) =>
    Number(Boolean(actionable(right))) - Number(Boolean(actionable(left)))
    || Number(Boolean(compact(right.innerText))) - Number(Boolean(compact(left.innerText))))[0];
  let usable = candidates.map(candidate => ({...candidate, close: pick(candidate.closers)}))
    .filter(candidate => actionable(candidate.close));
  if (!usable.length) {
    for (const candidate of [...candidates].reverse()) {
      const close = pick(candidate.closers);
      close.scrollIntoView({block: 'center'});
      if (actionable(close)) { usable.push({...candidate, close}); break; }
    }
  }
  if (!usable.length) {
    const announced = [...candidates].reverse()
      .find(candidate => candidate.dialog.matches(DIALOG_SELECTOR));
    return announced ? {state: 'unrecognized', shape: shapeOf(announced.dialog),
      reasons: ['close_unavailable']} : 'absent';
  }
  document.querySelectorAll('[data-rail-notice-close]')
    .forEach(node => node.removeAttribute('data-rail-notice-close'));
  usable.forEach(candidate => candidate.close.setAttribute('data-rail-notice-close', '1'));
  const chosen = usable[usable.length - 1];
  const marked = Array.from(document.querySelectorAll('[data-rail-notice-close]'))
    .filter(visible);
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
    reasons: tuple[str, ...] = ()


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
        reasons = known_rejection_reasons(value.get("reasons"))
        if isinstance(shape, str) and 0 < len(shape) <= 200:
            return NoticeObservation("unrecognized", shape=shape, reasons=reasons)
        return NoticeObservation("unrecognized", reasons=reasons)
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
    last_reasons: tuple[str, ...] = ()
    strict_script = RESERVATION_NOTICE_SCRIPT if context == "reservation" else OBSERVE_NOTICE_SCRIPT

    def report(stage: str, method: str, *, shape: str = "", reasons: tuple[str, ...] = ()) -> None:
        if report_unrecognized is not None:
            report_unrecognized(
                UnrecognizedNoticeReport(
                    stage=stage,
                    close_method=method,
                    attempts=len(closed_keys),
                    shape_summary=shape or last_shape or "no structural summary",
                    reasons=reasons,
                )
            )

    async def next_observation() -> tuple[NoticeObservation, str, str, str]:
        nonlocal last_shape, last_reasons
        strict = await _observe(execute_script, strict_script, accepted="public_search_notice")
        if strict.state in ("absent", "public_search_notice"):
            return strict, strict_script, "public_search_notice", "strict_close_button"
        if strict.shape:
            last_shape = strict.shape
            last_reasons = strict.reasons
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
                last_reasons = observed.reasons
            report("search_notice_unrecognized", method, reasons=last_reasons)
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
