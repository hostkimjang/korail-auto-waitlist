import { Bell, CaretDown, CaretUp, X } from "@phosphor-icons/react";
import { useMemo, useState, type ReactElement } from "react";

import type {
  AppNotificationNotice,
  NotificationCenterState,
  NotificationKind,
} from "./notificationCenter";
import { AppToast } from "../../shared/ui/AppToast";

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
  const [collapsed, setCollapsed] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<ReadonlySet<NotificationKind>>(
    () => new Set(),
  );
  const groups = useMemo(() => groupNotices(state.notices), [state.notices]);
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
    <aside className={collapsed ? "notification-center is-collapsed" : "notification-center"}>
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
            aria-label={collapsed ? "실시간 알림 펼치기" : "실시간 알림 접기"}
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <CaretDown size={20} /> : <CaretUp size={20} />}
          </button>
        </header>
        <div className="notification-center-body" hidden={collapsed}>
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
          <footer className="notification-center-footer" hidden={collapsed}>
            <button type="button" onClick={onDismissTimed}>정보 알림 {timedCount}건 모두 닫기</button>
          </footer>
        )}
      </section>
    </aside>
  );
}
