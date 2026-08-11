import {
  isWatchSeatClass,
  type WatchSeatClass,
} from "../domain/watch";
import { ApiError, request } from "./client";
import {
  awareTimestamp,
  normalizedRegistrationEvidenceId,
} from "./seatClasses";
import {
  formProviders,
  validateTravelDate,
  type TimetableSearchForm,
} from "./timetables";
import { mapWatch, type ProjectedWatch } from "./watchProjection";

export type { WatchProvider, WatchSeatClass, WatchStatus } from "../domain/watch";
export {
  mapWatch,
  type MappedWatch,
  type MappedWatchCandidate,
  type ProjectedWatch,
  type ProjectedWatchCandidate,
  type ReservationCandidateContext,
  type SeatFoundObservation,
  type WatchReadModel,
  type WatchCandidateReadModel,
} from "./watchProjection";

type UnknownRecord = Record<string, unknown>;

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

const OFFICIAL_PROVIDERS: ReadonlySet<string> = new Set(["KORAIL", "SRT"]);
const SERVICE_DATE_END_TIME = "23:59:59";

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, message: string): string {
  if (typeof value !== "string" || !value.trim()) throw new ApiError(message);
  return value.trim();
}

function watchSeatClass(value: unknown): WatchSeatClass | null {
  return isWatchSeatClass(value) ? value : null;
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

function watchWindowEndValue(firstDeparture: unknown, lastArrival: unknown): string {
  if (!awareTimestamp(firstDeparture) || !awareTimestamp(lastArrival)) {
    throw new ApiError("열차 운행 시각을 확인할 수 없습니다.");
  }
  const dateFormatter = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Seoul",
  });
  if (
    dateFormatter.format(new Date(lastArrival))
    !== dateFormatter.format(new Date(firstDeparture))
  ) {
    return SERVICE_DATE_END_TIME;
  }
  return apiTimeValue(lastArrival);
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
    // Watch windows belong to one KST service date. Keep the candidate's full
    // next-day arrival timestamp, but do not fold 00:xx back before a 23:xx departure.
    time_to: watchWindowEndValue(firstDeparture.departure_at, lastArrival.arrival_at),
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

export async function fetchWatches(): Promise<ProjectedWatch[]> {
  const payload = await request("/watches");
  if (!Array.isArray(payload)) throw new ApiError("대기 작업 목록 응답 형식을 확인할 수 없습니다.");
  return payload.map(mapWatch);
}

function watchId(id: unknown): string {
  return encodeURIComponent(requiredString(id, "대기 작업 식별자를 확인해 주세요."));
}

function watchCreateIdempotencyKey(payload: WatchCreatePayload | UnknownRecord): string {
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

export function createWatch(payload: WatchCreatePayload): Promise<ProjectedWatch>;
export function createWatch(payload: UnknownRecord): Promise<ProjectedWatch>;
export async function createWatch(
  payload: WatchCreatePayload | UnknownRecord,
): Promise<ProjectedWatch> {
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

export async function startWatch(id: string): Promise<ProjectedWatch> {
  const normalizedId = watchId(id);
  // One logical start episode owns one key. If the request transport retries this
  // invocation it reuses the header, while a later pause -> resume gets a new key.
  const idempotencyKey = crypto.randomUUID();
  try {
    return mapWatch(await request(`/watches/${normalizedId}/start`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    }));
  } catch (error) {
    if (error instanceof ApiError) error.operation = "watch.start";
    throw error;
  }
}

export async function updateWatch(id: string, payload: UnknownRecord): Promise<ProjectedWatch> {
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

export async function pauseWatch(id: string): Promise<ProjectedWatch> {
  const normalizedId = watchId(id);
  return mapWatch(await request(`/watches/${normalizedId}/pause`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  }));
}

export async function cancelWatch(id: string): Promise<ProjectedWatch> {
  const normalizedId = watchId(id);
  return mapWatch(await request(`/watches/${normalizedId}/cancel`, {
    method: "POST",
    headers: { "Idempotency-Key": `watch-cancel:${id}` },
  }));
}

export async function rearmWatchReservation(id: string): Promise<ProjectedWatch> {
  const normalizedId = watchId(id);
  return mapWatch(await request(`/watches/${normalizedId}/reservation-rearm`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
  }));
}

export async function deleteWatch(id: string): Promise<unknown> {
  return request(`/watches/${watchId(id)}`, { method: "DELETE" });
}
