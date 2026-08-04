import { normalizeSeatObservationReason } from "../domain/seatDiagnostics";

const SEAT_CLASS_IDS = ["standard", "first"] as const;
const SEAT_STATUSES: ReadonlySet<string> = new Set([
  "unavailable",
  "unknown",
  "available",
  "limited",
  "standing_plus_seat",
  "sold_out",
  "waitlist_available",
  "stale",
  "error",
  "not_enough_seats",
  "departed",
  "out_of_service",
  "reservation_completed",
  "not_offered",
]);
const OBSERVED_SEAT_STATUSES: ReadonlySet<string> = new Set([
  "unavailable",
  "available",
  "limited",
  "standing_plus_seat",
  "sold_out",
  "waitlist_available",
  "stale",
  "error",
  "not_enough_seats",
  "departed",
  "out_of_service",
  "reservation_completed",
  "not_offered",
]);

const MAX_USER_CONFIRMATION_TTL_MS = 5 * 60 * 1000;
const REGISTRATION_EVIDENCE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;

type SeatClassId = (typeof SEAT_CLASS_IDS)[number];
type UnknownRecord = Record<string, unknown>;

interface SeatAction extends UnknownRecord {
  kind: string;
  url: string | null;
}

export interface NormalizedSeatClass extends UnknownRecord {
  seat_class: SeatClassId;
  status: string;
  fare: number | null;
  fare_currency: "KRW";
  provenance: UnknownRecord;
  registration_evidence_id: string | null;
  registration_evidence_error: string | null;
  actions: SeatAction[];
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function normalizedRegistrationEvidenceId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return REGISTRATION_EVIDENCE_ID_PATTERN.test(normalized) ? normalized : null;
}

export function awareTimestamp(value: unknown): value is string {
  return typeof value === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
}

function officialPageTtlMs(provenance: UnknownRecord): number | null {
  if (!awareTimestamp(provenance.observed_at) || !awareTimestamp(provenance.fresh_until)) {
    return null;
  }
  const ttl = Date.parse(provenance.fresh_until) - Date.parse(provenance.observed_at);
  return ttl > 0 && ttl <= MAX_USER_CONFIRMATION_TTL_MS ? ttl : null;
}

function monotonicNow(): number | null {
  if (typeof performance === "undefined" || typeof performance.now !== "function") return null;
  const value = performance.now();
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function hasValidObservation(provenance: UnknownRecord): boolean {
  if (![
    "official_provider",
    "official_page_browser_companion",
    "user_confirmed_official_page",
    "mock",
  ].includes(String(provenance.kind ?? ""))) return false;
  if (typeof provenance.source !== "string" || !provenance.source.trim()) return false;
  if (!awareTimestamp(provenance.observed_at)) return false;
  if (![
    "official_page_browser_companion",
    "user_confirmed_official_page",
  ].includes(String(provenance.kind))) return true;
  const expectedSource = provenance.kind === "official_page_browser_companion"
    ? "korail-official-browser-companion"
    : "official-page-user-confirmation";
  if (provenance.source !== expectedSource) return false;
  return officialPageTtlMs(provenance) !== null && monotonicNow() !== null;
}

const OFFICIAL_HOST_ROOTS: Readonly<Record<string, readonly string[]>> = {
  KORAIL: ["korail.com", "letskorail.com"],
  SRT: ["srail.kr"],
  MOCK: ["example.invalid"],
};

function safeOfficialUrl(value: unknown, provider: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    const roots = OFFICIAL_HOST_ROOTS[String(provider ?? "").toUpperCase()] ?? [];
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    const allowed = roots.some((root) => host === root || host.endsWith(`.${root}`));
    return url.protocol === "https:" && allowed ? url.toString() : null;
  } catch {
    return null;
  }
}

function unknownSeatClass(
  seatClass: SeatClassId,
  officialBookingUrl: unknown,
  provider: unknown,
  reason = "public_api_not_available",
  registrationEvidenceId: unknown = null,
): NormalizedSeatClass {
  const officialUrl = safeOfficialUrl(officialBookingUrl, provider);
  const normalizedProvider = String(provider ?? "").toUpperCase();
  const normalizedEvidenceId = normalizedRegistrationEvidenceId(registrationEvidenceId);
  const registrationEvidenceRequired = normalizedProvider === "KORAIL" || normalizedProvider === "SRT";
  return {
    seat_class: seatClass,
    status: "unknown",
    fare: null,
    fare_currency: "KRW",
    provenance: {
      kind: "not_observed",
      source: null,
      observed_at: null,
      reason: normalizeSeatObservationReason(reason),
    },
    registration_evidence_id: normalizedEvidenceId,
    registration_evidence_error: registrationEvidenceRequired && !normalizedEvidenceId
      ? "대기 등록 근거를 확인할 수 없어 관심 열차에 추가할 수 없습니다. 시간표를 다시 조회해 주세요."
      : null,
    actions: [
      ...(officialUrl ? [{ kind: "official_check", url: officialUrl }] : []),
      ...(!registrationEvidenceRequired || normalizedEvidenceId
        ? [{ kind: "add_to_watch", url: null }]
        : []),
    ],
  };
}

function normalizedActions(raw: UnknownRecord, provider: unknown): SeatAction[] {
  if (!Array.isArray(raw.actions)) return [];
  return raw.actions.flatMap((value): SeatAction[] => {
    if (!isRecord(value) || typeof value.kind !== "string") return [];
    if (value.kind.startsWith("official_")) {
      const url = safeOfficialUrl(value.url, provider);
      return url ? [{ ...value, kind: value.kind, url }] : [];
    }
    if (value.kind === "add_to_watch" || value.kind === "retry_provider") {
      return [{ ...value, kind: value.kind, url: null }];
    }
    return [];
  });
}

export function normalizeSeatClass(
  rawValue: unknown,
  seatClass: SeatClassId,
  officialBookingUrl: unknown,
  provider: unknown,
): NormalizedSeatClass {
  if (!isRecord(rawValue) || rawValue.seat_class !== seatClass || !SEAT_STATUSES.has(String(rawValue.status))) {
    return unknownSeatClass(
      seatClass,
      officialBookingUrl,
      provider,
      "invalid_provider_payload",
      isRecord(rawValue) ? rawValue.registration_evidence_id : null,
    );
  }
  const raw = rawValue;
  const provenance = isRecord(raw.provenance) ? raw.provenance : {};
  const observed = [
    "official_provider",
    "official_page_browser_companion",
    "user_confirmed_official_page",
    "mock",
  ].includes(String(provenance.kind ?? ""));
  const hasEvidence = observed && hasValidObservation(provenance);
  if (OBSERVED_SEAT_STATUSES.has(String(raw.status)) && !hasEvidence) {
    return unknownSeatClass(
      seatClass,
      officialBookingUrl,
      provider,
      "invalid_provider_provenance",
      raw.registration_evidence_id,
    );
  }
  if (raw.status === "unknown" && provenance.kind !== "not_observed" && !hasEvidence) {
    return unknownSeatClass(
      seatClass,
      officialBookingUrl,
      provider,
      "invalid_provider_provenance",
      raw.registration_evidence_id,
    );
  }
  const actions = normalizedActions(raw, provider);
  const normalizedProvider = String(provider ?? "").toUpperCase();
  const registrationEvidenceId = normalizedRegistrationEvidenceId(raw.registration_evidence_id);
  const registrationEvidenceRequired = (normalizedProvider === "KORAIL" || normalizedProvider === "SRT")
    && provenance.kind !== "mock";
  const registrationActionNeedsEvidence = actions.some((action) => action.kind === "add_to_watch");
  const safeActions = registrationEvidenceRequired && !registrationEvidenceId
    ? actions.filter((action) => action.kind !== "add_to_watch")
    : actions;
  const normalizedProvenance = provenance.kind === "not_observed"
    ? { ...provenance, reason: normalizeSeatObservationReason(provenance.reason) }
    : ["official_page_browser_companion", "user_confirmed_official_page"].includes(String(provenance.kind))
      ? {
        ...provenance,
        client_freshness: {
          received_monotonic_ms: monotonicNow(),
          ttl_ms: officialPageTtlMs(provenance),
        },
      }
      : provenance;
  return {
    ...raw,
    seat_class: seatClass,
    status: String(raw.status),
    fare: typeof raw.fare === "number" && Number.isFinite(raw.fare) ? raw.fare : null,
    fare_currency: "KRW",
    provenance: normalizedProvenance,
    registration_evidence_id: registrationEvidenceId,
    registration_evidence_error: registrationEvidenceRequired
      && registrationActionNeedsEvidence
      && !registrationEvidenceId
      ? "대기 등록 근거를 확인할 수 없어 관심 열차에 추가할 수 없습니다. 시간표를 다시 조회해 주세요."
      : null,
    actions: safeActions,
  };
}

export function normalizeSeatClasses(item: unknown): NormalizedSeatClass[] {
  const record = isRecord(item) ? item : {};
  const rawSeatClasses = Array.isArray(record.seat_classes) ? record.seat_classes : [];
  const byClass = new Map<unknown, unknown>(rawSeatClasses.map((seat) => [
    isRecord(seat) ? seat.seat_class : undefined,
    seat,
  ]));
  return SEAT_CLASS_IDS.map((seatClass) => normalizeSeatClass(
    byClass.get(seatClass),
    seatClass,
    record.official_booking_url,
    record.provider,
  ));
}
