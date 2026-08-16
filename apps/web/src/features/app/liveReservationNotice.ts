import type { AppNotificationInput } from "./notificationCenter";
import {
  isReservationConfirmationOutcome,
  isReservationResultReasonCode,
  normalizeReservationConfirmationDiagnosticCode,
  normalizeReservedSeats,
  type ReservationAttemptOutcome,
  type ReservationConfirmationDiagnosticCode,
  type ReservationConfirmationOutcome,
  type ReservationResultReasonCode,
} from "../../domain/reservationAttempt";
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

interface ReservationResultEvidence {
  resultReasonCode: ReservationResultReasonCode | null;
  confirmationOutcome: ReservationConfirmationOutcome | null;
  confirmationDiagnosticCode: ReservationConfirmationDiagnosticCode | null;
  confirmationObservedAt: string | null;
  reconciliationAttemptCount: number;
  nextReconcileAt: string | null;
}

interface ReservationReconciledEvidence extends ReservationResultEvidence {
  outcome: ReservationAttemptOutcome | null;
  paymentActionable: boolean;
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

function awareEventInstant(value: unknown): string | null {
  const candidate = text(value);
  if (candidate === null || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(candidate)) return null;
  return Number.isFinite(Date.parse(candidate)) ? candidate : null;
}

function reservationResultEvidence(
  payload: Record<string, unknown>,
): ReservationResultEvidence | null {
  const reasonValue = payload.result_reason_code;
  if (
    reasonValue !== null
    && reasonValue !== undefined
    && !isReservationResultReasonCode(reasonValue)
  ) return null;
  const confirmationValue = payload.confirmation_outcome;
  if (
    confirmationValue !== null
    && confirmationValue !== undefined
    && !isReservationConfirmationOutcome(confirmationValue)
  ) return null;
  const confirmationOutcome = isReservationConfirmationOutcome(confirmationValue)
    ? confirmationValue
    : null;
  const observedValue = payload.confirmation_observed_at;
  const confirmationObservedAt = observedValue === null || observedValue === undefined
    ? null
    : awareEventInstant(observedValue);
  if (observedValue !== null && observedValue !== undefined && confirmationObservedAt === null) {
    return null;
  }
  const countValue = payload.reconciliation_attempt_count;
  if (
    countValue !== undefined
    && (
      !Number.isInteger(countValue)
      || Number(countValue) < 0
      || Number(countValue) > 6
    )
  ) return null;
  const nextValue = payload.next_reconcile_at;
  const nextReconcileAt = nextValue === null || nextValue === undefined
    ? null
    : awareEventInstant(nextValue);
  if (nextValue !== null && nextValue !== undefined && nextReconcileAt === null) return null;
  return {
    resultReasonCode: isReservationResultReasonCode(reasonValue) ? reasonValue : null,
    confirmationOutcome,
    confirmationDiagnosticCode: normalizeReservationConfirmationDiagnosticCode(
      confirmationOutcome,
      payload.confirmation_diagnostic_code,
    ),
    confirmationObservedAt,
    reconciliationAttemptCount: countValue === undefined ? 0 : Number(countValue),
    nextReconcileAt,
  };
}

function reservationAttemptOutcome(value: unknown): ReservationAttemptOutcome | null {
  switch (value) {
    case "pending":
    case "payment_required":
    case "reserved":
    case "not_available":
    case "auth_required":
    case "provider_blocked":
    case "failed":
    case "unknown":
      return value;
    default:
      return null;
  }
}

function reservationReconciledEvidence(
  payload: Record<string, unknown>,
): ReservationReconciledEvidence | null {
  const evidence = reservationResultEvidence(payload);
  if (evidence === null) return null;
  const outcomeValue = payload.outcome;
  if (
    outcomeValue !== null
    && outcomeValue !== undefined
    && reservationAttemptOutcome(outcomeValue) === null
  ) return null;
  const paymentActionableValue = payload.payment_actionable;
  if (paymentActionableValue !== undefined && typeof paymentActionableValue !== "boolean") {
    return null;
  }
  return {
    ...evidence,
    outcome: reservationAttemptOutcome(outcomeValue),
    paymentActionable: paymentActionableValue === true,
  };
}

const reservationProgressStageNames: ReadonlySet<ReservationProgressStageName> = new Set([
  "authenticated_session_ready",
  "target_rechecked",
  "seat_selected",
  "reservation_requested",
]);

const reservationProgressStageOrder: ReadonlyMap<ReservationProgressStageName, number> = new Map([
  ["authenticated_session_ready", 0],
  ["target_rechecked", 1],
  ["seat_selected", 2],
  ["reservation_requested", 3],
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

interface ExistingPaymentAttemptContext {
  startedAt: string;
  finishedAt: string;
  reservationProgress: ReadonlyArray<ReservationProgressStage>;
  reservedSeats: NonNullable<WatchActionTransition["reservedSeats"]>;
}

function existingAttemptProgress(
  value: ReadonlyArray<ReservationProgressStage> | undefined,
  startedAt: string,
  finishedAt: string,
): ReadonlyArray<ReservationProgressStage> {
  if (value === undefined || value.length === 0) return [];
  const parsed: ReservationProgressStage[] = [];
  let previousInstant = Date.parse(startedAt);
  let previousOrder = -1;
  const upperBound = Date.parse(finishedAt);
  for (const item of value) {
    const currentOrder = reservationProgressStageOrder.get(item.stage);
    const occurredAt = eventInstant(item.occurredAt);
    const currentInstant = occurredAt === null ? Number.NaN : Date.parse(occurredAt);
    if (
      currentOrder === undefined
      || occurredAt === null
      || !Number.isFinite(currentInstant)
      || currentOrder <= previousOrder
      || currentInstant < previousInstant
      || currentInstant > upperBound
    ) return [];
    parsed.push({ stage: item.stage, occurredAt });
    previousOrder = currentOrder;
    previousInstant = currentInstant;
  }
  return parsed;
}

function existingPaymentAttemptContext(
  watch: WatchLifecycleSnapshot,
  candidateId: string,
  revisionAt: string,
): ExistingPaymentAttemptContext | null {
  const attempt = watch.latestReservationAttempt;
  if (attempt === null || watch.latestReservationAttemptCandidateId !== candidateId) return null;
  const startedAt = eventTimeNoLaterThan(attempt.startedAt, revisionAt);
  const finishedAt = eventTimeNoLaterThan(attempt.finishedAt, revisionAt);
  if (
    startedAt === null
    || finishedAt === null
    || Date.parse(finishedAt) < Date.parse(startedAt)
  ) return null;
  return {
    startedAt,
    finishedAt,
    reservationProgress: existingAttemptProgress(
      attempt.progressStages,
      startedAt,
      finishedAt,
    ),
    reservedSeats: attempt.reservedSeats ?? [],
  };
}

function transitionFromEvent(
  event: LiveEventRecord,
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
  status: WatchActionTransition["status"],
): WatchActionTransition | null {
  if (!isRecord(event.payload)) return null;
  const evidence = reservationResultEvidence(event.payload);
  if (evidence === null) return null;
  const watchId = text(event.payload.watch_id);
  const aggregateId = text(event.aggregate_id);
  const candidateId = text(event.payload.candidate_id);
  if (watchId === null || aggregateId !== watchId || candidateId === null) return null;
  const watch = watches.find((item) => item.id === watchId);
  if (watch === undefined) return null;
  const candidate = watch.reservationCandidateContexts[candidateId];
  if (candidate === undefined) return null;
  const revisionAt = eventInstant(event.created_at);
  const revision = text(event.id) ?? revisionAt ?? `${status}:${watchId}`;
  const reportedStartedAt = eventTimeNoLaterThan(event.payload.attempt_started_at, revisionAt);
  const startedAt = reportedStartedAt ?? (status === "reserving" ? revisionAt : null);
  const detectedAt = startedAt === null
    ? null
    : eventTimeNoLaterThan(event.payload.seat_detected_at, startedAt);
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
  const monitoringResumed = event.payload.monitoring_resumed;
  const reservedSeats = normalizeReservedSeats(event.payload.reserved_seats);
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
    ...candidate,
    status,
    revision,
    ...(revisionAt === null ? {} : { revisionAt }),
    ...(detectedAt === null ? {} : { detectedAt }),
    ...(startedAt === null ? {} : { startedAt }),
    ...(finishedAt === null ? {} : { finishedAt }),
    ...(progress.length === 0 ? {} : { reservationProgress: progress }),
    ...(reservedSeats.length === 0 ? {} : { reservedSeats }),
    ...(typeof monitoringResumed === "boolean" ? { monitoringResumed } : {}),
    ...evidence,
  };
}

function progressedTransitionFromEvent(
  event: LiveEventRecord,
  watches: ReadonlyArray<WatchLifecycleSnapshot>,
): WatchActionTransition | null {
  if (!isRecord(event.payload)) return null;
  const eventId = text(event.id);
  const revisionAt = eventInstant(event.created_at);
  const watchId = text(event.payload.watch_id);
  const aggregateId = text(event.aggregate_id);
  const candidateId = text(event.payload.candidate_id);
  const attemptId = text(event.payload.attempt_id);
  const attemptSequence = event.payload.attempt_sequence;
  const startedAt = eventInstant(event.payload.attempt_started_at);
  const hasDetectedAt = Object.prototype.hasOwnProperty.call(event.payload, "seat_detected_at");
  const detectedAt = event.payload.seat_detected_at === null
    ? null
    : eventInstant(event.payload.seat_detected_at);
  const currentStage = text(event.payload.stage);
  const occurredAt = eventInstant(event.payload.occurred_at);
  if (
    eventId === null
    || revisionAt === null
    || watchId === null
    || aggregateId !== watchId
    || candidateId === null
    || attemptId === null
    || !Number.isInteger(attemptSequence)
    || Number(attemptSequence) < 1
    || startedAt === null
    || !hasDetectedAt
    || (event.payload.seat_detected_at !== null && detectedAt === null)
    || currentStage === null
    || !reservationProgressStageNames.has(currentStage as ReservationProgressStageName)
    || occurredAt === null
  ) return null;
  const watch = watches.find((item) => item.id === watchId);
  const candidate = watch?.reservationCandidateContexts[candidateId];
  if (watch === undefined || candidate === undefined) return null;
  const rawProgress = event.payload.progress_stages;
  if (!Array.isArray(rawProgress) || rawProgress.length === 0) return null;
  const progress = reservationProgress(rawProgress);
  if (progress.length !== rawProgress.length) return null;
  const startedInstant = Date.parse(startedAt);
  const detectedInstant = detectedAt === null ? null : Date.parse(detectedAt);
  const occurredInstant = Date.parse(occurredAt);
  const revisionInstant = Date.parse(revisionAt);
  let previousInstant = startedInstant;
  let previousOrder = -1;
  for (const item of progress) {
    const currentInstant = Date.parse(item.occurredAt);
    const currentOrder = reservationProgressStageOrder.get(item.stage);
    if (
      currentOrder === undefined
      || currentOrder <= previousOrder
      || currentInstant < previousInstant
      || currentInstant > occurredInstant
    ) return null;
    previousOrder = currentOrder;
    previousInstant = currentInstant;
  }
  const latest = progress.at(-1);
  if (
    (detectedInstant !== null && detectedInstant > startedInstant)
    || occurredInstant > revisionInstant
    || latest?.stage !== currentStage
    || latest.occurredAt !== occurredAt
  ) return null;
  return {
    id: watchId,
    provider: watch.provider,
    route: watch.route,
    train: candidate.train ?? watch.train,
    seatClassLabel: candidate.seatClassLabel ?? watch.seatClassLabel,
    date: candidate.date ?? watch.date,
    departure: candidate.departure ?? watch.departure,
    arrival: candidate.arrival ?? watch.arrival,
    reservationPolicy: watch.reservationPolicy,
    paymentDeadline: null,
    status: "reserving",
    revision: eventId,
    revisionAt,
    ...(detectedAt === null ? {} : { detectedAt }),
    startedAt,
    reservationProgress: progress,
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
  const evidence = reservationResultEvidence(payload);
  if (evidence === null) return null;
  return {
    outcome: outcome as ReservationResultOutcome,
    retryable: payload.retryable === true,
    manualCheckRequired: typeof payload.manual_check_required === "boolean"
      ? payload.manual_check_required
      : true,
    retryCondition,
    ...evidence,
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
    return buildWatchActionToast(transition);
  }
  if (eventType === "watch.reservation_progressed") {
    const transition = progressedTransitionFromEvent(event, watches);
    return transition === null ? null : buildWatchActionToast(transition);
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
  if (eventType === "watch.payment_completed") {
    const watchId = text(event.payload.watch_id);
    const candidateId = text(event.payload.candidate_id);
    const aggregateId = text(event.aggregate_id);
    const eventId = text(event.id);
    const revisionAt = eventInstant(event.created_at);
    const watch = watchId === null
      ? undefined
      : watches.find((item) => item.id === watchId);
    const attemptContext = watch === undefined || candidateId === null || revisionAt === null
      ? null
      : existingPaymentAttemptContext(watch, candidateId, revisionAt);
    const fromStatus = text(event.payload.from);
    if (
      watchId === null
      || aggregateId !== watchId
      || candidateId === null
      || eventId === null
      || revisionAt === null
      || watch === undefined
      || watch.reservationCandidateContexts[candidateId] === undefined
      || attemptContext === null
      || event.payload.terminal !== true
      || (fromStatus !== "payment_required" && fromStatus !== "watching")
      || text(event.payload.to) !== "completed"
      || text(event.payload.status) !== "completed"
      || text(event.payload.reason) !== "confirmed_paid"
      || event.payload.automatic_reservation_retry !== false
    ) return null;
    const transition = transitionFromEvent(event, watches, "payment_completed");
    return transition === null ? null : buildWatchActionToast({
      ...transition,
      startedAt: transition.startedAt ?? attemptContext.startedAt,
      finishedAt: transition.finishedAt ?? attemptContext.finishedAt,
      reservationProgress: transition.reservationProgress ?? attemptContext.reservationProgress,
      reservedSeats: transition.reservedSeats ?? attemptContext.reservedSeats,
    });
  }
  if (eventType === "watch.reservation_result_requires_manual_check") {
    const transition = transitionFromEvent(event, watches, "monitoring_resumed");
    const evidence = reservationResultEvidence(event.payload);
    return transition === null || evidence === null ? null : buildReservationRecoveryToast(transition, {
      outcome: "unknown",
      retryable: false,
      manualCheckRequired: true,
      retryCondition: null,
      ...evidence,
    });
  }
  if (eventType === "watch.reservation_reconciled") {
    const watchId = text(event.payload.watch_id) ?? text(event.aggregate_id);
    const watch = watchId === null ? undefined : watches.find((item) => item.id === watchId);
    const evidence = reservationReconciledEvidence(event.payload);
    if (watch === undefined || watch.latestReservationAttempt === null || evidence === null) return null;
    // 결제 완료 알림은 전용 terminal event 또는 REST status edge에서만 만든다.
    // reconciliation evidence가 먼저 도착해 완료 상태를 앞서 합성하면 안 된다.
    if (evidence.confirmationOutcome === "confirmed_paid") return null;
    const reconciledCandidateId = text(event.payload.candidate_id);
    const isUnknownAuthenticationReconciliation = evidence.outcome === "unknown"
      && (
        evidence.confirmationOutcome === "auth_required"
        || evidence.confirmationOutcome === "provider_blocked"
      );
    if (isUnknownAuthenticationReconciliation) {
      if (
        watch.latestReservationAttempt.outcome !== "unknown"
        || reconciledCandidateId === null
        || watch.latestReservationAttemptCandidateId !== reconciledCandidateId
        || evidence.confirmationObservedAt === null
        || evidence.reconciliationAttemptCount < 1
        || evidence.nextReconcileAt !== null
      ) return null;
      const transition = transitionFromEvent(event, watches, "auth_required");
      return transition === null ? null : buildWatchActionToast(transition);
    }
    if (
      evidence.paymentActionable
      && (evidence.outcome === "payment_required" || evidence.outcome === "reserved")
    ) {
      // 재확인 이벤트가 지닌 exact attempt context만 사용한다. REST latest attempt는 같은
      // candidate의 다른 sequence일 수 있어 시간·단계·좌석·기한을 fallback하지 않는다.
      const transition = transitionFromEvent(event, watches, "payment_required");
      return transition === null ? null : buildWatchActionToast(transition);
    }
    const result = recoveryResult(event.payload);
    const transition = transitionFromEvent(event, watches, "monitoring_resumed");
    return transition === null || result === null
      ? null
      : buildReservationRecoveryToast({
          ...transition,
          monitoringResumed: transition.monitoringResumed ?? watch.status === "watching",
        }, result);
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
