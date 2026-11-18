import { request } from "./client";

export interface SystemUpdate {
  managed: boolean;
  managerAvailable: boolean;
  currentVersion: string | null;
  latestVersion: string | null;
  planId: string | null;
  available: boolean;
  reason: string;
}
export type UpdateJobState = "queued" | "running" | "succeeded" | "failed" | "deferred" | "recovery_required" | "rejected";
export interface UpdateJob { id: string; state: UpdateJobState; stage: string }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("업데이트 응답을 확인하지 못했습니다.");
  return Object.fromEntries(Object.entries(value));
}
function version(value: unknown): string | null {
  return typeof value === "string" && /^\d+\.\d+\.\d+$/.test(value) ? value : null;
}
export function mapSystemUpdate(value: unknown): SystemUpdate {
  const item = record(value);
  if (item.schema !== 1 || typeof item.managed !== "boolean" || typeof item.manager_available !== "boolean" || typeof item.available !== "boolean") throw new Error("업데이트 응답 형식이 올바르지 않습니다.");
  const planId = typeof item.plan_id === "string" && /^[a-f0-9]{64}$/.test(item.plan_id) ? item.plan_id : null;
  const latestVersion = version(item.latest_version);
  return {
    managed: item.managed,
    managerAvailable: item.manager_available,
    currentVersion: version(item.current_version),
    latestVersion,
    planId,
    available: item.reason === "update_available" && item.available && item.managed && item.manager_available && planId !== null && latestVersion !== null,
    reason: typeof item.reason === "string" ? item.reason : "update_blocked",
  };
}
function jobState(value: unknown): UpdateJobState {
  switch (value) {
    case "queued": case "running": case "succeeded": case "failed": case "deferred": case "recovery_required": case "rejected": return value;
    default: throw new Error("업데이트 작업 상태를 확인하지 못했습니다.");
  }
}
export function mapUpdateJob(value: unknown): UpdateJob {
  const item = record(value);
  if (item.schema !== 1 || typeof item.id !== "string" || !/^[a-f0-9]{32}$/.test(item.id)) throw new Error("업데이트 작업 응답이 올바르지 않습니다.");
  return { id: item.id, state: jobState(item.state), stage: typeof item.stage === "string" ? item.stage : "queued" };
}
export async function fetchSystemUpdate(signal?: AbortSignal): Promise<SystemUpdate> {
  return mapSystemUpdate(await request("/system/update", { signal: signal ?? null }));
}
export async function submitSystemUpdate(target: string, plan: string, key: string): Promise<UpdateJob> {
  return mapUpdateJob(await request("/system/update", {
    method: "POST", body: JSON.stringify({ target_release_id: target, plan_id: plan, idempotency_key: key }),
  }));
}
export async function fetchUpdateJob(id: string, signal?: AbortSignal): Promise<UpdateJob> {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("작업 ID가 올바르지 않습니다.");
  return mapUpdateJob(await request(`/system/update/jobs/${id}`, { signal: signal ?? null }));
}
