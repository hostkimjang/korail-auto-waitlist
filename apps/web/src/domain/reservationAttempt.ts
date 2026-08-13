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

type ReservationConfirmationOutcome =
  | "confirmed_payment_required"
  | "confirmed_paid"
  | "not_found"
  | "auth_required"
  | "provider_blocked"
  | "inconclusive";

export type PaymentHoldEndReason =
  | "confirmed_payment_deadline_elapsed"
  | "confirmed_payment_hold_no_longer_present";

export interface LatestReservationAttempt {
  outcome: ReservationAttemptOutcome;
  startedAt: string;
  finishedAt: string | null;
  retryable: boolean;
  manualCheckRequired: boolean;
  retryCondition: ReservationRetryCondition | null;
  paymentHoldEndedAt: string | null;
  manualRearmAvailable?: boolean;
  paymentHoldEndReason?: PaymentHoldEndReason | null;
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
  const confirmationOutcomeValue = value.confirmation_outcome;
  if (
    confirmationOutcomeValue !== null
    && confirmationOutcomeValue !== undefined
    && (
      typeof confirmationOutcomeValue !== "string"
      || !confirmationOutcomes.has(confirmationOutcomeValue.toLowerCase())
    )
  ) return null;
  const confirmationOutcome = typeof confirmationOutcomeValue === "string"
    ? confirmationOutcomeValue.toLowerCase() as ReservationConfirmationOutcome
    : null;
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

  return {
    outcome: outcome as ReservationAttemptOutcome,
    startedAt,
    finishedAt,
    retryable: value.retryable,
    manualCheckRequired: value.manual_check_required,
    retryCondition,
    paymentHoldEndedAt,
    manualRearmAvailable: paymentHoldEndedAt !== null && value.manual_rearm_available === true,
    progressStages,
    reservedSeats,
    ...(paymentHoldEndedAt === null ? {} : { paymentHoldEndReason }),
  };
}
