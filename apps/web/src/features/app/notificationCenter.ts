import type { AppToastInput, AppToastNotice } from "../../shared/ui/AppToast";

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
  announcement: string;
  announcementMode: NotificationAnnouncement;
  sequence: number;
}

export type NotificationCenterAction =
  | { type: "push"; inputs: ReadonlyArray<AppNotificationInput> }
  | { type: "dismiss"; id: string }
  | { type: "dismiss_group"; kind: NotificationKind }
  | { type: "dismiss_timed" }
  | { type: "clear" };

const DEFAULTS: Record<NotificationKind, {
  priority: number;
  persistence: NotificationPersistence;
  announcement: NotificationAnnouncement;
}> = {
  payment_required: { priority: 100, persistence: "sticky", announcement: "assertive" },
  manual_check: { priority: 95, persistence: "sticky", announcement: "assertive" },
  auth_required: { priority: 90, persistence: "sticky", announcement: "assertive" },
  seat_found: { priority: 80, persistence: "sticky", announcement: "polite" },
  reserving: { priority: 60, persistence: "sticky", announcement: "polite" },
  recovery: { priority: 40, persistence: "timed", announcement: "polite" },
  generic: { priority: 10, persistence: "timed", announcement: "polite" },
};

const MAX_SEEN_REVISIONS = 240;

export const initialNotificationCenterState: NotificationCenterState = {
  notices: [],
  seenRevisionKeys: [],
  announcement: "",
  announcementMode: "polite",
  sequence: 0,
};

function validSortInstant(value: string | null | undefined): number | null {
  if (!value) return null;
  const instant = Date.parse(value);
  return Number.isFinite(instant) ? instant : null;
}

function validDuration(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
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
  const bySubject = new Map(state.notices.map((notice) => [notice.subjectKey, notice]));
  const added: AppNotificationNotice[] = [];
  let sequence = state.sequence;

  for (const input of inputs) {
    const { autoCloseMs, ...content } = input;
    const kind = input.kind ?? "generic";
    const defaults = DEFAULTS[kind];
    const nextSequence = sequence + 1;
    const subjectKey = input.subjectKey ?? input.key ?? `generic:${nextSequence}`;
    const revisionKey = input.revisionKey ?? `${subjectKey}:${nextSequence}`;
    if (seen.has(revisionKey)) continue;
    const existing = bySubject.get(subjectKey);
    const incomingRevisionAt = validSortInstant(input.revisionAt);
    const existingRevisionAt = validSortInstant(existing?.revisionAt);
    if (
      existing !== undefined
      && incomingRevisionAt !== null
      && existingRevisionAt !== null
      && incomingRevisionAt <= existingRevisionAt
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
    const durationMs = validDuration(input.durationMs) ?? calculatedDuration;
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
        : autoCloseMs === undefined
          ? {}
          : { autoCloseMs }),
      sortAt: input.sortAt ?? null,
      revisionAt: input.revisionAt ?? null,
      occurredAt,
      startedAt,
      durationMs,
      sequence,
    };
    bySubject.set(subjectKey, notice);
    added.push(notice);
  }

  if (added.length === 0) {
    return { ...state, sequence };
  }
  const seenRevisionKeys = [...seen].slice(-MAX_SEEN_REVISIONS);
  const notices = [...bySubject.values()].sort(compareNotifications);
  return {
    notices,
    seenRevisionKeys,
    announcement: announcementFor(added),
    announcementMode: added.some((notice) => notice.announcement === "assertive")
      ? "assertive"
      : "polite",
    sequence,
  };
}

export function notificationCenterReducer(
  state: NotificationCenterState,
  action: NotificationCenterAction,
): NotificationCenterState {
  switch (action.type) {
    case "push":
      return pushNotifications(state, action.inputs);
    case "dismiss":
      return { ...state, notices: state.notices.filter((notice) => notice.id !== action.id) };
    case "dismiss_group":
      return { ...state, notices: state.notices.filter((notice) => notice.kind !== action.kind) };
    case "dismiss_timed":
      return {
        ...state,
        notices: state.notices.filter((notice) => notice.persistence === "sticky"),
      };
    case "clear":
      return { ...state, notices: [] };
  }
}
