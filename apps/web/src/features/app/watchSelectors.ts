const activeWatchStatuses: ReadonlySet<string> = new Set([
  "draft",
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
  "reserving",
  "paused",
  "cooldown",
  "auth_required",
]);

export interface WatchStatusCarrier {
  status: string;
}

export function isActiveWatch(watch: WatchStatusCarrier): boolean {
  return activeWatchStatuses.has(watch.status);
}
