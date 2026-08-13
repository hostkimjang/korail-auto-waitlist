export type OperationsHealthStatus = "healthy" | "fresh" | "stale" | "unknown";
export type OperationsEntryLevel = "info" | "warning" | "error";
export type OperationsEntryProvider = "KORAIL" | "SRT" | "MOCK";
export type ProviderCircuitState = "closed" | "open" | "half_open" | "manual_hold" | "unknown";
export type OperationsSeatClass = "standard" | "first" | "infant" | "free" | "waitlist" | "any";
export type OperationsEntryReasonCode =
  | "reservation_pending"
  | "reservation_payment_required"
  | "reservation_reserved"
  | "reservation_not_available"
  | "reservation_auth_required"
  | "reservation_provider_blocked"
  | "reservation_failed"
  | "reservation_unknown"
  | "payment_completed"
  | "payment_deadline_elapsed_monitoring_resumed"
  | "payment_hold_no_longer_present_monitoring_resumed"
  | "payment_deadline_elapsed_one_off_expired"
  | "payment_hold_no_longer_present_one_off_expired";

export interface OperationsWindow {
  fromAt: string | null;
  toAt: string | null;
  hours: number | null;
}

export interface OperationsRate {
  numerator: number | null;
  denominator: number | null;
  rate: number | null;
  definition: string | null;
}

export interface OperationsWindowCounts {
  seatObservations: number | null;
  seatObservationErrors: number | null;
  reservationAttempts: number | null;
  reservationFailures: number | null;
  watchTransitions: number | null;
  watchFailureTransitions: number | null;
  notificationEvents: number | null;
  notificationSent: number | null;
  notificationFailed: number | null;
}

export interface OperationsStatusCount {
  status: string;
  count: number | null;
}

export interface OperationsCurrentCounts {
  watchesByStatus: OperationsStatusCount[];
  notificationOutboxPending: number | null;
}

export interface OperationsSourceFreshness {
  source: string;
  status: OperationsHealthStatus;
  observedAt: string | null;
  ageSeconds: number | null;
  timestampBasis: string;
}

export interface OperationsServiceState {
  service: string;
  status: OperationsHealthStatus;
  observedAt: string | null;
  evidence: string;
}

export interface ProviderCircuit {
  provider: string;
  state: ProviderCircuitState;
  updatedAt: string | null;
  manualResumeRequired: boolean | null;
}

export interface OperationsEntry {
  occurredAt: string | null;
  kind: string;
  level: OperationsEntryLevel;
  status: string;
  errorCategory: string | null;
  provider: OperationsEntryProvider | null;
  trainNumber: string | null;
  departureAt: string | null;
  seatClass: OperationsSeatClass | null;
  reasonCode: OperationsEntryReasonCode | null;
}

export interface OperationsSummary {
  generatedAt: string | null;
  window: OperationsWindow;
  seatObservationErrorRate: OperationsRate;
  notificationDeliveryFailureRate: OperationsRate;
  windowCounts: OperationsWindowCounts;
  currentCounts: OperationsCurrentCounts;
  sourceFreshness: OperationsSourceFreshness[];
  services: OperationsServiceState[];
  providerCircuits: ProviderCircuit[];
  recentEntries: OperationsEntry[];
  limitations: string[];
  isPartial: boolean;
}

const healthStatuses = new Set<OperationsHealthStatus>(["healthy", "fresh", "stale", "unknown"]);
const entryLevels = new Set<OperationsEntryLevel>(["info", "warning", "error"]);
const entryProviders = new Set<OperationsEntryProvider>(["KORAIL", "SRT", "MOCK"]);
const circuitStates = new Set<ProviderCircuitState>(["closed", "open", "half_open", "manual_hold", "unknown"]);
const seatClasses = new Set<OperationsSeatClass>([
  "standard",
  "first",
  "infant",
  "free",
  "waitlist",
  "any",
]);
const entryReasonCodes = new Set<OperationsEntryReasonCode>([
  "reservation_pending",
  "reservation_payment_required",
  "reservation_reserved",
  "reservation_not_available",
  "reservation_auth_required",
  "reservation_provider_blocked",
  "reservation_failed",
  "reservation_unknown",
  "payment_completed",
  "payment_deadline_elapsed_monitoring_resumed",
  "payment_hold_no_longer_present_monitoring_resumed",
  "payment_deadline_elapsed_one_off_expired",
  "payment_hold_no_longer_present_one_off_expired",
]);
const knownLimitations = new Set([
  "http_and_process_errors_are_not_durably_recorded",
  "worker_and_scheduler_health_require_durable_heartbeats",
  "recent_entries_are_sanitized_categories_without_identifiers_or_raw_errors",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeString(value: unknown): string {
  return typeof value === "string" ? value.trim().slice(0, 80) : "";
}

function safeDefinition(value: unknown): string | null {
  if (typeof value !== "string") return null;
  // eslint-disable-next-line no-control-regex -- Admin summaries intentionally remove control characters.
  const normalized = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  return normalized ? normalized.slice(0, 240) : null;
}

function nonNegativeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function boundedRate(value: unknown): number | null {
  const parsed = nonNegativeNumber(value);
  return parsed !== null && parsed <= 1 ? parsed : null;
}

function timestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  return Number.isNaN(new Date(value).getTime()) ? null : value;
}

function healthStatus(value: unknown): OperationsHealthStatus {
  return typeof value === "string" && healthStatuses.has(value as OperationsHealthStatus)
    ? (value as OperationsHealthStatus)
    : "unknown";
}

function entryLevel(value: unknown): OperationsEntryLevel {
  return typeof value === "string" && entryLevels.has(value as OperationsEntryLevel)
    ? (value as OperationsEntryLevel)
    : "warning";
}

function seatClass(value: unknown): OperationsSeatClass | null {
  return typeof value === "string" && seatClasses.has(value as OperationsSeatClass)
    ? (value as OperationsSeatClass)
    : null;
}

function entryReasonCode(value: unknown): OperationsEntryReasonCode | null {
  return typeof value === "string" && entryReasonCodes.has(value as OperationsEntryReasonCode)
    ? (value as OperationsEntryReasonCode)
    : null;
}

function entryProvider(value: unknown): OperationsEntryProvider | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toUpperCase();
  return entryProviders.has(normalized as OperationsEntryProvider)
    ? (normalized as OperationsEntryProvider)
    : null;
}

function trainNumber(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  const containsControlText = /[\u0000-\u001f\u007f]/.test(normalized); // eslint-disable-line no-control-regex
  return normalized.length > 0
    && normalized.length <= 40
    && !containsControlText
    ? normalized
    : null;
}

function circuitState(value: unknown): ProviderCircuitState {
  return typeof value === "string" && circuitStates.has(value as ProviderCircuitState)
    ? (value as ProviderCircuitState)
    : "unknown";
}

function rate(value: unknown): OperationsRate {
  const item = isRecord(value) ? value : {};
  const numerator = nonNegativeNumber(item.numerator);
  const denominator = nonNegativeNumber(item.denominator);
  const parsedRate = boundedRate(item.rate);
  return {
    numerator,
    denominator,
    rate: numerator !== null && denominator !== null && numerator <= denominator ? parsedRate : null,
    definition: safeDefinition(item.definition),
  };
}

function statusCounts(value: unknown): OperationsStatusCount[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    status: safeString(item.status),
    count: nonNegativeNumber(item.count),
  }));
}

function sourceFreshness(value: unknown): OperationsSourceFreshness[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    source: safeString(item.source),
    status: healthStatus(item.status),
    observedAt: timestamp(item.observed_at),
    ageSeconds: nonNegativeNumber(item.age_seconds),
    timestampBasis: safeString(item.timestamp_basis),
  }));
}

function services(value: unknown): OperationsServiceState[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    service: safeString(item.service),
    status: healthStatus(item.status),
    observedAt: timestamp(item.observed_at),
    evidence: safeString(item.evidence),
  }));
}

function recentEntries(value: unknown): OperationsEntry[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).slice(0, 20).map((item) => ({
    occurredAt: timestamp(item.occurred_at),
    kind: safeString(item.kind),
    level: entryLevel(item.level),
    status: safeString(item.status),
    errorCategory: safeString(item.error_category) || null,
    provider: entryProvider(item.provider),
    trainNumber: trainNumber(item.train_number),
    departureAt: timestamp(item.departure_at),
    seatClass: seatClass(item.seat_class),
    reasonCode: entryReasonCode(item.reason_code),
  }));
}

export function mapOperationsSummary(payload: unknown): OperationsSummary {
  if (!isRecord(payload)) throw new Error("운영 상태 응답 형식을 확인할 수 없습니다.");

  const window = isRecord(payload.window) ? payload.window : {};
  const counts = isRecord(payload.window_counts) ? payload.window_counts : {};
  const currentCounts = isRecord(payload.current_counts) ? payload.current_counts : {};
  const providerCircuits = Array.isArray(payload.provider_circuits)
    ? payload.provider_circuits.filter(isRecord).map((item) => ({
        provider: safeString(item.provider).toUpperCase(),
        state: circuitState(item.state),
        updatedAt: timestamp(item.updated_at),
        manualResumeRequired: typeof item.manual_resume_required === "boolean"
          ? item.manual_resume_required
          : null,
      }))
    : [];
  const result: OperationsSummary = {
    generatedAt: timestamp(payload.generated_at),
    window: {
      fromAt: timestamp(window.from_at),
      toAt: timestamp(window.to_at),
      hours: nonNegativeNumber(window.hours),
    },
    seatObservationErrorRate: rate(payload.seat_observation_error_rate),
    notificationDeliveryFailureRate: rate(payload.notification_delivery_failure_rate),
    windowCounts: {
      seatObservations: nonNegativeNumber(counts.seat_observations),
      seatObservationErrors: nonNegativeNumber(counts.seat_observation_errors),
      reservationAttempts: nonNegativeNumber(counts.reservation_attempts),
      reservationFailures: nonNegativeNumber(counts.reservation_failures),
      watchTransitions: nonNegativeNumber(counts.watch_transitions),
      watchFailureTransitions: nonNegativeNumber(counts.watch_failure_transitions),
      notificationEvents: nonNegativeNumber(counts.notification_events),
      notificationSent: nonNegativeNumber(counts.notification_sent),
      notificationFailed: nonNegativeNumber(counts.notification_failed),
    },
    currentCounts: {
      watchesByStatus: statusCounts(currentCounts.watches_by_status),
      notificationOutboxPending: nonNegativeNumber(currentCounts.notification_outbox_pending),
    },
    sourceFreshness: sourceFreshness(payload.source_freshness),
    services: services(payload.services),
    providerCircuits,
    recentEntries: recentEntries(payload.recent_entries),
    limitations: Array.isArray(payload.limitations)
      ? payload.limitations.filter((item): item is string => typeof item === "string" && knownLimitations.has(item))
      : [],
    isPartial: false,
  };

  const requiredNumbers = [
    result.window.hours,
    result.seatObservationErrorRate.numerator,
    result.seatObservationErrorRate.denominator,
    result.notificationDeliveryFailureRate.numerator,
    result.notificationDeliveryFailureRate.denominator,
    ...Object.values(result.windowCounts),
    result.currentCounts.notificationOutboxPending,
  ];
  result.isPartial = result.generatedAt === null
    || result.window.fromAt === null
    || result.window.toAt === null
    || requiredNumbers.some((value) => value === null)
    || result.services.length === 0
    || result.services.some((item) => item.status === "unknown")
    || result.sourceFreshness.some((item) => item.status === "unknown")
    || [result.seatObservationErrorRate, result.notificationDeliveryFailureRate].some((item) => (
      item.definition === null
      || (item.denominator !== null && item.denominator > 0 && item.rate === null)
      || (item.numerator !== null && item.denominator !== null && item.numerator > item.denominator)
    ));
  return result;
}

export function isOperationsSummaryEmpty(summary: OperationsSummary): boolean {
  return Object.values(summary.windowCounts).every((value) => value === null)
    && summary.currentCounts.watchesByStatus.length === 0
    && summary.currentCounts.notificationOutboxPending === null
    && summary.services.length === 0
    && summary.sourceFreshness.length === 0
    && summary.providerCircuits.length === 0
    && summary.recentEntries.length === 0;
}
