import { useId } from "react";

import type { InstallLocation } from "../../api/systemSettings";
import { CopyableCommand } from "./ServerSettingsGuidance";
import { installLocationCommands } from "./settingsCommands";

export interface ServerSettingsLocationProps {
  location: InstallLocation;
}

/** Shows where the install .env lives for edits this screen does not cover. */
export function ServerSettingsLocation({ location }: ServerSettingsLocationProps) {
  const id = useId();
  const titleId = `${id}-title`;
  return (
    <section className="server-settings-group server-settings-location" aria-labelledby={titleId}>
      <h3 id={titleId}>설정 파일 위치</h3>
      <p className="server-settings-help">
        이 화면에 없는 값은 설치한 컴퓨터의 <code>.env</code>에서 직접 고칠 수 있습니다. 고친 뒤에는 아래{" "}
        <strong>서비스 다시 시작</strong>으로 반영합니다. 브라우저는 설치한 컴퓨터의 폴더를 직접 열 수 없으므로,
        위치를 복사하거나 아래 명령을 설치한 컴퓨터의 터미널에서 실행하세요.
      </p>
      <CopyableCommand label=".env 위치" command={location.envPath} copyLabel=".env 위치 복사" />
      {installLocationCommands(location).map(({ id: commandId, label, command }) => (
        <CopyableCommand key={commandId} label={label} command={command} />
      ))}
      {location.hostOs === "darwin" && (
        <p className="server-settings-help">
          Finder는 <code>.env</code> 같은 숨김 파일을 기본으로 보여 주지 않습니다. 보이지 않으면{" "}
          <kbd>⌘</kbd> + <kbd>Shift</kbd> + <kbd>.</kbd>을 누르세요.
        </p>
      )}
      <p className="server-settings-warning">
        가린 비밀값(••••••)과 잠긴 항목을 잘못 바꾸면 서비스가 시작되지 않을 수 있습니다. 직접 고치기 전에 지금
        파일을 복사해 두세요.
      </p>
    </section>
  );
}
