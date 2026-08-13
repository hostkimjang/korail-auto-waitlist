import type {
  WatchObservationExecutionState,
  WatchProvider,
  WatchStatus,
} from "./watch";

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

export interface OperationalWatchContext {
  provider: WatchProvider;
  watchStatus: WatchStatus;
  nextCheckAt: string | null;
  observationExecutionState: WatchObservationExecutionState;
  cooldownUntil: string | null;
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
const supersedingUnavailableObservationStatuses: ReadonlySet<string> = new Set([
  "unavailable",
  "sold_out",
  "not_enough_seats",
  "not_offered",
  "departed",
  "out_of_service",
]);
const successfulObservationStatuses: ReadonlySet<string> = new Set([
  "unavailable",
  "available",
  "limited",
  "standing_plus_seat",
  "not_enough_seats",
  "sold_out",
  "waitlist_available",
  "reservation_completed",
  "not_offered",
  "departed",
  "out_of_service",
]);
const uncertainObservationStatuses: ReadonlySet<string> = new Set([
  "unknown",
  "stale",
]);
const observationErrorCategories: ReadonlySet<string> = new Set([
  "timeout",
  "schema_mismatch",
  "provider_unavailable",
  "partial_failure",
  "unknown",
]);
const activeObservationWatchStatuses: ReadonlySet<string> = new Set([
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
]);
const observationSourcePattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const operationalDelayGraceMilliseconds = 30_000;

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

type LatestObservationSignal =
  | { kind: "success"; status: string; observedAt: string }
  | { kind: "error"; status: "error"; observedAt: string }
  | { kind: "uncertain"; status: "unknown" | "stale"; observedAt: string }
  | { kind: "invalid"; observedAt: string | null };

function latestObservationSignal(
  candidate: Record<string, unknown>,
  provider: OperationalWatchContext["provider"],
): LatestObservationSignal | null {
  const observation = candidate.latest_observation;
  if (observation === null || observation === undefined) return null;
  if (!isRecord(observation)) return { kind: "invalid", observedAt: null };
  const status = typeof observation.status === "string"
    ? observation.status.toLowerCase()
    : "";
  const source = typeof observation.source === "string" ? observation.source.trim() : "";
  const observedAt = awareTimestamp(observation.observed_at);
  const freshUntil = awareTimestamp(observation.fresh_until);
  const mockSource = source.toLowerCase() === "mock";
  const sourceMatchesProvider = (provider === "MOCK") === mockSource;
  const errorCategory = observation.error_category;
  const validErrorCategory = typeof errorCategory === "string"
    && observationErrorCategories.has(errorCategory);
  if (
    !observationSourcePattern.test(source)
    || !sourceMatchesProvider
    || observedAt === null
    || freshUntil === null
    || Date.parse(freshUntil) < Date.parse(observedAt)
  ) return { kind: "invalid", observedAt };
  if (status === "error") {
    return validErrorCategory
      ? { kind: "error", status, observedAt }
      : { kind: "invalid", observedAt };
  }
  if (uncertainObservationStatuses.has(status)) {
    const allowedUncertainError = errorCategory === null
      || errorCategory === undefined
      || (status === "unknown" && validErrorCategory);
    if (!allowedUncertainError) {
      return { kind: "invalid", observedAt };
    }
    return {
      kind: "uncertain",
      status: status as "unknown" | "stale",
      observedAt,
    };
  }
  if (
    !successfulObservationStatuses.has(status)
    || (errorCategory !== null && errorCategory !== undefined)
  ) return { kind: "invalid", observedAt };
  return { kind: "success", status, observedAt };
}

function koreaTimeLabel(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function koreaTimeWithSecondsLabel(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function staleOperationalMeta(
  status: OperationalStatus,
  bookingWindowStatus: BookingWindowStatus,
  delayMinutes: number | null,
  estimatedDepartureAt: string | null,
  actualDepartureAt: string | null,
  observedAt: string | null,
  label: string,
): OperationalCandidateMeta {
  return {
    status,
    bookingWindowStatus,
    delayMinutes,
    estimatedDepartureAt,
    actualDepartureAt,
    observedAt,
    fresh: false,
    label,
  };
}

export function mapOperationalCandidate(
  candidate: unknown,
  now: Date,
  context: OperationalWatchContext,
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
  const source = typeof candidate.operational_source === "string"
    ? candidate.operational_source.trim()
    : "";
  const mockSource = source.toLowerCase() === "mock";
  const sourceMatchesProvider = (context.provider === "MOCK") === mockSource;
  const validProvenance = observationSourcePattern.test(source)
    && sourceMatchesProvider
    && observedAt !== null
    && freshUntil !== null
    && Date.parse(freshUntil) >= Date.parse(observedAt);
  const fresh = validProvenance
    && freshUntil !== null
    && Date.parse(freshUntil) > now.getTime();
  const hasOperationalFact = status !== "unknown"
    || bookingWindowStatus !== "unknown"
    || estimatedDepartureAt !== null
    || actualDepartureAt !== null;

  const hasTerminalFact = validProvenance && (
    status === "departed_origin"
      || status === "cancelled"
      || bookingWindowStatus === "closed"
      || actualDepartureAt !== null
  );

  if (hasTerminalFact && !fresh) {
    const terminalParts: string[] = [];
    if (status === "cancelled") terminalParts.push("운행 취소");
    if (status === "departed_origin") terminalParts.push("출발역 통과");
    if (actualDepartureAt) terminalParts.push(`실제 ${koreaTimeLabel(actualDepartureAt)}`);
    if (bookingWindowStatus === "closed") terminalParts.push("예매 종료");
    return staleOperationalMeta(
      status,
      bookingWindowStatus,
      delayMinutes,
      estimatedDepartureAt,
      actualDepartureAt,
      observedAt,
      terminalParts.join(" · "),
    );
  }

  if (!hasTerminalFact) {
    const activeObservation = activeObservationWatchStatuses.has(context.watchStatus);
    const healthActive = activeObservation || context.watchStatus === "cooldown";

    // A paused or terminal watch can retain the previous observation and cooldown timestamps.
    // Those historical fields must not look like a currently running health warning.
    if (!healthActive) {
      if (!fresh || !hasOperationalFact || !validProvenance) return null;
    } else {
      const latestSignal = latestObservationSignal(candidate, context.provider);
      const latestIsCurrent = latestSignal !== null && (
        !validProvenance
        || observedAt === null
        || latestSignal.observedAt === null
        || Date.parse(latestSignal.observedAt) >= Date.parse(observedAt)
      );
      const healthObservedAt = latestSignal?.observedAt ?? (validProvenance ? observedAt : null);
      const healthMeta = (label: string): OperationalCandidateMeta => staleOperationalMeta(
        "unknown",
        "unknown",
        null,
        null,
        null,
        healthObservedAt,
        label,
      );

      const cooldownUntil = awareTimestamp(context.cooldownUntil);
      if (cooldownUntil !== null && Date.parse(cooldownUntil) > now.getTime()) {
        return healthMeta(
          `운행·예매 상태 관측 일시 대기 · ${koreaTimeWithSecondsLabel(cooldownUntil)} 재개 목표`,
        );
      }
      if (latestIsCurrent && latestSignal?.kind === "error") {
        return healthMeta("운행·예매 상태 관측 오류 · 재시도 예정");
      }
      if (latestIsCurrent && latestSignal?.kind === "uncertain") {
        return healthMeta(latestSignal.status === "stale"
          ? "운행·예매 상태 관측 자료 만료 · 재시도 예정"
          : "운행·예매 상태 확인 필요 · 재시도 예정");
      }
      if (latestIsCurrent && latestSignal?.kind === "invalid") {
        return healthMeta("운행·예매 상태 확인 필요 · 재시도 예정");
      }
      if (context.observationExecutionState === "in_progress") {
        if (!fresh || !hasOperationalFact || !validProvenance) return null;
      } else {
        const nextCheckAt = awareTimestamp(context.nextCheckAt);
        if (nextCheckAt !== null) {
          const delayMilliseconds = now.getTime() - Date.parse(nextCheckAt);
          if (delayMilliseconds > operationalDelayGraceMilliseconds) {
            return healthMeta("운행·예매 상태 관측 지연 · 응답 대기 중");
          }
        } else {
          return healthMeta("운행·예매 상태 관측 지연 · 다음 확인 시각 확인 필요");
        }
      }

      if (
        latestIsCurrent
        && latestSignal?.kind === "success"
        && supersedingUnavailableObservationStatuses.has(latestSignal.status)
        && (!validProvenance
          || observedAt === null
          || latestSignal.observedAt === null
          || Date.parse(latestSignal.observedAt) > Date.parse(observedAt))
      ) return null;

      if (!fresh || !hasOperationalFact || !validProvenance) return null;
    }
  }

  if (!hasOperationalFact || !validProvenance || !fresh) return null;

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
