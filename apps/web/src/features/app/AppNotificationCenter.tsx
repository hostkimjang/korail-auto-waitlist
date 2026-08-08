import { Bell, CaretDown, CaretUp, X } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";

import {
  compareNotifications,
  type AppNotificationNotice,
  type NotificationCenterState,
  type NotificationKind,
} from "./notificationCenter";
import { AppToast } from "../../shared/ui/AppToast";

/*
 * This surface projects the canonical notification reducer state. It must not subscribe to
 * Push or SSE independently, otherwise the same revision can be presented twice.
 */
export const FOREGROUND_NOTIFICATION_PEEK_MS = 8_000;

interface AppNotificationCenterProps {
  state: NotificationCenterState;
  onDismiss: (id: string) => void;
  onDismissGroup: (kind: NotificationKind) => void;
  onDismissTimed: () => void;
}

const GROUP_LABELS: Record<NotificationKind, string> = {
  payment_required: "결제 필요",
  manual_check: "결과 확인 필요",
  auth_required: "로그인 확인 필요",
  seat_found: "좌석 발견",
  reserving: "예매 진행",
  recovery: "감시 상태 변경",
  generic: "일반 알림",
};

function groupNotices(notices: ReadonlyArray<AppNotificationNotice>) {
  const groups = new Map<NotificationKind, AppNotificationNotice[]>();
  for (const notice of notices) {
    const group = groups.get(notice.kind) ?? [];
    group.push(notice);
    groups.set(notice.kind, group);
  }
  return [...groups.entries()];
}

export function AppNotificationCenter({
  state,
  onDismiss,
  onDismissGroup,
  onDismissTimed,
}: AppNotificationCenterProps): ReactElement | null {
  const [expanded, setExpanded] = useState(false);
  const [peekNoticeId, setPeekNoticeId] = useState<string | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<ReadonlySet<NotificationKind>>(
    () => new Set(),
  );
  const lastPresentedSequenceRef = useRef(0);
  const wasEmptyRef = useRef(state.notices.length === 0);
  const groups = useMemo(() => groupNotices(state.notices), [state.notices]);
  const peekNotice = state.notices.find((notice) => notice.id === peekNoticeId) ?? null;

  useEffect(() => {
    if (state.notices.length === 0) {
      wasEmptyRef.current = true;
      return undefined;
    }
    const returnedAfterEmpty = wasEmptyRef.current;
    wasEmptyRef.current = false;
    const previousSequence = lastPresentedSequenceRef.current;
    if (state.sequence <= previousSequence) return undefined;
    lastPresentedSequenceRef.current = state.sequence;
    const nextNotice = state.notices
      .filter((notice) => notice.sequence > previousSequence)
      .sort(compareNotifications)[0];
    if (
      nextNotice === undefined
      || (expanded && !returnedAfterEmpty)
      || (typeof document !== "undefined" && document.visibilityState === "hidden")
    ) {
      return undefined;
    }
    const showTimer = window.setTimeout(() => {
      if (returnedAfterEmpty) setExpanded(false);
      setPeekNoticeId(nextNotice.id);
    }, 0);
    const hideTimer = window.setTimeout(() => {
      setPeekNoticeId((current) => current === nextNotice.id ? null : current);
    }, FOREGROUND_NOTIFICATION_PEEK_MS);
    return () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
    };
  }, [expanded, state.notices, state.sequence]);

  if (state.notices.length === 0) return null;

  const timedCount = state.notices.filter((notice) => notice.persistence === "timed").length;
  const toggleGroup = (kind: NotificationKind) => {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  return (
    <aside
      className={`notification-center ${expanded ? "is-expanded" : peekNotice ? "is-peeking" : "is-collapsed"}`}
    >
      <div
        key={`${state.announcementMode}-${state.sequence}`}
        className="notification-announcer sr-only"
        role={state.announcementMode === "assertive" ? "alert" : "status"}
        aria-atomic="true"
      >
        {state.announcement}
      </div>
      <section className="notification-center-surface" role="region" aria-label="실시간 알림">
        <header className="notification-center-header">
          <Bell size={20} weight="fill" aria-hidden="true" />
          <strong>실시간 알림</strong>
          <span>{state.notices.length}건</span>
          <button
            type="button"
            className="notification-center-toggle"
            aria-label={expanded ? "실시간 알림 접기" : "실시간 알림 펼치기"}
            aria-expanded={expanded}
            aria-controls="notification-center-body"
            onClick={() => {
              setExpanded((value) => !value);
              setPeekNoticeId(null);
            }}
          >
            {expanded ? <CaretUp size={20} /> : <CaretDown size={20} />}
          </button>
        </header>
        {!expanded && peekNotice && (
          <div className={`notification-center-peek toast-${peekNotice.tone ?? "info"}`}>
            <div className="notification-center-peek-content">
              <span>{GROUP_LABELS[peekNotice.kind]}</span>
              <strong>{peekNotice.title}</strong>
              {peekNotice.meta && <small>{peekNotice.meta}</small>}
            </div>
            <button
              type="button"
              className="notification-center-peek-detail"
              onClick={() => {
                setExpanded(true);
                setPeekNoticeId(null);
              }}
            >
              자세히
            </button>
            <button
              type="button"
              className="notification-center-peek-dismiss"
              aria-label={`${peekNotice.title} 알림 미리보기 숨기기`}
              onClick={() => setPeekNoticeId(null)}
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        )}
        <div id="notification-center-body" className="notification-center-body" hidden={!expanded}>
            {groups.map(([kind, notices]) => {
              const expanded = expandedGroups.has(kind);
              return (
                <section className="notification-group" key={kind} aria-labelledby={`notification-group-${kind}`}>
                  <div className="notification-group-heading">
                    <strong id={`notification-group-${kind}`}>{GROUP_LABELS[kind]}</strong>
                    <span>{notices.length}건</span>
                    {notices.length > 1 && (
                      <button type="button" onClick={() => toggleGroup(kind)}>
                        {expanded ? "접기" : `추가 ${notices.length - 1}건 보기`}
                      </button>
                    )}
                    <button
                      type="button"
                      className="notification-group-dismiss"
                      aria-label={`${GROUP_LABELS[kind]} ${notices.length}건 모두 닫기`}
                      onClick={() => onDismissGroup(kind)}
                    >
                      <X size={18} aria-hidden="true" />
                    </button>
                  </div>
                  <div className="notification-group-items">
                    {notices.map((notice, index) => (
                      <div key={notice.id} hidden={!expanded && index > 0}>
                        <AppToast
                          notice={notice}
                          embedded
                          onClose={() => onDismiss(notice.id)}
                        />
                      </div>
                    ))}
                  </div>
                </section>
              );
            })}
        </div>
        {timedCount > 1 && (
          <footer className="notification-center-footer" hidden={!expanded}>
            <button type="button" onClick={onDismissTimed}>정보 알림 {timedCount}건 모두 닫기</button>
          </footer>
        )}
      </section>
    </aside>
  );
}
