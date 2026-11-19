import { useId, useState } from "react";

import type { AvailableSystemSettings } from "../../api/systemSettings";
import {
  ANDROID_APP_LINK_EVIDENCE,
  appLinkEvidenceEntries,
  matchesAppLinkEvidence,
  type AppLinkEvidence,
} from "./androidAppLinkEvidence";
import {
  APP_LINK_ROWS,
  appLinkMissingVersion,
  changedFields,
  initialServerSettingsDraft,
  isSecretConfigured,
  restartScope,
  serverSettingsChanges,
  serverSettingsErrors,
  type AppLinkRow,
  type PlainSettingsKey,
  type SecretDraft,
  type ServerSettingsDraft,
} from "./serverSettingsForm";
import type { ServerSettingsRequest } from "./useServerSettings";

export const WEB_SCOPE_TEXT = "웹 화면만 다시 시작합니다(몇 초). 진행 중인 대기에는 영향이 없습니다.";
export const APP_SCOPE_TEXT =
  "앱 서비스를 다시 시작합니다. 진행 중인 작업이 끝나기를 기다린 뒤 암호화 백업을 하고 다시 시작하며, 1~3분 동안 접속이 잠시 끊깁니다. KORAIL 로그인은 다시 하며, KORAIL이 자동 로그인을 막으면 다시 로그인될 때까지 KORAIL 좌석 관측이 멈출 수 있습니다.";

function describedBy(...ids: ReadonlyArray<string | null | false>): string | undefined {
  const value = ids.filter((id): id is string => typeof id === "string" && id !== "").join(" ");
  return value === "" ? undefined : value;
}

interface SettingSwitchProps {
  id: string;
  label: string;
  checked: boolean;
  descriptionId?: string | undefined;
  onChange: (checked: boolean) => void;
}

function SettingSwitch({ id, label, checked, descriptionId, onChange }: SettingSwitchProps) {
  return (
    <label className="server-settings-switch" htmlFor={id}>
      <span className="server-settings-switch-label">{label}</span>
      <span className="switch">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          aria-describedby={descriptionId}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span aria-hidden="true" />
      </span>
    </label>
  );
}

interface AppLinkControlProps {
  row: AppLinkRow;
  evidence: AppLinkEvidence;
  draft: ServerSettingsDraft;
  error: string | undefined;
  helpId: string;
  onChange: (key: PlainSettingsKey, value: string) => void;
}

function AppLinkControl({ row, evidence, draft, error, helpId, onChange }: AppLinkControlProps) {
  const id = useId();
  const titleId = `${id}-title`;
  const switchTextId = `${id}-switch`;
  const versionTextId = `${id}-version`;
  const evidenceId = `${id}-evidence`;
  const warningId = `${id}-warning`;
  const errorId = `${id}-error`;
  const missingVersion = appLinkMissingVersion(draft, row);
  return (
    <div className="server-settings-app-link" role="group" aria-labelledby={titleId}>
      <strong id={titleId}>{row.label}</strong>
      <label className="server-settings-switch" htmlFor={`${id}-enabled`}>
        <span id={switchTextId} className="server-settings-switch-label">앱 연결</span>
        <span className="switch">
          <input
            id={`${id}-enabled`}
            type="checkbox"
            role="switch"
            checked={draft.values[row.enabledKey] === "true"}
            aria-labelledby={`${titleId} ${switchTextId}`}
            aria-describedby={describedBy(helpId, evidenceId, missingVersion && warningId)}
            onChange={(event) => onChange(row.enabledKey, event.target.checked ? "true" : "false")}
          />
          <span aria-hidden="true" />
        </span>
      </label>
      <label className="field server-settings-input" htmlFor={`${id}-version-input`}>
        <span id={versionTextId}>검증한 앱 버전</span>
        <input
          id={`${id}-version-input`}
          type="text"
          name={`railwait-${row.id}-validated-version`}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="예: 7.0.0"
          value={draft.values[row.versionKey]}
          aria-labelledby={`${titleId} ${versionTextId}`}
          aria-invalid={error === undefined ? undefined : true}
          aria-describedby={describedBy(missingVersion && warningId, error !== undefined && errorId)}
          onChange={(event) => onChange(row.versionKey, event.target.value)}
        />
      </label>
      <p id={evidenceId} className="server-settings-evidence-note">
        {evidence.recommended && evidence.version !== null
          ? `개발자 확인: ${evidence.version}에서 동작을 확인했습니다.`
          : `개발자 확인 전: ${evidence.pending ?? "실기기 확인 기록이 없습니다"}. 켜지 않는 것을 권장합니다.`}
      </p>
      {missingVersion && (
        <p id={warningId} className="server-settings-warning">
          버전을 입력하기 전까지 이 연결은 꺼진 상태로 동작합니다.
        </p>
      )}
      {error !== undefined && <p id={errorId} className="server-settings-error">{error}</p>}
    </div>
  );
}

export interface ServerSettingsFieldsFormProps {
  snapshot: AvailableSystemSettings;
  busy: boolean;
  onSubmit: (request: ServerSettingsRequest) => Promise<void>;
}

export function ServerSettingsFieldsForm({ snapshot, busy, onSubmit }: ServerSettingsFieldsFormProps) {
  const id = useId();
  const [draft, setDraft] = useState<ServerSettingsDraft>(() => initialServerSettingsDraft(snapshot.fields));
  const [consented, setConsented] = useState(false);
  const changes = serverSettingsChanges(snapshot.fields, draft);
  const errors = serverSettingsErrors(snapshot.fields, changes);
  const scope = restartScope(changes);
  const hasErrors = Object.keys(errors).length > 0;
  const secretConfigured = isSecretConfigured(snapshot.fields);
  const canApply = !busy && changes.length > 0 && !hasErrors && (scope !== "app" || consented);

  const setValue = (key: PlainSettingsKey, value: string): void => {
    setDraft((current) => ({ ...current, values: { ...current.values, [key]: value } }));
  };
  const setSecret = (secret: SecretDraft): void => {
    setDraft((current) => ({ ...current, secret }));
  };
  const reset = (): void => {
    setDraft(initialServerSettingsDraft(snapshot.fields));
    setConsented(false);
  };
  const fillAppLinkEvidence = (): void => {
    setDraft((current) => {
      const values: Record<PlainSettingsKey, string> = { ...current.values };
      for (const [key, value] of appLinkEvidenceEntries()) values[key] = value;
      return { ...current, values };
    });
  };
  const appLinksMatchEvidence = matchesAppLinkEvidence(draft.values);
  const apply = (): void => {
    if (!canApply || scope === null) return;
    void onSubmit({
      mode: "fields",
      fields: changedFields(changes),
      scope,
      reloadRequired: changes.some((change) => change.scope === "web"),
    });
  };

  const reservationHelpId = `${id}-reservation-help`;
  const secretHelpId = `${id}-secret-help`;
  const secretErrorId = `${id}-secret-error`;
  const contactHelpId = `${id}-contact-help`;
  const contactErrorId = `${id}-contact-error`;
  const appLinkHelpId = `${id}-app-link-help`;
  const evidenceStatusId = `${id}-app-link-evidence`;
  const reviewTitleId = `${id}-review-title`;
  const secretError = errors.TAGO_SERVICE_KEY;
  const contactError = errors.WEBPUSH_VAPID_SUBJECT;

  return (
    <form
      className="server-settings-form"
      aria-label="서버 설정 항목"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        apply();
      }}
    >
      <fieldset className="server-settings-group" disabled={busy}>
        <legend>자동 예매</legend>
        <p id={reservationHelpId} className="server-settings-help">
          켜면 '좌석 재발견마다 자동 예매'를 고른 대기가 좌석을 찾았을 때 저장한 철도 계정으로 결제 직전까지
          예매합니다. 결제는 하지 않습니다.
        </p>
        <SettingSwitch
          id={`${id}-korail-reservation`}
          label="KTX·코레일 자동 예매"
          checked={draft.values.KORAIL_RESERVATION_ONCE_ENABLED === "true"}
          descriptionId={reservationHelpId}
          onChange={(checked) => setValue("KORAIL_RESERVATION_ONCE_ENABLED", checked ? "true" : "false")}
        />
        <SettingSwitch
          id={`${id}-srt-reservation`}
          label="SRT 자동 예매"
          checked={draft.values.SRT_RESERVATION_ONCE_ENABLED === "true"}
          descriptionId={reservationHelpId}
          onChange={(checked) => setValue("SRT_RESERVATION_ONCE_ENABLED", checked ? "true" : "false")}
        />
      </fieldset>

      <fieldset className="server-settings-group" disabled={busy}>
        <legend>열차 시간표 인증키</legend>
        <p id={secretHelpId} className="server-settings-help">
          공공데이터포털 열차정보 인증키입니다. 저장한 키는 화면에 다시 표시하지 않습니다.
        </p>
        <p className="server-settings-secret-state">
          현재 상태 <strong>{secretConfigured ? "설정됨" : "없음"}</strong>
        </p>
        {draft.secret.action === "clear" ? (
          <div className="server-settings-staged">
            <p className="server-settings-warning">변경을 적용하면 저장한 인증키를 지웁니다.</p>
            <button
              type="button"
              className="button button-outline compact"
              onClick={() => setSecret({ action: "keep" })}
            >
              지우기 취소
            </button>
          </div>
        ) : (
          <>
            <label className="field server-settings-input" htmlFor={`${id}-secret`}>
              <span>{secretConfigured ? "새 인증키로 바꾸기" : "인증키 입력"}</span>
              <input
                id={`${id}-secret`}
                type="password"
                name="railwait-train-timetable-service-key"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={draft.secret.action === "replace" ? draft.secret.value : ""}
                aria-invalid={secretError === undefined ? undefined : true}
                aria-describedby={describedBy(secretHelpId, secretError !== undefined && secretErrorId)}
                onChange={(event) => setSecret(event.target.value === ""
                  ? { action: "keep" }
                  : { action: "replace", value: event.target.value })}
              />
            </label>
            {secretError !== undefined && (
              <p id={secretErrorId} className="server-settings-error">{secretError}</p>
            )}
            {secretConfigured && (
              <div className="server-settings-staged">
                <button
                  type="button"
                  className="button button-outline compact"
                  onClick={() => setSecret({ action: "clear" })}
                >
                  키 지우기
                </button>
              </div>
            )}
          </>
        )}
      </fieldset>

      <fieldset className="server-settings-group" disabled={busy}>
        <legend>알림 발신자 연락처</legend>
        <label className="field server-settings-input" htmlFor={`${id}-contact`}>
          <span>발신자 연락처</span>
          <input
            id={`${id}-contact`}
            type="text"
            inputMode="url"
            name="railwait-webpush-contact"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="mailto:me@example.com"
            value={draft.values.WEBPUSH_VAPID_SUBJECT}
            aria-invalid={contactError === undefined ? undefined : true}
            aria-describedby={describedBy(contactHelpId, contactError !== undefined && contactErrorId)}
            onChange={(event) => setValue("WEBPUSH_VAPID_SUBJECT", event.target.value)}
          />
        </label>
        <p id={contactHelpId} className="server-settings-help">
          브라우저 알림(웹 푸시)을 보낼 때 Google·Apple·Mozilla 같은 푸시 서비스에 함께 전하는 이 서버 운영자의
          연락처입니다. 알림을 받는 사람에게는 보이지 않고, 푸시 서비스가 문제를 알려야 할 때만 씁니다. 대부분 그대로
          두면 됩니다.
        </p>
        <details className="server-settings-details">
          <summary>어떤 값을 넣나요?</summary>
          <ul>
            <li>
              설치할 때 이 서버의 HTTPS 주소(예: <code>https://내서버.ts.net</code>)가 자동으로 들어갑니다. 원격 접속을
              켜지 않고 설치했다면 <code>https://github.com</code>이 들어갑니다.
            </li>
            <li>
              연락받을 메일 주소를 쓰려면 <code>mailto:me@example.com</code>처럼 앞에 <code>mailto:</code>를 붙입니다.
              웹 주소는 포트와 경로 없이 <code>https://example.com</code> 형식만 받습니다.
            </li>
            <li>
              비워 두면 <code>mailto:admin@localhost</code>를 씁니다. 푸시 서비스가 연락할 수 없는 주소라 권장하지
              않습니다.
            </li>
            <li>바꾸면 앱 서비스를 다시 시작합니다. 이미 알림을 켠 기기는 다시 연결하지 않아도 됩니다.</li>
          </ul>
        </details>
        {contactError !== undefined && (
          <p id={contactErrorId} className="server-settings-error">{contactError}</p>
        )}
      </fieldset>

      <fieldset className="server-settings-group" disabled={busy}>
        <legend>Android 공식 앱 연결</legend>
        <p id={appLinkHelpId} className="server-settings-help">
          켜면 Android에서 예매·승차권 버튼이 브라우저 대신 코레일+·SRT 앱을 바로 엽니다. 경로마다 실기기에서 확인한 앱
          버전과 함께 켜야 동작합니다. 직접 확인하지 않았다면 개발자가 확인한 값을 그대로 쓰세요. 이 값은 레일웨잇
          업데이트와 함께 갱신됩니다. 철도 앱이 업데이트된 뒤 엉뚱한 화면이 열리면 그 경로를 끄세요.
        </p>
        <div className="server-settings-evidence">
          <p id={evidenceStatusId}>
            {appLinksMatchEvidence
              ? "지금 값은 개발자 확인 값과 같습니다."
              : "지금 값은 개발자 확인 값과 다릅니다."}
          </p>
          <button
            type="button"
            className="button button-outline compact"
            disabled={busy || appLinksMatchEvidence}
            aria-describedby={evidenceStatusId}
            onClick={fillAppLinkEvidence}
          >
            개발자 확인 값 채우기
          </button>
        </div>
        <div className="server-settings-app-links">
          {APP_LINK_ROWS.map((row) => (
            <AppLinkControl
              key={row.id}
              row={row}
              evidence={ANDROID_APP_LINK_EVIDENCE[row.id]}
              draft={draft}
              error={errors[row.versionKey]}
              helpId={appLinkHelpId}
              onChange={setValue}
            />
          ))}
        </div>
      </fieldset>

      <section className="server-settings-review" aria-labelledby={reviewTitleId}>
        <h3 id={reviewTitleId}>{changes.length === 0 ? "바뀐 항목 없음" : `바뀐 항목 ${changes.length}개`}</h3>
        {changes.length > 0 && (
          <ul>
            {changes.map((change) => <li key={change.key}>{change.summary}</li>)}
          </ul>
        )}
        {scope !== null && (
          <p className={scope === "app" ? "server-settings-scope is-app" : "server-settings-scope"}>
            {scope === "app" ? APP_SCOPE_TEXT : WEB_SCOPE_TEXT}
          </p>
        )}
        {hasErrors && <p className="server-settings-error">표시한 입력값을 고친 뒤 적용할 수 있습니다.</p>}
        {scope === "app" && (
          <label className="server-settings-consent">
            <input
              type="checkbox"
              checked={consented}
              disabled={busy}
              onChange={(event) => setConsented(event.target.checked)}
            />
            <span>진행 중인 작업이 끝난 뒤 백업하고 앱 서비스를 다시 시작하는 동안 접속이 잠시 끊기는 데 동의합니다.</span>
          </label>
        )}
        <div className="server-settings-actions">
          <button
            type="button"
            className="button button-outline"
            disabled={busy || changes.length === 0}
            onClick={reset}
          >
            변경 취소
          </button>
          <button type="submit" className="button button-primary" disabled={!canApply}>
            {busy ? "적용 중…" : "변경 적용"}
          </button>
        </div>
      </section>
    </form>
  );
}
