import {
  mapCompatibleWatchLifecycleSnapshot,
  type LegacyWatchSnapshot,
  type WatchLifecycleSnapshot,
} from "./watchLifecycleSnapshot";
import type {
  ReservedSeat,
  ReservationProgressStage,
  ReservationRetryCondition,
} from "../../domain/reservationAttempt";

export type { LegacyWatchSnapshot as WatchSnapshot } from "./watchLifecycleSnapshot";
export type {
  ReservationProgressStage,
  ReservationProgressStageName,
} from "../../domain/reservationAttempt";

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
  reservedSeats?: ReadonlyArray<ReservedSeat>;
}

export type SeatAvailabilityLostTransition = SeatFoundTransition;

export type ReservationResultOutcome = "failed" | "not_available" | "unknown";

export interface ReservationRecoveryResult {
  outcome: ReservationResultOutcome;
  retryable: boolean;
  manualCheckRequired: boolean;
  retryCondition: ReservationRetryCondition | null;
}

export interface WatchActionTransition extends SeatFoundTransition {
  status:
    | "reserving"
    | "payment_required"
    | "payment_completed"
    | "payment_hold_ended"
    | "auth_required"
    | "authentication_recovered"
    | "failed"
    | "monitoring_resumed";
  automaticReservationRetry?: boolean;
  monitoringResumed?: boolean;
  reservationResult?: ReservationRecoveryResult;
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

function latestLifecycleTimestamp(
  ...values: ReadonlyArray<string | null | undefined>
): string | undefined {
  let latest: string | undefined;
  let latestInstant = Number.NEGATIVE_INFINITY;
  for (const value of values) {
    if (value === null || value === undefined) continue;
    const instant = Date.parse(value);
    if (!Number.isFinite(instant)) continue;
    if (instant > latestInstant) {
      latest = value;
      latestInstant = instant;
    }
  }
  return latest ?? values.find((value): value is string => value !== null && value !== undefined);
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
    return watch.latestReservationAttempt?.progressStages?.at(-1)?.occurredAt
      ?? attemptTimestamp(watch, "startedAt")
      ?? watch.updatedAt
      ?? undefined;
  }
  if (stage === "payment_hold_ended") {
    return attemptTimestamp(watch, "paymentHoldEndedAt")
      ?? watch.updatedAt
      ?? undefined;
  }
  if (stage === "payment_completed") {
    return watch.updatedAt
      ?? attemptTimestamp(watch, "finishedAt")
      ?? undefined;
  }
  if (stage === "payment_required") {
    return latestLifecycleTimestamp(
      attemptTimestamp(watch, "finishedAt"),
      watch.updatedAt,
    )
      ?? attemptTimestamp(watch, "startedAt")
      ?? undefined;
  }
  if (["auth_required", "failed", "monitoring_resumed"].includes(stage)) {
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
  const attemptCandidateId = watch.latestReservationAttemptCandidateId ?? null;
  const attemptCandidate = attemptCandidateId === null
    ? undefined
    : watch.reservationCandidateContexts[attemptCandidateId];
  const reservationProgress = watch.latestReservationAttempt?.progressStages ?? [];
  const reservedSeats = watch.latestReservationAttempt?.reservedSeats ?? [];
  return {
    id: watch.id,
    provider: watch.provider,
    route: watch.route,
    train: attemptCandidate?.train ?? watch.train,
    seatClassLabel: attemptCandidate?.seatClassLabel ?? watch.seatClassLabel,
    date: attemptCandidate?.date ?? watch.date,
    departure: attemptCandidate?.departure ?? watch.departure,
    arrival: attemptCandidate?.arrival ?? watch.arrival,
    revision: `${stage}:${revisionAt ?? "current"}`,
    ...(revisionAt === undefined ? {} : { revisionAt }),
    ...(detectedAt === undefined ? {} : { detectedAt }),
    ...(startedAt === undefined ? {} : { startedAt }),
    ...(finishedAt === undefined ? {} : { finishedAt }),
    ...(reservationProgress.length === 0 ? {} : { reservationProgress }),
    ...(reservedSeats.length === 0 ? {} : { reservedSeats }),
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

const hydratableActionStatuses: ReadonlySet<WatchActionTransition["status"]> = new Set([
  "reserving",
  "payment_required",
  "auth_required",
]);

function canonicalRecoveryResult(
  watch: WatchLifecycleSnapshot,
): ReservationRecoveryResult | null {
  const attempt = watch.latestReservationAttempt;
  if (
    (watch.status !== "watching" && watch.status !== "expired")
    || attempt === null
    || attempt.finishedAt === null
    || (
      attempt.outcome !== "failed"
      && attempt.outcome !== "not_available"
      && attempt.outcome !== "unknown"
    )
  ) return null;
  return {
    outcome: attempt.outcome,
    retryable: attempt.retryable,
    manualCheckRequired: attempt.manualCheckRequired,
    retryCondition: attempt.retryCondition,
  };
}

function canonicalRecoveryRevision(watch: WatchLifecycleSnapshot): string | null {
  const attempt = watch.latestReservationAttempt;
  const result = canonicalRecoveryResult(watch);
  if (attempt === null || attempt.finishedAt === null || result === null) return null;
  return [
    "monitoring_resumed",
    watch.latestReservationAttemptCandidateId ?? "candidate-unknown",
    attempt.startedAt ?? "start-unknown",
    attempt.finishedAt,
    result.outcome,
    result.retryable ? "retryable" : "not-retryable",
    result.manualCheckRequired ? "manual-check" : "automatic",
    result.retryCondition ?? "no-retry-condition",
  ].join(":");
}

function recoveryTransition(
  watch: WatchLifecycleSnapshot,
  result: ReservationRecoveryResult,
  revision: string,
): WatchActionTransition {
  return {
    ...transitionContext(watch, "monitoring_resumed"),
    status: "monitoring_resumed",
    revision,
    monitoringResumed: watch.status === "watching",
    reservationResult: result,
  };
}

function hydrateCurrentWatchActionLifecycleTransitions(
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
): WatchActionTransition[] {
  return watches.flatMap((watch) => {
    const recoveryResult = canonicalRecoveryResult(watch);
    const recoveryRevision = canonicalRecoveryRevision(watch);
    if (
      recoveryResult?.outcome === "unknown"
      && recoveryResult.manualCheckRequired
      && recoveryRevision !== null
    ) {
      return [recoveryTransition(watch, recoveryResult, recoveryRevision)];
    }
    const status = watch.status as WatchActionTransition["status"];
    if (!hydratableActionStatuses.has(status)) return [];
    return [{
      ...transitionContext(watch, status),
      status,
    }];
  });
}

export function hydrateCurrentWatchActionTransitions(
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
): WatchActionTransition[];
export function hydrateCurrentWatchActionTransitions(
  watches: ReadonlyArray<LegacyWatchSnapshot>,
): WatchActionTransition[];
export function hydrateCurrentWatchActionTransitions(
  watches: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
): WatchActionTransition[] {
  return hydrateCurrentWatchActionLifecycleTransitions(
    watches.map(mapCompatibleWatchLifecycleSnapshot),
  );
}

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
    if (previousWatch === undefined) return [];
    const recoveryResult = canonicalRecoveryResult(watch);
    const recoveryRevision = canonicalRecoveryRevision(watch);
    if (
      recoveryResult !== null
      && recoveryRevision !== null
      && recoveryRevision !== canonicalRecoveryRevision(previousWatch)
    ) {
      return [recoveryTransition(watch, recoveryResult, recoveryRevision)];
    }
    if (previousWatch.status === watch.status) {
      if (
        watch.status === "reserving"
        && transitionRevisionAt(previousWatch, "reserving")
          !== transitionRevisionAt(watch, "reserving")
      ) {
        return [{
          ...transitionContext(watch, "reserving"),
          status: "reserving" as const,
        }];
      }
      return [];
    }
    const holdEndedAt = attemptTimestamp(watch, "paymentHoldEndedAt");
    const holdEndReason = attemptPaymentHoldEndReason(watch);
    if (previousWatch.status === "payment_required" && watch.status === "completed") {
      return [{
        ...transitionContext(watch, "payment_completed"),
        status: "payment_completed" as const,
      }];
    }
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
