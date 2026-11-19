import { request } from "./client";

export type SettingsFieldKind = "boolean" | "version" | "secret" | "contact";
export type SettingsScope = "web" | "app";
export type HostOs = "darwin" | "linux" | "windows";
export type SystemSettingsReason =
  | "ready"
  | "managed_install_required"
  | "manager_unavailable"
  | "manager_upgrade_required";

// The fixed allowlist and order shared with the API and the host manager. A response that
// disagrees with any key, kind or scope is rejected instead of being shown as editable.
export const SETTINGS_FIELD_CONTRACT = [
  { key: "KORAIL_RESERVATION_ONCE_ENABLED", kind: "boolean", scope: "app" },
  { key: "SRT_RESERVATION_ONCE_ENABLED", kind: "boolean", scope: "app" },
  { key: "TAGO_SERVICE_KEY", kind: "secret", scope: "app" },
  { key: "WEBPUSH_VAPID_SUBJECT", kind: "contact", scope: "app" },
  { key: "VITE_KORAIL_BOOKING_DEEPLINK_ENABLED", kind: "boolean", scope: "web" },
  { key: "VITE_KORAIL_BOOKING_VALIDATED_VERSION", kind: "version", scope: "web" },
  { key: "VITE_KORAIL_TICKET_DEEPLINK_ENABLED", kind: "boolean", scope: "web" },
  { key: "VITE_KORAIL_TICKET_VALIDATED_VERSION", kind: "version", scope: "web" },
  { key: "VITE_SRT_MAIN_DEEPLINK_ENABLED", kind: "boolean", scope: "web" },
  { key: "VITE_SRT_MAIN_VALIDATED_VERSION", kind: "version", scope: "web" },
  { key: "VITE_SRT_TICKET_DEEPLINK_ENABLED", kind: "boolean", scope: "web" },
  { key: "VITE_SRT_TICKET_VALIDATED_VERSION", kind: "version", scope: "web" },
] as const satisfies ReadonlyArray<{ key: string; kind: SettingsFieldKind; scope: SettingsScope }>;

export type SettingsFieldKey = (typeof SETTINGS_FIELD_CONTRACT)[number]["key"];

// A secret never carries its value into the browser; only whether it is configured.
export interface SecretSettingsField {
  kind: "secret";
  key: SettingsFieldKey;
  scope: SettingsScope;
  configured: boolean;
}
export interface ValueSettingsField {
  kind: "boolean" | "version" | "contact";
  key: SettingsFieldKey;
  scope: SettingsScope;
  value: string;
  configured: boolean;
}
export type SettingsField = SecretSettingsField | ValueSettingsField;

/** Where the install .env lives on the install computer, for edits made there. */
export interface InstallLocation {
  installPath: string;
  envPath: string;
  /** Null when an older manager did not report it and the path does not reveal it. */
  hostOs: HostOs | null;
}

export interface AvailableSystemSettings {
  available: true;
  reason: "ready";
  managed: true;
  managerAvailable: true;
  revision: string;
  fields: readonly SettingsField[];
  raw: string;
  secretKeys: readonly string[];
  lockedKeys: readonly string[];
  /** Whether the running host manager can restart the app services on request. */
  restartAvailable: boolean;
  installLocation: InstallLocation | null;
}
export interface UnavailableSystemSettings {
  available: false;
  reason: Exclude<SystemSettingsReason, "ready">;
  managed: boolean;
  managerAvailable: boolean;
}
export type SystemSettings = AvailableSystemSettings | UnavailableSystemSettings;

export interface SettingsSubmissionAccepted {
  id: string;
}

interface SettingsSubmissionBase {
  baseRevision: string;
  idempotencyKey: string;
}
export type SettingsSubmission =
  | (SettingsSubmissionBase & { mode: "fields"; fields: Readonly<Partial<Record<SettingsFieldKey, string>>> })
  | (SettingsSubmissionBase & { mode: "raw"; raw: string });

export const RAW_SETTINGS_MAX_BYTES = 65536;
const MAX_INSTALL_PATH_LENGTH = 4096;
const REVISION = /^[a-f0-9]{64}$/;
const IDENTIFIER = /^[a-f0-9]{32}$/;
const ENV_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;

function invalid(): Error {
  return new Error("서버 설정 응답 형식이 올바르지 않습니다.");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid();
  return Object.fromEntries(Object.entries(value));
}

function settingsReason(value: unknown): SystemSettingsReason {
  switch (value) {
    case "ready":
    case "managed_install_required":
    case "manager_unavailable":
    case "manager_upgrade_required":
      return value;
    default:
      throw invalid();
  }
}

function envKeys(value: unknown): string[] {
  if (!Array.isArray(value)) throw invalid();
  return value.map((entry: unknown) => {
    if (typeof entry !== "string" || !ENV_KEY.test(entry)) throw invalid();
    return entry;
  });
}

function hostOs(value: unknown): HostOs | null {
  if (value === null || value === undefined) return null;
  if (value === "darwin" || value === "linux" || value === "windows") return value;
  throw invalid();
}

function displayablePath(value: unknown): string {
  if (
    typeof value !== "string"
    || value === ""
    || value.length > MAX_INSTALL_PATH_LENGTH + 8
    || Array.from(value).some((character) => character.charCodeAt(0) < 32)
  ) {
    throw invalid();
  }
  return value;
}

function installLocation(value: unknown): InstallLocation | null {
  // Absent from APIs before 1.5.0; null when the manager did not report a location.
  if (value === null || value === undefined) return null;
  const item = record(value);
  return {
    installPath: displayablePath(item.install_path),
    envPath: displayablePath(item.env_path),
    hostOs: hostOs(item.host_os),
  };
}

function settingsField(value: unknown, index: number): SettingsField {
  const expected = SETTINGS_FIELD_CONTRACT[index];
  const item = record(value);
  if (
    expected === undefined
    || item.key !== expected.key
    || item.kind !== expected.kind
    || item.scope !== expected.scope
    || typeof item.configured !== "boolean"
  ) {
    throw invalid();
  }
  if (expected.kind === "secret") {
    // A response that leaks a secret value is a contract violation; refuse it outright.
    if (item.value !== null && item.value !== undefined) throw invalid();
    return { kind: "secret", key: expected.key, scope: expected.scope, configured: item.configured };
  }
  if (item.value !== null && typeof item.value !== "string") throw invalid();
  return {
    kind: expected.kind,
    key: expected.key,
    scope: expected.scope,
    value: item.value ?? "",
    configured: item.configured,
  };
}

export function mapSystemSettings(value: unknown): SystemSettings {
  const item = record(value);
  if (
    item.schema !== 1
    || typeof item.managed !== "boolean"
    || typeof item.manager_available !== "boolean"
    || typeof item.available !== "boolean"
  ) {
    throw invalid();
  }
  const reason = settingsReason(item.reason);
  if (!item.available && reason !== "ready") {
    return {
      available: false,
      reason,
      managed: item.managed,
      managerAvailable: item.manager_available,
    };
  }
  if (!item.available || reason !== "ready" || !item.managed || !item.manager_available) throw invalid();
  if (typeof item.revision !== "string" || !REVISION.test(item.revision)) throw invalid();
  if (typeof item.raw !== "string") throw invalid();
  if (!Array.isArray(item.fields) || item.fields.length !== SETTINGS_FIELD_CONTRACT.length) throw invalid();
  if (item.restart_available !== undefined && typeof item.restart_available !== "boolean") throw invalid();
  return {
    available: true,
    reason: "ready",
    managed: true,
    managerAvailable: true,
    revision: item.revision,
    fields: item.fields.map((field: unknown, index) => settingsField(field, index)),
    raw: item.raw,
    secretKeys: envKeys(item.secret_keys),
    lockedKeys: envKeys(item.locked_keys),
    restartAvailable: item.restart_available === true,
    installLocation: installLocation(item.install_location),
  };
}

function mapAccepted(value: unknown, message: string): SettingsSubmissionAccepted {
  const item = record(value);
  if (item.schema !== 1 || typeof item.id !== "string" || !IDENTIFIER.test(item.id) || item.state !== "queued") {
    throw new Error(message);
  }
  return { id: item.id };
}

export function mapSettingsSubmissionAccepted(value: unknown): SettingsSubmissionAccepted {
  return mapAccepted(value, "서버 설정 변경 요청 응답이 올바르지 않습니다.");
}

export function mapRestartAccepted(value: unknown): SettingsSubmissionAccepted {
  return mapAccepted(value, "서비스 다시 시작 요청 응답이 올바르지 않습니다.");
}

export function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

export function isSubmittableRawSettings(raw: string): boolean {
  return !raw.includes("\u0000") && utf8ByteLength(raw) <= RAW_SETTINGS_MAX_BYTES;
}

function submissionBody(submission: SettingsSubmission): string {
  if (!REVISION.test(submission.baseRevision) || !IDENTIFIER.test(submission.idempotencyKey)) {
    throw new Error("서버 설정 변경 요청을 만들지 못했습니다.");
  }
  const base = { base_revision: submission.baseRevision, idempotency_key: submission.idempotencyKey };
  if (submission.mode === "raw") {
    if (!isSubmittableRawSettings(submission.raw)) throw new Error("서버 설정 원문을 보낼 수 없습니다.");
    return JSON.stringify({ ...base, raw: submission.raw });
  }
  const fields: Record<string, string> = {};
  for (const { key } of SETTINGS_FIELD_CONTRACT) {
    const fieldValue = submission.fields[key];
    if (fieldValue !== undefined) fields[key] = fieldValue;
  }
  if (Object.keys(fields).length === 0) throw new Error("바꿀 서버 설정이 없습니다.");
  return JSON.stringify({ ...base, fields });
}

export async function fetchSystemSettings(signal?: AbortSignal): Promise<SystemSettings> {
  return mapSystemSettings(await request("/system/settings", { signal: signal ?? null }));
}

export async function submitSystemSettings(submission: SettingsSubmission): Promise<SettingsSubmissionAccepted> {
  return mapSettingsSubmissionAccepted(await request("/system/settings", {
    method: "POST",
    body: submissionBody(submission),
  }));
}

/** Ask the host manager to recreate the app services; PostgreSQL and Redis keep running. */
export async function submitSystemRestart(idempotencyKey: string): Promise<SettingsSubmissionAccepted> {
  if (!IDENTIFIER.test(idempotencyKey)) throw new Error("서비스 다시 시작 요청을 만들지 못했습니다.");
  return mapRestartAccepted(await request("/system/restart", {
    method: "POST",
    body: JSON.stringify({ scope: "app", idempotency_key: idempotencyKey }),
  }));
}
