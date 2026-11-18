import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RAW_SETTINGS_MAX_BYTES,
  SETTINGS_FIELD_CONTRACT,
  mapSettingsSubmissionAccepted,
  mapSystemSettings,
  submitSystemSettings,
} from "../src/api/systemSettings";
import { mapUpdateJob } from "../src/api/systemUpdate";
import {
  SETTINGS_JOB_ID,
  SETTINGS_REVISION,
  jsonResponse,
  routeFetch,
  settingsPayload,
  unavailableSettingsPayload,
} from "./serverSettingsFixtures";

afterEach(() => vi.restoreAllMocks());

function withField(index: number, patch: Record<string, unknown>) {
  const payload = settingsPayload();
  return { ...payload, fields: payload.fields.map((field, position) => (position === index ? { ...field, ...patch } : field)) };
}

describe("system settings API boundary", () => {
  it("maps a ready snapshot in the fixed order and never carries a secret value", () => {
    const settings = mapSystemSettings(settingsPayload({ values: { WEBPUSH_VAPID_SUBJECT: "mailto:me@example.com" } }));
    if (!settings.available) throw new Error("expected an editable snapshot");
    expect(settings.revision).toBe(SETTINGS_REVISION);
    expect(settings.fields.map((field) => field.key)).toEqual(SETTINGS_FIELD_CONTRACT.map((field) => field.key));
    expect(settings.fields[2]).toEqual({ kind: "secret", key: "TAGO_SERVICE_KEY", scope: "app", configured: true });
    expect(settings.fields[3]).toMatchObject({ kind: "contact", value: "mailto:me@example.com", configured: true });
    expect(settings.secretKeys).toEqual(["POSTGRES_PASSWORD", "TAGO_SERVICE_KEY"]);
    expect(settings.lockedKeys).toEqual(["COMPOSE_PROJECT_NAME"]);
  });

  it("keeps unavailable states free of editable data", () => {
    expect(mapSystemSettings(unavailableSettingsPayload("managed_install_required", false, false))).toEqual({
      available: false,
      reason: "managed_install_required",
      managed: false,
      managerAvailable: false,
    });
    expect(mapSystemSettings(unavailableSettingsPayload("manager_upgrade_required"))).toMatchObject({
      available: false,
      reason: "manager_upgrade_required",
    });
  });

  it.each([
    ["a secret value in the response", withField(2, { value: "leaked" })],
    ["fields out of the fixed order", withField(0, { key: "SRT_RESERVATION_ONCE_ENABLED" })],
    ["a scope that disagrees with the contract", withField(4, { scope: "app" })],
    ["a kind that disagrees with the contract", withField(5, { kind: "contact" })],
    ["a missing field", { ...settingsPayload(), fields: settingsPayload().fields.slice(1) }],
    ["an unknown reason", { ...unavailableSettingsPayload("manager_unavailable"), reason: "maybe" }],
    ["available with a blocking reason", { ...settingsPayload(), reason: "manager_unavailable" }],
    ["unavailable but ready", { ...unavailableSettingsPayload("manager_unavailable"), reason: "ready" }],
    ["a malformed revision", { ...settingsPayload(), revision: "../etc" }],
    ["a malformed locked key", { ...settingsPayload(), locked_keys: ["A=B"] }],
    ["a missing raw text", { ...settingsPayload(), raw: null }],
  ])("rejects %s", (_label, payload) => {
    expect(() => mapSystemSettings(payload)).toThrow();
  });

  it("validates the accepted response and the job error code", () => {
    expect(mapSettingsSubmissionAccepted({ schema: 1, id: SETTINGS_JOB_ID, state: "queued" })).toEqual({ id: SETTINGS_JOB_ID });
    expect(() => mapSettingsSubmissionAccepted({ schema: 1, id: "../x", state: "queued" })).toThrow();
    const job = { schema: 1, id: SETTINGS_JOB_ID, state: "rejected", stage: "rejected" };
    expect(mapUpdateJob({ ...job, error_code: "E_SETTINGS_FORBIDDEN" }).errorCode).toBe("E_SETTINGS_FORBIDDEN");
    expect(mapUpdateJob({ ...job, error_code: "E_HANDLER_FAILED" }).errorCode).toBe("E_HANDLER_FAILED");
    expect(mapUpdateJob({ ...job, error_code: "E_SOMETHING_NEW" }).errorCode).toBeNull();
    expect(mapUpdateJob(job).errorCode).toBeNull();
  });

  it("posts fields or raw text with the base revision and refuses oversized raw text locally", async () => {
    const { requests } = routeFetch(() => jsonResponse({ schema: 1, id: SETTINGS_JOB_ID, state: "queued" }, 202));
    const idempotencyKey = "e".repeat(32);
    await submitSystemSettings({
      mode: "fields",
      baseRevision: SETTINGS_REVISION,
      idempotencyKey,
      fields: { VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41", KORAIL_RESERVATION_ONCE_ENABLED: "true" },
    });
    await submitSystemSettings({ mode: "raw", baseRevision: SETTINGS_REVISION, idempotencyKey, raw: "A=1\n" });
    expect(requests.map(({ method, path }) => `${method} ${path}`)).toEqual([
      "POST /api/v1/system/settings",
      "POST /api/v1/system/settings",
    ]);
    expect(requests[0]?.body).toEqual({
      base_revision: SETTINGS_REVISION,
      idempotency_key: idempotencyKey,
      fields: { KORAIL_RESERVATION_ONCE_ENABLED: "true", VITE_SRT_MAIN_VALIDATED_VERSION: "2.0.41" },
    });
    expect(requests[1]?.body).toEqual({ base_revision: SETTINGS_REVISION, idempotency_key: idempotencyKey, raw: "A=1\n" });

    await expect(submitSystemSettings({
      mode: "raw",
      baseRevision: SETTINGS_REVISION,
      idempotencyKey,
      raw: "가".repeat(Math.ceil(RAW_SETTINGS_MAX_BYTES / 3) + 1),
    })).rejects.toThrow();
    await expect(submitSystemSettings({ mode: "fields", baseRevision: SETTINGS_REVISION, idempotencyKey, fields: {} }))
      .rejects.toThrow();
    expect(requests).toHaveLength(2);
  });
});
