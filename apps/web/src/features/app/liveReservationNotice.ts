import type { AppNotificationInput } from "./notificationCenter";
import {
  buildReservationRecoveryToast,
  buildWatchActionToast,
  type ReservationRecoveryResult,
  type ReservationResultOutcome,
} from "./reservationToast";
import type {
  ReservationProgressStage,
  ReservationProgressStageName,
  WatchActionTransition,
} from "./watchSnapshots";
import {
  mapCompatibleWatchLifecycleSnapshot,
  type LegacyWatchSnapshot,
  type WatchLifecycleSnapshot,
} from "./watchLifecycleSnapshot";

interface LiveEventRecord {
  event_type?: unknown;
  id?: unknown;
  aggregate_id?: unknown;
  created_at?: unknown;
  payload?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function eventInstant(value: unknown): string | null {
  const candidate = text(value);
  return candidate !== null && Number.isFinite(Date.parse(candidate)) ? candidate : null;
}

const reservationProgressStageNames: ReadonlySet<ReservationProgressStageName> = new Set([
  "authenticated_session_ready",
  "target_rechecked",
  "seat_selected",
  "reservation_requested",
]);

function reservationProgress(value: unknown): ReadonlyArray<ReservationProgressStage> {
  if (!Array.isArray(value)) return [];
  const seen = new Set<ReservationProgressStageName>();
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const stage = text(item.stage);
    const occurredAt = eventInstant(item.occurred_at);
    if (
      stage === null
      || !reservationProgressStageNames.has(stage as ReservationProgressStageName)
      || occurredAt === null
      || seen.has(stage as ReservationProgressStageName)
    ) return [];
    const normalizedStage = stage as ReservationProgressStageName;
    seen.add(normalizedStage);
    return [{ stage: normalizedStage, occurredAt }];
  });
}

function eventTimeNoLaterThan(value: unknown, upperBound: string | null): string | null {
  const instant = eventInstant(value);
  if (instant === null || upperBound === null) return instant;
  return Date.parse(instant) <= Date.parse(upperBound) ? instant : null;
}

function monotonicReservationProgress(
  value: unknown,
  startedAt: string | null,
  finishedAt: string | null,
  revisionAt: string | null,
): ReadonlyArray<ReservationProgressStage> {
  const lowerBound = startedAt === null ? null : Date.parse(startedAt);
  const upperBoundValue = finishedAt ?? revisionAt;
  const upperBound = upperBoundValue === null ? null : Date.parse(upperBoundValue);
  let previous = lowerBound;
  return reservationProgress(value).filter((item) => {
    const current = Date.parse(item.occurredAt);
    if (
      (previous !== null && current < previous)
      || (upperBound !== null && current > upperBound)
    ) return false;
    previous = current;
    return true;
  });
}

function candidateContext(
  watch: WatchLifecycleSnapshot,
  candidateId: string | null,
): Partial<WatchActionTransition> {
  if (candidateId === null) return {};
  const candidate = watch.reservationCandidateContexts[candidateId];
  return candidate ?? {};
}

function transitionFromEvent(
  event: LiveEventRecord,
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
  status: WatchActionTransition["status"],
): WatchActionTransition | null {
  if (!isRecord(event.payload)) return null;
  const watchId = text(event.payload.watch_id) ?? text(event.aggregate_id);
  if (watchId === null) return null;
  const watch = watches.find((item) => item.id === watchId);
  if (watch === undefined) return null;
  const candidateId = text(event.payload.candidate_id);
  const revisionAt = eventInstant(event.created_at);
  const revision = text(event.id) ?? revisionAt ?? `${status}:${watchId}`;
  const startedAt = eventTimeNoLaterThan(event.payload.attempt_started_at, revisionAt);
  const candidateFinishedAt = eventTimeNoLaterThan(event.payload.attempt_finished_at, revisionAt);
  const finishedAt = startedAt !== null
    && candidateFinishedAt !== null
    && Date.parse(candidateFinishedAt) < Date.parse(startedAt)
    ? null
    : candidateFinishedAt;
  const progress = monotonicReservationProgress(
    event.payload.progress_stages,
    startedAt,
    finishedAt,
    revisionAt,
  );
  return {
    id: watchId,
    provider: watch.provider,
    route: watch.route,
    train: watch.train,
    seatClassLabel: watch.seatClassLabel,
    date: watch.date,
    departure: watch.departure,
    arrival: watch.arrival,
    reservationPolicy: watch.reservationPolicy,
    paymentDeadline: text(event.payload.payment_deadline),
    ...candidateContext(watch, candidateId),
    status,
    revision,
    ...(revisionAt === null ? {} : { revisionAt }),
    ...(startedAt === null ? {} : { startedAt }),
    ...(finishedAt === null ? {} : { finishedAt }),
    ...(progress.length === 0 ? {} : { reservationProgress: progress }),
  };
}

function recoveryResult(payload: Record<string, unknown>): ReservationRecoveryResult | null {
  const outcome = text(payload.outcome);
  if (!(["failed", "not_available", "unknown"] as ReadonlyArray<string>).includes(
    outcome ?? "",
  )) return null;
  const retryConditionValue = text(payload.retry_condition);
  const retryCondition = retryConditionValue === "new_availability_episode"
    || retryConditionValue === "provider_account_reverified"
    ? retryConditionValue
    : null;
  return {
    outcome: outcome as ReservationResultOutcome,
    retryable: payload.retryable === true,
    manualCheckRequired: payload.manual_check_required === true,
    retryCondition,
  };
}

function buildLiveReservationNoticeFromLifecycle(
  value: unknown,
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
): AppNotificationInput | null {
  if (!isRecord(value)) return null;
  const event = value as LiveEventRecord;
  const eventType = text(event.event_type);
  if (eventType === "watch.reservation_attempted") {
    const transition = transitionFromEvent(event, watches, "reserving");
    if (transition === null) return null;
    return buildWatchActionToast({
      ...transition,
      ...(transition.revisionAt === undefined ? {} : { startedAt: transition.revisionAt }),
    });
  }
  if (!isRecord(event.payload)) return null;
  if (
    eventType === "watch.payment_hold_ended_monitoring_resumed"
    || eventType === "watch.payment_hold_ended_one_off_expired"
  ) {
    const automaticReservationRetry = event.payload.automatic_reservation_retry;
    const expectedAutomaticRetry = eventType === "watch.payment_hold_ended_monitoring_resumed";
    const expectedStatus = expectedAutomaticRetry ? "watching" : "expired";
    const reason = text(event.payload.reason);
    if (
      event.payload.terminal !== true
      || text(event.payload.from) !== "payment_required"
      || text(event.payload.to) !== expectedStatus
      || text(event.payload.status) !== expectedStatus
      || automaticReservationRetry !== expectedAutomaticRetry
      || (
        reason !== "confirmed_payment_deadline_elapsed"
        && reason !== "confirmed_payment_hold_no_longer_present"
      )
    ) return null;
    const transition = transitionFromEvent(event, watches, "payment_hold_ended");
    return transition === null ? null : buildWatchActionToast({
      ...transition,
      automaticReservationRetry: expectedAutomaticRetry,
      paymentHoldEndReason: reason,
    });
  }
  if (eventType === "watch.reservation_result_requires_manual_check") {
    const transition = transitionFromEvent(event, watches, "monitoring_resumed");
    return transition === null ? null : buildReservationRecoveryToast(transition, {
      outcome: "unknown",
      retryable: false,
      manualCheckRequired: true,
      retryCondition: null,
    });
  }
  if (eventType !== "watch.reservation_result") return null;
  const outcome = text(event.payload.outcome);
  if (outcome === "payment_required" || outcome === "reserved") {
    const transition = transitionFromEvent(event, watches, "payment_required");
    return transition === null ? null : buildWatchActionToast(transition);
  }
  if (outcome === "auth_required" || outcome === "provider_blocked") {
    const transition = transitionFromEvent(event, watches, "auth_required");
    return transition === null ? null : buildWatchActionToast(transition);
  }
  const result = recoveryResult(event.payload);
  if (result === null) return null;
  const transition = transitionFromEvent(event, watches, "monitoring_resumed");
  return transition === null ? null : buildReservationRecoveryToast(transition, result);
}

export function buildLiveReservationNotice(
  value: unknown,
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
): AppNotificationInput | null;
export function buildLiveReservationNotice(
  value: unknown,
  watches: ReadonlyArray<LegacyWatchSnapshot>,
): AppNotificationInput | null;
export function buildLiveReservationNotice(
  value: unknown,
  watches: ReadonlyArray<LegacyWatchSnapshot | WatchLifecycleSnapshot>,
): AppNotificationInput | null {
  return buildLiveReservationNoticeFromLifecycle(
    value,
    watches.map(mapCompatibleWatchLifecycleSnapshot),
  );
}
