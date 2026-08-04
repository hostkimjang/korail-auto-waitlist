export interface TimetableSnapshotForm {
  origin: string;
  destination: string;
  origin_node_id: string | null;
  destination_node_id: string | null;
  date: string;
  time: string;
  timeEnd: string;
  passengers: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function fetchCachedTimetableSnapshot(
  form: TimetableSnapshotForm,
  provider: string,
): Promise<Array<Record<string, unknown>> | null> {
  const normalizedProvider = provider.toUpperCase();
  if (!new Set(["KORAIL", "SRT", "MOCK"]).has(normalizedProvider)) {
    throw new Error("시간표 snapshot 운영사를 확인할 수 없습니다.");
  }
  const originNodeId = String(form.origin_node_id ?? "").trim();
  const destinationNodeId = String(form.destination_node_id ?? "").trim();
  const params = new URLSearchParams({
    provider: normalizedProvider.toLowerCase(),
    origin: form.origin.trim(),
    destination: form.destination.trim(),
    departure_from: `${form.date}T${form.time}:00+09:00`,
    departure_to: `${form.date}T${form.timeEnd}:00+09:00`,
    passenger_count: String(Number(form.passengers || 1)),
  });
  if (originNodeId && destinationNodeId) {
    params.set("origin_node_id", originNodeId);
    params.set("destination_node_id", destinationNodeId);
  }
  const response = await fetch(`/api/v1/timetable-snapshots?${params.toString()}`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("서버의 최신 시간표 snapshot을 불러오지 못했습니다.");
  const payload: unknown = await response.json();
  if (!Array.isArray(payload) || !payload.every(isRecord)) {
    throw new Error("시간표 snapshot 응답 형식이 올바르지 않습니다.");
  }
  return payload;
}
