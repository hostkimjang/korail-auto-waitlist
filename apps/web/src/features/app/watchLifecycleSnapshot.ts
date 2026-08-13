import type { ReservationCandidateContext, WatchReadModel } from "../../api/watchProjection";
import type {
  PaymentHoldEndReason,
  ReservedSeat,
  ReservationAttemptOutcome,
  ReservationProgressStage,
  ReservationRetryCondition,
} from "../../domain/reservationAttempt";
import { normalizeReservedSeats } from "../../domain/reservationAttempt";
import { normalizeReservationPolicy, type ReservationPolicy } from "../../domain/reservationPolicy";
import { isWatchStatus, type WatchStatus } from "../../domain/watch";

export interface LegacyWatchSnapshot {
  id: string;
  status: string;
  provider?: string;
  route?: string;
  train?: string;
  seatClassLabel?: string;
  date?: string;
  departure?: string;
  arrival?: string;
  latestReservationAttempt?: unknown;
  latestReservationAttemptCandidateId?: unknown;
  payment_deadline?: string | null;
  reservationCandidateContexts?: unknown;
  reservationPolicy?: string;
  seatFoundObservation?: unknown;
  updated_at?: string | null;
}

export interface WatchLifecycleAttempt {
  outcome: ReservationAttemptOutcome;
  startedAt: string | null;
  finishedAt: string | null;
  retryable: boolean;
  manualCheckRequired: boolean;
  retryCondition: ReservationRetryCondition | null;
  progressStages?: ReadonlyArray<ReservationProgressStage>;
  paymentHoldEndedAt: string | null;
  paymentHoldEndReason?: PaymentHoldEndReason | null;
  reservedSeats?: ReadonlyArray<ReservedSeat>;
}

export type WatchLifecycleCandidateContext = Partial<ReservationCandidateContext>;

export interface WatchLifecycleSeatObservation {
  observedAt: string | null;
  kind?: "official_provider" | "mock";
  source?: string;
  observedLabel?: string;
}

export interface WatchLifecycleSnapshot {
  id: string;
  status: WatchStatus | "unknown";
  provider: string;
  route: string;
  train: string;
  seatClassLabel: string;
  date: string;
  departure: string;
  arrival: string;
  latestReservationAttempt: WatchLifecycleAttempt | null;
  latestReservationAttemptCandidateId?: string | null;
  paymentDeadline: string | null;
  reservationCandidateContexts: Readonly<Record<string, WatchLifecycleCandidateContext>>;
  reservationPolicy: ReservationPolicy;
  seatFoundObservation: WatchLifecycleSeatObservation | null;
  updatedAt: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function trimmedText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

function legacyAttempt(value: unknown): WatchLifecycleAttempt | null {
  if (!isRecord(value)) return null;
  const reason = value.paymentHoldEndReason;
  const legacyReservedSeats = Array.isArray(value.reservedSeats)
    ? value.reservedSeats.map((seat) => isRecord(seat)
      ? { car_number: seat.carNumber, seat_number: seat.seatNumber }
      : seat)
    : value.reserved_seats;
  return {
    outcome: typeof value.outcome === "string"
      && [
        "pending",
        "payment_required",
        "reserved",
        "not_available",
        "auth_required",
        "provider_blocked",
        "failed",
        "unknown",
      ].includes(value.outcome)
      ? value.outcome as ReservationAttemptOutcome
      : "pending",
    startedAt: nonEmptyString(value.startedAt),
    finishedAt: nonEmptyString(value.finishedAt),
    retryable: value.retryable === true,
    manualCheckRequired: value.manualCheckRequired === true,
    retryCondition: value.retryCondition === "new_availability_episode"
      || value.retryCondition === "provider_account_reverified"
      ? value.retryCondition
      : null,
    progressStages: Array.isArray(value.progressStages)
      ? value.progressStages.filter((item): item is ReservationProgressStage => (
        isRecord(item)
        && typeof item.stage === "string"
        && typeof item.occurredAt === "string"
      ))
      : [],
    paymentHoldEndedAt: nonEmptyString(value.paymentHoldEndedAt),
    paymentHoldEndReason: reason === "confirmed_payment_deadline_elapsed"
      || reason === "confirmed_payment_hold_no_longer_present"
      ? reason
      : null,
    reservedSeats: normalizeReservedSeats(legacyReservedSeats),
  };
}

function legacyCandidateContexts(
  value: unknown,
): Readonly<Record<string, WatchLifecycleCandidateContext>> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).flatMap(([candidateId, candidate]) => {
    if (!isRecord(candidate)) return [];
    const context: WatchLifecycleCandidateContext = {};
    for (const field of ["train", "seatClassLabel", "date", "departure", "arrival"] as const) {
      const fieldValue = trimmedText(candidate[field]);
      if (fieldValue !== null) context[field] = fieldValue;
    }
    return [[candidateId, context]];
  }));
}

function legacySeatObservation(value: unknown): WatchLifecycleSeatObservation | null {
  if (!isRecord(value)) return null;
  return { observedAt: nonEmptyString(value.observedAt) };
}

function isWatchLifecycleSnapshot(
  watch: LegacyWatchSnapshot | WatchLifecycleSnapshot,
): watch is WatchLifecycleSnapshot {
  return "paymentDeadline" in watch && "updatedAt" in watch;
}

export function mapWatchLifecycleSnapshot(watch: WatchReadModel): WatchLifecycleSnapshot {
  const attempt = watch.latestReservationAttempt ?? null;
  return {
    id: watch.id,
    status: watch.status,
    provider: watch.provider,
    route: watch.route,
    train: watch.train,
    seatClassLabel: watch.seatClassLabel,
    date: watch.date,
    departure: watch.departure,
    arrival: watch.arrival,
    latestReservationAttempt: attempt === null
      ? null
      : {
        outcome: attempt.outcome,
        startedAt: attempt.startedAt,
        finishedAt: attempt.finishedAt,
        retryable: attempt.retryable,
        manualCheckRequired: attempt.manualCheckRequired,
        retryCondition: attempt.retryCondition,
        progressStages: attempt.progressStages ?? [],
        paymentHoldEndedAt: attempt.paymentHoldEndedAt,
        paymentHoldEndReason: attempt.paymentHoldEndReason ?? null,
        reservedSeats: attempt.reservedSeats ?? [],
      },
    latestReservationAttemptCandidateId: watch.latestReservationAttemptCandidateId ?? null,
    paymentDeadline: watch.paymentDeadline ?? null,
    reservationCandidateContexts: watch.reservationCandidateContexts ?? {},
    reservationPolicy: normalizeReservationPolicy(watch.reservationPolicy),
    seatFoundObservation: watch.seatFoundObservation ?? null,
    updatedAt: watch.updatedAt ?? null,
  };
}

export function mapLegacyWatchLifecycleSnapshot(
  watch: LegacyWatchSnapshot,
): WatchLifecycleSnapshot {
  return {
    id: watch.id,
    status: isWatchStatus(watch.status) ? watch.status : "unknown",
    provider: watch.provider ?? "철도",
    route: watch.route ?? "여정 정보 없음",
    train: watch.train ?? "열차 정보 없음",
    seatClassLabel: watch.seatClassLabel ?? "좌석",
    date: watch.date ?? "날짜 미정",
    departure: watch.departure ?? "--:--",
    arrival: watch.arrival ?? "--:--",
    latestReservationAttempt: legacyAttempt(watch.latestReservationAttempt),
    latestReservationAttemptCandidateId: nonEmptyString(
      watch.latestReservationAttemptCandidateId,
    ),
    paymentDeadline: typeof watch.payment_deadline === "string" ? watch.payment_deadline : null,
    reservationCandidateContexts: legacyCandidateContexts(watch.reservationCandidateContexts),
    reservationPolicy: normalizeReservationPolicy(watch.reservationPolicy),
    seatFoundObservation: legacySeatObservation(watch.seatFoundObservation),
    updatedAt: typeof watch.updated_at === "string" ? watch.updated_at : null,
  };
}

export function mapCompatibleWatchLifecycleSnapshot(
  watch: LegacyWatchSnapshot | WatchLifecycleSnapshot,
): WatchLifecycleSnapshot {
  return isWatchLifecycleSnapshot(watch) ? watch : mapLegacyWatchLifecycleSnapshot(watch);
}
