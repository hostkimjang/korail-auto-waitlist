import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchCachedTimetableSnapshot } from "../src/api/timetableSnapshots";

const form = {
  origin: " 서울 ",
  destination: " 부산 ",
  origin_node_id: " N-SEOUL ",
  destination_node_id: " N-BUSAN ",
  date: "2026-08-01",
  time: "10:00",
  timeEnd: "12:00",
  passengers: "2",
};

describe("timetable snapshot API boundary", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("treats a cache 404 as a cache miss", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));

    await expect(fetchCachedTimetableSnapshot(form, "KORAIL")).resolves.toBeNull();
  });

  it("builds a cache-only query with the exact journey identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([{ train_number: "KTX 001" }]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchCachedTimetableSnapshot(form, "srt")).resolves.toEqual([{ train_number: "KTX 001" }]);

    const [url, request] = fetchMock.mock.calls[0] ?? [];
    const params = new URL(url, "https://railwait.local").searchParams;
    expect(Object.fromEntries(params)).toMatchObject({
      provider: "srt",
      origin: "서울",
      destination: "부산",
      origin_node_id: "N-SEOUL",
      destination_node_id: "N-BUSAN",
      departure_from: "2026-08-01T10:00:00+09:00",
      departure_to: "2026-08-01T12:00:00+09:00",
      passenger_count: "2",
    });
    expect(request).toMatchObject({ method: "GET", credentials: "include", cache: "no-store" });
  });

  it("rejects unsupported providers and malformed successful payloads before use", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ train_number: "KTX 001" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchCachedTimetableSnapshot(form, "unknown")).rejects.toThrow("운영사를 확인할 수 없습니다");
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(fetchCachedTimetableSnapshot(form, "KORAIL")).rejects.toThrow("응답 형식이 올바르지 않습니다");
  });
});
