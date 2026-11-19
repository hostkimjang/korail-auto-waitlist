import { APP_LINK_ROWS, type AppLinkId, type PlainSettingsKey } from "./serverSettingsForm";

export interface AppLinkEvidence {
  /** Turn this path on for users who have not verified it on their own phone. */
  recommended: boolean;
  /** versionName+versionCode the developer verified on a real device; null while pending. */
  version: string | null;
  /** What is still unverified; null when the path is recommended. */
  pending: string | null;
}

export const ANDROID_APP_LINK_EVIDENCE_SOURCE = "docs/ANDROID_APP_HANDOFF_QA.md";

/**
 * The developer's real-device evidence for each Android app link path. It must equal the
 * "현재 증거에 따른 보수적 활성 후보" block of the QA document (a test compares them) and ships
 * with every release, so updating Railwait brings newer evidence to the settings screen. The
 * screen only fills it in on request; the app link contract keeps every path off by default.
 */
export const ANDROID_APP_LINK_EVIDENCE: Readonly<Record<AppLinkId, AppLinkEvidence>> = {
  "korail-booking": { recommended: true, version: "7.0.0+70000006", pending: null },
  "korail-ticket": {
    recommended: false,
    version: null,
    pending: "예약 승차권 조회(bookedTicket) 화면으로 여는 버튼 확인 전",
  },
  "srt-main": {
    recommended: false,
    version: null,
    pending: "앱이 이미 열려 있을 때 예매 홈으로 가는지 확인 전",
  },
  "srt-ticket": { recommended: true, version: "2.0.41+150", pending: null },
};

/** The .env values that apply the evidence: recommended paths on with their version, others off. */
export function appLinkEvidenceEntries(): ReadonlyArray<readonly [PlainSettingsKey, string]> {
  return APP_LINK_ROWS.flatMap((row) => {
    const evidence = ANDROID_APP_LINK_EVIDENCE[row.id];
    const enabled = evidence.recommended && evidence.version !== null;
    return [
      [row.enabledKey, enabled ? "true" : "false"],
      [row.versionKey, enabled ? evidence.version ?? "" : ""],
    ] as const;
  });
}

export function matchesAppLinkEvidence(values: Readonly<Record<PlainSettingsKey, string>>): boolean {
  return appLinkEvidenceEntries().every(([key, value]) => values[key] === value);
}
