import {
  RAW_SETTINGS_MAX_BYTES,
  utf8ByteLength,
  type SettingsField,
  type SettingsFieldKey,
  type SettingsScope,
} from "../../api/systemSettings";

export type SecretSettingsKey = "TAGO_SERVICE_KEY";
export type PlainSettingsKey = Exclude<SettingsFieldKey, SecretSettingsKey>;
export type AppLinkEnabledKey =
  | "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED"
  | "VITE_KORAIL_TICKET_DEEPLINK_ENABLED"
  | "VITE_SRT_MAIN_DEEPLINK_ENABLED"
  | "VITE_SRT_TICKET_DEEPLINK_ENABLED";
export type AppLinkVersionKey =
  | "VITE_KORAIL_BOOKING_VALIDATED_VERSION"
  | "VITE_KORAIL_TICKET_VALIDATED_VERSION"
  | "VITE_SRT_MAIN_VALIDATED_VERSION"
  | "VITE_SRT_TICKET_VALIDATED_VERSION";

// The browser only ever stages a replacement or a removal; it never learns the stored key.
export type SecretDraft =
  | { action: "keep" }
  | { action: "replace"; value: string }
  | { action: "clear" };

export interface ServerSettingsDraft {
  values: Readonly<Record<PlainSettingsKey, string>>;
  secret: SecretDraft;
}

export interface SettingsChange {
  key: SettingsFieldKey;
  scope: SettingsScope;
  value: string;
  summary: string;
}

export interface AppLinkRow {
  id: string;
  label: string;
  enabledKey: AppLinkEnabledKey;
  versionKey: AppLinkVersionKey;
}

export const APP_LINK_ROWS: readonly AppLinkRow[] = [
  {
    id: "korail-booking",
    label: "코레일+ 예매",
    enabledKey: "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED",
    versionKey: "VITE_KORAIL_BOOKING_VALIDATED_VERSION",
  },
  {
    id: "korail-ticket",
    label: "코레일+ 승차권",
    enabledKey: "VITE_KORAIL_TICKET_DEEPLINK_ENABLED",
    versionKey: "VITE_KORAIL_TICKET_VALIDATED_VERSION",
  },
  {
    id: "srt-main",
    label: "SRT 예매 홈",
    enabledKey: "VITE_SRT_MAIN_DEEPLINK_ENABLED",
    versionKey: "VITE_SRT_MAIN_VALIDATED_VERSION",
  },
  {
    id: "srt-ticket",
    label: "SRT 승차권 확인",
    enabledKey: "VITE_SRT_TICKET_DEEPLINK_ENABLED",
    versionKey: "VITE_SRT_TICKET_VALIDATED_VERSION",
  },
];

export const SETTINGS_FIELD_LABELS: Readonly<Record<SettingsFieldKey, string>> = {
  KORAIL_RESERVATION_ONCE_ENABLED: "KTX·코레일 자동 예매",
  SRT_RESERVATION_ONCE_ENABLED: "SRT 자동 예매",
  TAGO_SERVICE_KEY: "공공데이터포털 열차정보 인증키",
  WEBPUSH_VAPID_SUBJECT: "알림 발신자 연락처",
  VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "코레일+ 예매 앱 연결",
  VITE_KORAIL_BOOKING_VALIDATED_VERSION: "코레일+ 예매 검증한 앱 버전",
  VITE_KORAIL_TICKET_DEEPLINK_ENABLED: "코레일+ 승차권 앱 연결",
  VITE_KORAIL_TICKET_VALIDATED_VERSION: "코레일+ 승차권 검증한 앱 버전",
  VITE_SRT_MAIN_DEEPLINK_ENABLED: "SRT 예매 홈 앱 연결",
  VITE_SRT_MAIN_VALIDATED_VERSION: "SRT 예매 홈 검증한 앱 버전",
  VITE_SRT_TICKET_DEEPLINK_ENABLED: "SRT 승차권 확인 앱 연결",
  VITE_SRT_TICKET_VALIDATED_VERSION: "SRT 승차권 확인 검증한 앱 버전",
};

const VERSION = /^[0-9A-Za-z.+_-]{1,64}$/;
// Printable ASCII without whitespace, single quote or double quote.
const SECRET = /^[!#-&(-~]{1,512}$/;
// Mirrors the API's contact rule (case-sensitive scheme, strict host labels, no port or path).
const HOST_LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?";
const HOSTNAME = `${HOST_LABEL}(?:\\.${HOST_LABEL})*`;
const CONTACT = new RegExp(`^(?:mailto:[A-Za-z0-9.!#$%&*+/=^_\`{|}~-]+@${HOSTNAME}|https://${HOSTNAME})$`);
const CONTACT_MAX_LENGTH = 254;

export const VERSION_ERROR = "영문·숫자와 . + _ - 만 써서 64자 이내로 입력하세요. 예: 7.0.0";
export const CONTACT_ERROR =
  "mailto:주소@example.com 또는 https://example.com 형식으로 입력하세요. https 주소에는 포트·경로를 넣을 수 없습니다.";
export const SECRET_ERROR = "인증키는 공백과 따옴표 없이 512자 이내의 영문·숫자·기호로 입력하세요.";

function fieldOf(fields: readonly SettingsField[], key: SettingsFieldKey): SettingsField | undefined {
  return fields.find((field) => field.key === key);
}

function baselineValue(fields: readonly SettingsField[], key: PlainSettingsKey): string {
  const field = fieldOf(fields, key);
  if (field === undefined || field.kind === "secret") return "";
  // A switch is on only for the exact text the manager writes; anything else reads as off.
  if (field.kind === "boolean") return field.value === "true" ? "true" : "false";
  return field.value;
}

export function initialServerSettingsDraft(fields: readonly SettingsField[]): ServerSettingsDraft {
  const value = (key: PlainSettingsKey): string => baselineValue(fields, key);
  return {
    values: {
      KORAIL_RESERVATION_ONCE_ENABLED: value("KORAIL_RESERVATION_ONCE_ENABLED"),
      SRT_RESERVATION_ONCE_ENABLED: value("SRT_RESERVATION_ONCE_ENABLED"),
      WEBPUSH_VAPID_SUBJECT: value("WEBPUSH_VAPID_SUBJECT"),
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: value("VITE_KORAIL_BOOKING_DEEPLINK_ENABLED"),
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: value("VITE_KORAIL_BOOKING_VALIDATED_VERSION"),
      VITE_KORAIL_TICKET_DEEPLINK_ENABLED: value("VITE_KORAIL_TICKET_DEEPLINK_ENABLED"),
      VITE_KORAIL_TICKET_VALIDATED_VERSION: value("VITE_KORAIL_TICKET_VALIDATED_VERSION"),
      VITE_SRT_MAIN_DEEPLINK_ENABLED: value("VITE_SRT_MAIN_DEEPLINK_ENABLED"),
      VITE_SRT_MAIN_VALIDATED_VERSION: value("VITE_SRT_MAIN_VALIDATED_VERSION"),
      VITE_SRT_TICKET_DEEPLINK_ENABLED: value("VITE_SRT_TICKET_DEEPLINK_ENABLED"),
      VITE_SRT_TICKET_VALIDATED_VERSION: value("VITE_SRT_TICKET_VALIDATED_VERSION"),
    },
    secret: { action: "keep" },
  };
}

export function isSecretConfigured(fields: readonly SettingsField[]): boolean {
  const field = fieldOf(fields, "TAGO_SERVICE_KEY");
  return field?.kind === "secret" && field.configured;
}

export function isValidVersion(value: string): boolean {
  return value === "" || VERSION.test(value);
}

export function isValidContact(value: string): boolean {
  return value === "" || (value.length <= CONTACT_MAX_LENGTH && CONTACT.test(value));
}

export function isValidSecret(value: string): boolean {
  return SECRET.test(value);
}

// Text inputs compare and submit trimmed values so a pasted trailing space is not a change.
function normalized(kind: SettingsField["kind"], value: string): string {
  return kind === "boolean" ? value : value.trim();
}

function fieldKind(fields: readonly SettingsField[], key: SettingsFieldKey): SettingsField["kind"] | null {
  return fieldOf(fields, key)?.kind ?? null;
}

function changeSummary(kind: SettingsField["kind"], key: SettingsFieldKey, value: string): string {
  const label = SETTINGS_FIELD_LABELS[key];
  switch (kind) {
    case "boolean":
      return `${label}: ${value === "true" ? "켬" : "끔"}`;
    case "version":
      return `${label}: ${value === "" ? "비움" : value}`;
    case "contact":
      return `${label}: ${value === "" ? "기본값 사용" : value}`;
    case "secret":
      return `${label}: ${value === "" ? "지움" : "새 키로 바꿈"}`;
  }
}

export function serverSettingsChanges(
  fields: readonly SettingsField[],
  draft: ServerSettingsDraft,
): SettingsChange[] {
  const baseline = initialServerSettingsDraft(fields);
  const changes: SettingsChange[] = [];
  for (const field of fields) {
    if (field.kind === "secret") {
      if (field.key !== "TAGO_SERVICE_KEY") continue;
      const secret = draft.secret;
      if (secret.action === "replace" && secret.value.trim() !== "") {
        const value = secret.value.trim();
        changes.push({ key: field.key, scope: field.scope, value, summary: changeSummary("secret", field.key, value) });
      } else if (secret.action === "clear" && field.configured) {
        changes.push({ key: field.key, scope: field.scope, value: "", summary: changeSummary("secret", field.key, "") });
      }
      continue;
    }
    if (field.key === "TAGO_SERVICE_KEY") continue;
    const next = normalized(field.kind, draft.values[field.key]);
    if (next === normalized(field.kind, baseline.values[field.key])) continue;
    changes.push({ key: field.key, scope: field.scope, value: next, summary: changeSummary(field.kind, field.key, next) });
  }
  return changes;
}

export type ServerSettingsErrors = Partial<Record<SettingsFieldKey, string>>;

// Only changed values are validated: an odd value already in .env must not block other edits.
export function serverSettingsErrors(
  fields: readonly SettingsField[],
  changes: readonly SettingsChange[],
): ServerSettingsErrors {
  const errors: ServerSettingsErrors = {};
  for (const change of changes) {
    const kind = fieldKind(fields, change.key);
    if (kind === "version" && !isValidVersion(change.value)) errors[change.key] = VERSION_ERROR;
    if (kind === "contact" && !isValidContact(change.value)) errors[change.key] = CONTACT_ERROR;
    if (kind === "secret" && change.value !== "" && !isValidSecret(change.value)) errors[change.key] = SECRET_ERROR;
  }
  return errors;
}

export function restartScope(changes: readonly SettingsChange[]): SettingsScope | null {
  if (changes.length === 0) return null;
  return changes.some((change) => change.scope === "app") ? "app" : "web";
}

export function changedFields(changes: readonly SettingsChange[]): Partial<Record<SettingsFieldKey, string>> {
  const fields: Partial<Record<SettingsFieldKey, string>> = {};
  for (const change of changes) fields[change.key] = change.value;
  return fields;
}

export function appLinkMissingVersion(draft: ServerSettingsDraft, row: AppLinkRow): boolean {
  return draft.values[row.enabledKey] === "true" && draft.values[row.versionKey].trim() === "";
}

export type RawSettingsProblem = "nul" | "too_large";

export function rawSettingsProblem(raw: string): RawSettingsProblem | null {
  if (raw.includes("\u0000")) return "nul";
  if (utf8ByteLength(raw) > RAW_SETTINGS_MAX_BYTES) return "too_large";
  return null;
}

function webSettingLines(raw: string): string {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => /^(?:export\s+)?VITE_/.test(line))
    .join("\n");
}

// The server decides the restart scope for raw edits; the browser only needs to know whether a
// web-build line changed so it can ask for a page reload afterwards.
export function rawChangesWebSettings(original: string, next: string): boolean {
  return webSettingLines(original) !== webSettingLines(next);
}
