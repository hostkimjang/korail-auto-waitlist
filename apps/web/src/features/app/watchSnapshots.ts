export interface WatchSnapshot {
  id: string;
  status: string;
  provider?: string;
  route?: string;
  train?: string;
  seatClassLabel?: string;
  date?: string;
  departure?: string;
  arrival?: string;
  [key: string]: unknown;
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function attemptTimestamp(
  watch: WatchSnapshot,
  field: "startedAt" | "finishedAt" | "paymentHoldEndedAt",
): string | undefined {
  const attempt = watch.latestReservationAttempt;
  if (!isRecord(attempt)) return undefined;
  const value = attempt[field];
  return typeof value === "string" && value ? value : undefined;
}

function attemptPaymentHoldEndReason(
  watch: WatchSnapshot,
): WatchActionTransition["paymentHoldEndReason"] | undefined {
  const attempt = watch.latestReservationAttempt;
  if (!isRecord(attempt)) return undefined;
  const value = attempt.paymentHoldEndReason;
  return value === "confirmed_payment_deadline_elapsed"
    || value === "confirmed_payment_hold_no_longer_present"
    ? value
    : undefined;
}

function transitionRevisionAt(
  watch: WatchSnapshot,
  stage: TransitionStage,
): string | undefined {
  if (stage === "seat_found") {
    const observation = watch.seatFoundObservation;
    if (typeof observation === "object" && observation !== null && "observedAt" in observation) {
      const observedAt = observation.observedAt;
      if (typeof observedAt === "string" && observedAt) return observedAt;
    }
  }
  if (stage === "reserving") {
    return attemptTimestamp(watch, "startedAt")
      ?? (typeof watch.updated_at === "string" && watch.updated_at ? watch.updated_at : undefined);
  }
  if (stage === "payment_hold_ended") {
    return attemptTimestamp(watch, "paymentHoldEndedAt")
      ?? (typeof watch.updated_at === "string" && watch.updated_at ? watch.updated_at : undefined);
  }
  if (["payment_required", "auth_required", "failed"].includes(stage)) {
    return attemptTimestamp(watch, "finishedAt")
      ?? attemptTimestamp(watch, "startedAt")
      ?? (typeof watch.updated_at === "string" && watch.updated_at ? watch.updated_at : undefined);
  }
  return typeof watch.updated_at === "string" && watch.updated_at ? watch.updated_at : undefined;
}

function transitionContext(watch: WatchSnapshot, stage: TransitionStage): SeatFoundTransition {
  const revisionAt = transitionRevisionAt(watch, stage);
  const observation = isRecord(watch.seatFoundObservation)
    ? watch.seatFoundObservation.observedAt
    : undefined;
  const detectedAt = typeof observation === "string" && observation ? observation : undefined;
  const startedAt = attemptTimestamp(watch, "startedAt");
  const finishedAt = attemptTimestamp(watch, "finishedAt");
  return {
    id: watch.id,
    provider: watch.provider ?? "철도",
    route: watch.route ?? "여정 정보 없음",
    train: watch.train ?? "열차 정보 없음",
    seatClassLabel: watch.seatClassLabel ?? "좌석",
    date: watch.date ?? "날짜 미정",
    departure: watch.departure ?? "--:--",
    arrival: watch.arrival ?? "--:--",
    revision: `${stage}:${revisionAt ?? "current"}`,
    ...(revisionAt === undefined ? {} : { revisionAt }),
    ...(detectedAt === undefined ? {} : { detectedAt }),
    ...(startedAt === undefined ? {} : { startedAt }),
    ...(finishedAt === undefined ? {} : { finishedAt }),
    reservationPolicy: typeof watch.reservationPolicy === "string"
      ? watch.reservationPolicy
      : "notify_only",
    paymentDeadline: typeof watch.payment_deadline === "string"
      ? watch.payment_deadline
      : null,
  };
}

const watchingStatuses: ReadonlySet<string> = new Set([
  "scheduled",
  "watching",
  "official_waitlist",
  "cooldown",
]);

export function detectSeatFoundTransitions(
  previous: ReadonlyArray<WatchSnapshot>,
  next: ReadonlyArray<WatchSnapshot>,
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

function hasCurrentSeatAvailability(watch: WatchSnapshot): boolean {
  return typeof watch.seatFoundObservation === "object"
    && watch.seatFoundObservation !== null;
}

export function detectSeatAvailabilityLostTransitions(
  previous: ReadonlyArray<WatchSnapshot>,
  next: ReadonlyArray<WatchSnapshot>,
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

export function detectWatchActionTransitions(
  previous: ReadonlyArray<WatchSnapshot>,
  next: ReadonlyArray<WatchSnapshot>,
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

function sameSnapshot(left: WatchSnapshot, right: WatchSnapshot): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function reconcileWatchSnapshots<T extends WatchSnapshot>(
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
