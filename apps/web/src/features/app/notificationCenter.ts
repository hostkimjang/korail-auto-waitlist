import {
  IMPORTANT_TOAST_AUTO_CLOSE_MS,
  TOAST_AUTO_CLOSE_MS,
  type AppToastInput,
  type AppToastNotice,
} from "../../shared/ui/AppToast";

export type NotificationKind =
  | "payment_required"
  | "manual_check"
  | "auth_required"
  | "seat_found"
  | "reserving"
  | "recovery"
  | "generic";

export type NotificationPersistence = "sticky" | "timed";
export type NotificationAnnouncement = "assertive" | "polite";

export interface AppNotificationInput extends AppToastInput {
  subjectKey?: string;
  revisionKey?: string;
  kind?: NotificationKind;
  priority?: number;
  persistence?: NotificationPersistence;
  announcement?: NotificationAnnouncement;
  sortAt?: string | null;
  revisionAt?: string | null;
}

export interface AppNotificationNotice extends AppToastNotice {
  subjectKey: string;
  revisionKey: string;
  kind: NotificationKind;
  priority: number;
  persistence: NotificationPersistence;
  announcement: NotificationAnnouncement;
  sortAt: string | null;
  revisionAt: string | null;
  occurredAt: string | null;
  startedAt: string | null;
  durationMs: number | null;
  sequence: number;
}

export interface NotificationCenterState {
  notices: ReadonlyArray<AppNotificationNotice>;
  seenRevisionKeys: ReadonlyArray<string>;
  subjectRevisionWatermarks: ReadonlyArray<NotificationSubjectRevisionWatermark>;
  dismissalLedger: ReadonlyArray<NotificationDismissalLedgerEntry>;
  announcement: string;
  announcementMode: NotificationAnnouncement;
  sequence: number;
}

interface NotificationSubjectRevisionWatermark {
  subjectKey: string;
  revisionAt: string;
  lifecyclePhase: NotificationLifecyclePhase;
}

export type NotificationLifecyclePhase = 0 | 1 | 2;

export interface NotificationDismissalLedgerEntry {
  subjectKey: string;
  revisionKey: string;
  revisionAt: string | null;
  lifecyclePhase: NotificationLifecyclePhase;
}

export type NotificationCenterAction =
  | { type: "push"; inputs: ReadonlyArray<AppNotificationInput> }
  | { type: "dismiss"; id: string }
  | { type: "dismiss_group"; kind: NotificationKind }
  | { type: "dismiss_timed" }
  | { type: "prune_stale_subjects"; subjectKeys: ReadonlyArray<string> }
  | { type: "clear" };

const DEFAULTS: Record<NotificationKind, {
  priority: number;
  persistence: NotificationPersistence;
  announcement: NotificationAnnouncement;
  autoCloseMs: number | null;
}> = {
  payment_required: {
    priority: 100,
    persistence: "sticky",
    announcement: "assertive",
    autoCloseMs: null,
  },
  manual_check: {
    priority: 95,
    persistence: "sticky",
    announcement: "assertive",
    autoCloseMs: null,
  },
  auth_required: {
    priority: 90,
    persistence: "sticky",
    announcement: "assertive",
    autoCloseMs: null,
  },
  seat_found: {
    priority: 80,
    persistence: "sticky",
    announcement: "polite",
    autoCloseMs: null,
  },
  reserving: {
    priority: 60,
    persistence: "sticky",
    announcement: "polite",
    autoCloseMs: null,
  },
  recovery: {
    priority: 40,
    persistence: "timed",
    announcement: "polite",
    autoCloseMs: IMPORTANT_TOAST_AUTO_CLOSE_MS,
  },
  generic: {
    priority: 10,
    persistence: "timed",
    announcement: "polite",
    autoCloseMs: TOAST_AUTO_CLOSE_MS,
  },
};

export const MAX_NOTIFICATION_REVISION_HISTORY = 240;

function dismissalWatermarks(
  ledger: ReadonlyArray<NotificationDismissalLedgerEntry>,
): ReadonlyArray<NotificationSubjectRevisionWatermark> {
  const bySubject = new Map<string, NotificationSubjectRevisionWatermark>();
  for (const entry of ledger) {
    if (entry.revisionAt === null) continue;
    const current = bySubject.get(entry.subjectKey);
    const currentInstant = validSortInstant(current?.revisionAt);
    const entryInstant = validSortInstant(entry.revisionAt);
    if (entryInstant === null) continue;
    if (
      currentInstant === null
      || entryInstant > currentInstant
      || (
        entryInstant === currentInstant
        && current !== undefined
        && entry.lifecyclePhase > current.lifecyclePhase
      )
    ) {
      bySubject.set(entry.subjectKey, {
        subjectKey: entry.subjectKey,
        revisionAt: entry.revisionAt,
        lifecyclePhase: entry.lifecyclePhase,
      });
    }
  }
  return [...bySubject.values()].slice(-MAX_NOTIFICATION_REVISION_HISTORY);
}

export function createInitialNotificationCenterState(
  dismissalLedger: ReadonlyArray<NotificationDismissalLedgerEntry> = [],
): NotificationCenterState {
  const boundedLedger = dismissalLedger.slice(-MAX_NOTIFICATION_REVISION_HISTORY);
  return {
    notices: [],
    seenRevisionKeys: boundedLedger.map((entry) => entry.revisionKey),
    subjectRevisionWatermarks: dismissalWatermarks(boundedLedger),
    dismissalLedger: boundedLedger,
    announcement: "",
    announcementMode: "polite",
    sequence: 0,
  };
}

export const initialNotificationCenterState = createInitialNotificationCenterState();

function validSortInstant(value: string | null | undefined): number | null {
  if (!value) return null;
  const instant = Date.parse(value);
  return Number.isFinite(instant) ? instant : null;
}

function validDuration(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function notificationLifecyclePhasePriority(
  kind: NotificationKind,
): NotificationLifecyclePhase {
  if (kind === "reserving") return 1;
  if (
    kind === "payment_required"
    || kind === "manual_check"
    || kind === "auth_required"
    || kind === "recovery"
  ) return 2;
  return 0;
}

export function compareNotifications(
  left: AppNotificationNotice,
  right: AppNotificationNotice,
): number {
  if (left.priority !== right.priority) return right.priority - left.priority;
  const leftSort = validSortInstant(left.sortAt);
  const rightSort = validSortInstant(right.sortAt);
  if (leftSort !== null || rightSort !== null) {
    if (leftSort === null) return 1;
    if (rightSort === null) return -1;
    if (leftSort !== rightSort) return leftSort - rightSort;
  }
  return left.sequence - right.sequence;
}

function announcementFor(notices: ReadonlyArray<AppNotificationNotice>): string {
  const counts = new Map<NotificationKind, number>();
  for (const notice of notices) counts.set(notice.kind, (counts.get(notice.kind) ?? 0) + 1);
  const labels: Record<NotificationKind, string> = {
    payment_required: "결제 필요",
    manual_check: "결과 확인 필요",
    auth_required: "로그인 확인 필요",
    seat_found: "좌석 발견",
    reserving: "예매 진행",
    recovery: "감시 상태 변경",
    generic: "일반 알림",
  };
  return [...counts.entries()].map(([kind, count]) => `${labels[kind]} ${count}건`).join(", ");
}

export function pushNotifications(
  state: NotificationCenterState,
  inputs: ReadonlyArray<AppNotificationInput>,
): NotificationCenterState {
  if (inputs.length === 0) return state;
  const seen = new Set(state.seenRevisionKeys);
  const dismissedRevisionKeys = new Set(
    state.dismissalLedger.map((entry) => entry.revisionKey),
  );
  const bySubject = new Map(state.notices.map((notice) => [notice.subjectKey, notice]));
  const watermarks = new Map(
    [
      ...dismissalWatermarks(state.dismissalLedger),
      ...state.subjectRevisionWatermarks,
    ].map((watermark) => [watermark.subjectKey, watermark]),
  );
  const added: AppNotificationNotice[] = [];
  let sequence = state.sequence;

  for (const input of inputs) {
    const { autoCloseMs, ...content } = input;
    const kind = input.kind ?? "generic";
    const defaults = DEFAULTS[kind];
    const nextSequence = sequence + 1;
    const subjectKey = input.subjectKey ?? input.key ?? `generic:${nextSequence}`;
    const revisionKey = input.revisionKey ?? `${subjectKey}:${nextSequence}`;
    if (seen.has(revisionKey) || dismissedRevisionKeys.has(revisionKey)) continue;
    const existing = bySubject.get(subjectKey);
    const incomingRevisionAt = validSortInstant(input.revisionAt);
    const watermark = watermarks.get(subjectKey);
    const existingRevisionAt = validSortInstant(existing?.revisionAt)
      ?? validSortInstant(watermark?.revisionAt);
    const existingLifecyclePhase = existing === undefined
      ? watermark?.lifecyclePhase ?? 0
      : notificationLifecyclePhasePriority(existing.kind);
    const incomingLifecyclePhase = notificationLifecyclePhasePriority(kind);
    const isOlderRevision = incomingRevisionAt !== null
      && existingRevisionAt !== null
      && incomingRevisionAt < existingRevisionAt;
    const replacesVisibleEqualPhaseRevision = existing !== undefined
      && incomingLifecyclePhase === existingLifecyclePhase
      && revisionKey !== existing.revisionKey;
    const losesEqualTimeLifecycleTie = incomingRevisionAt !== null
      && existingRevisionAt !== null
      && incomingRevisionAt === existingRevisionAt
      && (
        incomingLifecyclePhase < existingLifecyclePhase
        || (
          incomingLifecyclePhase === existingLifecyclePhase
          && !replacesVisibleEqualPhaseRevision
        )
      );
    if (
      existingRevisionAt !== null
      && (isOlderRevision || losesEqualTimeLifecycleTie)
    ) {
      seen.add(revisionKey);
      continue;
    }
    sequence = nextSequence;
    seen.add(revisionKey);
    const persistence = input.persistence ?? defaults.persistence;
    const occurredAtValue = input.occurredAt ?? input.revisionAt ?? null;
    const occurredAt = validSortInstant(occurredAtValue) === null ? null : occurredAtValue;
    const inputStartedAt = validSortInstant(input.startedAt) === null ? null : input.startedAt ?? null;
    const startedAt = inputStartedAt
      ?? existing?.startedAt
      ?? (kind === "reserving" ? occurredAt : null);
    const calculatedDuration = kind !== "reserving"
      && startedAt !== null
      && occurredAt !== null
      ? Math.max(0, Date.parse(occurredAt) - Date.parse(startedAt))
      : null;
    const durationMs = Object.prototype.hasOwnProperty.call(input, "durationMs")
      ? validDuration(input.durationMs)
      : calculatedDuration;
    const existingSteps = new Map(existing?.steps?.map((step) => [step.label, step]) ?? []);
    const steps = input.steps?.map((step) => {
      const previousStep = existingSteps.get(step.label);
      const durationPrefix = step.durationPrefix ?? previousStep?.durationPrefix;
      return {
        ...step,
        occurredAt: step.occurredAt ?? previousStep?.occurredAt ?? null,
        durationMs: validDuration(step.durationMs) ?? validDuration(previousStep?.durationMs),
        ...(durationPrefix === undefined ? {} : { durationPrefix }),
      };
    });
    const notice: AppNotificationNotice = {
      ...content,
      ...(steps === undefined ? {} : { steps }),
      id: `notification-${sequence}`,
      subjectKey,
      revisionKey,
      kind,
      priority: input.priority ?? defaults.priority,
      persistence,
      announcement: input.announcement ?? defaults.announcement,
      ...(persistence === "sticky"
        ? { autoCloseMs: null }
        : { autoCloseMs: autoCloseMs ?? defaults.autoCloseMs }),
      sortAt: input.sortAt ?? null,
      revisionAt: input.revisionAt ?? null,
      occurredAt,
      startedAt,
      durationMs,
      sequence,
    };
    bySubject.set(subjectKey, notice);
    if (incomingRevisionAt !== null && typeof input.revisionAt === "string") {
      watermarks.set(subjectKey, {
        subjectKey,
        revisionAt: input.revisionAt,
        lifecyclePhase: notificationLifecyclePhasePriority(kind),
      });
    }
    added.push(notice);
  }

  const seenRevisionKeys = [...seen].slice(-MAX_NOTIFICATION_REVISION_HISTORY);
  const subjectRevisionWatermarks = [...watermarks.values()]
    .slice(-MAX_NOTIFICATION_REVISION_HISTORY);
  if (added.length === 0) {
    return { ...state, seenRevisionKeys, subjectRevisionWatermarks, sequence };
  }
  const notices = [...bySubject.values()].sort(compareNotifications);
  return {
    notices,
    seenRevisionKeys,
    subjectRevisionWatermarks,
    dismissalLedger: state.dismissalLedger,
    announcement: announcementFor(added),
    announcementMode: added.some((notice) => notice.announcement === "assertive")
      ? "assertive"
      : "polite",
    sequence,
  };
}

function appendStickyDismissals(
  ledger: ReadonlyArray<NotificationDismissalLedgerEntry>,
  notices: ReadonlyArray<AppNotificationNotice>,
): ReadonlyArray<NotificationDismissalLedgerEntry> {
  const byRevision = new Map(ledger.map((entry) => [entry.revisionKey, entry]));
  for (const notice of notices) {
    if (notice.persistence !== "sticky") continue;
    const entry: NotificationDismissalLedgerEntry = {
      subjectKey: notice.subjectKey,
      revisionKey: notice.revisionKey,
      revisionAt: validSortInstant(notice.revisionAt) === null ? null : notice.revisionAt,
      lifecyclePhase: notificationLifecyclePhasePriority(notice.kind),
    };
    byRevision.delete(entry.revisionKey);
    byRevision.set(entry.revisionKey, entry);
  }
  return [...byRevision.values()].slice(-MAX_NOTIFICATION_REVISION_HISTORY);
}

function dismissNotices(
  state: NotificationCenterState,
  matches: (notice: AppNotificationNotice) => boolean,
): NotificationCenterState {
  const dismissed = state.notices.filter(matches);
  if (dismissed.length === 0) return state;
  return {
    ...state,
    notices: state.notices.filter((notice) => !matches(notice)),
    dismissalLedger: appendStickyDismissals(state.dismissalLedger, dismissed),
  };
}

function pruneStaleSubjects(
  state: NotificationCenterState,
  subjectKeys: ReadonlyArray<string>,
): NotificationCenterState {
  if (subjectKeys.length === 0) return state;
  const staleSubjects = new Set(subjectKeys);
  const notices = state.notices.filter((notice) => (
    notice.persistence !== "sticky" || !staleSubjects.has(notice.subjectKey)
  ));
  if (notices.length === state.notices.length) return state;
  return { ...state, notices };
}

export function notificationCenterReducer(
  state: NotificationCenterState,
  action: NotificationCenterAction,
): NotificationCenterState {
  switch (action.type) {
    case "push":
      return pushNotifications(state, action.inputs);
    case "dismiss":
      return dismissNotices(state, (notice) => notice.id === action.id);
    case "dismiss_group":
      return dismissNotices(state, (notice) => notice.kind === action.kind);
    case "dismiss_timed":
      return {
        ...state,
        notices: state.notices.filter((notice) => notice.persistence === "sticky"),
      };
    case "prune_stale_subjects":
      return pruneStaleSubjects(state, action.subjectKeys);
    case "clear":
      return createInitialNotificationCenterState(state.dismissalLedger);
  }
}
