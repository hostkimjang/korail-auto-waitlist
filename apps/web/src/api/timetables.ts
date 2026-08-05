import { ApiError, request } from "./client";
import type { RailProvider } from "./providerAccounts";
import {
  awareTimestamp,
  normalizeSeatClasses,
  safeOfficialUrl,
} from "./seatClasses";
import type { NormalizedSeatClass } from "../domain/seatClasses";

type UnknownRecord = Record<string, unknown>;
type WeekdayValue = number | string;

export interface TimetableSearchForm {
  provider?: RailProvider | string | null;
  providers?: readonly (RailProvider | string)[];
  origin?: string;
  origin_node_id?: string | null;
  destination?: string;
  destination_node_id?: string | null;
  date?: string;
  time?: string;
  timeFrom?: string;
  timeTo?: string;
  passengers?: string | number;
  passenger_count?: string | number;
  selectedWeekdays?: readonly WeekdayValue[];
}

export interface TimetableItemDto extends UnknownRecord {
  provider: RailProvider;
  train_number: string;
  train_type: string | null;
  origin: string;
  destination: string;
  departure_at: string;
  arrival_at: string;
  adult_fare: number | null;
  fare_currency: "KRW";
  timetable_source: TimetableSource;
  timetable_retrieved_at: string | null;
  seat_classes: unknown[];
  official_booking_url: string | null;
  official_search_url: string | null;
}

export interface Timetable extends TimetableItemDto {
  id: string;
  name: string;
  departure: string;
  arrival: string;
  duration: string;
  seat_classes: NormalizedSeatClass[];
}

export type TimetableSource = "official_provider" | "TAGO" | "mock" | "unknown";

export type TimetableProviderResult =
  | { status: "success"; count: number }
  | { status: "error"; provider: RailProvider; httpStatus: number; message: string };

export interface TimetableSearchResult {
  trains: Timetable[];
  providerResults: Partial<Record<RailProvider, TimetableProviderResult>>;
}

const WEEKDAY_ALIASES: readonly (readonly WeekdayValue[])[] = [
  [0, "0", "SUN", "SUNDAY", "일", "일요일"],
  [1, "1", "MON", "MONDAY", "월", "월요일"],
  [2, "2", "TUE", "TUESDAY", "화", "화요일"],
  [3, "3", "WED", "WEDNESDAY", "수", "수요일"],
  [4, "4", "THU", "THURSDAY", "목", "목요일"],
  [5, "5", "FRI", "FRIDAY", "금", "금요일"],
  [6, "6", "SAT", "SATURDAY", "토", "토요일"],
];

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function railProvider(value: unknown): RailProvider | null {
  const provider = typeof value === "string" ? value.toUpperCase() : "";
  if (provider === "KORAIL") return "KORAIL";
  if (provider === "SRT") return "SRT";
  return null;
}

function optionalText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

function normalizedTimetableSource(value: unknown): TimetableSource {
  if (value === "official_provider" || value === "TAGO" || value === "mock") return value;
  return "unknown";
}

function normalizedAdultFare(value: unknown, currency: unknown): number | null {
  return currency === "KRW"
    && typeof value === "number"
    && Number.isInteger(value)
    && value >= 0
    ? value
    : null;
}

function normalizedOfficialUrl(value: unknown, provider: RailProvider): string | null {
  if (typeof value !== "string") return null;
  const candidate = value.trim();
  return candidate && safeOfficialUrl(candidate, provider) ? candidate : null;
}

export function formProviders(form: TimetableSearchForm): RailProvider[] {
  const values = Array.isArray(form.providers) ? form.providers : [form.provider];
  const normalized = values.map(railProvider);
  if (!normalized.length || normalized.some((provider) => provider === null)) {
    throw new ApiError("KORAIL 또는 SRT 운영사를 하나 이상 선택해 주세요.");
  }
  const providers = new Set<RailProvider>();
  for (const provider of normalized) {
    if (provider !== null) providers.add(provider);
  }
  if (!providers.size) throw new ApiError("KORAIL 또는 SRT 운영사를 하나 이상 선택해 주세요.");
  return [...providers];
}

export function formTimeRange(form: TimetableSearchForm): { timeFrom: string; timeTo: string } {
  const timeFrom = form.timeFrom ?? form.time ?? "00:00";
  const timeTo = form.timeTo ?? "23:59";
  const pattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
  if (!pattern.test(timeFrom) || !pattern.test(timeTo) || timeFrom > timeTo) {
    throw new ApiError("조회 시간 범위를 올바르게 선택해 주세요.");
  }
  return { timeFrom, timeTo };
}

function selectedWeekdays(form: TimetableSearchForm): Set<number> | null {
  if (!Array.isArray(form.selectedWeekdays) || !form.selectedWeekdays.length) return null;
  const normalized = form.selectedWeekdays.map((value) => {
    const key = typeof value === "number" ? value : String(value).trim().toUpperCase();
    return WEEKDAY_ALIASES.find((aliases) => aliases.includes(key))?.[0];
  });
  if (normalized.some((value) => value === undefined)) {
    throw new ApiError("선택한 요일 값을 확인해 주세요.");
  }
  return new Set(normalized.filter((value): value is number => typeof value === "number"));
}

export function validateTravelDate(form: TimetableSearchForm): void {
  const date = form.date ?? "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new ApiError("여행 날짜를 선택해 주세요.");
  }
  const [year, month, day] = date.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined) {
    throw new ApiError("여행 날짜를 확인해 주세요.");
  }
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year
    || parsed.getUTCMonth() !== month - 1
    || parsed.getUTCDate() !== day
  ) {
    throw new ApiError("여행 날짜를 확인해 주세요.");
  }
  const weekdays = selectedWeekdays(form);
  if (weekdays && !weekdays.has(parsed.getUTCDay())) {
    throw new ApiError(
      "여행 날짜가 선택한 요일과 일치하지 않습니다. 반복 날짜는 아직 자동 생성하지 않습니다.",
    );
  }
}

function seoulDateAndMinutes(value: unknown): { date: string; minutes: number } | null {
  if (typeof value !== "string") return null;
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
  }).formatToParts(parsed).filter((part) => part.type !== "literal")
    .map((part) => [part.type, part.value]));
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
  };
}

function timeMinutes(value: string): number {
  const [hour = 0, minute = 0] = value.split(":").map(Number);
  return hour * 60 + minute;
}

export function filterTimetables<T extends UnknownRecord>(
  form: TimetableSearchForm,
  items: readonly T[] | null | undefined,
): T[] {
  const providers = new Set(formProviders(form));
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const fromMinutes = timeMinutes(timeFrom);
  const toMinutes = timeMinutes(timeTo);
  const unique = new Map<string, T>();

  for (const item of items ?? []) {
    const provider = railProvider(item.provider);
    const departure = seoulDateAndMinutes(item.departure_at);
    if (!provider || !providers.has(provider) || !departure || departure.date !== form.date) continue;
    if (departure.minutes < fromMinutes || departure.minutes > toMinutes) continue;
    const key = `${provider}:${String(item.train_number ?? "")}:${String(item.departure_at)}`;
    if (!unique.has(key)) unique.set(key, item);
  }

  return [...unique.values()].sort((left, right) => {
    const departureOrder = new Date(String(left.departure_at)).getTime()
      - new Date(String(right.departure_at)).getTime();
    if (departureOrder) return departureOrder;
    return String(left.provider).localeCompare(String(right.provider))
      || String(left.train_number).localeCompare(String(right.train_number));
  });
}

export function timetableTimeLabel(value: unknown): string {
  if (typeof value !== "string" || !value) return "--:--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "--:--";
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  }).format(parsed);
}

function durationLabel(departureAt: string, arrivalAt: string): string {
  const minutes = Math.max(
    0,
    Math.round((new Date(arrivalAt).getTime() - new Date(departureAt).getTime()) / 60000),
  );
  if (!Number.isFinite(minutes)) return "소요 시간 미정";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours > 0 ? `${hours}시간 ${rest}분` : `${rest}분`;
}

function timetableDto(
  value: unknown,
  requestedProvider: RailProvider | null = null,
): TimetableItemDto {
  const errorProvider = requestedProvider ?? "공식 철도";
  if (!isRecord(value)) {
    throw new ApiError(`${errorProvider} 시간표 응답 형식을 확인할 수 없습니다.`);
  }
  const provider = railProvider(value.provider);
  const trainNumber = optionalText(value.train_number);
  const origin = optionalText(value.origin);
  const destination = optionalText(value.destination);
  const departureAt = awareTimestamp(value.departure_at) ? value.departure_at : null;
  const arrivalAt = awareTimestamp(value.arrival_at) ? value.arrival_at : null;
  if (
    provider === null
    || (requestedProvider !== null && provider !== requestedProvider)
    || !trainNumber
    || !origin
    || !destination
    || !departureAt
    || !arrivalAt
  ) {
    throw new ApiError(`${errorProvider} 시간표 응답 형식을 확인할 수 없습니다.`);
  }
  const officialBookingUrl = normalizedOfficialUrl(value.official_booking_url, provider);
  const officialSearchUrl = provider === "KORAIL"
    ? normalizedOfficialUrl(value.official_search_url, provider)
    : null;
  return {
    provider,
    train_number: trainNumber,
    train_type: optionalText(value.train_type),
    origin,
    destination,
    departure_at: departureAt,
    arrival_at: arrivalAt,
    adult_fare: normalizedAdultFare(value.adult_fare, value.fare_currency),
    fare_currency: "KRW",
    timetable_source: normalizedTimetableSource(value.timetable_source),
    timetable_retrieved_at: awareTimestamp(value.timetable_retrieved_at)
      ? value.timetable_retrieved_at
      : null,
    seat_classes: Array.isArray(value.seat_classes) ? value.seat_classes : [],
    official_booking_url: officialBookingUrl,
    official_search_url: officialSearchUrl,
  };
}

function timetableDtos(payload: unknown, provider: RailProvider): TimetableItemDto[] {
  if (!Array.isArray(payload)) {
    throw new ApiError(`${provider} 시간표 응답 형식을 확인할 수 없습니다.`);
  }
  return payload.map((value) => timetableDto(value, provider));
}

function mappedTimetable(item: TimetableItemDto): Timetable {
  return {
    ...item,
    id: `${item.provider}:${item.train_number}:${item.departure_at}`,
    name: item.train_number,
    departure: timetableTimeLabel(item.departure_at),
    arrival: timetableTimeLabel(item.arrival_at),
    duration: durationLabel(item.departure_at, item.arrival_at),
    seat_classes: normalizeSeatClasses(item),
  };
}

export function mapTimetable(value: unknown): Timetable {
  return mappedTimetable(timetableDto(value));
}

function timetableProviderError(
  provider: RailProvider,
  reason: unknown,
): Extract<TimetableProviderResult, { status: "error" }> {
  const httpStatus = reason instanceof ApiError ? reason.status : 0;
  const prefix = httpStatus === 503
    ? "공식 시간표 제공자가 응답하지 않습니다."
    : "공식 시간표를 불러오지 못했습니다.";
  const detail = reason instanceof Error && reason.message
    ? reason.message
    : "잠시 후 다시 시도해 주세요.";
  return { provider, httpStatus, message: `${prefix} ${detail}`, status: "error" };
}

export async function fetchTimetables(
  form: TimetableSearchForm,
  providerOverride: RailProvider | string | null = null,
): Promise<TimetableSearchResult> {
  const providers = providerOverride
    ? [railProvider(providerOverride)]
    : formProviders(form);
  if (providers.some((provider) => provider === null)) {
    throw new ApiError("KORAIL 또는 SRT 운영사를 하나 이상 선택해 주세요.");
  }
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  if (Boolean(originNodeId) !== Boolean(destinationNodeId) || !originNodeId || !destinationNodeId) {
    throw new ApiError("출발역과 도착역 식별자를 다시 선택해 주세요.");
  }

  const responses = await Promise.allSettled(providers.map(async (provider) => {
    if (provider === null) throw new ApiError("KORAIL 또는 SRT 운영사를 하나 이상 선택해 주세요.");
    const params = new URLSearchParams({
      provider: provider.toLowerCase(),
      origin: String(form.origin ?? ""),
      destination: String(form.destination ?? ""),
      departure_from: `${form.date}T${timeFrom}:00+09:00`,
      departure_to: `${form.date}T${timeTo}:00+09:00`,
      passenger_count: String(Number(form.passengers ?? form.passenger_count ?? 1)),
      origin_node_id: originNodeId,
      destination_node_id: destinationNodeId,
    });
    return timetableDtos(await request(`/timetables?${params.toString()}`), provider);
  }));
  const providerResults: Partial<Record<RailProvider, TimetableProviderResult>> = {};
  const items: TimetableItemDto[] = [];
  responses.forEach((result, index) => {
    const provider = providers[index];
    if (provider === undefined || provider === null) return;
    if (result.status === "fulfilled") {
      providerResults[provider] = { status: "success", count: result.value.length };
      items.push(...result.value);
      return;
    }
    providerResults[provider] = timetableProviderError(provider, result.reason);
  });
  return {
    trains: filterTimetables(form, items).map(mappedTimetable),
    providerResults,
  };
}

export async function refreshSeatStatus(
  form: TimetableSearchForm,
  providerOverride: RailProvider | string,
): Promise<Timetable[]> {
  const provider = String(providerOverride ?? "").toUpperCase();
  if (provider !== "KORAIL" && provider !== "SRT") {
    throw new ApiError("좌석 상태를 다시 조회할 운영사를 확인해 주세요.");
  }
  const { timeFrom, timeTo } = formTimeRange(form);
  validateTravelDate(form);
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  if (!originNodeId || !destinationNodeId || originNodeId === destinationNodeId) {
    throw new ApiError("출발역과 도착역 식별자를 다시 선택해 주세요.");
  }
  const passengerCount = Number(form.passengers ?? form.passenger_count ?? 1);
  if (!Number.isInteger(passengerCount) || passengerCount < 1) {
    throw new ApiError("승객 수를 확인해 주세요.");
  }
  const payload = await request("/seat-status/refresh", {
    method: "POST",
    body: JSON.stringify({
      provider: provider.toLowerCase(),
      origin: form.origin,
      destination: form.destination,
      departure_from: `${form.date}T${timeFrom}:00+09:00`,
      departure_to: `${form.date}T${timeTo}:00+09:00`,
      passenger_count: passengerCount,
      origin_node_id: originNodeId,
      destination_node_id: destinationNodeId,
    }),
  });
  const items = timetableDtos(payload, provider);
  return filterTimetables(form, items).map(mappedTimetable);
}
