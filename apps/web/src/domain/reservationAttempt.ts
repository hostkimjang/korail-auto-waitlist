export type ReservationAttemptOutcome =
  | "pending"
  | "payment_required"
  | "reserved"
  | "not_available"
  | "auth_required"
  | "provider_blocked"
  | "failed"
  | "unknown";

export type ReservationRetryCondition =
  | "new_availability_episode"
  | "provider_account_reverified";

export type ReservationProgressStageName =
  | "authenticated_session_ready"
  | "target_rechecked"
  | "seat_selected"
  | "reservation_requested";

export interface ReservationProgressStage {
  stage: ReservationProgressStageName;
  occurredAt: string;
}

export interface ReservedSeat {
  carNumber: string;
  seatNumber: string;
}

export type ReservationConfirmationOutcome =
  | "confirmed_payment_required"
  | "confirmed_paid"
  | "not_found"
  | "auth_required"
  | "provider_blocked"
  | "inconclusive";

export type ReservationConfirmationDiagnosticCode =
  | "official_read_unavailable"
  | "credential_context_mismatch"
  | "official_record_ambiguous"
  | "official_evidence_insufficient"
  | "unspecified";

export type ReservationResultReasonCode =
  | "reservation_pending"
  | "payment_hold_created"
  | "target_not_available"
  | "target_ambiguous"
  | "seat_not_available"
  | "reservation_control_unavailable"
  | "seat_selection_lost"
  | "delay_consent_required"
  | "existing_reservation_action_required"
  | "provider_notice_action_required"
  | "authentication_required"
  | "provider_blocked"
  | "provider_unavailable"
  | "provider_response_invalid"
  | "reservation_request_result_unknown"
  | "reservation_failed";

export type PaymentHoldEndReason =
  | "confirmed_payment_deadline_elapsed"
  | "confirmed_payment_hold_no_longer_present";

export type ManualRearmReason =
  | "payment_hold_ended"
  | "unknown_result_unresolved";

export type ReservationReconciliationResolution =
  | "confirmed_absent"
  | "exhausted_unresolved";

export type AutomaticReservationRetryFenceReason =
  | "confirmed_absent_recovery_consumed";

export interface LatestReservationAttempt {
  outcome: ReservationAttemptOutcome;
  resultReasonCode?: ReservationResultReasonCode | null;
  startedAt: string;
  finishedAt: string | null;
  retryable: boolean;
  manualCheckRequired: boolean;
  retryCondition: ReservationRetryCondition | null;
  paymentHoldEndedAt: string | null;
  manualRearmAvailable?: boolean;
  manualRearmReason?: ManualRearmReason | null;
  paymentHoldEndReason?: PaymentHoldEndReason | null;
  confirmationOutcome?: ReservationConfirmationOutcome | null;
  confirmationDiagnosticCode?: ReservationConfirmationDiagnosticCode | null;
  confirmationObservedAt?: string | null;
  reconciliationAttemptCount?: number;
  reconciliationResolution?: ReservationReconciliationResolution | null;
  automaticReservationRetryFenceReason?: AutomaticReservationRetryFenceReason | null;
  nextReconcileAt?: string | null;
  progressStages?: ReadonlyArray<ReservationProgressStage>;
  reservedSeats?: ReadonlyArray<ReservedSeat>;
}

const outcomes: ReadonlySet<string> = new Set([
  "pending",
  "payment_required",
  "reserved",
  "not_available",
  "auth_required",
  "provider_blocked",
  "failed",
  "unknown",
]);
const retryConditions: ReadonlySet<string> = new Set([
  "new_availability_episode",
  "provider_account_reverified",
]);
const confirmationOutcomes: ReadonlySet<string> = new Set([
  "confirmed_payment_required",
  "confirmed_paid",
  "not_found",
  "auth_required",
  "provider_blocked",
  "inconclusive",
]);
const confirmationDiagnosticCodes: ReadonlySet<string> = new Set([
  "official_read_unavailable",
  "credential_context_mismatch",
  "official_record_ambiguous",
  "official_evidence_insufficient",
  "unspecified",
]);
const resultReasonCodes: ReadonlySet<string> = new Set([
  "reservation_pending",
  "payment_hold_created",
  "target_not_available",
  "target_ambiguous",
  "seat_not_available",
  "reservation_control_unavailable",
  "seat_selection_lost",
  "delay_consent_required",
  "existing_reservation_action_required",
  "provider_notice_action_required",
  "authentication_required",
  "provider_blocked",
  "provider_unavailable",
  "provider_response_invalid",
  "reservation_request_result_unknown",
  "reservation_failed",
]);
const manualRearmReasons: ReadonlySet<string> = new Set([
  "payment_hold_ended",
  "unknown_result_unresolved",
]);
const reconciliationResolutions: ReadonlySet<string> = new Set([
  "confirmed_absent",
  "exhausted_unresolved",
]);
const automaticReservationRetryFenceReasons: ReadonlySet<string> = new Set([
  "confirmed_absent_recovery_consumed",
]);

interface ReservationReconciliationProjection {
  outcome: ReservationAttemptOutcome | null;
  confirmationOutcome: ReservationConfirmationOutcome | null;
  manualCheckRequired: boolean;
  reconciliationResolution: ReservationReconciliationResolution | null;
  automaticReservationRetryFenceReason: AutomaticReservationRetryFenceReason | null;
}

export function hasConfirmedAbsentReservationEvidence(
  projection: {
    outcome: ReservationAttemptOutcome | null;
    confirmationOutcome?: ReservationConfirmationOutcome | null;
    manualCheckRequired: boolean;
    reconciliationResolution?: ReservationReconciliationResolution | null;
  },
): boolean {
  return projection.outcome === "unknown"
    && projection.confirmationOutcome === "not_found"
    && projection.manualCheckRequired === false
    && projection.reconciliationResolution === "confirmed_absent";
}

export function normalizeReservationReconciliationProjection(
  projection: ReservationReconciliationProjection,
): Pick<
  ReservationReconciliationProjection,
  "manualCheckRequired" | "reconciliationResolution" | "automaticReservationRetryFenceReason"
> & { invalidClosureEvidence: boolean } {
  const confirmedAbsent = hasConfirmedAbsentReservationEvidence(projection);
  const invalidConfirmedAbsent = projection.reconciliationResolution === "confirmed_absent"
    && !confirmedAbsent;
  const invalidRetryFence = projection.automaticReservationRetryFenceReason !== null
    && !confirmedAbsent;
  const unresolvedUnknown = projection.outcome === "unknown"
    && projection.confirmationOutcome !== "confirmed_paid"
    && !confirmedAbsent;
  return {
    invalidClosureEvidence: invalidConfirmedAbsent || invalidRetryFence,
    manualCheckRequired: invalidConfirmedAbsent || invalidRetryFence || unresolvedUnknown
      ? true
      : projection.manualCheckRequired,
    reconciliationResolution: invalidConfirmedAbsent
      ? null
      : projection.reconciliationResolution,
    automaticReservationRetryFenceReason: invalidRetryFence
      ? null
      : projection.automaticReservationRetryFenceReason,
  };
}

export function validatedManualRearmReason(
  attempt: LatestReservationAttempt,
): ManualRearmReason | null {
  if (attempt.manualRearmAvailable !== true) return null;
  if (
    attempt.manualRearmReason === "payment_hold_ended"
    && attempt.outcome === "payment_required"
    && attempt.paymentHoldEndedAt !== null
    && attempt.paymentHoldEndReason !== null
    && attempt.paymentHoldEndReason !== undefined
  ) return attempt.manualRearmReason;
  if (
    attempt.manualRearmReason === "unknown_result_unresolved"
    && attempt.outcome === "unknown"
    && attempt.finishedAt !== null
    && attempt.manualCheckRequired
    && attempt.confirmationObservedAt !== null
    && attempt.confirmationObservedAt !== undefined
    && (
      (
        attempt.confirmationOutcome === "inconclusive"
        && (attempt.reconciliationAttemptCount ?? 0) >= 3
        && attempt.reconciliationResolution !== "confirmed_absent"
      )
      || (
        attempt.confirmationOutcome === "not_found"
        && attempt.reconciliationAttemptCount === 6
        && attempt.nextReconcileAt === null
        && attempt.reconciliationResolution === "exhausted_unresolved"
      )
    )
  ) return attempt.manualRearmReason;
  return null;
}

export function isReservationConfirmationOutcome(
  value: unknown,
): value is ReservationConfirmationOutcome {
  return typeof value === "string" && confirmationOutcomes.has(value);
}

export function isReservationConfirmationDiagnosticCode(
  value: unknown,
): value is ReservationConfirmationDiagnosticCode {
  return typeof value === "string" && confirmationDiagnosticCodes.has(value);
}

export function normalizeReservationConfirmationDiagnosticCode(
  confirmationOutcome: ReservationConfirmationOutcome | null,
  value: unknown,
): ReservationConfirmationDiagnosticCode | null {
  if (confirmationOutcome !== "inconclusive") return null;
  return isReservationConfirmationDiagnosticCode(value) ? value : "unspecified";
}

export function isReservationResultReasonCode(
  value: unknown,
): value is ReservationResultReasonCode {
  return typeof value === "string" && resultReasonCodes.has(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function awareTimestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  return Number.isNaN(Date.parse(value)) ? null : value;
}

function reservationProgressStage(value: unknown): ReservationProgressStageName | null {
  switch (value) {
    case "authenticated_session_ready":
    case "target_rechecked":
    case "seat_selected":
    case "reservation_requested":
      return value;
    default:
      return null;
  }
}

function mapProgressStages(
  value: unknown,
  startedAt: string,
  finishedAt: string | null,
): ReadonlyArray<ReservationProgressStage> | null {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return null;
  const order: Readonly<Record<ReservationProgressStageName, number>> = {
    authenticated_session_ready: 0,
    target_rechecked: 1,
    seat_selected: 2,
    reservation_requested: 3,
  };
  const upperBound = finishedAt === null ? null : Date.parse(finishedAt);
  let previousTime = Date.parse(startedAt);
  let previousOrder = -1;
  const parsed: ReservationProgressStage[] = [];
  for (const item of value) {
    if (!isRecord(item)) return null;
    const stage = reservationProgressStage(item.stage);
    const occurredAt = awareTimestamp(item.occurred_at);
    if (stage === null || occurredAt === null) return null;
    const currentTime = Date.parse(occurredAt);
    const currentOrder = order[stage];
    if (
      currentOrder <= previousOrder
      || currentTime < previousTime
      || (upperBound !== null && currentTime > upperBound)
    ) return null;
    previousOrder = currentOrder;
    previousTime = currentTime;
    parsed.push({ stage, occurredAt });
  }
  return parsed;
}

function boundedSeatIdentifier(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toUpperCase();
  return /^[A-Z0-9-]{1,10}$/.test(normalized) ? normalized : null;
}

export function normalizeReservedSeats(value: unknown): ReadonlyArray<ReservedSeat> {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) return [];
  if (value.length > 9) return [];
  const seats: ReservedSeat[] = [];
  const identities = new Set<string>();
  for (const item of value) {
    if (!isRecord(item)) return [];
    const carNumber = boundedSeatIdentifier(item.car_number);
    const seatNumber = boundedSeatIdentifier(item.seat_number);
    if (carNumber === null || seatNumber === null) return [];
    const identity = `${carNumber}\u0000${seatNumber}`;
    if (identities.has(identity)) return [];
    identities.add(identity);
    seats.push({ carNumber, seatNumber });
  }
  return seats;
}

export function formatReservedSeats(seats: ReadonlyArray<ReservedSeat>): string | null {
  if (seats.length === 0) return null;
  return seats.map(({ carNumber, seatNumber }) => (
    `${carNumber.endsWith("호차") ? carNumber : `${carNumber}호차`} ${seatNumber}`
  )).join(", ");
}

export function mapLatestReservationAttempt(value: unknown): LatestReservationAttempt | null {
  if (!isRecord(value) || typeof value.outcome !== "string") return null;
  const outcome = value.outcome.toLowerCase();
  const startedAt = awareTimestamp(value.started_at);
  if (
    !outcomes.has(outcome)
    || startedAt === null
    || typeof value.retryable !== "boolean"
    || typeof value.manual_check_required !== "boolean"
  ) return null;
  const finishedAt = value.finished_at === null || value.finished_at === undefined
    ? null
    : awareTimestamp(value.finished_at);
  if (value.finished_at !== null && value.finished_at !== undefined && finishedAt === null) {
    return null;
  }
  if (finishedAt !== null && Date.parse(finishedAt) < Date.parse(startedAt)) return null;
  const progressStages = mapProgressStages(value.progress_stages, startedAt, finishedAt);
  if (progressStages === null) return null;
  const retryConditionValue = value.retry_condition;
  if (
    retryConditionValue !== null
    && retryConditionValue !== undefined
    && (
      typeof retryConditionValue !== "string"
      || !retryConditions.has(retryConditionValue.toLowerCase())
    )
  ) return null;
  const retryCondition = typeof retryConditionValue === "string"
    ? retryConditionValue.toLowerCase() as ReservationRetryCondition
    : null;
  const resultReasonCodeValue = value.result_reason_code;
  if (
    resultReasonCodeValue !== null
    && resultReasonCodeValue !== undefined
    && !isReservationResultReasonCode(resultReasonCodeValue)
  ) return null;
  const resultReasonCode = isReservationResultReasonCode(resultReasonCodeValue)
    ? resultReasonCodeValue
    : null;
  const confirmationOutcomeValue = value.confirmation_outcome;
  if (
    confirmationOutcomeValue !== null
    && confirmationOutcomeValue !== undefined
    && (
      typeof confirmationOutcomeValue !== "string"
      || !isReservationConfirmationOutcome(confirmationOutcomeValue.toLowerCase())
    )
  ) return null;
  const confirmationOutcome = typeof confirmationOutcomeValue === "string"
    ? confirmationOutcomeValue.toLowerCase() as ReservationConfirmationOutcome
    : null;
  const confirmationDiagnosticCode = normalizeReservationConfirmationDiagnosticCode(
    confirmationOutcome,
    value.confirmation_diagnostic_code,
  );
  const confirmationObservedAt = value.confirmation_observed_at === null
    || value.confirmation_observed_at === undefined
    ? null
    : awareTimestamp(value.confirmation_observed_at);
  if (
    value.confirmation_observed_at !== null
    && value.confirmation_observed_at !== undefined
    && confirmationObservedAt === null
  ) return null;
  const reconciliationAttemptCountValue = value.reconciliation_attempt_count;
  if (
    reconciliationAttemptCountValue !== undefined
    && (
      !Number.isInteger(reconciliationAttemptCountValue)
      || Number(reconciliationAttemptCountValue) < 0
      || Number(reconciliationAttemptCountValue) > 6
    )
  ) return null;
  const reconciliationAttemptCount = reconciliationAttemptCountValue === undefined
    ? 0
    : Number(reconciliationAttemptCountValue);
  const reconciliationResolutionValue = value.reconciliation_resolution;
  if (
    reconciliationResolutionValue !== null
    && reconciliationResolutionValue !== undefined
    && (
      typeof reconciliationResolutionValue !== "string"
      || !reconciliationResolutions.has(reconciliationResolutionValue)
    )
  ) return null;
  const reconciliationResolution = typeof reconciliationResolutionValue === "string"
    ? reconciliationResolutionValue as ReservationReconciliationResolution
    : null;
  const automaticReservationRetryFenceReasonValue =
    value.automatic_reservation_retry_fence_reason;
  const automaticReservationRetryFenceReason =
    typeof automaticReservationRetryFenceReasonValue === "string"
      && automaticReservationRetryFenceReasons.has(automaticReservationRetryFenceReasonValue)
      ? automaticReservationRetryFenceReasonValue as AutomaticReservationRetryFenceReason
      : null;
  const normalizedReconciliation = normalizeReservationReconciliationProjection({
    outcome: outcome as ReservationAttemptOutcome,
    confirmationOutcome,
    manualCheckRequired: value.manual_check_required,
    reconciliationResolution,
    automaticReservationRetryFenceReason,
  });
  const nextReconcileAt = value.next_reconcile_at === null
    || value.next_reconcile_at === undefined
    ? null
    : awareTimestamp(value.next_reconcile_at);
  if (
    value.next_reconcile_at !== null
    && value.next_reconcile_at !== undefined
    && nextReconcileAt === null
  ) return null;
  const postDeadlineReconciledAt = value.post_deadline_reconciled_at === null
    || value.post_deadline_reconciled_at === undefined
    ? null
    : awareTimestamp(value.post_deadline_reconciled_at);
  if (
    value.post_deadline_reconciled_at !== null
    && value.post_deadline_reconciled_at !== undefined
    && postDeadlineReconciledAt === null
  ) return null;
  const paymentHoldEndReasonValue = value.payment_hold_end_reason;
  if (
    paymentHoldEndReasonValue !== null
    && paymentHoldEndReasonValue !== undefined
    && paymentHoldEndReasonValue !== "confirmed_payment_deadline_elapsed"
    && paymentHoldEndReasonValue !== "confirmed_payment_hold_no_longer_present"
  ) return null;
  const paymentHoldEndReason = typeof paymentHoldEndReasonValue === "string"
    ? paymentHoldEndReasonValue as PaymentHoldEndReason
    : null;
  const holdReasonMatchesConfirmation = (
    paymentHoldEndReason === "confirmed_payment_deadline_elapsed"
    && confirmationOutcome === "confirmed_payment_required"
  ) || (
    paymentHoldEndReason === "confirmed_payment_hold_no_longer_present"
    && confirmationOutcome === "not_found"
  );
  const paymentHoldEndedAt = outcome === "payment_required"
    && paymentHoldEndReason !== null
    && holdReasonMatchesConfirmation
    && postDeadlineReconciledAt !== null
    && Date.parse(postDeadlineReconciledAt) >= Date.parse(finishedAt ?? startedAt)
    && !value.manual_check_required
    ? postDeadlineReconciledAt
    : null;
  const reservedSeats = outcome === "payment_required" || outcome === "reserved"
    ? normalizeReservedSeats(value.reserved_seats)
    : [];
  const manualRearmReasonValue = value.manual_rearm_reason;
  if (
    manualRearmReasonValue !== null
    && manualRearmReasonValue !== undefined
    && (
      typeof manualRearmReasonValue !== "string"
      || !manualRearmReasons.has(manualRearmReasonValue)
    )
  ) return null;
  const requestedManualRearmReason = typeof manualRearmReasonValue === "string"
    ? manualRearmReasonValue as ManualRearmReason
    : null;
  const mappedAttempt: LatestReservationAttempt = {
    outcome: outcome as ReservationAttemptOutcome,
    resultReasonCode,
    startedAt,
    finishedAt,
    retryable: value.retryable,
    manualCheckRequired: normalizedReconciliation.manualCheckRequired,
    retryCondition,
    paymentHoldEndedAt,
    manualRearmAvailable: value.manual_rearm_available === true
      && !normalizedReconciliation.invalidClosureEvidence,
    manualRearmReason: requestedManualRearmReason,
    confirmationOutcome,
    confirmationDiagnosticCode,
    confirmationObservedAt,
    reconciliationAttemptCount,
    reconciliationResolution: normalizedReconciliation.reconciliationResolution,
    automaticReservationRetryFenceReason:
      normalizedReconciliation.automaticReservationRetryFenceReason,
    nextReconcileAt,
    progressStages,
    reservedSeats,
    ...(paymentHoldEndedAt === null ? {} : { paymentHoldEndReason }),
  };
  const manualRearmReason = validatedManualRearmReason(mappedAttempt);
  return {
    ...mappedAttempt,
    manualRearmAvailable: manualRearmReason !== null,
    manualRearmReason,
  };
}
