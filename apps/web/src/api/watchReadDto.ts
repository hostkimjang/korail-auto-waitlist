import type { WatchProvider, WatchSeatClass, WatchStatus } from "../domain/watch";
import {
  isWatchSeatClass,
  isWatchStatus,
  normalizeWatchProvider,
} from "../domain/watch";
import { ApiError } from "./client";
import { awareTimestamp } from "./seatClasses";

type UnknownRecord = Record<string, unknown>;

export interface WatchCandidateReadDto {
  id: string;
  train_number: string;
  train_type: string | null;
  departure_at: string;
  arrival_at: string | null;
  seat_class: WatchSeatClass;
  priority: number;
  state: unknown;
  registration_evidence: unknown;
  latest_observation: unknown;
  latest_reservation_attempt: unknown;
  operational_status: unknown;
  booking_window_status: unknown;
  delay_minutes: unknown;
  estimated_departure_at: unknown;
  actual_departure_at: unknown;
  operational_source: unknown;
  operational_observed_at: unknown;
  operational_fresh_until: unknown;
}

export interface WatchReadDto {
  id: string;
  provider: WatchProvider;
  status: WatchStatus;
  origin: string;
  destination: string;
  travel_date: string;
  candidates: readonly unknown[];
  payment_deadline: unknown;
  created_at: unknown;
  updated_at: unknown;
  official_booking_url: unknown;
  reservation_policy: unknown;
  train_numbers: unknown;
  seat_class: unknown;
  last_checked_at: unknown;
  time_from: unknown;
  time_to: unknown;
  seat_observation_mode: unknown;
  focused_observation_interval_seconds: unknown;
  next_check_at: unknown;
  observation_execution_state: unknown;
  cooldown_until: unknown;
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new ApiError("대기 작업 응답 형식을 확인할 수 없습니다.");
  }
  return value.trim();
}

function isCalendarDate(value: string): boolean {
  const [year = Number.NaN, month = Number.NaN, day = Number.NaN] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint !== undefined && (codePoint <= 0x1f || codePoint === 0x7f);
  });
}

function optionalDisplayText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized.length > 0 && normalized.length <= 40 && !hasControlCharacter(normalized)
    ? normalized
    : null;
}

export function parseWatchReadDto(value: unknown): WatchReadDto {
  if (!isRecord(value)) throw new ApiError("대기 작업 응답 형식을 확인할 수 없습니다.");
  const provider = normalizeWatchProvider(requiredString(value.provider));
  const status = requiredString(value.status);
  const travelDate = requiredString(value.travel_date);
  if (provider === null || !isWatchStatus(status) || !isCalendarDate(travelDate)) {
    throw new ApiError("대기 작업 응답 형식을 확인할 수 없습니다.");
  }
  return {
    id: requiredString(value.id),
    provider,
    status,
    origin: requiredString(value.origin),
    destination: requiredString(value.destination),
    travel_date: travelDate,
    candidates: Array.isArray(value.candidates) ? value.candidates : [],
    payment_deadline: value.payment_deadline,
    created_at: value.created_at,
    updated_at: value.updated_at,
    official_booking_url: value.official_booking_url,
    reservation_policy: value.reservation_policy,
    train_numbers: value.train_numbers,
    seat_class: value.seat_class,
    last_checked_at: value.last_checked_at,
    time_from: value.time_from,
    time_to: value.time_to,
    seat_observation_mode: value.seat_observation_mode,
    focused_observation_interval_seconds: value.focused_observation_interval_seconds,
    next_check_at: value.next_check_at,
    observation_execution_state: value.observation_execution_state,
    cooldown_until: value.cooldown_until,
  };
}

export function parseWatchCandidateReadDto(value: unknown): WatchCandidateReadDto | null {
  if (!isRecord(value)) return null;
  const id = typeof value.id === "string" ? value.id.trim() : "";
  const trainNumber = typeof value.train_number === "string" ? value.train_number.trim() : "";
  const departureAt = awareTimestamp(value.departure_at) ? value.departure_at : null;
  const arrivalAt = value.arrival_at === null || value.arrival_at === undefined
    ? null
    : awareTimestamp(value.arrival_at)
      ? value.arrival_at
      : null;
  if (
    !id
    || !trainNumber
    || departureAt === null
    || (value.arrival_at !== null && value.arrival_at !== undefined && arrivalAt === null)
    || !isWatchSeatClass(value.seat_class)
    || !Number.isInteger(value.priority)
    || Number(value.priority) < 1
  ) return null;
  return {
    id,
    train_number: trainNumber,
    train_type: optionalDisplayText(value.train_type),
    departure_at: departureAt,
    arrival_at: arrivalAt,
    seat_class: value.seat_class,
    priority: Number(value.priority),
    state: value.state,
    registration_evidence: value.registration_evidence,
    latest_observation: value.latest_observation,
    latest_reservation_attempt: value.latest_reservation_attempt,
    operational_status: value.operational_status,
    booking_window_status: value.booking_window_status,
    delay_minutes: value.delay_minutes,
    estimated_departure_at: value.estimated_departure_at,
    actual_departure_at: value.actual_departure_at,
    operational_source: value.operational_source,
    operational_observed_at: value.operational_observed_at,
    operational_fresh_until: value.operational_fresh_until,
  };
}
