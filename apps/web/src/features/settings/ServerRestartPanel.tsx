import { useId, useState } from "react";

import { CopyableCommand, INSTALLER_COMMANDS } from "./ServerSettingsGuidance";

export const RESTART_PROGRESS_TEXT =
  "앱 서비스를 다시 시작하는 동안 1~3분 접속이 끊길 수 있습니다. 이 화면을 닫아도 다시 시작은 계속됩니다.";

export interface ServerRestartPanelProps {
  restartAvailable: boolean;
  busy: boolean;
  onRestart: () => Promise<void>;
}

/** Recreates the app services through the host manager when something seems stuck. */
export function ServerRestartPanel({ restartAvailable, busy, onRestart }: ServerRestartPanelProps) {
  const id = useId();
  const titleId = `${id}-title`;
  const helpId = `${id}-help`;
  const warningId = `${id}-warning`;
  const [consented, setConsented] = useState(false);

  return (
    <section className="server-settings-group server-restart" aria-labelledby={titleId}>
      <h3 id={titleId}>서비스 다시 시작</h3>
      <p id={helpId} className="server-settings-help">
        화면이 응답하지 않거나 좌석 조회·알림이 멈춘 것처럼 보일 때, 또는 설치 폴더의 <code>.env</code>를 직접 고친 뒤
        반영할 때 앱 서비스를 다시 시작합니다. PostgreSQL·Redis와 저장한 데이터는 그대로 두고, 진행 중인 작업이 끝나기를
        기다린 뒤 다시 시작하며 1~3분 동안 접속이 잠시 끊깁니다.
      </p>
      <p id={warningId} className="server-settings-warning">
        다시 시작하면 KORAIL 로그인 세션이 끊겨 다시 로그인합니다. KORAIL이 자동 로그인을 막으면 다시 로그인될 때까지
        (30분 이상) KORAIL 좌석 관측이 멈출 수 있으니 필요할 때만 사용하세요.
      </p>
      {restartAvailable ? (
        <>
          <label className="server-settings-consent">
            <input
              type="checkbox"
              checked={consented}
              disabled={busy}
              aria-describedby={`${helpId} ${warningId}`}
              onChange={(event) => setConsented(event.target.checked)}
            />
            <span>지금 앱 서비스를 다시 시작하고, 진행 중인 작업이 끝난 뒤 1~3분 동안 접속이 끊기는 것에 동의합니다.</span>
          </label>
          <div className="server-settings-actions">
            <button
              type="button"
              className="button button-outline"
              disabled={busy || !consented}
              onClick={() => {
                setConsented(false);
                void onRestart();
              }}
            >
              {busy ? "진행 중…" : "앱 서비스 다시 시작"}
            </button>
          </div>
        </>
      ) : (
        <details className="server-settings-details">
          <summary>지금 관리 서비스는 화면에서 다시 시작하기를 지원하지 않습니다</summary>
          <div className="server-settings-guidance">
            <p>
              설치한 컴퓨터에서 처음 설치할 때 쓴 한 줄 설치 명령을 다시 실행해 관리 서비스를 1.5.0 이상으로 바꾸면 이
              화면에서 다시 시작할 수 있습니다. 데이터와 설정은 그대로입니다.
            </p>
            {INSTALLER_COMMANDS.map(({ id: commandId, label, command }) => (
              <CopyableCommand key={commandId} label={`관리 서비스 교체 · ${label}`} command={command} />
            ))}
            <p className="server-settings-help">
              관리 서비스를 바꾸지 않고 지금 다시 시작하려면 설치한 컴퓨터에서 1.5.0 이상 CLI로{" "}
              <code>railwait restart --path &lt;설치 폴더&gt;</code>를 실행하세요.
            </p>
          </div>
        </details>
      )}
    </section>
  );
}
