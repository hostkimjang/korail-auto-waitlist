export type LiveKorailSeatClass = "standard" | "first";

export type LiveKorailSeatStatus =
  | "available"
  | "limited"
  | "standing_plus_seat"
  | "standing_only"
  | "sold_out"
  | "waitlist_available"
  | "not_offered";

export type LiveKorailExpectedAction =
  | "official_booking"
  | "official_waitlist"
  | "add_to_watch";

export type LiveKorailFailureCause =
  | "provider_access_restricted"
  | "provider_timeout"
  | "source_unavailable"
  | "passenger_count_not_supported"
  | "no_exact_match"
  | "not_observed"
  | "invalid_status_response"
  | "invalid_capability_response"
  | "invalid_timetable_response"
  | "no_official_provider_observation";

export type LiveKorailPreflight =
  | { state: "ready"; cause: null; retryAfterSeconds: null }
  | {
      state: "cooldown";
      cause: "provider_access_restricted" | "source_unavailable";
      retryAfterSeconds: number;
    };

export interface LiveKorailCapability {
  enabled: true;
  timetable: true;
  officialBookingLink: true;
  officialWaitlistLink: false;
  seatMonitoring: boolean;
  reservationOnce: boolean;
}

export interface LiveKorailSeatObservation {
  seatClass: LiveKorailSeatClass;
  status: LiveKorailSeatStatus;
  observedAt: string;
  registrationEvidencePresent: boolean;
}

export interface LiveKorailObservedTrain {
  trainNumber: string;
  departureAt: string;
  standard: LiveKorailSeatObservation;
  first: LiveKorailSeatObservation;
}

export interface LiveKorailJourneyExpectation {
  origin: string;
  destination: string;
  departureDate: string;
  departureFrom: string;
  departureTo: string;
}

const seatStatuses = new Set<LiveKorailSeatStatus>([
  "available",
  "limited",
  "standing_plus_seat",
  "standing_only",
  "sold_out",
  "waitlist_available",
  "not_offered",
]);

const artifactCauses = new Set<LiveKorailFailureCause>([
  "provider_access_restricted",
  "provider_timeout",
  "source_unavailable",
  "passenger_count_not_supported",
  "no_exact_match",
  "not_observed",
  "invalid_status_response",
  "invalid_capability_response",
  "invalid_timetable_response",
  "no_official_provider_observation",
]);

export function assertNoLiveKorailWatchRegistration(payload: unknown): void {
  if (!Array.isArray(payload)) throw new Error("invalid_timetable_response");
  const korailItems = payload.filter(
    (item) => isRecord(item) && item.provider === "korail",
  );
  if (korailItems.length === 0) throw new Error("invalid_timetable_response");
  for (const item of korailItems) {
    if (!isRecord(item) || !Array.isArray(item.seat_classes)) {
      throw new Error("invalid_timetable_response");
    }
    for (const seat of item.seat_classes) {
      if (!isRecord(seat) || !Array.isArray(seat.actions)) {
        throw new Error("invalid_timetable_response");
      }
      if (seat.registration_evidence_id !== null && seat.registration_evidence_id !== undefined) {
        throw new Error("unexpected_watch_registration_evidence");
      }
      if (seat.actions.some((action) => isRecord(action) && action.kind === "add_to_watch")) {
        throw new Error("unexpected_watch_registration_action");
      }
    }
  }
}

function isLiveKorailSeatStatus(value: unknown): value is LiveKorailSeatStatus {
  return typeof value === "string" && seatStatuses.has(value as LiveKorailSeatStatus);
}

function isLiveKorailFailureCause(value: unknown): value is LiveKorailFailureCause {
  return typeof value === "string" && artifactCauses.has(value as LiveKorailFailureCause);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseObservedAt(value: unknown, minimumEpochMs: number): string | null {
  if (
    typeof value !== "string"
    || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  ) return null;
  const epochMs = Date.parse(value);
  return Number.isFinite(epochMs) && epochMs >= minimumEpochMs ? value : null;
}

export function parseLiveKorailPreflight(payload: unknown): LiveKorailPreflight {
  if (!Array.isArray(payload)) throw new Error("invalid_status_response");
  const matching = payload.filter(
    (item) => isRecord(item)
      && item.provider === "korail"
      && item.source === "korail_browser",
  );
  if (matching.length !== 1) throw new Error("invalid_status_response");
  const item = matching[0];
  if (!isRecord(item)) throw new Error("invalid_status_response");
  if (
    item.state === "ready"
    && item.cause === null
    && item.retry_after_seconds === null
  ) return { state: "ready", cause: null, retryAfterSeconds: null };
  if (
    item.state === "cooldown"
    && (item.cause === "provider_access_restricted" || item.cause === "source_unavailable")
    && typeof item.retry_after_seconds === "number"
    && Number.isFinite(item.retry_after_seconds)
    && item.retry_after_seconds > 0
  ) {
    return {
      state: "cooldown",
      cause: item.cause,
      retryAfterSeconds: Math.ceil(item.retry_after_seconds),
    };
  }
  throw new Error("invalid_status_response");
}

export function parseLiveKorailCapability(payload: unknown): LiveKorailCapability {
  if (!Array.isArray(payload)) throw new Error("invalid_capability_response");
  const officialKorail = payload.filter((item) => {
    if (!isRecord(item)) return false;
    return item.provider === "korail"
      && item.timetable === true
      && item.experimental === false;
  });
  if (officialKorail.length !== 1) throw new Error("invalid_capability_response");
  const item = officialKorail[0];
  if (
    !isRecord(item)
    || item.enabled !== true
    || item.timetable !== true
    || item.official_booking_link !== true
    || item.official_waitlist_link !== false
    || typeof item.seat_monitoring !== "boolean"
    || typeof item.reservation_once !== "boolean"
  ) throw new Error("invalid_capability_response");
  return {
    enabled: true,
    timetable: true,
    officialBookingLink: true,
    officialWaitlistLink: false,
    seatMonitoring: item.seat_monitoring,
    reservationOnce: item.reservation_once,
  };
}

function isOfficialKorailUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    const allowedHost = ["korail.com", "letskorail.com"].some(
      (root) => host === root || host.endsWith(`.${root}`),
    );
    return url.protocol === "https:" && allowedHost && url.username === "" && url.password === "";
  } catch {
    return false;
  }
}

function expectedRawActions(
  status: LiveKorailSeatStatus,
  seatMonitoring: boolean,
): Array<"official_check" | "official_waitlist" | "add_to_watch"> {
  if (["available", "limited", "standing_plus_seat"].includes(status)) {
    return seatMonitoring ? ["official_check", "add_to_watch"] : ["official_check"];
  }
  if (status === "standing_only") {
    return seatMonitoring ? ["official_check", "add_to_watch"] : ["official_check"];
  }
  if (status === "waitlist_available") {
    return seatMonitoring ? ["official_waitlist", "add_to_watch"] : ["official_waitlist"];
  }
  if (status === "sold_out" && seatMonitoring) return ["add_to_watch"];
  return [];
}

function parseSeat(
  value: unknown,
  seatClass: LiveKorailSeatClass,
  minimumEpochMs: number,
  officialBookingUrl: string,
  seatMonitoring: boolean,
): LiveKorailSeatObservation | null {
  if (
    !isRecord(value)
    || value.seat_class !== seatClass
    || !isLiveKorailSeatStatus(value.status)
    || !isRecord(value.provenance)
    || value.provenance.kind !== "official_provider"
    || value.provenance.source !== "korail-official-page-browser"
    || !Array.isArray(value.actions)
  ) return null;
  const observedAt = parseObservedAt(value.provenance.observed_at, minimumEpochMs);
  if (observedAt === null) return null;
  const expectedActions = expectedRawActions(value.status, seatMonitoring);
  const requiresEvidence = seatMonitoring
    && [
      "available",
      "limited",
      "standing_plus_seat",
      "standing_only",
      "sold_out",
      "waitlist_available",
    ].includes(value.status);
  if (
    requiresEvidence
      ? typeof value.registration_evidence_id !== "string"
        || value.registration_evidence_id.length === 0
      : value.registration_evidence_id !== null
        && value.registration_evidence_id !== undefined
  ) return null;
  if (value.actions.length !== expectedActions.length) return null;
  for (const [index, expectedAction] of expectedActions.entries()) {
    const action = value.actions[index];
    if (!isRecord(action) || action.kind !== expectedAction) return null;
    if (expectedAction === "add_to_watch") {
      if (action.url !== null && action.url !== undefined) return null;
      continue;
    }
    if (
      !isOfficialKorailUrl(action.url)
      || action.url !== officialBookingUrl
    ) return null;
  }
  return {
    seatClass,
    status: value.status,
    observedAt,
    registrationEvidencePresent: requiresEvidence,
  };
}

export function parseFreshLiveKorailTrain(
  payload: unknown,
  minimumObservedAtEpochMs: number,
  journey?: LiveKorailJourneyExpectation,
  seatMonitoring = false,
): LiveKorailObservedTrain {
  if (!Array.isArray(payload)) throw new Error("invalid_timetable_response");
  for (const item of payload) {
    if (
      !isRecord(item)
      || item.provider !== "korail"
      || typeof item.train_number !== "string"
      || item.train_number.trim().length === 0
      || !isOfficialKorailUrl(item.official_booking_url)
      || !Array.isArray(item.seat_classes)
    ) continue;
    if (journey !== undefined) {
      if (
        item.origin !== journey.origin
        || item.destination !== journey.destination
        || typeof item.departure_at !== "string"
      ) continue;
      const departureEpochMs = Date.parse(item.departure_at);
      const fromEpochMs = Date.parse(`${journey.departureDate}T${journey.departureFrom}:00+09:00`);
      const toEpochMs = Date.parse(`${journey.departureDate}T${journey.departureTo}:00+09:00`);
      if (
        !Number.isFinite(departureEpochMs)
        || departureEpochMs < fromEpochMs
        || departureEpochMs > toEpochMs
      ) continue;
    }
    const standardValues = item.seat_classes.filter(
      (seat) => isRecord(seat) && seat.seat_class === "standard",
    );
    const firstValues = item.seat_classes.filter(
      (seat) => isRecord(seat) && seat.seat_class === "first",
    );
    if (standardValues.length !== 1 || firstValues.length !== 1) continue;
    const officialBookingUrl = item.official_booking_url;
    const standard = parseSeat(
      standardValues[0],
      "standard",
      minimumObservedAtEpochMs,
      officialBookingUrl,
      seatMonitoring,
    );
    const first = parseSeat(
      firstValues[0],
      "first",
      minimumObservedAtEpochMs,
      officialBookingUrl,
      seatMonitoring,
    );
    if (
      standard !== null
      && first !== null
      && (standard.status !== "not_offered" || first.status !== "not_offered")
    ) {
      return {
        trainNumber: item.train_number,
        departureAt: typeof item.departure_at === "string" ? item.departure_at : "",
        standard,
        first,
      };
    }
  }
  throw new Error("no_official_provider_observation");
}

export function expectedLiveKorailActions(
  status: LiveKorailSeatStatus,
  seatMonitoring: boolean,
): LiveKorailExpectedAction[] {
  if (["available", "limited", "standing_plus_seat"].includes(status)) {
    return seatMonitoring ? ["official_booking", "add_to_watch"] : ["official_booking"];
  }
  if (status === "standing_only") {
    return seatMonitoring ? ["official_booking", "add_to_watch"] : ["official_booking"];
  }
  if (status === "waitlist_available") {
    return seatMonitoring
      ? ["official_waitlist", "add_to_watch"]
      : ["official_waitlist"];
  }
  if (status === "sold_out" && seatMonitoring) return ["add_to_watch"];
  return [];
}

export function sanitizedLiveKorailFailureCause(value: unknown): LiveKorailFailureCause {
  if (isLiveKorailFailureCause(value)) return value;
  return "no_official_provider_observation";
}

export function failureCauseFromLiveTimetable(payload: unknown): LiveKorailFailureCause {
  if (!Array.isArray(payload)) return "invalid_timetable_response";
  for (const item of payload) {
    if (!isRecord(item) || item.provider !== "korail" || !Array.isArray(item.seat_classes)) {
      continue;
    }
    for (const seat of item.seat_classes) {
      if (
        isRecord(seat)
        && isRecord(seat.provenance)
        && seat.provenance.kind === "not_observed"
      ) return sanitizedLiveKorailFailureCause(seat.provenance.reason);
    }
  }
  return "no_official_provider_observation";
}
