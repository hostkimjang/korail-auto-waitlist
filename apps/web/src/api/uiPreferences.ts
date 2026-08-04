export const DEFAULT_TIMETABLE_REFRESH_INTERVAL_SECONDS = 5;
export const MIN_TIMETABLE_REFRESH_INTERVAL_SECONDS = 5;
export const MAX_TIMETABLE_REFRESH_INTERVAL_SECONDS = 300;
export const DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS = 5;
export const MIN_SEAT_OBSERVATION_INTERVAL_SECONDS = 1;
export const MAX_SEAT_OBSERVATION_INTERVAL_SECONDS = 600;

export interface UiPreferences {
  timetableRefreshIntervalSeconds: number;
  seatObservationIntervalSeconds: number;
  updatedAt: string;
}

interface UiPreferencesDto {
  timetable_refresh_interval_seconds: number;
  seat_observation_interval_seconds: number;
  preferences_updated_at: string;
}

export interface UpdateUiPreferencesInput {
  timetableRefreshIntervalSeconds: number;
  seatObservationIntervalSeconds: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parsePreferences(payload: unknown): UiPreferences {
  if (!isRecord(payload)) throw new Error("화면 갱신 설정 응답을 확인할 수 없습니다.");
  const dto: UiPreferencesDto = {
    timetable_refresh_interval_seconds: Number(payload.timetable_refresh_interval_seconds),
    seat_observation_interval_seconds: Number(payload.seat_observation_interval_seconds),
    preferences_updated_at: String(payload.preferences_updated_at ?? ""),
  };
  if (
    !Number.isInteger(dto.timetable_refresh_interval_seconds)
    || dto.timetable_refresh_interval_seconds < MIN_TIMETABLE_REFRESH_INTERVAL_SECONDS
    || dto.timetable_refresh_interval_seconds > MAX_TIMETABLE_REFRESH_INTERVAL_SECONDS
    || !Number.isInteger(dto.seat_observation_interval_seconds)
    || dto.seat_observation_interval_seconds < MIN_SEAT_OBSERVATION_INTERVAL_SECONDS
    || dto.seat_observation_interval_seconds > MAX_SEAT_OBSERVATION_INTERVAL_SECONDS
    || !Number.isFinite(Date.parse(dto.preferences_updated_at))
  ) {
    throw new Error("화면 갱신 설정 응답이 올바르지 않습니다.");
  }
  return {
    timetableRefreshIntervalSeconds: dto.timetable_refresh_interval_seconds,
    seatObservationIntervalSeconds: dto.seat_observation_interval_seconds,
    updatedAt: dto.preferences_updated_at,
  };
}

function csrfToken(): string {
  const match = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith("rail_csrf="));
  return match ? decodeURIComponent(match.slice("rail_csrf=".length)) : "";
}

async function preferencesRequest(method: "GET" | "PATCH", body?: unknown): Promise<UiPreferences> {
  const headers = new Headers({ Accept: "application/json" });
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    headers.set("X-CSRF-Token", csrfToken());
  }
  const response = await fetch("/api/v1/preferences/ui", {
    method,
    credentials: "include",
    cache: "no-store",
    headers,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    throw new Error(method === "GET"
      ? "화면 갱신 설정을 불러오지 못했습니다."
      : "화면 갱신 설정을 저장하지 못했습니다.");
  }
  return parsePreferences(await response.json());
}

export function fetchUiPreferences(): Promise<UiPreferences> {
  return preferencesRequest("GET");
}

export function updateUiPreferences(
  input: UpdateUiPreferencesInput,
): Promise<UiPreferences> {
  return preferencesRequest("PATCH", {
    timetable_refresh_interval_seconds: input.timetableRefreshIntervalSeconds,
    seat_observation_interval_seconds: input.seatObservationIntervalSeconds,
  });
}
