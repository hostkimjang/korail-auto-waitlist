import { describe, expect, it } from "vitest";

import { mapSystemSettings, type SettingsField } from "../src/api/systemSettings";
import {
  APP_LINK_ROWS,
  CONTACT_ERROR,
  SECRET_ERROR,
  VERSION_ERROR,
  appLinkMissingVersion,
  changedFields,
  initialServerSettingsDraft,
  isValidContact,
  rawChangesWebSettings,
  rawSettingsProblem,
  restartScope,
  serverSettingsChanges,
  serverSettingsErrors,
  type ServerSettingsDraft,
} from "../src/features/settings/serverSettingsForm";
import {
  parseSettingsJobContext,
  serializeSettingsJobContext,
  settingsJobOutcome,
  type SettingsJobContext,
} from "../src/features/settings/serverSettingsJob";
import type { UpdateJob } from "../src/api/systemUpdate";
import { SETTINGS_JOB_ID, settingsPayload } from "./serverSettingsFixtures";

function fields(options: Parameters<typeof settingsPayload>[0] = {}): readonly SettingsField[] {
  const settings = mapSystemSettings(settingsPayload(options));
  if (!settings.available) throw new Error("expected an editable snapshot");
  return settings.fields;
}

function withValues(draft: ServerSettingsDraft, values: Partial<ServerSettingsDraft["values"]>): ServerSettingsDraft {
  return { ...draft, values: { ...draft.values, ...values } };
}

describe("server settings form state", () => {
  it("reports no change for the loaded snapshot, including untouched unset switches", () => {
    const loaded = fields({ values: { VITE_KORAIL_TICKET_DEEPLINK_ENABLED: "" } });
    const draft = initialServerSettingsDraft(loaded);
    expect(draft.values.VITE_KORAIL_TICKET_DEEPLINK_ENABLED).toBe("false");
    expect(serverSettingsChanges(loaded, draft)).toEqual([]);
    expect(restartScope([])).toBeNull();
  });

  it("keeps web-only changes in the web scope and widens to app for any app key", () => {
    const loaded = fields();
    const webOnly = withValues(initialServerSettingsDraft(loaded), {
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "true",
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: " 7.0.0 ",
    });
    const webChanges = serverSettingsChanges(loaded, webOnly);
    expect(webChanges.map((change) => change.summary)).toEqual([
      "코레일+ 예매 앱 연결: 켬",
      "코레일+ 예매 검증한 앱 버전: 7.0.0",
    ]);
    expect(restartScope(webChanges)).toBe("web");
    expect(changedFields(webChanges)).toEqual({
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "true",
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: "7.0.0",
    });

    const withApp = withValues(webOnly, { SRT_RESERVATION_ONCE_ENABLED: "false" });
    expect(restartScope(serverSettingsChanges(loaded, withApp))).toBe("app");
  });

  it("stages a key replacement or removal without echoing the key", () => {
    const loaded = fields();
    const replaced = { ...initialServerSettingsDraft(loaded), secret: { action: "replace" as const, value: " new-key%2B== " } };
    const [replacement] = serverSettingsChanges(loaded, replaced);
    expect(replacement).toMatchObject({ key: "TAGO_SERVICE_KEY", scope: "app", value: "new-key%2B==" });
    expect(replacement?.summary).toBe("공공데이터포털 열차정보 인증키: 새 키로 바꿈");

    const cleared = { ...initialServerSettingsDraft(loaded), secret: { action: "clear" as const } };
    expect(serverSettingsChanges(loaded, cleared)).toMatchObject([{ key: "TAGO_SERVICE_KEY", value: "", summary: "공공데이터포털 열차정보 인증키: 지움" }]);
    // Clearing a key that does not exist is not a change.
    const unset = fields({ secretConfigured: false });
    expect(serverSettingsChanges(unset, { ...initialServerSettingsDraft(unset), secret: { action: "clear" } })).toEqual([]);
  });

  it("validates only changed values against the shared contract", () => {
    const loaded = fields({ values: { WEBPUSH_VAPID_SUBJECT: "not-a-contact" } });
    const untouched = initialServerSettingsDraft(loaded);
    expect(serverSettingsErrors(loaded, serverSettingsChanges(loaded, untouched))).toEqual({});

    const invalid = {
      ...withValues(untouched, { VITE_SRT_TICKET_VALIDATED_VERSION: "2.0 beta", WEBPUSH_VAPID_SUBJECT: "https://example.com:8443" }),
      secret: { action: "replace" as const, value: "has'quote" },
    };
    expect(serverSettingsErrors(loaded, serverSettingsChanges(loaded, invalid))).toEqual({
      VITE_SRT_TICKET_VALIDATED_VERSION: VERSION_ERROR,
      WEBPUSH_VAPID_SUBJECT: CONTACT_ERROR,
      TAGO_SERVICE_KEY: SECRET_ERROR,
    });
  });

  it.each([
    ["", true],
    ["mailto:ops@example.com", true],
    ["https://railwait.example.com", true],
    ["HTTPS://example.com", false],
    ["https://example.com/", false],
    ["https://example.com:443", false],
    ["https://example.com?x=1", false],
    ["mailto:a@b.com,c@d.com", false],
    ["mailto:ops@example..com", false],
    ["ops@example.com", false],
    [`mailto:${"a".repeat(250)}@b.c`, false],
  ])("contact %j is valid: %s", (value, expected) => {
    expect(isValidContact(value)).toBe(expected);
  });

  it("warns when an app link is on without a validated version", () => {
    const row = APP_LINK_ROWS[2];
    if (row === undefined) throw new Error("missing SRT main row");
    const draft = withValues(initialServerSettingsDraft(fields()), { VITE_SRT_MAIN_DEEPLINK_ENABLED: "true" });
    expect(appLinkMissingVersion(draft, row)).toBe(true);
    expect(appLinkMissingVersion(withValues(draft, { VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41" }), row)).toBe(false);
  });

  it("checks raw text limits and whether a web-build line changed", () => {
    expect(rawSettingsProblem("A=1\n")).toBeNull();
    expect(rawSettingsProblem("A=1\u0000")).toBe("nul");
    expect(rawSettingsProblem("x".repeat(65537))).toBe("too_large");
    expect(rawChangesWebSettings("A=1\nVITE_X=false\n", "A=2\nVITE_X=false\n")).toBe(false);
    expect(rawChangesWebSettings("A=1\nVITE_X=false\n", "A=1\nVITE_X=true\n")).toBe(true);
  });
});

describe("server settings job presentation", () => {
  const context: SettingsJobContext = { id: SETTINGS_JOB_ID, mode: "fields", scope: "web", reloadRequired: true };
  const job = (state: UpdateJob["state"], stage: string, errorCode: UpdateJob["errorCode"] = null): UpdateJob => ({
    id: SETTINGS_JOB_ID,
    state,
    stage,
    errorCode,
  });

  it("round-trips the remembered job and rejects tampered values", () => {
    expect(parseSettingsJobContext(serializeSettingsJobContext(context))).toEqual(context);
    expect(parseSettingsJobContext(null)).toBeNull();
    expect(parseSettingsJobContext("{")).toBeNull();
    expect(parseSettingsJobContext(JSON.stringify({ ...context, id: "../x" }))).toBeNull();
    expect(parseSettingsJobContext(JSON.stringify({ ...context, scope: null }))).toBeNull();
    expect(parseSettingsJobContext(JSON.stringify({ ...context, mode: "raw", scope: null }))).toMatchObject({ mode: "raw", scope: null });
  });

  it.each([
    [job("running", "draining"), "progress", "서버 설정 적용 중: 진행 중인 작업이 끝나기를 기다리는 중"],
    [job("succeeded", "succeeded"), "success", "서버 설정을 적용했습니다. 바뀐 앱 연결 설정을 쓰려면 이 페이지를 새로고침하세요."],
    [job("failed", "rolled_back"), "error", "적용에 실패해 이전 설정으로 되돌렸습니다."],
    [job("rejected", "rejected", "E_SETTINGS_CHANGED"), "warning", "다른 곳에서 설정이 바뀌어"],
    [job("rejected", "rejected", "E_SETTINGS_INVALID"), "error", "입력값을 받아들이지 않아"],
    [job("rejected", "rejected", "E_SETTINGS_FORBIDDEN"), "error", "CLI로만 바꿀 수 있는 항목"],
    [job("rejected", "rejected", "E_SETTINGS_UNCHANGED"), "warning", "바뀐 항목이 없어"],
    [job("rejected", "rejected", "E_UNAUTHORIZED"), "error", "인증을 확인하지 못해"],
    [job("deferred", "draining"), "warning", "진행 중인 작업이 있어 설정 적용을 보류했습니다."],
    [job("recovery_required", "applying"), "error", "복구 확인이 필요합니다"],
  ])("describes %o", (value, tone, message) => {
    const outcome = settingsJobOutcome(value, context);
    expect(outcome.tone).toBe(tone);
    expect(outcome.message).toContain(message);
  });

  it("points failed jobs at the CLI job status and omits the reload hint for app-only changes", () => {
    expect(settingsJobOutcome(job("failed", "applying"), context).command).toBe(`railwait status --job ${SETTINGS_JOB_ID}`);
    const appOnly = { ...context, scope: "app" as const, reloadRequired: false };
    expect(settingsJobOutcome(job("succeeded", "succeeded"), appOnly).message).toBe("서버 설정을 적용했습니다.");
  });
});
