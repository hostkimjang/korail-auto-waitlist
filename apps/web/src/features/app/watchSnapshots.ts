import {
  mapCompatibleWatchLifecycleSnapshot,
  type LegacyWatchSnapshot,
  type WatchLifecycleSnapshot,
} from "./watchLifecycleSnapshot";

export type { LegacyWatchSnapshot as WatchSnapshot } from "./watchLifecycleSnapshot";

export interface SeatFoundTransition {
  id: string;
  provider: string;
  route: string;
  train: string;
  seatClassLabel: string;
  date: string;
  departure: string;
  arrival: string;
  revision?: string;
  revisionAt?: string;
  detectedAt?: string;
  startedAt?: string;
  finishedAt?: string;
  reservationPolicy?: string;
  paymentDeadline?: string | null;
  reservationProgress?: ReadonlyArray<ReservationProgressStage>;
}

export type ReservationProgressStageName =
  | "authenticated_session_ready"
  | "target_rechecked"
  | "seat_selected"
  | "reservation_requested";

export interface ReservationProgressStage {
  stage: ReservationProgressStageName;
  occurredAt: string;
}

export type SeatAvailabilityLostTransition = SeatFoundTransition;

export interface WatchActionTransition extends SeatFoundTransition {
  status:
    | "reserving"
    | "payment_required"
    | "payment_hold_ended"
    | "auth_required"
    | "authentication_recovered"
    | "failed"
    | "monitoring_resumed";
  automaticReservationRetry?: boolean;
  paymentHoldEndReason?:
    | "confirmed_payment_deadline_elapsed"
    | "confirmed_payment_hold_no_longer_present";
}

type TransitionStage = "seat_found" | "availability_lost" | WatchActionTransition["status"];

function attemptTimestamp(
  watch: WatchLifecycleSnapshot,
  field: "startedAt" | "finishedAt" | "paymentHoldEndedAt",
): string | undefined {
  const attempt = watch.latestReservationAttempt;
  if (attempt === null) return undefined;
  return attempt[field] ?? undefined;
}

function attemptPaymentHoldEndReason(
  watch: WatchLifecycleSnapshot,
): WatchActionTransition["paymentHoldEndReason"] | undefined {
  return watch.latestReservationAttempt?.paymentHoldEndReason ?? undefined;
}

function transitionRevisionAt(
  watch: WatchLifecycleSnapshot,
  stage: TransitionStage,
): string | undefined {
  if (stage === "seat_found") {
    return watch.seatFoundObservation?.observedAt ?? undefined;
  }
  if (stage === "reserving") {
    return attemptTimestamp(watch, "startedAt")
      ?? watch.updatedAt
      ?? undefined;
  }
  if (stage === "payment_hold_ended") {
    return attemptTimestamp(watch, "paymentHoldEndedAt")
      ?? watch.updatedAt
      ?? undefined;
  }
  if (["payment_required", "auth_required", "failed"].includes(stage)) {
    return attemptTimestamp(watch, "finishedAt")
      ?? attemptTimestamp(watch, "startedAt")
      ?? watch.updatedAt
      ?? undefined;
  }
  return watch.updatedAt ?? undefined;
}

function transitionContext(
  watch: WatchLifecycleSnapshot,
  stage: TransitionStage,
): SeatFoundTransition {
  const revisionAt = transitionRevisionAt(watch, stage);
  const detectedAt = watch.seatFoundObservation?.observedAt ?? undefined;
  const startedAt = attemptTimestamp(watch, "startedAt");
  const finishedAt = attemptTimestamp(watch, "finishedAt");
  return {
    id: watch.id,
    provider: watch.provider,
    route: watch.route,
    train: watch.train,
    seatClassLabel: watch.seatClassLabel,
    date: watch.date,
    departure: watch.departure,
    arrival: watch.arrival,
    revision: `${stage}:${revisionAt ?? "current"}`,
    ...(revisionAt === undefined ? {} : { revisionAt }),
    ...(detectedAt === undefined ? {} : { detectedAt }),
    ...(startedAt === undefined ? {} : { startedAt }),
    ...(finishedAt === undefined ? {} : { finishedAt }),
    reservationPolicy: watch.reservationPolicy,
    paymentDeadline: watch.paymentDeadline,
  };
}

const watchingStatuses: ReadonlySet<string> = new Set([
  "scheduled",
  "watching",
  "official_waitlist",
  "cooldown",
]);

function detectSeatFoundLifecycleTransitions(
  previous: ReadonlyArray<WatchLifecycleSnapshot>,
  next: ReadonlyArray<WatchLifecycleSnapshot>,
): SeatFoundTransition[] {
  const previousById = new Map(previous.map((watch) => [watch.id, watch]));
  return next.flatMap((watch) => {
    const previousWatch = previousById.get(watch.id);
    if (
      watch.status !== "seat_found"
      || !hasCurrentSeatAvailability(watch)
      || watch.reservationPolicy === "reserve_once_before_payment"
      || previousWatch === undefined
      || !watchingStatuses.has(previousWatch.status)
    ) return [];
    return [transitionContext(watch, "seat_found")];
  });
}

export function detectSeatFoundTransitions(
  previous: ReadonlyArray<WatchLifecycleSnapshot>,
  next: ReadonlyArray<WatchLifecycleSnapshot>,
): SeatFoundTransition[];
export function detectSeatFoundTransitions(
  previous: ReadonlyArray<LegacyWatchSnapshot>,
  next: ReadonlyArray<LegacyWatchSnapshot>,
): SeatFoundTransition[];
export function detectSeatFoundTransitions(
  previous: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
  next: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
): SeatFoundTransition[] {
  return detectSeatFoundLifecycleTransitions(
    previous.map(mapCompatibleWatchLifecycleSnapshot),
    next.map(mapCompatibleWatchLifecycleSnapshot),
  );
}

function hasCurrentSeatAvailability(watch: WatchLifecycleSnapshot): boolean {
  return watch.seatFoundObservation !== null;
}

function detectSeatAvailabilityLostLifecycleTransitions(
  previous: ReadonlyArray<WatchLifecycleSnapshot>,
  next: ReadonlyArray<WatchLifecycleSnapshot>,
): SeatAvailabilityLostTransition[] {
  const nextById = new Map(next.map((watch) => [watch.id, watch]));
  return previous.flatMap((watch) => {
    const nextWatch = nextById.get(watch.id);
    if (
      !hasCurrentSeatAvailability(watch)
      || nextWatch === undefined
      || hasCurrentSeatAvailability(nextWatch)
      || !watchingStatuses.has(nextWatch.status)
    ) return [];
    return [transitionContext(nextWatch, "availability_lost")];
  });
}

export function detectSeatAvailabilityLostTransitions(
  previous: ReadonlyArray<WatchLifecycleSnapshot>,
  next: ReadonlyArray<WatchLifecycleSnapshot>,
): SeatAvailabilityLostTransition[];
export function detectSeatAvailabilityLostTransitions(
  previous: ReadonlyArray<LegacyWatchSnapshot>,
  next: ReadonlyArray<LegacyWatchSnapshot>,
): SeatAvailabilityLostTransition[];
export function detectSeatAvailabilityLostTransitions(
  previous: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
  next: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
): SeatAvailabilityLostTransition[] {
  return detectSeatAvailabilityLostLifecycleTransitions(
    previous.map(mapCompatibleWatchLifecycleSnapshot),
    next.map(mapCompatibleWatchLifecycleSnapshot),
  );
}

const actionStatuses: ReadonlySet<WatchActionTransition["status"]> = new Set([
  "reserving",
  "payment_required",
  "auth_required",
  "failed",
]);

const authenticationRecoveredStatuses: ReadonlySet<string> = new Set([
  "scheduled",
  "watching",
]);

function detectWatchActionLifecycleTransitions(
  previous: ReadonlyArray<WatchLifecycleSnapshot>,
  next: ReadonlyArray<WatchLifecycleSnapshot>,
): WatchActionTransition[] {
  const previousById = new Map(previous.map((watch) => [watch.id, watch]));
  return next.flatMap((watch) => {
    const previousWatch = previousById.get(watch.id);
    if (previousWatch === undefined || previousWatch.status === watch.status) return [];
    const holdEndedAt = attemptTimestamp(watch, "paymentHoldEndedAt");
    const holdEndReason = attemptPaymentHoldEndReason(watch);
    if (
      previousWatch.status === "payment_required"
      && (watch.status === "watching" || watch.status === "expired")
      && holdEndedAt !== undefined
      && holdEndReason !== undefined
    ) {
      return [{
        ...transitionContext(watch, "payment_hold_ended"),
        status: "payment_hold_ended" as const,
        automaticReservationRetry: watch.status === "watching",
        paymentHoldEndReason: holdEndReason,
      }];
    }
    if (
      previousWatch.status === "auth_required"
      && authenticationRecoveredStatuses.has(watch.status)
    ) {
      return [{
        ...transitionContext(watch, "authentication_recovered"),
        status: "authentication_recovered" as const,
      }];
    }
    if (!actionStatuses.has(watch.status as WatchActionTransition["status"])) return [];
    return [{
      ...transitionContext(watch, watch.status as WatchActionTransition["status"]),
      status: watch.status as WatchActionTransition["status"],
    }];
  });
}

export function detectWatchActionTransitions(
  previous: ReadonlyArray<WatchLifecycleSnapshot>,
  next: ReadonlyArray<WatchLifecycleSnapshot>,
): WatchActionTransition[];
export function detectWatchActionTransitions(
  previous: ReadonlyArray<LegacyWatchSnapshot>,
  next: ReadonlyArray<LegacyWatchSnapshot>,
): WatchActionTransition[];
export function detectWatchActionTransitions(
  previous: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
  next: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
): WatchActionTransition[] {
  return detectWatchActionLifecycleTransitions(
    previous.map(mapCompatibleWatchLifecycleSnapshot),
    next.map(mapCompatibleWatchLifecycleSnapshot),
  );
}

function sameSnapshot<T>(left: T, right: T): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function reconcileWatchSnapshots<T extends { id: string }>(
  previous: ReadonlyArray<T>,
  next: ReadonlyArray<T>,
): ReadonlyArray<T> {
  const previousById = new Map(previous.map((watch) => [watch.id, watch]));
  const reconciled = next.map((watch) => {
    const current = previousById.get(watch.id);
    return current !== undefined && sameSnapshot(current, watch) ? current : watch;
  });
  const unchanged = previous.length === reconciled.length
    && previous.every((watch, index) => watch === reconciled[index]);
  return unchanged ? previous : reconciled;
}
