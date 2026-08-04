export type OperationalStatus =
  | "scheduled"
  | "delayed"
  | "boarding"
  | "departed_origin"
  | "cancelled"
  | "unknown";

export type BookingWindowStatus = "open" | "waitlist" | "closed" | "unknown";

export interface OperationalCandidateMeta {
  status: OperationalStatus;
  bookingWindowStatus: BookingWindowStatus;
  delayMinutes: number | null;
  estimatedDepartureAt: string | null;
  actualDepartureAt: string | null;
  observedAt: string | null;
  fresh: boolean;
  label: string;
}

const operationalStatuses: ReadonlySet<string> = new Set([
  "scheduled",
  "delayed",
  "boarding",
  "departed_origin",
  "cancelled",
  "unknown",
]);
const bookingWindowStatuses: ReadonlySet<string> = new Set([
  "open",
  "waitlist",
  "closed",
  "unknown",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function enumValue<T extends string>(
  value: unknown,
  allowed: ReadonlySet<string>,
  fallback: T,
): T {
  if (typeof value !== "string") return fallback;
  const normalized = value.toLowerCase();
  return allowed.has(normalized) ? normalized as T : fallback;
}

function awareTimestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  return Number.isNaN(Date.parse(value)) ? null : value;
}

function koreaTimeLabel(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

export function mapOperationalCandidate(
  candidate: unknown,
  now: Date = new Date(),
): OperationalCandidateMeta | null {
  if (!isRecord(candidate)) return null;
  const status = enumValue<OperationalStatus>(
    candidate.operational_status,
    operationalStatuses,
    "unknown",
  );
  const bookingWindowStatus = enumValue<BookingWindowStatus>(
    candidate.booking_window_status,
    bookingWindowStatuses,
    "unknown",
  );
  const delayMinutes = typeof candidate.delay_minutes === "number"
    && Number.isInteger(candidate.delay_minutes)
    && candidate.delay_minutes >= 0
    ? candidate.delay_minutes
    : null;
  const estimatedDepartureAt = awareTimestamp(candidate.estimated_departure_at);
  const actualDepartureAt = awareTimestamp(candidate.actual_departure_at);
  const observedAt = awareTimestamp(candidate.operational_observed_at);
  const freshUntil = awareTimestamp(candidate.operational_fresh_until);
  const fresh = observedAt !== null
    && freshUntil !== null
    && Date.parse(freshUntil) > now.getTime();
  const hasOperationalFact = status !== "unknown"
    || bookingWindowStatus !== "unknown"
    || estimatedDepartureAt !== null
    || actualDepartureAt !== null;

  if (!hasOperationalFact) return null;
  if (!fresh) {
    return {
      status,
      bookingWindowStatus,
      delayMinutes,
      estimatedDepartureAt,
      actualDepartureAt,
      observedAt,
      fresh: false,
      label: "운행·예매 상태 관측 만료 · 다시 확인 중",
    };
  }

  const parts: string[] = [];
  if (status === "delayed") {
    parts.push(delayMinutes === null ? "지연 운행" : `${delayMinutes}분 지연`);
  } else if (status === "boarding") {
    parts.push("승차 진행 중");
  } else if (status === "cancelled") {
    parts.push("운행 취소");
  } else if (status === "departed_origin") {
    parts.push("출발역 통과");
  }
  if (estimatedDepartureAt) parts.push(`예상 ${koreaTimeLabel(estimatedDepartureAt)}`);
  if (actualDepartureAt) parts.push(`실제 ${koreaTimeLabel(actualDepartureAt)}`);
  if (bookingWindowStatus === "open") parts.push("예매창 열림");
  if (bookingWindowStatus === "waitlist") parts.push("예약대기 접수");
  if (bookingWindowStatus === "closed") parts.push("예매 종료");
  if (parts.length === 0) return null;

  return {
    status,
    bookingWindowStatus,
    delayMinutes,
    estimatedDepartureAt,
    actualDepartureAt,
    observedAt,
    fresh: true,
    label: parts.join(" · "),
  };
}
