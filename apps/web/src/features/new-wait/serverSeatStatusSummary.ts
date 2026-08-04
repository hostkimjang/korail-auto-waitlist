export type ServerSeatStatusState =
  | "loading"
  | "complete"
  | "elapsed"
  | "partial"
  | "restricted"
  | "error"
  | "empty";

export interface ServerSeatStatusSummary {
  state: ServerSeatStatusState;
  observedSeatCount: number;
  unknownSeatCount: number;
  retryableProviders: string[];
  korailImportContext: KorailImportContext | null;
}

export interface KorailImportContext {
  origin: string;
  destination: string;
  travelDate: string;
}

interface TrainLike {
  provider: string;
  seatClasses: SeatLike[];
  origin: string | null;
  destination: string | null;
  travelDate: string | null;
}

interface SeatLike {
  status: SeatStatus;
  provenanceKind: ProvenanceKind;
  unobservedReason: string | null;
}

type SeatStatus =
  | "unavailable"
  | "unknown"
  | "available"
  | "limited"
  | "standing_plus_seat"
  | "sold_out"
  | "waitlist_available"
  | "stale"
  | "error"
  | "not_enough_seats"
  | "departed"
  | "out_of_service"
  | "reservation_completed"
  | "not_offered";

type ProvenanceKind =
  | "not_observed"
  | "official_provider"
  | "official_page_browser_companion"
  | "user_confirmed_official_page"
  | "mock";

const observedProvenanceKinds = new Set<ProvenanceKind>([
  "official_provider",
  // Legacy snapshots remain readable, but the current UI never asks users to
  // install or operate the browser companion.
  "official_page_browser_companion",
  "user_confirmed_official_page",
]);

export function summarizeServerSeatStatus(
  trainsValue: unknown,
  selectedProvidersValue: unknown,
  providerResultsValue: unknown,
  loadingProvidersValue: unknown,
): ServerSeatStatusSummary {
  const providers = normalizedProviders(selectedProvidersValue);
  const loadingProviders = normalizedProviders(loadingProvidersValue);
  if (loadingProviders.length > 0) {
    return {
      state: "loading",
      observedSeatCount: 0,
      unknownSeatCount: 0,
      retryableProviders: [],
      korailImportContext: null,
    };
  }

  const trains = normalizedTrains(trainsValue);
  const korailImportContext = exactKorailImportContext(trains);
  const failedProviders = failedProviderNames(providerResultsValue, providers);
  let observedSeatCount = 0;
  let unknownSeatCount = 0;
  const providersWithUnknownSeats = new Set<string>();
  const restrictedProviders = new Set<string>();
  const elapsedProviders = new Set<string>();
  const providersWithOtherUnknownReasons = new Set<string>();

  for (const train of trains) {
    for (const seat of train.seatClasses) {
      if (seat.status !== "unknown" && observedProvenanceKinds.has(seat.provenanceKind)) {
        observedSeatCount += 1;
      } else {
        unknownSeatCount += 1;
        providersWithUnknownSeats.add(train.provider);
        if (seat.unobservedReason === "provider_access_restricted") {
          restrictedProviders.add(train.provider);
        } else if (seat.unobservedReason === "departure_window_elapsed") {
          elapsedProviders.add(train.provider);
        } else {
          providersWithOtherUnknownReasons.add(train.provider);
        }
      }
    }
  }

  const retryableProviders = providers.filter((provider) => {
    if (restrictedProviders.has(provider)) return false;
    const hasOnlyElapsedUnknownSeats = elapsedProviders.has(provider)
      && !providersWithOtherUnknownReasons.has(provider);
    return failedProviders.has(provider)
      || (providersWithUnknownSeats.has(provider) && !hasOnlyElapsedUnknownSeats);
  });
  if (failedProviders.size > 0) {
    return {
      state: "error",
      observedSeatCount,
      unknownSeatCount,
      retryableProviders,
      korailImportContext,
    };
  }
  if (
    providersWithUnknownSeats.size > 0
    && [...providersWithUnknownSeats].every((provider) => (
      restrictedProviders.has(provider) && !providersWithOtherUnknownReasons.has(provider)
      && !elapsedProviders.has(provider)
    ))
  ) {
    return {
      state: "restricted",
      observedSeatCount,
      unknownSeatCount,
      retryableProviders,
      korailImportContext,
    };
  }
  if (
    observedSeatCount === 0
    && unknownSeatCount > 0
    && [...providersWithUnknownSeats].every((provider) => (
      elapsedProviders.has(provider)
      && !restrictedProviders.has(provider)
      && !providersWithOtherUnknownReasons.has(provider)
    ))
  ) {
    return {
      state: "elapsed",
      observedSeatCount,
      unknownSeatCount,
      retryableProviders: [],
      korailImportContext,
    };
  }
  if (unknownSeatCount > 0) {
    return {
      state: "partial",
      observedSeatCount,
      unknownSeatCount,
      retryableProviders,
      korailImportContext,
    };
  }
  return {
    state: trains.length > 0 ? "complete" : "empty",
    observedSeatCount,
    unknownSeatCount,
    retryableProviders: [],
    korailImportContext,
  };
}

function normalizedProviders(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.flatMap((item) => {
    if (typeof item !== "string") return [];
    const provider = item.trim().toUpperCase();
    return provider === "KORAIL" || provider === "SRT" ? [provider] : [];
  }))];
}

function normalizedTrains(value: unknown): TrainLike[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isRecord(item) || typeof item.provider !== "string" || !Array.isArray(item.seat_classes)) {
      return [];
    }
    const provider = item.provider.trim().toUpperCase();
    if (provider !== "KORAIL" && provider !== "SRT") return [];
    const seatClasses = item.seat_classes.flatMap((seat): SeatLike[] => {
      if (!isRecord(seat)) return [];
      const rawStatus = normalizedSeatStatus(seat.status);
      const rawProvenanceKind = isRecord(seat.provenance)
        ? normalizedProvenanceKind(seat.provenance.kind)
        : null;
      const unobservedReason = isRecord(seat.provenance)
        && typeof seat.provenance.reason === "string"
        ? seat.provenance.reason
        : null;
      if (rawStatus === null) {
        return [{
          status: "unknown",
          provenanceKind: "not_observed",
          unobservedReason: "invalid_provider_payload",
        }];
      }
      if (rawProvenanceKind === null) {
        return [{
          status: "unknown",
          provenanceKind: "not_observed",
          unobservedReason: "invalid_provider_provenance",
        }];
      }
      if (rawStatus !== "unknown" && rawProvenanceKind === "not_observed") {
        return [{
          status: "unknown",
          provenanceKind: "not_observed",
          unobservedReason: unobservedReason ?? "invalid_provider_provenance",
        }];
      }
      return [{
        status: rawStatus,
        provenanceKind: rawProvenanceKind,
        unobservedReason,
      }];
    });
    const origin = normalizedNonEmptyString(item.origin);
    const destination = normalizedNonEmptyString(item.destination);
    const travelDate = normalizedTravelDate(item.departure_at);
    return [{ provider, seatClasses, origin, destination, travelDate }];
  });
}

function normalizedSeatStatus(value: unknown): SeatStatus | null {
  if (typeof value !== "string") return null;
  switch (value) {
    case "unavailable":
    case "unknown":
    case "available":
    case "limited":
    case "standing_plus_seat":
    case "sold_out":
    case "waitlist_available":
    case "stale":
    case "error":
    case "not_enough_seats":
    case "departed":
    case "out_of_service":
    case "reservation_completed":
    case "not_offered":
      return value;
    default:
      return null;
  }
}

function normalizedProvenanceKind(value: unknown): ProvenanceKind | null {
  if (typeof value !== "string") return null;
  switch (value) {
    case "not_observed":
    case "official_provider":
    case "official_page_browser_companion":
    case "user_confirmed_official_page":
    case "mock":
      return value;
    default:
      return null;
  }
}

function exactKorailImportContext(trains: readonly TrainLike[]): KorailImportContext | null {
  const korailTrains = trains.filter((train) => train.provider === "KORAIL");
  if (
    korailTrains.length === 0 ||
    korailTrains.some((train) => (
      train.origin === null || train.destination === null || train.travelDate === null
    ))
  ) {
    return null;
  }
  const identities = new Map<string, KorailImportContext>();
  for (const train of korailTrains) {
    if (train.origin === null || train.destination === null || train.travelDate === null) continue;
    const context = {
      origin: train.origin,
      destination: train.destination,
      travelDate: train.travelDate,
    };
    identities.set(JSON.stringify(context), context);
  }
  return identities.size === 1 ? [...identities.values()][0] ?? null : null;
}

function normalizedNonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function normalizedTravelDate(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = /^(20\d{2})-(\d{2})-(\d{2})T/.exec(value);
  if (match === null) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return `${match[1]}-${match[2]}-${match[3]}`;
}

function failedProviderNames(value: unknown, providers: readonly string[]): Set<string> {
  if (!isRecord(value)) return new Set();
  return new Set(providers.filter((provider) => {
    const result = value[provider];
    return isRecord(result) && result.status === "error";
  }));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
