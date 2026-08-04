import {
  normalizeSeatObservationReason,
  seatObservationReasonMeta,
} from "./domain/seatDiagnostics";
import { normalizeReservationPolicy } from "./domain/reservationPolicy";
import { mapLatestReservationAttempt } from "./domain/reservationAttempt";
import { mapOperationalCandidate } from "./domain/watchOperational";
import { API_ROOT, ApiError, request } from "./api/client";

export { ApiError } from "./api/client";
export {
  getAuthStatus,
  loginWithPassword,
  logout,
  registerAdmin,
} from "./api/auth";

export const DEMO_MODE = import.meta.env.DEV && import.meta.env.VITE_DEMO_MODE !== "false";

const statusLabels = {
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

function timeLabel(value) {
  return typeof value === "string" ? value.slice(0, 5) : "--:--";
}

function dateLabel(value) {
  if (!value) return "날짜 미정";
  const parsed = new Date(`${value}T00:00:00+09:00`);
  return new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" }).format(parsed);
}

function timetableTimeLabel(value) {
  if (!value) return "--:--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "--:--";
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(parsed);
}

function durationLabel(departureAt, arrivalAt) {
  const minutes = Math.max(0, Math.round((new Date(arrivalAt).getTime() - new Date(departureAt).getTime()) / 60000));
  if (!Number.isFinite(minutes)) return "소요 시간 미정";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours > 0 ? `${hours}시간 ${rest}분` : `${rest}분`;
}

function apiTimeValue(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) throw new ApiError("열차 운행 시각을 확인할 수 없습니다.");
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).format(parsed);
}

const supportedProviders = new Set(["KORAIL", "SRT"]);
const weekdayAliases = [
  [0, "0", "SUN", "SUNDAY", "일", "일요일"],
  [1, "1", "MON", "MONDAY", "월", "월요일"],
  [2, "2", "TUE", "TUESDAY", "화", "화요일"],
  [3, "3", "WED", "WEDNESDAY", "수", "수요일"],
  [4, "4", "THU", "THURSDAY", "목", "목요일"],
  [5, "5", "FRI", "FRIDAY", "금", "금요일"],
  [6, "6", "SAT", "SATURDAY", "토", "토요일"],
];

function formProviders(form) {
  const values = Array.isArray(form.providers) ? form.providers : [form.provider];
  const providers = [...new Set(values.filter(Boolean).map((value) => String(value).toUpperCase()))];
  if (!providers.length || providers.some((provider) => !supportedProviders.has(provider))) {
    throw new ApiError("KORAIL 또는 SRT 운영사를 하나 이상 선택해 주세요.");
  }
  return providers;
}

function formTimeRange(form) {
  const timeFrom = form.timeFrom ?? form.time ?? "00:00";
  const timeTo = form.timeTo ?? "23:59";
  const pattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
  if (!pattern.test(timeFrom) || !pattern.test(timeTo) || timeFrom > timeTo) {
    throw new ApiError("조회 시간 범위를 올바르게 선택해 주세요.");
  }
  return { timeFrom, timeTo };
}

function seoulDateAndMinutes(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  }).formatToParts(parsed).filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
  };
}

function timeMinutes(value) {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
}

function selectedWeekdays(form) {
  if (!Array.isArray(form.selectedWeekdays) || !form.selectedWeekdays.length) return null;
  const normalized = form.selectedWeekdays.map((value) => {
    const key = typeof value === "number" ? value : String(value).trim().toUpperCase();
    return weekdayAliases.find((aliases) => aliases.includes(key))?.[0];
  });
  if (normalized.some((value) => value === undefined)) throw new ApiError("선택한 요일 값을 확인해 주세요.");
  return new Set(normalized);
}

function validateTravelDate(form) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.date ?? "")) throw new ApiError("여행 날짜를 선택해 주세요.");
  const [year, month, day] = form.date.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
    throw new ApiError("여행 날짜를 확인해 주세요.");
  }
  const weekdays = selectedWeekdays(form);
  if (weekdays && !weekdays.has(parsed.getUTCDay())) {
    throw new ApiError("여행 날짜가 선택한 요일과 일치하지 않습니다. 반복 날짜는 아직 자동 생성하지 않습니다.");
  }
}

export function filterTimetables(form, items) {
  const providers = new Set(formProviders(form));
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const fromMinutes = timeMinutes(timeFrom);
  const toMinutes = timeMinutes(timeTo);
  const unique = new Map();

  for (const item of items ?? []) {
    const provider = String(item.provider ?? "").toUpperCase();
    const departure = seoulDateAndMinutes(item.departure_at);
    if (!providers.has(provider) || !departure || departure.date !== form.date) continue;
    if (departure.minutes < fromMinutes || departure.minutes > toMinutes) continue;
    const key = `${provider}:${item.train_number}:${item.departure_at}`;
    if (!unique.has(key)) unique.set(key, item);
  }

  return [...unique.values()].sort((left, right) => {
    const departureOrder = new Date(left.departure_at).getTime() - new Date(right.departure_at).getTime();
    if (departureOrder) return departureOrder;
    return String(left.provider).localeCompare(String(right.provider)) || String(left.train_number).localeCompare(String(right.train_number));
  });
}

const seatClassIds = ["standard", "first"];
const seatStatuses = new Set([
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
const observedSeatStatuses = new Set([
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
const registrationEvidenceIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;

function normalizedRegistrationEvidenceId(value) {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return registrationEvidenceIdPattern.test(normalized) ? normalized : null;
}

function awareTimestamp(value) {
  return typeof value === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
}

function officialPageTtlMs(provenance) {
  if (!awareTimestamp(provenance?.observed_at) || !awareTimestamp(provenance?.fresh_until)) return null;
  const ttl = Date.parse(provenance.fresh_until) - Date.parse(provenance.observed_at);
  return ttl > 0 && ttl <= MAX_USER_CONFIRMATION_TTL_MS ? ttl : null;
}

function monotonicNow() {
  if (typeof performance === "undefined" || typeof performance.now !== "function") return null;
  const value = performance.now();
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function hasValidObservation(provenance) {
  if (!provenance || !["official_provider", "official_page_browser_companion", "user_confirmed_official_page", "mock"].includes(provenance.kind)) return false;
  if (typeof provenance.source !== "string" || !provenance.source.trim()) return false;
  if (!awareTimestamp(provenance.observed_at)) return false;
  if (!["official_page_browser_companion", "user_confirmed_official_page"].includes(provenance.kind)) return true;
  const expectedSource = provenance.kind === "official_page_browser_companion"
    ? "korail-official-browser-companion"
    : "official-page-user-confirmation";
  if (provenance.source !== expectedSource) return false;
  return officialPageTtlMs(provenance) !== null && monotonicNow() !== null;
}

const officialHostRoots = {
  KORAIL: ["korail.com", "letskorail.com"],
  SRT: ["srail.kr"],
  MOCK: ["example.invalid"],
};

function safeOfficialUrl(value, provider) {
  try {
    const url = new URL(value);
    const roots = officialHostRoots[String(provider ?? "").toUpperCase()] ?? [];
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    const allowed = roots.some((root) => host === root || host.endsWith(`.${root}`));
    return url.protocol === "https:" && allowed ? url.toString() : null;
  } catch {
    return null;
  }
}

function unknownSeatClass(
  seatClass,
  officialBookingUrl,
  provider,
  reason = "public_api_not_available",
  registrationEvidenceId = null,
) {
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

function normalizeSeatClass(raw, seatClass, officialBookingUrl, provider) {
  if (!raw || raw.seat_class !== seatClass || !seatStatuses.has(raw.status)) {
    return unknownSeatClass(
      seatClass,
      officialBookingUrl,
      provider,
      "invalid_provider_payload",
      raw?.registration_evidence_id,
    );
  }
  const provenance = raw.provenance ?? {};
  const observed = provenance.kind === "official_provider"
    || provenance.kind === "official_page_browser_companion"
    || provenance.kind === "user_confirmed_official_page"
    || provenance.kind === "mock";
  const hasEvidence = observed && hasValidObservation(provenance);
  if (observedSeatStatuses.has(raw.status) && !hasEvidence) {
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
  const actions = (Array.isArray(raw.actions) ? raw.actions : []).flatMap((action) => {
    if (!action || typeof action.kind !== "string") return [];
    if (action.kind.startsWith("official_")) {
      const url = safeOfficialUrl(action.url, provider);
      return url ? [{ ...action, url }] : [];
    }
    if (action.kind === "add_to_watch" || action.kind === "retry_provider") {
      return [{ ...action, url: null }];
    }
    return [];
  });
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
    : ["official_page_browser_companion", "user_confirmed_official_page"].includes(provenance.kind)
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
    fare: Number.isFinite(raw.fare) ? raw.fare : null,
    fare_currency: raw.fare_currency === "KRW" ? "KRW" : "KRW",
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

export function normalizeSeatClasses(item) {
  const byClass = new Map((Array.isArray(item?.seat_classes) ? item.seat_classes : []).map((seat) => [seat?.seat_class, seat]));
  return seatClassIds.map((seatClass) => normalizeSeatClass(byClass.get(seatClass), seatClass, item?.official_booking_url, item?.provider));
}

export function mapTimetable(item) {
  const provider = String(item.provider).toUpperCase();
  return {
    ...item,
    id: `${provider}:${item.train_number}:${item.departure_at}`,
    provider,
    name: item.train_number,
    departure: timetableTimeLabel(item.departure_at),
    arrival: timetableTimeLabel(item.arrival_at),
    duration: durationLabel(item.departure_at, item.arrival_at),
    seat_classes: normalizeSeatClasses(item),
  };
}

function timetableProviderError(provider, reason) {
  const httpStatus = reason instanceof ApiError ? reason.status : 0;
  const prefix = httpStatus === 503
    ? "공식 시간표 제공자가 응답하지 않습니다."
    : "공식 시간표를 불러오지 못했습니다.";
  return {
    provider,
    httpStatus,
    message: `${prefix} ${reason?.message || "잠시 후 다시 시도해 주세요."}`,
  };
}

function requestedSeatClass(form, train = null) {
  if (["standard", "first", "any"].includes(train?.selected_seat_class)) return train.selected_seat_class;
  return form.seat === "특실" ? "first" : form.seat === "상관없음" ? "any" : "standard";
}

function selectedSeatRegistrationEvidenceId(train, seatClass) {
  return normalizedRegistrationEvidenceId(
    train?.seat_classes?.find((seat) => seat?.seat_class === seatClass)?.registration_evidence_id,
  );
}

export function buildWatchCreatePayload(form, selectedTrains, notificationChannelIds = []) {
  const provider = String(form.provider ?? (form.providers?.length === 1 ? form.providers[0] : "")).toUpperCase();
  if (!selectedTrains.length || !provider || selectedTrains.some((train) => String(train.provider).toUpperCase() !== provider)) {
    throw new ApiError("선택한 운영사의 실제 열차를 다시 선택해 주세요.");
  }
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  const isOfficialProvider = provider === "KORAIL" || provider === "SRT";
  if (
    Boolean(originNodeId) !== Boolean(destinationNodeId)
    || (isOfficialProvider && (!originNodeId || !destinationNodeId))
    || (originNodeId && originNodeId === destinationNodeId)
  ) {
    throw new ApiError("공식 역 목록에서 출발역과 도착역을 다시 선택해 주세요.");
  }
  const seatClasses = new Set(selectedTrains.map((train) => requestedSeatClass(form, train)));
  if (seatClasses.size !== 1) throw new ApiError("한 대기 작업에는 같은 좌석 등급의 열차만 선택해 주세요.");
  validateTravelDate(form);
  const departures = [...selectedTrains].sort((a, b) => new Date(a.departure_at) - new Date(b.departure_at));
  const arrivals = [...selectedTrains].sort((a, b) => new Date(b.arrival_at) - new Date(a.arrival_at));
  return {
    provider: provider.toLowerCase(),
    origin: form.origin,
    destination: form.destination,
    ...(originNodeId && destinationNodeId
      ? { origin_node_id: originNodeId, destination_node_id: destinationNodeId }
      : {}),
    travel_date: form.date,
    time_from: apiTimeValue(departures[0].departure_at),
    time_to: apiTimeValue(arrivals[0].arrival_at),
    seat_class: [...seatClasses][0],
    passenger_count: Number(form.passengers),
    train_numbers: [...new Set(selectedTrains.map((train) => train.train_number))],
    candidates: selectedTrains.map((train, index) => {
      const seatClass = requestedSeatClass(form, train);
      const registrationEvidenceId = selectedSeatRegistrationEvidenceId(train, seatClass);
      if (isOfficialProvider && !registrationEvidenceId) {
        throw new ApiError("선택한 좌석의 대기 등록 근거가 없습니다. 시간표를 다시 조회해 주세요.");
      }
      return {
        train_number: train.train_number,
        departure_at: train.departure_at,
        arrival_at: train.arrival_at || null,
        seat_class: seatClass,
        priority: index + 1,
        ...(registrationEvidenceId ? { registration_evidence_id: registrationEvidenceId } : {}),
      };
    }),
    notification_channel_ids: notificationChannelIds,
    mode: "official",
    reservation_policy: form.reservationPolicy === "reserve_once_before_payment"
      ? "reserve_once_before_payment"
      : "notify_only",
  };
}

export function buildWatchCreatePayloads(form, selectedTrains, notificationChannelIds = []) {
  const providers = formProviders(form);
  validateTravelDate(form);
  if (!selectedTrains?.length) throw new ApiError("대기할 실제 열차를 하나 이상 선택해 주세요.");
  const allowed = new Set(providers);
  const payloadsByProvider = new Map(providers.map((provider) => [provider, []]));
  for (const train of selectedTrains) {
    const provider = String(train.provider ?? "").toUpperCase();
    if (!allowed.has(provider)) throw new ApiError("선택한 운영사의 실제 열차를 다시 선택해 주세요.");
    const seatClass = requestedSeatClass(form, train);
    payloadsByProvider.get(provider).push(buildWatchCreatePayload(
      { ...form, provider },
      [{ ...train, selected_seat_class: seatClass }],
      notificationChannelIds,
    ));
  }
  return providers.flatMap((provider) => payloadsByProvider.get(provider));
}

const actionableLatestObservationStatuses = new Set(["available", "limited", "standing_plus_seat"]);

function latestObservationMeta(observation, seatClassLabel) {
  if (!observation || typeof observation !== "object") return null;
  const status = typeof observation.status === "string" ? observation.status.toLowerCase() : "";
  const observedAt = awareTimestamp(observation.observed_at) ? String(observation.observed_at) : null;
  const freshUntil = awareTimestamp(observation.fresh_until) ? String(observation.fresh_until) : null;
  if (!status || !observedAt || !freshUntil) return null;
  const fresh = Date.parse(freshUntil) > Date.now();
  const statusLabels = {
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
  const statusLabel = fresh ? statusLabels[status] ?? "확인 필요" : "관측 만료 · 다시 확인 중";
  return {
    status,
    observedAt,
    freshUntil,
    fresh,
    actionable: fresh && actionableLatestObservationStatuses.has(status),
    label: `${seatClassLabel} · ${statusLabel} · 최근 관측 ${timetableTimeLabel(observedAt)}`,
  };
}

function hasActionableLatestObservation(candidate) {
  const latest = candidate?.latest_observation;
  if (!latest || typeof latest !== "object") return false;
  const status = typeof latest.status === "string" ? latest.status.toLowerCase() : "";
  const freshUntil = awareTimestamp(latest.fresh_until) ? Date.parse(latest.fresh_until) : Number.NaN;
  return actionableLatestObservationStatuses.has(status)
    && Number.isFinite(freshUntil)
    && freshUntil > Date.now();
}

export function mapWatch(watch) {
  const candidates = Array.isArray(watch?.candidates) ? watch.candidates : [];
  const prioritizedCandidates = [...candidates]
    .filter((item) => item && Number.isInteger(item.priority) && item.priority >= 1)
    .sort((left, right) => left.priority - right.priority);
  const statePreferredCandidate = watch?.status === "reserving"
    ? prioritizedCandidates.find((item) => item.state === "reservation_attempted")
    : watch?.status === "payment_required"
      ? prioritizedCandidates.find((item) => item.state === "payment_required")
      : ["auth_required", "failed"].includes(watch?.status)
        ? prioritizedCandidates.find((item) => item.state === "failed")
        : null;
  const candidate = statePreferredCandidate
    ?? (watch?.status === "seat_found"
      ? prioritizedCandidates.find(hasActionableLatestObservation)
      : null)
    ?? prioritizedCandidates[0]
    ?? null;
  const seatClass = ["standard", "first", "any"].includes(candidate?.seat_class)
    ? candidate.seat_class
    : ["standard", "first", "any"].includes(watch?.seat_class)
      ? watch.seat_class
      : "any";
  const seatClassLabels = { standard: "일반실", first: "특실", any: "좌석 무관" };
  const seatClassLabel = seatClassLabels[seatClass];
  const lastCheckedAt = awareTimestamp(watch?.last_checked_at)
    ? String(watch.last_checked_at)
    : null;
  const lastCheckedLabel = lastCheckedAt
    ? `최근 확인 ${timetableTimeLabel(lastCheckedAt)}`
    : "최근 확인 기록 없음";
  const normalizedProvider = String(watch.provider).toUpperCase();
  const evidence = candidate?.registration_evidence;
  let seatEvidenceLabel = `${seatClassLabel} · 등록 근거 없음`;

  if (evidence && typeof evidence === "object") {
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
      seatClass,
      null,
      watch.provider,
    );
    const provenance = normalizedSeat.provenance;
    const observed = ["official_provider", "official_page_browser_companion", "user_confirmed_official_page", "mock"]
      .includes(provenance?.kind);
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
      const observedAt = timetableTimeLabel(provenance.observed_at);
      const sourceLabel = provenance.kind === "official_provider"
        ? "공식 관측"
        : provenance.kind === "official_page_browser_companion"
          ? "공식 화면 동기화"
        : provenance.kind === "user_confirmed_official_page"
          ? "공식 페이지에서 직접 확인"
          : "데모 관측";
      seatEvidenceLabel = `${seatClassLabel} · ${statusLabel} · ${sourceLabel} ${observedAt}`;
    }
  }
  const registrationEvidenceLabel = seatEvidenceLabel;
  const currentObservation = latestObservationMeta(candidate?.latest_observation, seatClassLabel);
  const operational = mapOperationalCandidate(candidate);
  const latestReservationAttempt = mapLatestReservationAttempt(
    candidate?.latest_reservation_attempt,
  );
  if (currentObservation) seatEvidenceLabel = currentObservation.label;
  const seatFoundObservation = watch.status === "seat_found"
    && currentObservation?.actionable
    && ["KORAIL", "SRT", "MOCK"].includes(normalizedProvider)
    ? {
      kind: normalizedProvider === "MOCK" ? "mock" : "official_provider",
      observedAt: currentObservation.observedAt,
      observedLabel: `최근 확인 ${timetableTimeLabel(currentObservation.observedAt)}`,
    }
    : null;
  const reservationCandidateContexts = Object.fromEntries(prioritizedCandidates.flatMap((item) => {
    const id = typeof item.id === "string" ? item.id.trim() : "";
    if (!id) return [];
    const itemSeatClass = ["standard", "first", "any"].includes(item.seat_class)
      ? item.seat_class
      : "any";
    return [[id, {
      train: item.train_number ?? "열차 미정",
      seatClassLabel: seatClassLabels[itemSeatClass],
      date: dateLabel(watch.travel_date),
      departure: item.departure_at
        ? timetableTimeLabel(item.departure_at)
        : timeLabel(watch.time_from),
      arrival: item.arrival_at
        ? timetableTimeLabel(item.arrival_at)
        : timeLabel(watch.time_to),
    }]];
  }));
  return {
    ...watch,
    provider: normalizedProvider,
    train: candidate?.train_number ?? watch.train_numbers?.[0] ?? "열차 미정",
    route: `${watch.origin} → ${watch.destination}`,
    departure: candidate?.departure_at
      ? timetableTimeLabel(candidate.departure_at)
      : timeLabel(watch.time_from),
    arrival: candidate?.arrival_at
      ? timetableTimeLabel(candidate.arrival_at)
      : timeLabel(watch.time_to),
    date: dateLabel(watch.travel_date),
    statusLabel: statusLabels[watch.status] ?? watch.status,
    seatClass,
    seatClassLabel,
    seatEvidenceLabel,
    registrationEvidenceLabel,
    activityLabel: seatEvidenceLabel,
    lastCheckedAt,
    lastCheckedLabel,
    operational,
    latestReservationAttempt,
    origin: String(watch.origin ?? ""),
    destination: String(watch.destination ?? ""),
    travelDate: typeof watch.travel_date === "string" ? watch.travel_date : "",
    officialBookingUrl: typeof watch.official_booking_url === "string" ? watch.official_booking_url : null,
    seatFoundObservation,
    reservationCandidateContexts,
    reservationPolicy: normalizeReservationPolicy(watch.reservation_policy),
    seatObservationMode: watch.seat_observation_mode === "focused" ? "focused" : "balanced",
    focusedObservationIntervalSeconds: Number.isInteger(watch.focused_observation_interval_seconds)
      && watch.focused_observation_interval_seconds >= 20
      && watch.focused_observation_interval_seconds <= 30
      ? watch.focused_observation_interval_seconds
      : 25,
    nextCheckAt: awareTimestamp(watch.next_check_at) ? String(watch.next_check_at) : null,
  };
}

export async function fetchWatches() {
  const items = await request("/watches");
  return items.map(mapWatch);
}

function normalizedStationCatalog(payload, requestedProvider) {
  const provider = String(payload?.provider ?? "").toUpperCase();
  const source = String(payload?.source ?? "").trim();
  const catalogScope = String(payload?.catalog_scope ?? "").trim();
  const providerMembership = String(payload?.provider_membership ?? "").trim();
  const note = String(payload?.note ?? "").trim();
  const retrievedAt = String(payload?.retrieved_at ?? "");
  const retrievedDate = new Date(retrievedAt);
  const catalogTuple = `${source}|${catalogScope}|${providerMembership}`;
  const allowedCatalogTuples = new Set([
    "TAGO|intercity_station_guide_intersection|not_verified_by_source",
    "mock|mock|mock",
  ]);
  if (
    !payload
    || typeof payload !== "object"
    || provider !== requestedProvider
    || !allowedCatalogTuples.has(catalogTuple)
    || (Object.hasOwn(payload, "note") && !note)
    || !retrievedAt
    || Number.isNaN(retrievedDate.getTime())
    || !Array.isArray(payload.stations)
    || payload.stations.length === 0
  ) {
    throw new ApiError(`${requestedProvider} 역 목록 응답 형식을 확인할 수 없습니다.`);
  }

  const stations = payload.stations.map((item) => {
    const name = String(item?.name ?? "").trim();
    const nodeId = String(item?.node_id ?? "").trim();
    const cityCode = String(item?.city_code ?? "").trim();
    const cityName = String(item?.city_name ?? "").trim();
    if (!name || !nodeId || !cityCode || !cityName) {
      throw new ApiError(`${requestedProvider} 역 목록에 불완전한 항목이 있습니다.`);
    }
    return { name, nodeId, cityCode, cityName };
  });

  return { provider, source, catalogScope, providerMembership, note, retrievedAt, stations };
}

export function mergeStationCatalogs(catalogs) {
  const merged = new Map();
  for (const catalog of catalogs) {
    for (const station of catalog.stations) {
      const key = station.nodeId;
      const existing = merged.get(key);
      if (existing) {
        if (existing.name !== station.name || existing.cityCode !== station.cityCode) {
          throw new ApiError(`역 식별자 ${station.nodeId}의 정보가 응답마다 다릅니다.`);
        }
        if (!existing.catalogProviders.includes(catalog.provider)) existing.catalogProviders.push(catalog.provider);
        if (!existing.sources.includes(catalog.source)) existing.sources.push(catalog.source);
        continue;
      }
      merged.set(key, {
        name: station.name,
        nodeId: station.nodeId,
        cityCode: station.cityCode,
        cityName: station.cityName,
        catalogProviders: [catalog.provider],
        sources: [catalog.source],
        providerMembershipVerified: false,
      });
    }
  }
  return [...merged.values()].sort((left, right) => left.name.localeCompare(right.name, "ko-KR"));
}

export async function fetchStations(providerValues) {
  const values = Array.isArray(providerValues) ? providerValues : [providerValues];
  const selectedProviders = [...new Set(values.filter(Boolean).map((value) => String(value).toUpperCase()))];
  if (!selectedProviders.length || selectedProviders.some((provider) => !supportedProviders.has(provider))) {
    throw new ApiError("역 목록을 받을 KORAIL 또는 SRT 운영사를 선택해 주세요.");
  }
  const providers = selectedProviders.includes("SRT")
    ? [...new Set(["KORAIL", ...selectedProviders])]
    : selectedProviders;

  // 공식 철도 여정은 교차 운행 구간을 놓치지 않도록 두 카탈로그를 합칩니다.
  // 한 운영사의 응답이라도 실패하면 이전/내장 목록으로 대체하지 않습니다.
  const catalogs = await Promise.all(providers.map(async (provider) => {
    const payload = await request(`/stations?${new URLSearchParams({ provider: provider.toLowerCase() })}`);
    return normalizedStationCatalog(payload, provider);
  }));
  return {
    stations: mergeStationCatalogs(catalogs),
    catalogs,
    providerMembershipVerified: catalogs.every((catalog) => catalog.providerMembership === "mock"),
  };
}

export async function fetchTimetables(form, providerOverride = null) {
  const providers = providerOverride ? [String(providerOverride).toUpperCase()] : formProviders(form);
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  const hasOfficialProvider = providers.some((provider) => provider === "KORAIL" || provider === "SRT");
  if (Boolean(originNodeId) !== Boolean(destinationNodeId) || (hasOfficialProvider && (!originNodeId || !destinationNodeId))) {
    throw new ApiError("출발역과 도착역 식별자를 다시 선택해 주세요.");
  }
  const responses = await Promise.allSettled(providers.map((provider) => {
    const params = new URLSearchParams({
      provider: provider.toLowerCase(),
      origin: form.origin,
      destination: form.destination,
      departure_from: `${form.date}T${timeFrom}:00+09:00`,
      departure_to: `${form.date}T${timeTo}:00+09:00`,
      passenger_count: String(Number(form.passengers ?? form.passenger_count ?? 1)),
    });
    if (originNodeId && destinationNodeId) {
      params.set("origin_node_id", originNodeId);
      params.set("destination_node_id", destinationNodeId);
    }
    return request(`/timetables?${params.toString()}`);
  }));
  const providerResults = {};
  const items = [];
  responses.forEach((result, index) => {
    const provider = providers[index];
    if (result.status === "fulfilled") {
      providerResults[provider] = { status: "success", count: result.value.length };
      items.push(...result.value);
      return;
    }
    providerResults[provider] = { status: "error", ...timetableProviderError(provider, result.reason) };
  });
  return {
    trains: filterTimetables(form, items).map(mapTimetable),
    providerResults,
  };
}

export async function refreshSeatStatus(form, providerOverride) {
  const provider = String(providerOverride ?? "").toUpperCase();
  if (!supportedProviders.has(provider)) {
    throw new ApiError("좌석 상태를 다시 조회할 운영사를 확인해 주세요.");
  }
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  if (!originNodeId || !destinationNodeId || originNodeId === destinationNodeId) {
    throw new ApiError("출발역과 도착역 식별자를 다시 선택해 주세요.");
  }
  const payload = await request("/seat-status/refresh", {
    method: "POST",
    body: JSON.stringify({
      provider: provider.toLowerCase(),
      origin: form.origin,
      destination: form.destination,
      departure_from: `${form.date}T${timeFrom}:00+09:00`,
      departure_to: `${form.date}T${timeTo}:00+09:00`,
      passenger_count: Number(form.passengers ?? form.passenger_count ?? 1),
      origin_node_id: originNodeId,
      destination_node_id: destinationNodeId,
    }),
  });
  return filterTimetables(form, payload).map(mapTimetable);
}

export async function fetchKorailSnapshotRevision(options = {}) {
  const payload = await request("/korail-browser-snapshot-revision", {
    method: "GET",
    cache: "no-store",
    signal: options.signal,
  });
  const revision = payload?.revision;
  return awareTimestamp(revision) ? revision : null;
}

export async function fetchBrowserCompanionStatus() {
  return request("/browser-companion/status", { cache: "no-store" });
}

export async function createBrowserCompanionPairing(label = "내 브라우저") {
  return request("/browser-companion/pairings", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

export async function revokeBrowserCompanionCredential(id) {
  return request(`/browser-companion/credentials/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

function watchCreateIdempotencyKey(payload) {
  const evidenceIds = Array.isArray(payload?.candidates)
    ? payload.candidates
      .map((candidate) => normalizedRegistrationEvidenceId(candidate?.registration_evidence_id))
      .filter(Boolean)
    : [];
  if (evidenceIds.length === 1) return `watch-create:${evidenceIds[0]}`;
  return crypto.randomUUID();
}

export async function createWatch(payload) {
  try {
    const watch = await request("/watches", {
      method: "POST",
      // The evidence-bound key lets a retry recover the same watch when creation
      // succeeded but the following start response was lost.
      headers: { "Idempotency-Key": watchCreateIdempotencyKey(payload) },
      body: JSON.stringify(payload),
    });
    return mapWatch(watch);
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.create";
    throw error;
  }
}

export async function startWatch(id) {
  try {
    return mapWatch(await request(`/watches/${id}/start`, {
      method: "POST",
      headers: { "Idempotency-Key": `watch-start:${id}` },
    }));
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.start";
    throw error;
  }
}

export async function updateWatch(id, payload) {
  try {
    return mapWatch(await request(`/watches/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }));
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.update";
    throw error;
  }
}

export async function pauseWatch(id) {
  return mapWatch(await request(`/watches/${id}/pause`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  }));
}

export async function cancelWatch(id) {
  return mapWatch(await request(`/watches/${id}/cancel`, {
    method: "POST",
    headers: { "Idempotency-Key": `watch-cancel:${id}` },
  }));
}

export async function deleteWatch(id) {
  return request(`/watches/${id}`, { method: "DELETE" });
}

export async function fetchNotificationChannels() {
  return request("/notifications/channels");
}

export async function createNotificationChannel(payload) {
  return request("/notifications/channels", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateNotificationChannel(id, payload) {
  return request(`/notifications/channels/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function testNotificationChannel(id) {
  return request(`/notifications/channels/${id}/test-send`, { method: "POST" });
}

export async function waitForServiceWorkerRegistration(timeoutMs = 8_000) {
  let timeoutId;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => {
        timeoutId = window.setTimeout(() => {
          reject(new ApiError("알림 서비스를 준비하지 못했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요."));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) window.clearTimeout(timeoutId);
  }
}

function browserPushSupported() {
  return typeof navigator !== "undefined"
    && "serviceWorker" in navigator
    && typeof window !== "undefined"
    && "PushManager" in window
    && "Notification" in window;
}

function requireBrowserPushSupport() {
  if (!browserPushSupported()) {
    throw new ApiError("이 브라우저는 OS 알림을 지원하지 않습니다.");
  }
  if (window.isSecureContext === false) {
    throw new ApiError("OS 알림은 HTTPS 또는 이 기기의 localhost 주소에서만 사용할 수 있습니다.");
  }
}

export async function readBrowserPushState() {
  if (!browserPushSupported()) {
    return { support: "unsupported", permission: "default", subscribed: false };
  }
  if (window.isSecureContext === false) {
    return { support: "insecure", permission: Notification.permission, subscribed: false };
  }
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = registration
    ? await registration.pushManager.getSubscription()
    : null;
  return {
    support: "supported",
    permission: Notification.permission,
    subscribed: subscription !== null,
  };
}

export async function disconnectBrowserPush() {
  requireBrowserPushSupport();
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = registration
    ? await registration.pushManager.getSubscription()
    : null;
  if (subscription) await subscription.unsubscribe();
  return readBrowserPushState();
}

/**
 * @param {string} name
 * @param {string | null} existingChannelId
 */
export async function connectBrowserPush(name = "이 브라우저", existingChannelId = null) {
  requireBrowserPushSupport();
  const { public_key: publicKey } = await request("/notifications/web-push/public-key");
  const existingRegistration = await navigator.serviceWorker.getRegistration();
  if (!existingRegistration) await navigator.serviceWorker.register("/sw.js");
  const registration = await waitForServiceWorkerRegistration();
  const permission = await Notification.requestPermission();
  if (permission === "denied") {
    throw new ApiError("OS 알림 권한이 차단되어 있습니다. 브라우저 사이트 설정에서 알림을 허용해 주세요.");
  }
  if (permission !== "granted") throw new ApiError("OS 알림 권한을 허용해야 연결할 수 있습니다.");
  const subscription = await registration.pushManager.getSubscription()
    ?? await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: fromBase64Url(publicKey),
    });
  const payload = {
    name,
    config: { subscription_info: JSON.stringify(subscription.toJSON()) },
    enabled: true,
  };
  if (existingChannelId) return updateNotificationChannel(existingChannelId, payload);
  return createNotificationChannel({ kind: "web_push", ...payload });
}

export async function fetchProviders() {
  return request("/providers");
}

function parseLiveEvent(event, onError) {
  try {
    return JSON.parse(event.data);
  } catch (error) {
    onError(error);
    return null;
  }
}

function isCurrentLiveEvent(payload, subscribedAt) {
  const createdAt = Date.parse(String(payload?.created_at ?? ""));
  return Number.isFinite(createdAt) && createdAt >= subscribedAt;
}

export function subscribeToEvents(onEvent, onError, options = {}) {
  // The API intentionally exposes the durable outbox through SSE. A new browser
  // connection has no Last-Event-ID, so it receives historical rows first. The
  // initial REST load is the canonical snapshot; only events created after this
  // subscription should invalidate it.
  const subscribedAt = Number.isFinite(options.subscribedAt)
    ? options.subscribedAt
    : Date.now();
  const source = new EventSource(`${API_ROOT}/events`, { withCredentials: true });
  const handleEvent = (event) => {
    const payload = parseLiveEvent(event, onError);
    if (payload && isCurrentLiveEvent(payload, subscribedAt)) onEvent(payload);
  };
  source.onmessage = handleEvent;
  const types = [
    "watch.created",
    "watch.updated",
    "watch.status_changed",
    "watch.seat_observed",
    "watch.reservation_attempted",
    "watch.reservation_result",
    "watch.reservation_result_requires_manual_check",
    "notification.dispatch_requested",
  ];
  for (const type of types) source.addEventListener(type, handleEvent);
  source.onerror = onError;
  return () => source.close();
}

function fromBase64Url(value) {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}
