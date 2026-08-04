import { seatObservationReasonMeta } from "../domain/seatDiagnostics";
import {
  normalizeReservationPolicy,
  type ReservationPolicy,
} from "../domain/reservationPolicy";
import {
  mapLatestReservationAttempt,
  type LatestReservationAttempt,
} from "../domain/reservationAttempt";
import {
  mapOperationalCandidate,
  type OperationalCandidateMeta,
} from "../domain/watchOperational";
import { ApiError, request } from "./client";
import {
  awareTimestamp,
  normalizedRegistrationEvidenceId,
  normalizeSeatClass,
  safeOfficialUrl,
} from "./seatClasses";
import {
  formProviders,
  timetableTimeLabel,
  validateTravelDate,
  type TimetableSearchForm,
} from "./timetables";

type UnknownRecord = Record<string, unknown>;
export type WatchSeatClass = "standard" | "first" | "any";
export type WatchProvider = "KORAIL" | "SRT" | "MOCK";
export type WatchStatus =
  | "draft"
  | "scheduled"
  | "watching"
  | "official_waitlist"
  | "seat_found"
  | "reserving"
  | "payment_required"
  | "completed"
  | "paused"
  | "cooldown"
  | "auth_required"
  | "expired"
  | "failed";

export interface WatchCreateForm extends TimetableSearchForm {
  seat?: string;
  reservationPolicy?: string;
}

export interface WatchCreateCandidatePayload {
  train_number: string;
  departure_at: string;
  arrival_at: string | null;
  seat_class: WatchSeatClass;
  priority: number;
  registration_evidence_id?: string;
}

export interface WatchCreatePayload {
  provider: string;
  origin: string;
  destination: string;
  origin_node_id?: string;
  destination_node_id?: string;
  travel_date: string;
  time_from: string;
  time_to: string;
  seat_class: WatchSeatClass;
  passenger_count: number;
  train_numbers: string[];
  candidates: WatchCreateCandidatePayload[];
  notification_channel_ids: readonly string[];
  mode: "official";
  reservation_policy: "notify_only" | "reserve_once_before_payment";
}

export interface MappedWatchCandidate {
  id: string;
  train_number: string;
  departure_at: string;
  arrival_at: string | null;
  seat_class: WatchSeatClass;
  priority: number;
}

interface ValidWatchCandidate {
  raw: UnknownRecord;
  mapped: MappedWatchCandidate;
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

export interface MappedWatch {
  id: string;
  provider: WatchProvider;
  status: WatchStatus;
  candidates: MappedWatchCandidate[];
  payment_deadline: string | null;
  created_at: string | null;
  updated_at: string | null;
  official_booking_url: string | null;
  reservation_policy: ReservationPolicy;
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
  seatFoundObservation: SeatFoundObservation | null;
  reservationCandidateContexts: Record<string, ReservationCandidateContext>;
  reservationPolicy: ReservationPolicy;
  seatObservationMode: "balanced" | "focused";
  focusedObservationIntervalSeconds: number;
  nextCheckAt: string | null;
}

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
const WATCH_SEAT_CLASSES: ReadonlySet<string> = new Set(["standard", "first", "any"]);
const WATCH_PROVIDERS: ReadonlySet<string> = new Set<WatchProvider>(["KORAIL", "SRT", "MOCK"]);
const OFFICIAL_PROVIDERS: ReadonlySet<string> = new Set(["KORAIL", "SRT"]);
const WATCH_STATUSES: ReadonlySet<string> = new Set<WatchStatus>(
  Object.keys(STATUS_LABELS) as WatchStatus[],
);
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

function requiredString(value: unknown, message: string): string {
  if (typeof value !== "string" || !value.trim()) throw new ApiError(message);
  return value.trim();
}

function watchSeatClass(value: unknown): WatchSeatClass | null {
  return typeof value === "string" && WATCH_SEAT_CLASSES.has(value)
    ? value as WatchSeatClass
    : null;
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
  }).format(parsed);
}

function apiTimeValue(value: unknown): string {
  if (!awareTimestamp(value)) throw new ApiError("열차 운행 시각을 확인할 수 없습니다.");
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function requestedSeatClass(form: WatchCreateForm, train: UnknownRecord | null): WatchSeatClass {
  const selected = watchSeatClass(train?.selected_seat_class);
  if (selected) return selected;
  return form.seat === "특실" ? "first" : form.seat === "상관없음" ? "any" : "standard";
}

function selectedSeatRegistrationEvidenceId(
  train: UnknownRecord,
  seatClass: WatchSeatClass,
): string | null {
  const seats = Array.isArray(train.seat_classes) ? train.seat_classes : [];
  const selected = seats.find((seat) => isRecord(seat) && seat.seat_class === seatClass);
  return normalizedRegistrationEvidenceId(isRecord(selected) ? selected.registration_evidence_id : null);
}

function selectedTrain(value: unknown): UnknownRecord {
  if (!isRecord(value)) throw new ApiError("선택한 운영사의 실제 열차를 다시 선택해 주세요.");
  return value;
}

export function buildWatchCreatePayload(
  form: WatchCreateForm,
  selectedTrains: readonly unknown[],
  notificationChannelIds: readonly string[] = [],
): WatchCreatePayload {
  const provider = String(
    form.provider ?? (form.providers?.length === 1 ? form.providers[0] : ""),
  ).toUpperCase();
  const trains = selectedTrains.map(selectedTrain);
  if (
    !trains.length
    || !provider
    || trains.some((train) => String(train.provider).toUpperCase() !== provider)
  ) {
    throw new ApiError("선택한 운영사의 실제 열차를 다시 선택해 주세요.");
  }
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  const isOfficialProvider = OFFICIAL_PROVIDERS.has(provider);
  if (
    Boolean(originNodeId) !== Boolean(destinationNodeId)
    || (isOfficialProvider && (!originNodeId || !destinationNodeId))
    || (originNodeId && originNodeId === destinationNodeId)
  ) {
    throw new ApiError("공식 역 목록에서 출발역과 도착역을 다시 선택해 주세요.");
  }
  const seatClasses = new Set(trains.map((train) => requestedSeatClass(form, train)));
  if (seatClasses.size !== 1) {
    throw new ApiError("한 대기 작업에는 같은 좌석 등급의 열차만 선택해 주세요.");
  }
  validateTravelDate(form);
  const departures = [...trains].sort(
    (left, right) => Date.parse(String(left.departure_at)) - Date.parse(String(right.departure_at)),
  );
  const arrivals = [...trains].sort(
    (left, right) => Date.parse(String(right.arrival_at)) - Date.parse(String(left.arrival_at)),
  );
  const firstDeparture = departures[0];
  const lastArrival = arrivals[0];
  if (!firstDeparture || !lastArrival) {
    throw new ApiError("대기할 실제 열차를 하나 이상 선택해 주세요.");
  }
  const seatClass = [...seatClasses][0];
  if (!seatClass) throw new ApiError("선택한 좌석 등급을 확인해 주세요.");
  const origin = requiredString(form.origin, "출발역을 다시 선택해 주세요.");
  const destination = requiredString(form.destination, "도착역을 다시 선택해 주세요.");
  const passengerCount = Number(form.passengers ?? form.passenger_count);
  if (!Number.isInteger(passengerCount) || passengerCount < 1) {
    throw new ApiError("승객 수를 확인해 주세요.");
  }
  const trainNumbers = trains.map((train) => requiredString(
    train.train_number,
    "선택한 열차 번호를 확인해 주세요.",
  ));
  const candidates = trains.map((train, index): WatchCreateCandidatePayload => {
    const candidateSeatClass = requestedSeatClass(form, train);
    const registrationEvidenceId = selectedSeatRegistrationEvidenceId(train, candidateSeatClass);
    if (isOfficialProvider && !registrationEvidenceId) {
      throw new ApiError("선택한 좌석의 대기 등록 근거가 없습니다. 시간표를 다시 조회해 주세요.");
    }
    const departureAt = requiredString(
      train.departure_at,
      "선택한 열차 운행 시각을 확인해 주세요.",
    );
    if (!awareTimestamp(departureAt)) {
      throw new ApiError("선택한 열차 운행 시각을 확인해 주세요.");
    }
    const arrivalAt = train.arrival_at === null || train.arrival_at === undefined
      ? null
      : requiredString(train.arrival_at, "선택한 열차 운행 시각을 확인해 주세요.");
    if (arrivalAt !== null && !awareTimestamp(arrivalAt)) {
      throw new ApiError("선택한 열차 운행 시각을 확인해 주세요.");
    }
    return {
      train_number: requiredString(train.train_number, "선택한 열차 번호를 확인해 주세요."),
      departure_at: departureAt,
      arrival_at: arrivalAt,
      seat_class: candidateSeatClass,
      priority: index + 1,
      ...(registrationEvidenceId ? { registration_evidence_id: registrationEvidenceId } : {}),
    };
  });
  return {
    provider: provider.toLowerCase(),
    origin,
    destination,
    ...(originNodeId && destinationNodeId
      ? { origin_node_id: originNodeId, destination_node_id: destinationNodeId }
      : {}),
    travel_date: requiredString(form.date, "여행 날짜를 선택해 주세요."),
    time_from: apiTimeValue(firstDeparture.departure_at),
    time_to: apiTimeValue(lastArrival.arrival_at),
    seat_class: seatClass,
    passenger_count: passengerCount,
    train_numbers: [...new Set(trainNumbers)],
    candidates,
    notification_channel_ids: notificationChannelIds,
    mode: "official",
    reservation_policy: form.reservationPolicy === "reserve_once_before_payment"
      ? "reserve_once_before_payment"
      : "notify_only",
  };
}

export function buildWatchCreatePayloads(
  form: WatchCreateForm,
  selectedTrains: readonly unknown[],
  notificationChannelIds: readonly string[] = [],
): WatchCreatePayload[] {
  const providers = formProviders(form);
  validateTravelDate(form);
  if (!selectedTrains.length) throw new ApiError("대기할 실제 열차를 하나 이상 선택해 주세요.");
  const allowed = new Set<string>(providers);
  const payloadsByProvider = new Map<string, WatchCreatePayload[]>(
    providers.map((provider) => [provider, []]),
  );
  for (const value of selectedTrains) {
    const train = selectedTrain(value);
    const provider = String(train.provider ?? "").toUpperCase();
    if (!allowed.has(provider)) {
      throw new ApiError("선택한 운영사의 실제 열차를 다시 선택해 주세요.");
    }
    const seatClass = requestedSeatClass(form, train);
    payloadsByProvider.get(provider)?.push(buildWatchCreatePayload(
      { ...form, provider },
      [{ ...train, selected_seat_class: seatClass }],
      notificationChannelIds,
    ));
  }
  return providers.flatMap((provider) => payloadsByProvider.get(provider) ?? []);
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
  candidate: UnknownRecord,
  provider: WatchProvider,
): boolean {
  const latest = isRecord(candidate.latest_observation) ? candidate.latest_observation : null;
  return latestObservationMeta(latest, "좌석", provider)?.actionable === true;
}

function watchDto(value: unknown): UnknownRecord & {
  id: string;
  provider: WatchProvider;
  status: WatchStatus;
  origin: string;
  destination: string;
  travel_date: string;
} {
  if (!isRecord(value)) throw new ApiError("대기 작업 응답 형식을 확인할 수 없습니다.");
  const id = requiredString(value.id, "대기 작업 응답 형식을 확인할 수 없습니다.");
  const providerValue = requiredString(
    value.provider,
    "대기 작업 응답 형식을 확인할 수 없습니다.",
  ).toUpperCase();
  const statusValue = requiredString(
    value.status,
    "대기 작업 응답 형식을 확인할 수 없습니다.",
  );
  const origin = requiredString(value.origin, "대기 작업 응답 형식을 확인할 수 없습니다.");
  const destination = requiredString(value.destination, "대기 작업 응답 형식을 확인할 수 없습니다.");
  const travelDate = requiredString(
    value.travel_date,
    "대기 작업 응답 형식을 확인할 수 없습니다.",
  );
  const [year = Number.NaN, month = Number.NaN, day = Number.NaN] = travelDate
    .split("-")
    .map(Number);
  const parsedTravelDate = new Date(Date.UTC(year, month - 1, day));
  if (
    !WATCH_PROVIDERS.has(providerValue)
    || !WATCH_STATUSES.has(statusValue)
    || !/^\d{4}-\d{2}-\d{2}$/.test(travelDate)
    || parsedTravelDate.getUTCFullYear() !== year
    || parsedTravelDate.getUTCMonth() !== month - 1
    || parsedTravelDate.getUTCDate() !== day
  ) {
    throw new ApiError("대기 작업 응답 형식을 확인할 수 없습니다.");
  }
  return {
    ...value,
    id,
    provider: providerValue as WatchProvider,
    status: statusValue as WatchStatus,
    origin,
    destination,
    travel_date: travelDate,
  };
}

function optionalAwareTimestamp(value: unknown): string | null {
  return awareTimestamp(value) ? value : null;
}

function mappedWatchCandidate(value: unknown): MappedWatchCandidate | null {
  if (!isRecord(value)) return null;
  const id = typeof value.id === "string" ? value.id.trim() : "";
  const trainNumber = typeof value.train_number === "string" ? value.train_number.trim() : "";
  const departureAt = optionalAwareTimestamp(value.departure_at);
  const arrivalAt = value.arrival_at === null || value.arrival_at === undefined
    ? null
    : optionalAwareTimestamp(value.arrival_at);
  const seatClass = watchSeatClass(value.seat_class);
  if (
    !id
    || !trainNumber
    || departureAt === null
    || (value.arrival_at !== null && value.arrival_at !== undefined && arrivalAt === null)
    || seatClass === null
    || !Number.isInteger(value.priority)
    || Number(value.priority) < 1
  ) return null;
  return {
    id,
    train_number: trainNumber,
    departure_at: departureAt,
    arrival_at: arrivalAt,
    seat_class: seatClass,
    priority: Number(value.priority),
  };
}

export function mapWatch(value: unknown): MappedWatch {
  const watch = watchDto(value);
  const candidates = Array.isArray(watch.candidates) ? watch.candidates : [];
  const prioritizedCandidates = candidates
    .flatMap((item): ValidWatchCandidate[] => {
      const mapped = mappedWatchCandidate(item);
      return mapped === null || !isRecord(item) ? [] : [{ raw: item, mapped }];
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
  const candidate = selectedCandidate?.raw ?? null;
  const mappedCandidate = selectedCandidate?.mapped ?? null;
  const seatClass = mappedCandidate?.seat_class
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
  const latestReservationAttempt = mapLatestReservationAttempt(candidate?.latest_reservation_attempt);
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
      train: mapped.train_number,
      seatClassLabel: SEAT_CLASS_LABELS[mapped.seat_class],
      date: dateLabel(watch.travel_date),
      departure: timetableTimeLabel(mapped.departure_at),
      arrival: mapped.arrival_at !== null
        ? timetableTimeLabel(mapped.arrival_at)
        : timeLabel(watch.time_to),
    }]
  )));
  const officialBookingUrl = safeOfficialUrl(watch.official_booking_url, normalizedProvider);
  const reservationPolicy = normalizeReservationPolicy(watch.reservation_policy);
  return {
    id: watch.id,
    provider: normalizedProvider,
    status: watch.status,
    candidates: prioritizedCandidates.map(({ mapped }) => mapped),
    payment_deadline: optionalAwareTimestamp(watch.payment_deadline),
    created_at: optionalAwareTimestamp(watch.created_at),
    updated_at: optionalAwareTimestamp(watch.updated_at),
    official_booking_url: officialBookingUrl,
    reservation_policy: reservationPolicy,
    train: mappedCandidate !== null
      ? mappedCandidate.train_number
      : Array.isArray(watch.train_numbers) && typeof watch.train_numbers[0] === "string"
        ? watch.train_numbers[0]
        : "열차 미정",
    route: `${watch.origin} → ${watch.destination}`,
    departure: mappedCandidate !== null
      ? timetableTimeLabel(mappedCandidate.departure_at)
      : timeLabel(watch.time_from),
    arrival: mappedCandidate?.arrival_at !== null && mappedCandidate?.arrival_at !== undefined
      ? timetableTimeLabel(mappedCandidate.arrival_at)
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

export async function fetchWatches(): Promise<MappedWatch[]> {
  const payload = await request("/watches");
  if (!Array.isArray(payload)) throw new ApiError("대기 작업 목록 응답 형식을 확인할 수 없습니다.");
  return payload.map(mapWatch);
}

function watchId(id: unknown): string {
  return encodeURIComponent(requiredString(id, "대기 작업 식별자를 확인해 주세요."));
}

function watchCreateIdempotencyKey(payload: UnknownRecord): string {
  const evidenceIds = Array.isArray(payload.candidates)
    ? payload.candidates
      .map((candidate) => normalizedRegistrationEvidenceId(
        isRecord(candidate) ? candidate.registration_evidence_id : null,
      ))
      .filter((value): value is string => value !== null)
    : [];
  if (evidenceIds.length === 1) return `watch-create:${evidenceIds[0]}`;
  return crypto.randomUUID();
}

export async function createWatch(payload: UnknownRecord): Promise<MappedWatch> {
  try {
    const value = await request("/watches", {
      method: "POST",
      // The evidence-bound key lets a retry recover the same watch when creation
      // succeeded but the following start response was lost.
      headers: { "Idempotency-Key": watchCreateIdempotencyKey(payload) },
      body: JSON.stringify(payload),
    });
    return mapWatch(value);
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.create";
    throw error;
  }
}

export async function startWatch(id: string): Promise<MappedWatch> {
  const normalizedId = watchId(id);
  try {
    return mapWatch(await request(`/watches/${normalizedId}/start`, {
      method: "POST",
      headers: { "Idempotency-Key": `watch-start:${id}` },
    }));
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.start";
    throw error;
  }
}

export async function updateWatch(id: string, payload: UnknownRecord): Promise<MappedWatch> {
  const normalizedId = watchId(id);
  try {
    return mapWatch(await request(`/watches/${normalizedId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }));
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.update";
    throw error;
  }
}

export async function pauseWatch(id: string): Promise<MappedWatch> {
  const normalizedId = watchId(id);
  return mapWatch(await request(`/watches/${normalizedId}/pause`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  }));
}

export async function cancelWatch(id: string): Promise<MappedWatch> {
  const normalizedId = watchId(id);
  return mapWatch(await request(`/watches/${normalizedId}/cancel`, {
    method: "POST",
    headers: { "Idempotency-Key": `watch-cancel:${id}` },
  }));
}

export async function deleteWatch(id: string): Promise<unknown> {
  return request(`/watches/${watchId(id)}`, { method: "DELETE" });
}
