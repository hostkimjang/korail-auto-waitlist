import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchTimetables as compatibilityFetchTimetables,
  filterTimetables as compatibilityFilterTimetables,
  mapTimetable as compatibilityMapTimetable,
} from "../src/api.js";
import {
  fetchTimetables,
  filterTimetables,
  mapTimetable,
  type TimetableSearchForm,
} from "../src/api/timetables";

const FORM: TimetableSearchForm = {
  providers: ["KORAIL", "SRT"],
  origin: "서울",
  origin_node_id: "N-SEOUL",
  destination: "부산",
  destination_node_id: "N-BUSAN",
  date: "2026-08-04",
  timeFrom: "10:00",
  timeTo: "14:00",
  passengers: "2",
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function timetable(provider: "korail" | "srt", trainNumber: string, departure: string) {
  return {
    provider,
    train_number: trainNumber,
    origin: "서울",
    destination: "부산",
    departure_at: `2026-08-04T${departure}:00+09:00`,
    arrival_at: "2026-08-04T15:00:00+09:00",
    official_booking_url: provider === "korail"
      ? "https://www.korail.com/ticket/search"
      : "https://etk.srail.kr/main.do",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("timetable API boundary", () => {
  it("keeps api.js compatibility exports identical to the TypeScript owners", () => {
    expect(compatibilityFetchTimetables).toBe(fetchTimetables);
    expect(compatibilityFilterTimetables).toBe(filterTimetables);
    expect(compatibilityMapTimetable).toBe(mapTimetable);
  });

  it("queries every selected provider with exact route, range, passenger, and station identities", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _options?: RequestInit) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return jsonResponse(provider === "korail"
        ? [timetable("korail", "KTX 002", "12:00")]
        : [timetable("srt", "SRT 101", "11:00")]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchTimetables(FORM);

    expect(result.trains.map(({ train_number }) => train_number)).toEqual(["SRT 101", "KTX 002"]);
    expect(result.providerResults).toEqual({
      KORAIL: { status: "success", count: 1 },
      SRT: { status: "success", count: 1 },
    });
    for (const [input, options] of fetchMock.mock.calls) {
      const url = new URL(String(input), "https://railwait.local");
      expect(url.pathname).toBe("/api/v1/timetables");
      expect(Object.fromEntries(url.searchParams)).toMatchObject({
        origin: "서울",
        destination: "부산",
        departure_from: "2026-08-04T10:00:00+09:00",
        departure_to: "2026-08-04T14:00:00+09:00",
        passenger_count: "2",
        origin_node_id: "N-SEOUL",
        destination_node_id: "N-BUSAN",
      });
      expect(options).toMatchObject({ method: "GET", credentials: "include" });
    }
  });

  it("uses a provider override without discarding its successful result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([
      timetable("srt", "SRT 327", "13:00"),
    ]));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchTimetables(FORM, "SRT");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(new URL(String(fetchMock.mock.calls[0]?.[0]), "https://railwait.local")
      .searchParams.get("provider")).toBe("srt");
    expect(result.trains.map(({ provider }) => provider)).toEqual(["SRT"]);
    expect(result.providerResults).toEqual({ SRT: { status: "success", count: 1 } });
  });

  it("keeps one provider result when the other fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return provider === "srt"
        ? jsonResponse({ detail: "SRT unavailable" }, 503)
        : jsonResponse([timetable("korail", "KTX 001", "10:30")]);
    }));

    const result = await fetchTimetables(FORM);

    expect(result.trains.map(({ train_number }) => train_number)).toEqual(["KTX 001"]);
    expect(result.providerResults.SRT).toMatchObject({
      status: "error",
      provider: "SRT",
      httpStatus: 503,
      message: expect.stringContaining("SRT unavailable"),
    });
  });

  it("maps malformed provider DTOs to provider-scoped failure and fails seats closed", async () => {
    const valid = {
      ...timetable("korail", "KTX 003", "11:30"),
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: { kind: "official_provider" },
        actions: [{ kind: "add_to_watch" }],
      }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return jsonResponse(provider === "korail" ? [valid] : [{ provider: "srt" }]);
    }));

    const result = await fetchTimetables(FORM);

    expect(result.trains[0]?.seat_classes[0]).toMatchObject({
      status: "unknown",
      provenance: { kind: "not_observed", reason: "invalid_provider_provenance" },
    });
    expect(result.providerResults.SRT).toMatchObject({
      status: "error",
      provider: "SRT",
      httpStatus: 0,
      message: expect.stringContaining("시간표 응답 형식"),
    });
  });
});
