import { useId, useState } from "react";

import { RAW_SETTINGS_MAX_BYTES, type AvailableSystemSettings } from "../../api/systemSettings";
import { rawChangesWebSettings, rawSettingsProblem } from "./serverSettingsForm";
import type { ServerSettingsRequest } from "./useServerSettings";

export interface ServerSettingsRawEditorProps {
  snapshot: AvailableSystemSettings;
  busy: boolean;
  open: boolean;
  onToggle: (open: boolean) => void;
  onSubmit: (request: ServerSettingsRequest) => Promise<void>;
}

function KeyList({ label, keys }: { label: string; keys: readonly string[] }) {
  if (keys.length === 0) return null;
  return (
    <div className="server-settings-key-group">
      <span>{label}</span>
      <ul className="server-settings-key-list" aria-label={label}>
        {keys.map((key) => <li key={key}><code>{key}</code></li>)}
      </ul>
    </div>
  );
}

export function ServerSettingsRawEditor({ snapshot, busy, open, onToggle, onSubmit }: ServerSettingsRawEditorProps) {
  const id = useId();
  const [raw, setRaw] = useState(snapshot.raw);
  const [consented, setConsented] = useState(false);
  const changed = raw !== snapshot.raw;
  const problem = rawSettingsProblem(raw);
  const canApply = !busy && changed && problem === null && consented;
  const warningId = `${id}-warning`;
  const legendId = `${id}-legend`;
  const errorId = `${id}-error`;
  const textareaId = `${id}-raw`;

  const apply = (): void => {
    if (!canApply) return;
    void onSubmit({ mode: "raw", raw, reloadRequired: rawChangesWebSettings(snapshot.raw, raw) });
  };

  return (
    <details
      className="server-settings-advanced"
      open={open}
      onToggle={(event) => onToggle(event.currentTarget.open)}
    >
      <summary>고급: .env 직접 편집</summary>
      <div className="server-settings-advanced-body">
        <p id={warningId} className="server-settings-warning">
          설치 폴더의 .env 원문을 직접 고칩니다. 잘못 고치면 서비스가 시작되지 않을 수 있습니다. 관리 서비스가 적용
          전에 검사하고, 적용에 실패하면 이전 설정으로 되돌립니다.
        </p>
        <div id={legendId} className="server-settings-legend">
          <p>
            ••••••로 표시된 값은 숨긴 비밀값입니다. 숨긴 값과 아래 잠긴 항목은 CLI로만 바꿀 수 있으니 이 화면에서는
            그대로 두세요.
          </p>
          <KeyList label="숨긴 값" keys={snapshot.secretKeys} />
          <KeyList label="CLI로만 바꾸는 항목" keys={snapshot.lockedKeys} />
        </div>
        <label className="server-settings-raw-label" htmlFor={textareaId}>.env 원문</label>
        <textarea
          id={textareaId}
          className="server-settings-raw"
          value={raw}
          rows={16}
          wrap="off"
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          readOnly={busy}
          aria-invalid={problem === null ? undefined : true}
          aria-describedby={problem === null ? `${warningId} ${legendId}` : `${warningId} ${legendId} ${errorId}`}
          onChange={(event) => setRaw(event.target.value)}
        />
        {problem !== null && (
          <p id={errorId} className="server-settings-error">
            {problem === "nul"
              ? "원문에 쓸 수 없는 문자(NUL)가 들어 있습니다."
              : `원문이 ${RAW_SETTINGS_MAX_BYTES / 1024}KiB를 넘어 적용할 수 없습니다.`}
          </p>
        )}
        <label className="server-settings-consent">
          <input
            type="checkbox"
            checked={consented}
            disabled={busy}
            onChange={(event) => setConsented(event.target.checked)}
          />
          <span>
            바뀐 항목에 따라 웹만 또는 앱 서비스 전체를 다시 시작합니다. 앱 서비스를 다시 시작하면 진행 중인 작업이
            끝난 뒤 백업하고 1~3분 동안 접속이 잠시 끊기는 데 동의합니다.
          </span>
        </label>
        <div className="server-settings-actions">
          <button
            type="button"
            className="button button-outline"
            disabled={busy || !changed}
            onClick={() => {
              setRaw(snapshot.raw);
              setConsented(false);
            }}
          >
            원래대로
          </button>
          <button type="button" className="button button-primary" disabled={!canApply} onClick={apply}>
            {busy ? "적용 중…" : "원문 적용"}
          </button>
        </div>
      </div>
    </details>
  );
}
