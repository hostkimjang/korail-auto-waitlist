import {
  seatObservationReasonMeta,
} from "./domain/seatDiagnostics";
import { normalizeReservationPolicy } from "./domain/reservationPolicy";
import { mapLatestReservationAttempt } from "./domain/reservationAttempt";
import { mapOperationalCandidate } from "./domain/watchOperational";
import { ApiError, request } from "./api/client";
import {
  awareTimestamp,
  normalizedRegistrationEvidenceId,
  normalizeSeatClass,
} from "./api/seatClasses";
import {
  filterTimetables,
  formProviders,
  formTimeRange,
  mapTimetable,
  timetableTimeLabel,
  validateTravelDate,
} from "./api/timetables";

export { ApiError } from "./api/client";
export {
  getAuthStatus,
  loginWithPassword,
  logout,
  registerAdmin,
} from "./api/auth";
export {
  connectBrowserPush,
  createNotificationChannel,
  deleteNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
  waitForServiceWorkerRegistration,
} from "./api/notifications";
export { subscribeToEvents } from "./api/events";
export { fetchStations, mergeStationCatalogs } from "./api/stations";
export { normalizeSeatClasses } from "./api/seatClasses";
export { fetchTimetables, filterTimetables, mapTimetable } from "./api/timetables";

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

export async function fetchProviders() {
  return request("/providers");
}
