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
}

export function CopyableCommand({ label, command }: CopyableCommandProps) {
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
        aria-label={`${label} 명령 복사`}
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

export function ServerSettingsUnavailable({ reason }: ServerSettingsUnavailableProps) {
  switch (reason) {
    case "managed_install_required":
      return (
        <div className="server-settings-guidance">
          <p>
            이 서버는 설치 CLI로 관리하는 설치가 아니어서 화면에서 설정을 바꿀 수 없습니다. Git 저장소로 설치했다면
            저장소 폴더의 <code>.env</code>를 직접 고친 뒤 서비스를 다시 시작하세요.
          </p>
        </div>
      );
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
