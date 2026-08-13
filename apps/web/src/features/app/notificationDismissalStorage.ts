import {
  MAX_NOTIFICATION_REVISION_HISTORY,
  type NotificationDismissalLedgerEntry,
  type NotificationLifecyclePhase,
} from "./notificationCenter";

export const NOTIFICATION_DISMISSAL_STORAGE_KEY =
  "railwait:notification-dismissals:v1";

const STORAGE_VERSION = 1;
const MAX_KEY_LENGTH = 512;
const MAX_TIMESTAMP_LENGTH = 64;
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function boundedNonEmptyText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string" || value.length > maxLength || !value.trim()) return null;
  return value;
}

function lifecyclePhase(value: unknown): NotificationLifecyclePhase | null {
  if (value === 0 || value === 1 || value === 2) return value;
  return null;
}

function revisionInstant(value: unknown): string | null | undefined {
  if (value === null) return null;
  const timestamp = boundedNonEmptyText(value, MAX_TIMESTAMP_LENGTH);
  if (timestamp === null || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp)) return undefined;
  const instant = Date.parse(timestamp);
  if (!Number.isFinite(instant) || instant > Date.now() + MAX_FUTURE_SKEW_MS) return undefined;
  return timestamp;
}

function dismissalEntry(value: unknown): NotificationDismissalLedgerEntry | null {
  if (!isRecord(value)) return null;
  const subjectKey = boundedNonEmptyText(value.subjectKey, MAX_KEY_LENGTH);
  const revisionKey = boundedNonEmptyText(value.revisionKey, MAX_KEY_LENGTH);
  const revisionAt = revisionInstant(value.revisionAt);
  const phase = lifecyclePhase(value.lifecyclePhase);
  if (
    subjectKey === null
    || revisionKey === null
    || revisionAt === undefined
    || phase === null
  ) return null;
  return { subjectKey, revisionKey, revisionAt, lifecyclePhase: phase };
}

function normalizedLedger(
  entries: ReadonlyArray<NotificationDismissalLedgerEntry>,
): ReadonlyArray<NotificationDismissalLedgerEntry> {
  const byRevision = new Map<string, NotificationDismissalLedgerEntry>();
  for (const rawEntry of entries) {
    const entry = dismissalEntry(rawEntry);
    if (entry === null) continue;
    byRevision.delete(entry.revisionKey);
    byRevision.set(entry.revisionKey, entry);
  }
  return [...byRevision.values()].slice(-MAX_NOTIFICATION_REVISION_HISTORY);
}

function browserLocalStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadNotificationDismissalLedger(): ReadonlyArray<NotificationDismissalLedgerEntry> {
  const storage = browserLocalStorage();
  if (storage === null) return [];
  try {
    const raw = storage.getItem(NOTIFICATION_DISMISSAL_STORAGE_KEY);
    if (raw === null) return [];
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value) || value.version !== STORAGE_VERSION || !Array.isArray(value.entries)) {
      return [];
    }
    const entries = value.entries.flatMap((entry) => {
      const parsed = dismissalEntry(entry);
      return parsed === null ? [] : [parsed];
    });
    return normalizedLedger(entries);
  } catch {
    return [];
  }
}

export function saveNotificationDismissalLedger(
  entries: ReadonlyArray<NotificationDismissalLedgerEntry>,
): void {
  const storage = browserLocalStorage();
  if (storage === null) return;
  try {
    const merged = normalizedLedger([
      ...loadNotificationDismissalLedger(),
      ...entries,
    ]);
    storage.setItem(NOTIFICATION_DISMISSAL_STORAGE_KEY, JSON.stringify({
      version: STORAGE_VERSION,
      entries: merged,
    }));
  } catch {
    // Storage denial or quota exhaustion must not break the in-memory notification center.
  }
}
