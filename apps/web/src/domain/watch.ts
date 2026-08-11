import type { SeatClassId } from "./seatClasses";

export type WatchSeatClass = SeatClassId | "any";
export type WatchProvider = "KORAIL" | "SRT" | "MOCK";
export type WatchStatus =
  | "draft"
  | "scheduled"
  | "watching"
  | "official_waitlist"
  | "seat_found"
  | "reserving"
  | "payment_required"
  | "completed"
  | "paused"
  | "cooldown"
  | "auth_required"
  | "expired"
  | "failed";
export type WatchObservationMode = "balanced" | "focused";
export type WatchObservationExecutionState = "idle" | "in_progress";

const WATCH_STATUSES: readonly WatchStatus[] = [
  "draft",
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
  "reserving",
  "payment_required",
  "completed",
  "paused",
  "cooldown",
  "auth_required",
  "expired",
  "failed",
];

export function normalizeWatchProvider(value: unknown): WatchProvider | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toUpperCase();
  if (normalized === "KORAIL" || normalized === "SRT" || normalized === "MOCK") {
    return normalized;
  }
  return null;
}

export function isWatchStatus(value: unknown): value is WatchStatus {
  return typeof value === "string" && WATCH_STATUSES.some((status) => status === value);
}

export function isWatchSeatClass(value: unknown): value is WatchSeatClass {
  return value === "standard" || value === "first" || value === "any";
}

export function normalizeWatchObservationExecutionState(
  value: unknown,
): WatchObservationExecutionState {
  return value === "in_progress" ? "in_progress" : "idle";
}

export function formatTrainIdentity(trainType: string | null | undefined, train: string): string {
  const normalizedType = trainType?.trim() ?? "";
  if (!normalizedType) return train;
  const comparableType = normalizedType.replace(/[\s-]/g, "").toLocaleLowerCase("ko-KR");
  const comparableTrain = train.replace(/[\s-]/g, "").toLocaleLowerCase("ko-KR");
  return comparableTrain.startsWith(comparableType) ? train : `${normalizedType} · ${train}`;
}
