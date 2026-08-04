import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchTimetables,
  mapTimetable,
  refreshSeatStatus,
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
    train_type: provider === "korail" ? "KTX" : "SRT",
    origin: "서울",
    destination: "부산",
    departure_at: `2026-08-04T${departure}:00+09:00`,
    arrival_at: "2026-08-04T15:00:00+09:00",
    adult_fare: 59_800,
    fare_currency: "KRW",
    timetable_source: "official_provider",
    timetable_retrieved_at: "2026-08-04T00:30:00Z",
    official_booking_url: provider === "korail"
      ? "https://www.korail.com/ticket/search"
      : "https://etk.srail.kr/main.do",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("timetable API boundary", () => {
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

  it("normalizes every train-card field and fails unsafe optional metadata closed", () => {
    const mapped = mapTimetable({
      ...timetable("korail", "  KTX 026  ", "12:00"),
      train_type: " KTX-산천 ",
      origin: " 대전 ",
      destination: " 서울 ",
      adult_fare: -1,
      fare_currency: "USD",
      timetable_source: "untrusted_source",
      timetable_retrieved_at: "2026-08-04T00:30:00",
      official_booking_url: "https://attacker.example/ticket",
      official_search_url: "https://attacker.example/search",
    });

    expect(mapped).toMatchObject({
      id: "KORAIL:KTX 026:2026-08-04T12:00:00+09:00",
      provider: "KORAIL",
      train_number: "KTX 026",
      train_type: "KTX-산천",
      origin: "대전",
      destination: "서울",
      adult_fare: null,
      fare_currency: "KRW",
      timetable_source: "unknown",
      timetable_retrieved_at: null,
      official_booking_url: null,
      official_search_url: null,
      departure: "12:00",
      arrival: "15:00",
    });
    expect(mapped.seat_classes).toHaveLength(2);
    expect(mapped.seat_classes.every((seat) => seat.status === "unknown")).toBe(true);
  });

  it("rejects missing journey identity and timezone-naive required timestamps", () => {
    expect(() => mapTimetable({
      ...timetable("korail", "KTX 027", "12:00"),
      origin: " ",
    })).toThrow("시간표 응답 형식");
    expect(() => mapTimetable({
      ...timetable("korail", "KTX 028", "12:00"),
      departure_at: "2026-08-04T12:00:00",
    })).toThrow("시간표 응답 형식");
  });

  it("refreshes one provider with the exact journey payload and fails seat evidence closed", async () => {
    Object.defineProperty(document, "cookie", {
      configurable: true,
      writable: true,
      value: "rail_csrf=seat-refresh-csrf",
    });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{
      ...timetable("korail", "KTX 009", "12:30"),
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        provenance: { kind: "official_provider" },
        actions: [{ kind: "add_to_watch" }],
      }],
    }]));
    vi.stubGlobal("fetch", fetchMock);

    const result = await refreshSeatStatus(FORM, "KORAIL");

    expect(result).toHaveLength(1);
    expect(result[0]?.seat_classes[0]).toMatchObject({
      status: "unknown",
      provenance: { kind: "not_observed", reason: "invalid_provider_provenance" },
    });
    const [url, options] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/seat-status/refresh");
    expect(options).toMatchObject({ method: "POST", credentials: "include" });
    expect(new Headers(options?.headers).get("X-CSRF-Token")).toBe("seat-refresh-csrf");
    expect(JSON.parse(String(options?.body))).toEqual({
      provider: "korail",
      origin: "서울",
      destination: "부산",
      departure_from: "2026-08-04T10:00:00+09:00",
      departure_to: "2026-08-04T14:00:00+09:00",
      passenger_count: 2,
      origin_node_id: "N-SEOUL",
      destination_node_id: "N-BUSAN",
    });
  });

  it("rejects malformed seat refresh envelopes and cross-provider rows", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse([timetable("srt", "SRT 100", "12:30")])));

    await expect(refreshSeatStatus(FORM, "KORAIL"))
      .rejects.toThrow("KORAIL 시간표 응답 형식");
    await expect(refreshSeatStatus(FORM, "KORAIL"))
      .rejects.toThrow("KORAIL 시간표 응답 형식");
  });
});
