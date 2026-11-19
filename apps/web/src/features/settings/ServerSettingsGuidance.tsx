import { useId, useState } from "react";

import type { SystemSettingsReason } from "../../api/systemSettings";
import { MANAGER_UNAVAILABLE_MESSAGE, MANAGER_UPGRADE_MESSAGE } from "./useServerSettings";

export const INSTALLER_COMMANDS = [
  {
    id: "unix",
    label: "macOS·Linux",
    command: "curl -fsSL https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.sh | sh",
  },
  {
    id: "windows",
    label: "Windows PowerShell",
    command: "irm https://github.com/hostkimjang/korail-auto-waitlist/releases/latest/download/install.ps1 | iex",
  },
] as const;

type CopyState = "idle" | "copied" | "failed";

export interface CopyableCommandProps {
  label: string;
  command: string;
  /** Accessible name of the copy button; defaults to "<label> 명령 복사". */
  copyLabel?: string;
}

export function CopyableCommand({ label, command, copyLabel }: CopyableCommandProps) {
  const labelId = useId();
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const copy = async (): Promise<void> => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(command);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };
  return (
    <div className="server-settings-command">
      <span id={labelId} className="server-settings-command-label">{label}</span>
      <code aria-labelledby={labelId}>{command}</code>
      <button
        type="button"
        className="button button-outline compact"
        aria-label={copyLabel ?? `${label} 명령 복사`}
        onClick={() => void copy()}
      >
        복사
      </button>
      <span role="status" className="server-settings-command-status">
        {copyState === "copied" && "복사했습니다."}
        {copyState === "failed" && "복사하지 못했습니다. 명령을 직접 선택해 복사하세요."}
      </span>
    </div>
  );
}

export function ManagerUpgradeGuidance() {
  return (
    <div className="server-settings-guidance">
      <p>
        설치한 컴퓨터에서 처음 설치할 때 쓴 한 줄 설치 명령을 다시 실행하세요. 데이터와 설정은 그대로 두고
        관리 서비스만 바꿉니다.
      </p>
      {INSTALLER_COMMANDS.map(({ id, label, command }) => (
        <CopyableCommand key={id} label={label} command={command} />
      ))}
      <p className="server-settings-help">
        기본 위치가 아닌 폴더에 설치했다면 같은 폴더를 지정하세요. macOS·Linux는 명령 끝을{" "}
        <code>| sh -s -- --path &lt;설치 폴더&gt;</code>로 바꾸고, Windows는 먼저{" "}
        <code>$env:RAILWAIT_INSTALL_ARGS = '--path &lt;설치 폴더&gt;'</code>를 실행합니다. <code>railwait</code> 명령을
        쓸 수 있다면 <code>railwait manager upgrade --path &lt;설치 폴더&gt;</code>로도 교체할 수 있습니다.
      </p>
    </div>
  );
}

export interface ServerSettingsUnavailableProps {
  reason: Exclude<SystemSettingsReason, "ready">;
}

// Run from the folder the repository was cloned into; the API cannot see that host path.
export const GIT_INSTALL_ENV_COMMANDS = [
  { id: "mac-reveal", label: "macOS: Finder에서 .env 보기", command: "open -R .env" },
  { id: "linux-edit", label: "Linux: 터미널에서 .env 고치기", command: "nano .env" },
  { id: "windows-edit", label: "Windows: 메모장으로 .env 열기", command: "notepad .env" },
  { id: "unix-apply", label: "macOS·Linux: 바꾼 설정 반영", command: "bash ./scripts/ops.sh experimental" },
  { id: "windows-apply", label: "Windows: 바꾼 설정 반영", command: "./scripts/ops.ps1 experimental" },
] as const;

export function GitInstallSettingsGuidance() {
  return (
    <div className="server-settings-guidance">
      <p>
        이 서버는 설치 CLI 없이 Git 저장소와 Docker Compose로 직접 설치한 서버여서 화면에서 설정을 바꿀 수 없습니다.
        설정은 저장소를 받은 폴더의 <code>.env</code>에 있습니다. 아래 명령은 그 폴더에서 실행합니다.
      </p>
      {GIT_INSTALL_ENV_COMMANDS.map(({ id, label, command }) => (
        <CopyableCommand key={id} label={label} command={command} />
      ))}
      <p className="server-settings-help">
        macOS Finder는 <code>.env</code> 같은 숨김 파일을 기본으로 보여 주지 않습니다. 보이지 않으면 Finder에서{" "}
        <kbd>⌘</kbd> + <kbd>Shift</kbd> + <kbd>.</kbd>을 누르세요. 고친 뒤에는 반영 명령으로 서비스를 다시 만들어야
        바뀐 값이 적용됩니다. 비밀번호·키 같은 기존 비밀값은 바꾸지 마세요.
      </p>
    </div>
  );
}

export function ServerSettingsUnavailable({ reason }: ServerSettingsUnavailableProps) {
  switch (reason) {
    case "managed_install_required":
      return <GitInstallSettingsGuidance />;
    case "manager_unavailable":
      return (
        <div className="server-settings-guidance">
          <p>{MANAGER_UNAVAILABLE_MESSAGE}</p>
          <p className="server-settings-help">
            Docker가 실행 중인데도 이 안내가 계속되면 설치한 컴퓨터에서 처음 설치할 때 쓴 한 줄 설치 명령을 다시
            실행하세요. 데이터와 설정은 그대로 두고 관리 서비스를 다시 등록해 시작합니다.
          </p>
        </div>
      );
    case "manager_upgrade_required":
      return (
        <>
          <p className="server-settings-notice">{MANAGER_UPGRADE_MESSAGE}</p>
          <ManagerUpgradeGuidance />
        </>
      );
  }
}
