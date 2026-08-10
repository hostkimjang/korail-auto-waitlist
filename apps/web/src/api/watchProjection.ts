import { seatObservationReasonMeta } from "../domain/seatDiagnostics";
import {
  mapLatestReservationAttempt,
  type LatestReservationAttempt,
} from "../domain/reservationAttempt";
import {
  normalizeReservationPolicy,
  type ReservationPolicy,
} from "../domain/reservationPolicy";
import {
  isWatchSeatClass,
  type WatchObservationMode,
  type WatchProvider,
  type WatchSeatClass,
  type WatchStatus,
} from "../domain/watch";
import {
  mapOperationalCandidate,
  type OperationalCandidateMeta,
} from "../domain/watchOperational";
import {
  awareTimestamp,
  normalizedRegistrationEvidenceId,
  normalizeSeatClass,
  safeOfficialChannelUrl,
  safeOfficialUrl,
} from "./seatClasses";
import { timetableTimeLabel } from "./timetables";
import {
  parseWatchCandidateReadDto,
  parseWatchReadDto,
  type WatchCandidateReadDto,
} from "./watchReadDto";

type UnknownRecord = Record<string, unknown>;

export { safeOfficialChannelUrl };

interface WatchCandidateReadModelBase {
  id: string;
  priority: number;
}

export interface WatchCandidateReadModel extends WatchCandidateReadModelBase {
  trainNumber: string;
  departureAt: string;
  arrivalAt: string | null;
  seatClass: WatchSeatClass;
}

export interface MappedWatchCandidate extends WatchCandidateReadModelBase {
  train_number: string;
  departure_at: string;
  arrival_at: string | null;
  seat_class: WatchSeatClass;
}

export type ProjectedWatchCandidate = WatchCandidateReadModel & MappedWatchCandidate;

interface ValidWatchCandidate {
  raw: WatchCandidateReadDto;
  mapped: ProjectedWatchCandidate;
}

export interface SeatFoundObservation {
  kind: "official_provider" | "mock";
  source: string;
  observedAt: string;
  observedLabel: string;
}

export interface ReservationCandidateContext {
  train: string;
  seatClassLabel: string;
  date: string;
  departure: string;
  arrival: string;
}

interface WatchReadModelBase {
  id: string;
  provider: WatchProvider;
  status: WatchStatus;
  train: string;
  route: string;
  departure: string;
  arrival: string;
  date: string;
  statusLabel: string;
  seatClass: WatchSeatClass;
  seatClassLabel: string;
  seatEvidenceLabel: string;
  registrationEvidenceLabel: string;
  activityLabel: string;
  lastCheckedAt: string | null;
  lastCheckedLabel: string;
  origin: string;
  destination: string;
  travelDate: string;
  officialBookingUrl: string | null;
  operational: OperationalCandidateMeta | null;
  latestReservationAttempt: LatestReservationAttempt | null;
  latestReservationAttemptCandidateId?: string | null;
  seatFoundObservation: SeatFoundObservation | null;
  reservationCandidateContexts: Record<string, ReservationCandidateContext>;
  reservationPolicy: ReservationPolicy;
  seatObservationMode: WatchObservationMode;
  focusedObservationIntervalSeconds: number;
  nextCheckAt: string | null;
}

export interface WatchReadModel extends WatchReadModelBase {
  readonly candidates: readonly WatchCandidateReadModel[];
  paymentDeadline: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface MappedWatch extends WatchReadModelBase {
  readonly candidates: readonly MappedWatchCandidate[];
  payment_deadline: string | null;
  created_at: string | null;
  updated_at: string | null;
  official_booking_url: string | null;
  reservation_policy: ReservationPolicy;
  paymentDeadline?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type ProjectedWatch = Omit<WatchReadModel, "candidates">
  & Omit<MappedWatch, "candidates">
  & { readonly candidates: readonly ProjectedWatchCandidate[] };

const STATUS_LABELS: Readonly<Record<WatchStatus, string>> = {
  draft: "초안",
  scheduled: "대기 등록됨",
  watching: "감시 중",
  official_waitlist: "공식 예약대기",
  seat_found: "좌석 발견",
  reserving: "예약 진행",
  payment_required: "결제 필요",
  completed: "결제 완료",
  paused: "일시정지",
  cooldown: "요청 제한",
  auth_required: "로그인 필요",
  expired: "만료",
  failed: "실패",
};
const SEAT_CLASS_LABELS: Readonly<Record<WatchSeatClass, string>> = {
  standard: "일반실",
  first: "특실",
  any: "좌석 무관",
};
const ACTIONABLE_OBSERVATION_STATUSES: ReadonlySet<string> = new Set([
  "available",
  "limited",
  "standing_plus_seat",
]);
const LATEST_OBSERVATION_STATUSES: ReadonlySet<string> = new Set([
  "unavailable",
  "unknown",
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
  "stale",
  "error",
]);
const OBSERVATION_SOURCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function watchSeatClass(value: unknown): WatchSeatClass | null {
  return isWatchSeatClass(value) ? value : null;
}

function timeLabel(value: unknown): string {
  return typeof value === "string" ? value.slice(0, 5) : "--:--";
}

function dateLabel(value: string): string {
  const parsed = new Date(`${value}T00:00:00+09:00`);
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
    timeZone: "Asia/Seoul",
  }).format(parsed);
}

interface LatestObservationMeta {
  status: string;
  source: string;
  observedAt: string;
  freshUntil: string;
  fresh: boolean;
  actionable: boolean;
  label: string;
}

function observationSource(value: unknown, provider: WatchProvider): string | null {
  if (typeof value !== "string") return null;
  const source = value.trim();
  if (!OBSERVATION_SOURCE_PATTERN.test(source)) return null;
  const mockSource = source.toLowerCase() === "mock";
  if ((provider === "MOCK") !== mockSource) return null;
  return source;
}

function latestObservationMeta(
  observation: unknown,
  seatClassLabel: string,
  provider: WatchProvider,
): LatestObservationMeta | null {
  if (!isRecord(observation)) return null;
  const status = typeof observation.status === "string" ? observation.status.toLowerCase() : "";
  const source = observationSource(observation.source, provider);
  const observedAt = awareTimestamp(observation.observed_at) ? observation.observed_at : null;
  const freshUntil = awareTimestamp(observation.fresh_until) ? observation.fresh_until : null;
  if (
    !LATEST_OBSERVATION_STATUSES.has(status)
    || source === null
    || observedAt === null
    || freshUntil === null
    || Date.parse(freshUntil) <= Date.parse(observedAt)
  ) return null;
  const fresh = Date.parse(freshUntil) > Date.now();
  const labels: Readonly<Record<string, string>> = {
    available: "예매 가능",
    limited: "매진 임박",
    standing_plus_seat: "입석+좌석",
    sold_out: "매진",
    waitlist_available: "예약대기 가능",
    not_enough_seats: "좌석 부족",
    not_offered: "예매 불가",
    departed: "출발 완료",
    out_of_service: "운행 없음",
    stale: "관측 만료",
    error: "조회 오류",
    unknown: "확인 필요",
    unavailable: "예매 불가",
  };
  const statusLabel = fresh ? labels[status] ?? "확인 필요" : "관측 만료 · 다시 확인 중";
  return {
    status,
    source,
    observedAt,
    freshUntil,
    fresh,
    actionable: fresh && ACTIONABLE_OBSERVATION_STATUSES.has(status),
    label: `${seatClassLabel} · ${statusLabel} · 최근 관측 ${timetableTimeLabel(observedAt)}`,
  };
}

function hasActionableLatestObservation(
  candidate: WatchCandidateReadDto,
  provider: WatchProvider,
): boolean {
  const latest = isRecord(candidate.latest_observation) ? candidate.latest_observation : null;
  return latestObservationMeta(latest, "좌석", provider)?.actionable === true;
}

function optionalAwareTimestamp(value: unknown): string | null {
  return awareTimestamp(value) ? value : null;
}

export function mapWatch(value: unknown): ProjectedWatch {
  const watch = parseWatchReadDto(value);
  const prioritizedCandidates = watch.candidates
    .flatMap((item): ValidWatchCandidate[] => {
      const candidate = parseWatchCandidateReadDto(item);
      return candidate === null
        ? []
        : [{
          raw: candidate,
          mapped: {
            id: candidate.id,
            trainNumber: candidate.train_number,
            departureAt: candidate.departure_at,
            arrivalAt: candidate.arrival_at,
            seatClass: candidate.seat_class,
            train_number: candidate.train_number,
            departure_at: candidate.departure_at,
            arrival_at: candidate.arrival_at,
            seat_class: candidate.seat_class,
            priority: candidate.priority,
          },
        }];
    })
    .sort((left, right) => left.mapped.priority - right.mapped.priority);
  const statePreferredCandidate = watch.status === "reserving"
    ? prioritizedCandidates.find(({ raw }) => raw.state === "reservation_attempted")
    : watch.status === "payment_required"
      ? prioritizedCandidates.find(({ raw }) => raw.state === "payment_required")
      : ["auth_required", "failed"].includes(watch.status)
        ? prioritizedCandidates.find(({ raw }) => raw.state === "failed")
        : null;
  const selectedCandidate = statePreferredCandidate
    ?? (watch.status === "seat_found"
      ? prioritizedCandidates.find(({ raw }) => (
        hasActionableLatestObservation(raw, watch.provider)
      ))
      : null)
    ?? prioritizedCandidates[0]
    ?? null;
  const latestAttemptOwner = prioritizedCandidates.reduce<{
    candidateId: string;
    attempt: LatestReservationAttempt;
  } | null>((latest, { raw, mapped }) => {
    const attempt = mapLatestReservationAttempt(raw.latest_reservation_attempt);
    if (attempt === null) return latest;
    if (latest === null || Date.parse(attempt.startedAt) > Date.parse(latest.attempt.startedAt)) {
      return { candidateId: mapped.id, attempt };
    }
    return latest;
  }, null);
  const candidate = selectedCandidate?.raw ?? null;
  const mappedCandidate = selectedCandidate?.mapped ?? null;
  const seatClass = mappedCandidate?.seatClass
    ?? watchSeatClass(watch.seat_class)
    ?? "any";
  const seatClassLabel = SEAT_CLASS_LABELS[seatClass];
  const lastCheckedAt = awareTimestamp(watch.last_checked_at) ? watch.last_checked_at : null;
  const lastCheckedLabel = lastCheckedAt
    ? `최근 확인 ${timetableTimeLabel(lastCheckedAt)}`
    : "최근 확인 기록 없음";
  const normalizedProvider = watch.provider;
  const evidence = isRecord(candidate?.registration_evidence)
    ? candidate.registration_evidence
    : null;
  let seatEvidenceLabel = `${seatClassLabel} · 등록 근거 없음`;

  if (evidence) {
    const evidenceId = normalizedRegistrationEvidenceId(evidence.id);
    const createdAt = awareTimestamp(evidence.created_at) ? Date.parse(evidence.created_at) : Number.NaN;
    const validUntil = awareTimestamp(evidence.registration_valid_until)
      ? Date.parse(evidence.registration_valid_until)
      : Number.NaN;
    const outerEvidenceValid = Boolean(evidenceId)
      && Number.isFinite(createdAt)
      && Number.isFinite(validUntil)
      && validUntil > createdAt;
    const normalizedSeat = normalizeSeatClass(
      outerEvidenceValid
        ? {
          seat_class: seatClass,
          status: evidence.status,
          provenance: evidence.provenance,
          actions: [],
        }
        : null,
      seatClass === "any" ? "standard" : seatClass,
      null,
      watch.provider,
    );
    const provenance = normalizedSeat.provenance;
    const observed = [
      "official_provider",
      "official_page_browser_companion",
      "user_confirmed_official_page",
      "mock",
    ].includes(String(provenance?.kind ?? ""));
    const statusLabel = normalizedSeat.status === "available"
      || normalizedSeat.status === "limited"
      || normalizedSeat.status === "standing_plus_seat"
      ? "예매 가능"
      : normalizedSeat.status === "sold_out"
        ? "매진"
        : normalizedSeat.status === "waitlist_available"
          ? "예약대기 가능"
          : normalizedSeat.status === "unknown"
            || normalizedSeat.status === "stale"
            || normalizedSeat.status === "error"
            ? observed
              ? "확인 필요"
              : seatObservationReasonMeta(provenance?.reason).label
            : "예매 불가";
    if (!observed) {
      seatEvidenceLabel = `${seatClassLabel} · ${statusLabel}`;
    } else {
      const observedAt = timetableTimeLabel(provenance?.observed_at);
      const sourceLabel = provenance?.kind === "official_provider"
        ? "공식 관측"
        : provenance?.kind === "official_page_browser_companion"
          ? "공식 화면 동기화"
          : provenance?.kind === "user_confirmed_official_page"
            ? "공식 페이지에서 직접 확인"
            : "데모 관측";
      seatEvidenceLabel = `${seatClassLabel} · ${statusLabel} · ${sourceLabel} ${observedAt}`;
    }
  }
  const registrationEvidenceLabel = seatEvidenceLabel;
  const currentObservation = latestObservationMeta(
    candidate?.latest_observation,
    seatClassLabel,
    normalizedProvider,
  );
  const operational = mapOperationalCandidate(candidate);
  const latestReservationAttempt = latestAttemptOwner?.attempt ?? null;
  if (currentObservation) seatEvidenceLabel = currentObservation.label;
  const seatFoundObservation: SeatFoundObservation | null = watch.status === "seat_found"
    && currentObservation?.actionable
    && ["KORAIL", "SRT", "MOCK"].includes(normalizedProvider)
    ? {
      kind: normalizedProvider === "MOCK" ? "mock" : "official_provider",
      source: currentObservation.source,
      observedAt: currentObservation.observedAt,
      observedLabel: `최근 확인 ${timetableTimeLabel(currentObservation.observedAt)}`,
    }
    : null;
  const reservationCandidateContexts = Object.fromEntries(prioritizedCandidates.map(({ mapped }) => (
    [mapped.id, {
      train: mapped.trainNumber,
      seatClassLabel: SEAT_CLASS_LABELS[mapped.seatClass],
      date: dateLabel(watch.travel_date),
      departure: timetableTimeLabel(mapped.departureAt),
      arrival: mapped.arrivalAt !== null
        ? timetableTimeLabel(mapped.arrivalAt)
        : timeLabel(watch.time_to),
    }]
  )));
  const officialBookingUrl = safeOfficialUrl(watch.official_booking_url, normalizedProvider);
  const reservationPolicy = normalizeReservationPolicy(watch.reservation_policy);
  const paymentDeadline = optionalAwareTimestamp(watch.payment_deadline);
  const createdAt = optionalAwareTimestamp(watch.created_at);
  const updatedAt = optionalAwareTimestamp(watch.updated_at);
  return {
    id: watch.id,
    provider: normalizedProvider,
    status: watch.status,
    candidates: prioritizedCandidates.map(({ mapped }) => mapped),
    payment_deadline: paymentDeadline,
    created_at: createdAt,
    updated_at: updatedAt,
    official_booking_url: officialBookingUrl,
    reservation_policy: reservationPolicy,
    paymentDeadline,
    createdAt,
    updatedAt,
    train: mappedCandidate !== null
      ? mappedCandidate.trainNumber
      : Array.isArray(watch.train_numbers) && typeof watch.train_numbers[0] === "string"
        ? watch.train_numbers[0]
        : "열차 미정",
    route: `${watch.origin} → ${watch.destination}`,
    departure: mappedCandidate !== null
      ? timetableTimeLabel(mappedCandidate.departureAt)
      : timeLabel(watch.time_from),
    arrival: mappedCandidate?.arrivalAt !== null && mappedCandidate?.arrivalAt !== undefined
      ? timetableTimeLabel(mappedCandidate.arrivalAt)
      : timeLabel(watch.time_to),
    date: dateLabel(watch.travel_date),
    statusLabel: STATUS_LABELS[watch.status],
    seatClass,
    seatClassLabel,
    seatEvidenceLabel,
    registrationEvidenceLabel,
    activityLabel: seatEvidenceLabel,
    lastCheckedAt,
    lastCheckedLabel,
    operational,
    latestReservationAttempt,
    latestReservationAttemptCandidateId: latestAttemptOwner?.candidateId ?? null,
    origin: watch.origin,
    destination: watch.destination,
    travelDate: watch.travel_date,
    officialBookingUrl,
    seatFoundObservation,
    reservationCandidateContexts,
    reservationPolicy,
    seatObservationMode: watch.seat_observation_mode === "focused" ? "focused" : "balanced",
    focusedObservationIntervalSeconds: Number.isInteger(watch.focused_observation_interval_seconds)
      && Number(watch.focused_observation_interval_seconds) >= 20
      && Number(watch.focused_observation_interval_seconds) <= 30
      ? Number(watch.focused_observation_interval_seconds)
      : 25,
    nextCheckAt: awareTimestamp(watch.next_check_at) ? watch.next_check_at : null,
  };
}
