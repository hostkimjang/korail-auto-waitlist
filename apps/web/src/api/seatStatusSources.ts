import {
  mapSeatStatusSources,
  type SeatStatusSource,
} from "./seatStatusSourcesContract";

export class SeatStatusSourcesRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "SeatStatusSourcesRequestError";
    this.status = status;
  }
}

export async function fetchSeatStatusSources(signal?: AbortSignal): Promise<SeatStatusSource[]> {
  const options: RequestInit = {
    headers: { Accept: "application/json" },
    credentials: "include",
    ...(signal ? { signal } : {}),
  };
  const response = await fetch("/api/v1/seat-status/status", options);
  if (!response.ok) {
    throw new SeatStatusSourcesRequestError("좌석 조회 제공원 상태를 불러오지 못했습니다.", response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new SeatStatusSourcesRequestError("좌석 조회 제공원 상태 응답 형식을 확인할 수 없습니다.", response.status);
  }
  return mapSeatStatusSources(payload);
}
