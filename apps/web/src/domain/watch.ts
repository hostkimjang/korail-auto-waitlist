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
