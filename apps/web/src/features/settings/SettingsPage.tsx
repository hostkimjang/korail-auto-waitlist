import { useState } from "react";
import {
  ArrowRight,
  Bell,
  Clock,
  GlobeSimple,
  LockKey,
  ShieldCheck,
  User,
  WifiHigh,
} from "@phosphor-icons/react";

import type {
  BrowserPushState,
  NotificationChannel,
  NotificationChannelEditorSubmission,
} from "../../api/notifications";
import type {
  ProviderAccount,
  ProviderAccountCredentialInput,
  RailProvider,
} from "../../api/providerAccounts";
import type { ProviderRuntimeStatus } from "../../api/providerRuntime";
import type {
  UiPreferences,
  UpdateUiPreferencesInput,
} from "../../api/uiPreferences";
import { PageHeader } from "../../shared/ui/PageHeader";
import { StatusPill } from "../../shared/ui/StatusPill";
import { NotificationChannelSettings } from "./NotificationChannelSettings";
import { ProviderAccountSettings } from "./ProviderAccountSettings";
import { SystemStatusDashboard } from "./SystemStatusDashboard";
import { TimetableRefreshSettings } from "./TimetableRefreshSettings";

export type SettingsSection =
  | "rail-accounts"
  | "notifications"
  | "display"
  | "security"
  | "system";

export interface SettingsPageProps {
  channels: readonly NotificationChannel[];
  demo: boolean;
  browserPushState?: BrowserPushState;
  providerAccounts?: ReadonlyArray<ProviderAccount>;
  providerRuntimeStatuses?: ReadonlyArray<ProviderRuntimeStatus>;
  providerAccountsLoading?: boolean;
  pendingProviderAccount?: RailProvider | null;
  uiPreferences: UiPreferences;
  savingUiPreferences: boolean;
  onSaveUiPreferences: (input: UpdateUiPreferencesInput) => Promise<UiPreferences>;
  onSaveChannel: (submission: NotificationChannelEditorSubmission) => Promise<void>;
  onToggleChannel: (channel: NotificationChannel, nextEnabled: boolean) => Promise<void>;
  onTestChannel: (channel: NotificationChannel) => Promise<void>;
  onConnectWebPush: () => Promise<void>;
  onSaveProviderAccount?: (
    provider: RailProvider,
    input: ProviderAccountCredentialInput,
  ) => Promise<void>;
  onDeleteProviderAccount?: (provider: RailProvider) => Promise<void>;
  onSectionChange?: (section: SettingsSection) => void;
  onLogout: () => void | Promise<void>;
  initialSection?: SettingsSection;
}

const sections = [
  { id: "rail-accounts", label: "철도 계정", icon: User },
  { id: "notifications", label: "알림 채널", icon: Bell },
  { id: "display", label: "화면 동작", icon: Clock },
  { id: "security", label: "보안", icon: LockKey },
  { id: "system", label: "로그·진행 상태", icon: WifiHigh },
] as const;

const defaultBrowserPushState: BrowserPushState = {
  support: "checking",
  permission: "default",
  subscribed: false,
  deviceKey: null,
};

const saveNoProviderAccount = async (
  _provider: RailProvider,
  _input: ProviderAccountCredentialInput,
): Promise<void> => undefined;

const deleteNoProviderAccount = async (_provider: RailProvider): Promise<void> => undefined;

export function SettingsPage({
  channels,
  demo,
  browserPushState = defaultBrowserPushState,
  providerAccounts = [],
  providerRuntimeStatuses = [],
  providerAccountsLoading = false,
  pendingProviderAccount = null,
  uiPreferences,
  savingUiPreferences,
  onSaveUiPreferences,
  onSaveChannel,
  onToggleChannel,
  onTestChannel,
  onConnectWebPush,
  onSaveProviderAccount = saveNoProviderAccount,
  onDeleteProviderAccount = deleteNoProviderAccount,
  onSectionChange = () => undefined,
  onLogout,
  initialSection = "notifications",
}: SettingsPageProps) {
  const [section, setSection] = useState<SettingsSection>(initialSection);

  const selectSection = (nextSection: SettingsSection): void => {
    setSection(nextSection);
    onSectionChange(nextSection);
  };

  return (
    <div className="page settings-page">
      <PageHeader title="설정" helper="개인 서비스 연결과 운영 상태를 관리합니다." />
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="설정 메뉴">
          {sections.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={section === id ? "is-active" : ""}
              onClick={() => selectSection(id)}
            >
              <Icon size={21} />
              {label}
              <ArrowRight size={17} />
            </button>
          ))}
        </nav>
        <section className="settings-panel">
          {section === "rail-accounts" && (
            <>
              <div className="panel-heading">
                <h2>철도 계정</h2>
                <p>새 좌석 가용성 에피소드마다 결제 직전까지 자동 예매할 계정을 연결합니다. 자동 결제는 하지 않습니다.</p>
              </div>
              <ProviderAccountSettings
                accounts={providerAccounts}
                runtimeStatuses={providerRuntimeStatuses}
                loading={providerAccountsLoading}
                pendingProvider={pendingProviderAccount}
                onSave={onSaveProviderAccount}
                onDelete={onDeleteProviderAccount}
              />
            </>
          )}
          {section === "notifications" && (
            <NotificationChannelSettings
              channels={channels}
              browserPushState={browserPushState}
              onSaveChannel={onSaveChannel}
              onToggleChannel={onToggleChannel}
              onTestChannel={onTestChannel}
              onConnectWebPush={onConnectWebPush}
            />
          )}
          {section === "display" && (
            <>
              <div className="panel-heading">
                <h2>화면 동작</h2>
                <p>실시간 화면 동기화 상태를 확인하고 좌석 관측 목표를 관리합니다.</p>
              </div>
              <TimetableRefreshSettings
                preferences={uiPreferences}
                saving={savingUiPreferences}
                onSave={onSaveUiPreferences}
              />
            </>
          )}
          {section === "security" && (
            <>
              <div className="panel-heading">
                <h2>보안</h2>
                <p>관리자 한 명만 사용하며 공개 가입 기능은 없습니다.</p>
              </div>
              <div className="security-card">
                <ShieldCheck size={34} weight="fill" />
                <div>
                  <strong>관리자 ID·비밀번호 로그인 활성화</strong>
                  <span>비밀번호는 Argon2id 단방향 해시로 저장됩니다.</span>
                </div>
                <StatusPill status="watching">보호됨</StatusPill>
              </div>
              <div className="security-card">
                <GlobeSimple size={34} />
                <div>
                  <strong>접속 경로</strong>
                  <span>Tailscale 우선 · 공개 도메인 선택 지원</span>
                </div>
              </div>
              <button
                type="button"
                className="button button-outline logout-button"
                onClick={onLogout}
              >
                이 기기에서 로그아웃
              </button>
            </>
          )}
          {section === "system" && <SystemStatusDashboard demo={demo} />}
        </section>
      </div>
    </div>
  );
}
