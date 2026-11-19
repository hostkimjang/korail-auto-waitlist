import { vi } from "vitest";

import { SETTINGS_FIELD_CONTRACT } from "../src/api/systemSettings";

export const SETTINGS_REVISION = "a".repeat(64);
export const NEXT_SETTINGS_REVISION = "b".repeat(64);
export const SETTINGS_JOB_ID = "c".repeat(32);
export const SETTINGS_RAW = [
  "COMPOSE_PROJECT_NAME=railwait",
  "POSTGRES_PASSWORD=••••••",
  "KORAIL_RESERVATION_ONCE_ENABLED=false",
  "VITE_SRT_MAIN_DEEPLINK_ENABLED=false",
  "",
].join("\n");

interface SettingsPayloadOptions {
  revision?: string;
  values?: Readonly<Record<string, string>>;
  secretConfigured?: boolean;
  restartAvailable?: boolean;
  installLocation?: { install_path: string; env_path: string; host_os: string | null } | null;
}

const defaultValues: Readonly<Record<string, string>> = {
  KORAIL_RESERVATION_ONCE_ENABLED: "false",
  SRT_RESERVATION_ONCE_ENABLED: "true",
  VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "false",
  VITE_KORAIL_TICKET_DEEPLINK_ENABLED: "false",
  VITE_SRT_MAIN_DEEPLINK_ENABLED: "false",
  VITE_SRT_TICKET_DEEPLINK_ENABLED: "false",
};

export function settingsPayload(options: SettingsPayloadOptions = {}) {
  const values = { ...defaultValues, ...options.values };
  return {
    schema: 1,
    managed: true,
    manager_available: true,
    available: true,
    reason: "ready",
    revision: options.revision ?? SETTINGS_REVISION,
    fields: SETTINGS_FIELD_CONTRACT.map(({ key, kind, scope }) => (kind === "secret"
      ? { key, kind, scope, value: null, configured: options.secretConfigured ?? true }
      : { key, kind, scope, value: values[key] ?? "", configured: (values[key] ?? "") !== "" })),
    raw: SETTINGS_RAW,
    secret_keys: ["POSTGRES_PASSWORD", "TAGO_SERVICE_KEY"],
    locked_keys: ["COMPOSE_PROJECT_NAME"],
    restart_available: options.restartAvailable ?? true,
    install_location: options.installLocation ?? null,
  };
}

export function unavailableSettingsPayload(reason: string, managed = true, managerAvailable = true) {
  return {
    schema: 1,
    managed,
    manager_available: managerAvailable,
    available: false,
    reason,
    revision: null,
    fields: [],
    raw: null,
    secret_keys: [],
    locked_keys: [],
  };
}

export function settingsJobPayload(state: string, stage: string, errorCode: string | null = null) {
  return {
    schema: 1,
    id: SETTINGS_JOB_ID,
    target_release_id: "1.4.8",
    plan_id: "0".repeat(64),
    idempotency_key: "d".repeat(32),
    state,
    stage,
    created_at: "2026-11-19T00:00:00Z",
    updated_at: "2026-11-19T00:00:01Z",
    error_code: errorCode,
  };
}

export function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

export interface RecordedRequest {
  path: string;
  method: string;
  body: unknown;
}

export type FetchRoute = (request: RecordedRequest) => Response | Promise<Response>;

// Routes every API call through one handler and records what the browser sent.
export function routeFetch(route: FetchRoute) {
  const requests: RecordedRequest[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const body: unknown = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    const recorded = { path: url.pathname, method: init?.method ?? "GET", body };
    requests.push(recorded);
    return route(recorded);
  });
  return { spy, requests };
}
