import {
  Bell,
  GearSix,
  House,
  Plus,
  Ticket,
  type Icon,
} from "@phosphor-icons/react";
import type { ReactElement, ReactNode } from "react";

import { Brand } from "../shared/ui/Brand";
import type { AppView } from "./useAppNavigation";

interface AppNavItem {
  id: AppView;
  label: string;
  icon: Icon;
}

const navItems: ReadonlyArray<AppNavItem> = [
  { id: "home", label: "홈", icon: House },
  { id: "new", label: "새 대기", icon: Plus },
  { id: "reservations", label: "내 예약", icon: Ticket },
  { id: "settings", label: "설정", icon: GearSix },
];

export interface AppShellProps {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  notificationCount: number;
  notificationsExpanded: boolean;
  onToggleNotifications: () => void;
  children: ReactNode;
  overlay?: ReactNode;
}

interface AppNavigationProps {
  activeView: AppView;
  onNavigate: (view: AppView) => void;
}

function Sidebar({ activeView, onNavigate }: AppNavigationProps) {
  return (
    <aside className="sidebar">
      <Brand />
      <nav aria-label="주 메뉴" className="side-nav">
        {navItems.map(({ id, label, icon: IconComponent }) => (
          <button
            key={id}
            type="button"
            className={activeView === id ? "nav-item is-active" : "nav-item"}
            onClick={() => onNavigate(id)}
          >
            <IconComponent size={24} weight={activeView === id ? "fill" : "regular"} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}

function BottomNav({ activeView, onNavigate }: AppNavigationProps) {
  return (
    <nav className="bottom-nav" aria-label="모바일 주 메뉴">
      {navItems.map(({ id, label, icon: IconComponent }) => (
        <button
          key={id}
          type="button"
          className={activeView === id ? "bottom-item is-active" : "bottom-item"}
          onClick={() => onNavigate(id)}
        >
          <IconComponent size={24} weight={activeView === id ? "fill" : "regular"} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

export function AppShell({
  activeView,
  onNavigate,
  notificationCount,
  notificationsExpanded,
  onToggleNotifications,
  children,
  overlay,
}: AppShellProps): ReactElement {
  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onNavigate={onNavigate} />
      <main className="main-content">
        <div className="mobile-header">
          <Brand />
          <button
            type="button"
            className="icon-button mobile-notification-button"
            aria-label={`실시간 알림 ${notificationCount}건 ${notificationsExpanded ? "닫기" : "열기"}`}
            aria-controls="notification-center-body"
            aria-expanded={notificationsExpanded}
            onClick={onToggleNotifications}
          >
            <Bell size={23} />
            {notificationCount > 0 ? (
              <span className="mobile-notification-badge" aria-hidden="true">
                {notificationCount > 99 ? "99+" : notificationCount}
              </span>
            ) : null}
          </button>
        </div>
        {children}
      </main>
      <BottomNav activeView={activeView} onNavigate={onNavigate} />
      {overlay}
    </div>
  );
}
