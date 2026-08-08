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
  children,
  overlay,
}: AppShellProps): ReactElement {
  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onNavigate={onNavigate} />
      <main className="main-content">
        <div className="mobile-header">
          <Brand />
          <button type="button" className="icon-button" aria-label="알림">
            <Bell size={23} />
          </button>
        </div>
        {children}
      </main>
      <BottomNav activeView={activeView} onNavigate={onNavigate} />
      {overlay}
    </div>
  );
}
