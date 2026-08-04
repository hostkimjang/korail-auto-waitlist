import type { SeatClass, WatchRegistrationState } from "./useInstantWatchRegistration";
import { normalizeReservationPolicy } from "../../domain/reservationPolicy";

type RecordValue = Record<string, unknown>;

interface SeatRegistrationIdentity {
  provider: string;
  trainNumber: string;
  departureInstant: number;
  seatClass: SeatClass;
}

const activeWatchStatuses = new Set([
  "draft",
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
  "reserving",
  "paused",
  "cooldown",
  "auth_required",
]);

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function seatClassFrom(value: unknown): SeatClass | null {
  return value === "standard" || value === "first" || value === "any" ? value : null;
}

function departureInstant(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const instant = Date.parse(value);
  return Number.isFinite(instant) ? instant : null;
}

function trainIdentity(train: unknown, seatClass: SeatClass): SeatRegistrationIdentity | null {
  if (!isRecord(train)) return null;
  const provider = typeof train.provider === "string" ? train.provider.trim().toUpperCase() : "";
  const trainNumber = typeof train.train_number === "string"
    ? train.train_number.trim()
    : typeof train.name === "string"
      ? train.name.trim()
      : "";
  const instant = departureInstant(train.departure_at);
  if (!provider || !trainNumber || instant === null) return null;
  return { provider, trainNumber, departureInstant: instant, seatClass };
}

function candidateMatches(candidate: unknown, identity: SeatRegistrationIdentity): boolean {
  if (!isRecord(candidate)) return false;
  const provider = typeof candidate.provider === "string" ? candidate.provider.trim().toUpperCase() : "";
  const trainNumber = typeof candidate.train_number === "string" ? candidate.train_number.trim() : "";
  const instant = departureInstant(candidate.departure_at);
  const seatClass = seatClassFrom(candidate.seat_class);
  return provider === identity.provider
    && trainNumber === identity.trainNumber
    && instant === identity.departureInstant
    && seatClass === identity.seatClass;
}

/**
 * Reconnects a remounted Step 3 seat button to an active DB watch.  Candidate
 * times are compared as instants because the API may serialize an original KST
 * timestamp as UTC after persistence.
 */
export function persistedSeatRegistration(
  watches: readonly unknown[],
  train: unknown,
  seatClass: SeatClass,
): WatchRegistrationState | null {
  const identity = trainIdentity(train, seatClass);
  if (!identity) return null;

  for (const watch of watches) {
    if (
      !isRecord(watch)
      || typeof watch.status !== "string"
      || !activeWatchStatuses.has(watch.status)
      || typeof watch.id !== "string"
      || !watch.id.trim()
    ) {
      continue;
    }
    const candidates = Array.isArray(watch.candidates) ? watch.candidates : [];
    if (candidates.some((candidate) => candidateMatches({
      ...(isRecord(candidate) ? candidate : {}),
      provider: watch.provider,
    }, identity))) {
      return {
        status: "active",
        watchId: watch.id,
        reservationPolicy: normalizeReservationPolicy(
          watch.reservationPolicy ?? watch.reservation_policy,
        ),
      };
    }
  }
  return null;
}

export function resolvedSeatRegistration(
  local: WatchRegistrationState,
  watches: readonly unknown[],
  train: unknown,
  seatClass: SeatClass,
): WatchRegistrationState {
  return local.status === "idle"
    ? persistedSeatRegistration(watches, train, seatClass) ?? local
    : local;
}
