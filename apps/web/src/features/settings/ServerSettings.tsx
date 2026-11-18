import { Fragment, useState } from "react";

import { ManagerUpgradeGuidance, ServerSettingsUnavailable } from "./ServerSettingsGuidance";
import { APP_SCOPE_TEXT, ServerSettingsFieldsForm } from "./ServerSettingsFieldsForm";
import { ServerSettingsRawEditor } from "./ServerSettingsRawEditor";
import { settingsJobOutcome } from "./serverSettingsJob";
import { useServerSettings } from "./useServerSettings";
import "./serverSettings.css";

export interface ServerSettingsProps {
  demo: boolean;
}

function ServerSettingsContent() {
  const {
    snapshot,
    loading,
    loadError,
    tracked,
    jobRunning,
    jobError,
    busy,
    submitError,
    reload,
    submit,
  } = useServerSettings(false);
  // Lives above the keyed editor so a reloaded snapshot does not collapse an open raw editor.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const outcome = tracked === null ? null : settingsJobOutcome(tracked.job, tracked.context);
  // While a job restarts services, a failed snapshot read is expected and must not hide progress.
  const visibleLoadError = jobRunning ? null : loadError;

  return (
    <>
      {snapshot === null && loading && <p role="status">서버 설정을 불러오는 중…</p>}
      {snapshot !== null && !snapshot.available && <ServerSettingsUnavailable reason={snapshot.reason} />}
      {snapshot !== null && snapshot.available && (
        <Fragment key={snapshot.revision}>
          <ServerSettingsFieldsForm snapshot={snapshot} busy={busy} onSubmit={submit} />
          <ServerSettingsRawEditor
            snapshot={snapshot}
            busy={busy}
            open={advancedOpen}
            onToggle={setAdvancedOpen}
            onSubmit={submit}
          />
        </Fragment>
      )}
      {visibleLoadError !== null && !loading && (
        <div className="server-settings-reload">
          <p role="alert">{visibleLoadError}</p>
          <button type="button" className="button button-outline compact" onClick={reload}>
            다시 불러오기
          </button>
        </div>
      )}
      {snapshot !== null && !snapshot.available && visibleLoadError === null && (
        <div className="server-settings-reload">
          <button type="button" className="button button-outline compact" disabled={loading} onClick={reload}>
            다시 확인
          </button>
        </div>
      )}
      <div className="server-settings-status" role="status">
        {outcome !== null && (
          <p className={`server-settings-outcome is-${outcome.tone}`}>
            {outcome.message}
            {outcome.command !== null && <> <code>{outcome.command}</code></>}
          </p>
        )}
        {jobRunning && tracked?.context.scope === "app" && (
          <p>{APP_SCOPE_TEXT} 이 화면을 닫아도 적용은 계속됩니다.</p>
        )}
        {jobError !== null && <p>{jobError}</p>}
      </div>
      {submitError !== null && (
        <div className="server-settings-submit-error">
          <p role="alert" className="server-settings-outcome is-error">{submitError.message}</p>
          {submitError.guidance === "manager_upgrade" && <ManagerUpgradeGuidance />}
        </div>
      )}
    </>
  );
}

export function ServerSettings({ demo }: ServerSettingsProps) {
  return (
    <section className="server-settings" aria-labelledby="server-settings-title">
      <div className="panel-heading">
        <h2 id="server-settings-title">서버 설정</h2>
        <p>설치한 서버의 .env 설정을 바꾸고 필요한 서비스만 다시 시작합니다.</p>
      </div>
      {demo ? <p>체험 화면에서는 서버 설정을 바꾸지 않습니다.</p> : <ServerSettingsContent />}
    </section>
  );
}
