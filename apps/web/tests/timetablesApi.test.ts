import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchTimetables,
  filterTimetables,
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

function requestedProviderKey(input: RequestInfo | URL): "korail" | "srt" {
  const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
  if (provider !== "korail" && provider !== "srt") {
    throw new Error(`Unexpected timetable provider: ${provider ?? "missing"}`);
  }
  return provider;
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

  it("maps the production timetable contract fields", () => {
    expect(mapTimetable({
      provider: "srt",
      train_number: "SRT 327",
      origin: "수서",
      destination: "부산",
      departure_at: "2026-08-01T14:30:00+09:00",
      arrival_at: "2026-08-01T16:58:00+09:00",
      official_booking_url: "https://etk.srail.kr",
    })).toMatchObject({
      provider: "SRT",
      name: "SRT 327",
      departure: "14:30",
      arrival: "16:58",
      duration: "2시간 28분",
      official_booking_url: "https://etk.srail.kr",
    });
  });

  it("queries production timetables with provider, route, date and time", async () => {
    const item = {
      provider: "srt",
      train_number: "SRT 327",
      origin: "수서",
      destination: "부산",
      departure_at: "2026-08-01T14:30:00+09:00",
      arrival_at: "2026-08-01T16:58:00+09:00",
      official_booking_url: "https://etk.srail.kr",
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([item]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTimetables({
      provider: "SRT",
      origin: "수서",
      origin_node_id: "N-SUSEO",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      time: "10:00",
    })).resolves.toMatchObject({
      trains: [{ provider: "SRT", train_number: "SRT 327" }],
      providerResults: { SRT: { status: "success", count: 1 } },
    });
    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [input, options] = requestCall ?? [];
    const parsed = new URL(String(input), "https://railwait.local");
    expect(parsed.pathname).toBe("/api/v1/timetables");
    expect(parsed.searchParams.get("passenger_count")).toBe("1");
    expect(Object.fromEntries(parsed.searchParams)).toEqual({
      provider: "srt",
      origin: "수서",
      destination: "부산",
      departure_from: "2026-08-01T10:00:00+09:00",
      departure_to: "2026-08-01T23:59:00+09:00",
      passenger_count: "1",
      origin_node_id: "N-SUSEO",
      destination_node_id: "N-BUSAN",
    });
    expect(options?.credentials).toBe("include");
  });

  it("forwards the selected passenger count to seat-aware timetable sources", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTimetables({
      provider: "SRT",
      origin: "수서",
      origin_node_id: "N-SUSEO",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      time: "10:00",
      passengers: "3",
    });

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [input] = requestCall ?? [];
    expect(new URL(String(input), "https://railwait.local").searchParams.get("passenger_count"))
      .toBe("3");
  });

  it("queries each selected provider once and merges, filters, sorts and deduplicates the result", async () => {
    const trains: Record<"korail" | "srt", unknown[]> = {
      korail: [
        { provider: "korail", train_number: "KTX 002", origin: "서울", destination: "부산", departure_at: "2026-08-01T12:30:00+09:00", arrival_at: "2026-08-01T15:00:00+09:00", official_booking_url: "https://www.letskorail.com" },
        { provider: "korail", train_number: "KTX 001", origin: "서울", destination: "부산", departure_at: "2026-08-01T10:30:00+09:00", arrival_at: "2026-08-01T13:00:00+09:00", official_booking_url: "https://www.letskorail.com" },
      ],
      srt: [
        { provider: "srt", train_number: "SRT 100", origin: "서울", destination: "부산", departure_at: "2026-08-01T11:00:00+09:00", arrival_at: "2026-08-01T13:20:00+09:00", official_booking_url: "https://etk.srail.kr" },
        { provider: "srt", train_number: "SRT 100", origin: "서울", destination: "부산", departure_at: "2026-08-01T11:00:00+09:00", arrival_at: "2026-08-01T13:20:00+09:00", official_booking_url: "https://etk.srail.kr" },
        { provider: "srt", train_number: "SRT 200", origin: "서울", destination: "부산", departure_at: "2026-08-01T13:01:00+09:00", arrival_at: "2026-08-01T15:20:00+09:00", official_booking_url: "https://etk.srail.kr" },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => (
      jsonResponse(trains[requestedProviderKey(input)])
    ));
    vi.stubGlobal("fetch", fetchMock);

    const { trains: items, providerResults } = await fetchTimetables({
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "13:00",
      selectedWeekdays: ["토"],
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([input]) => requestedProviderKey(input)))
      .toEqual(["korail", "srt"]);
    expect(fetchMock.mock.calls.every(([input]) => (
      new URL(String(input), "https://railwait.local").searchParams.get("departure_from")
      === "2026-08-01T10:00:00+09:00"
    ))).toBe(true);
    expect(fetchMock.mock.calls.every(([input]) => (
      new URL(String(input), "https://railwait.local").searchParams.get("departure_to")
      === "2026-08-01T13:00:00+09:00"
    ))).toBe(true);
    expect(items.map((item) => item.id)).toEqual([
      "KORAIL:KTX 001:2026-08-01T10:30:00+09:00",
      "SRT:SRT 100:2026-08-01T11:00:00+09:00",
      "KORAIL:KTX 002:2026-08-01T12:30:00+09:00",
    ]);
    expect(items[1]?.official_booking_url).toBe("https://etk.srail.kr");
    expect(providerResults).toMatchObject({
      KORAIL: { status: "success" },
      SRT: { status: "success" },
    });
  });

  it("sends the displayed evening midnight as the established service-date 23:59 boundary", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await fetchTimetables({
      providers: ["KORAIL"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "18:00",
      timeTo: "23:59",
    });

    const [input] = fetchMock.mock.calls[0] ?? [];
    const params = new URL(String(input), "https://railwait.local").searchParams;
    expect(params.get("departure_from")).toBe("2026-08-01T18:00:00+09:00");
    expect(params.get("departure_to")).toBe("2026-08-01T23:59:00+09:00");
  });

  it("applies the same pure timetable filter to demo-shaped items", () => {
    const form: TimetableSearchForm = {
      providers: ["SRT", "KORAIL"],
      date: "2026-08-01",
      timeFrom: "11:00",
      timeTo: "12:00",
      selectedWeekdays: [6],
    };
    const items = [
      { provider: "KORAIL", train_number: "KTX 003", departure_at: "2026-08-01T12:01:00+09:00" },
      { provider: "SRT", train_number: "SRT 101", departure_at: "2026-08-01T11:30:00+09:00" },
      { provider: "SRT", train_number: "SRT 101", departure_at: "2026-08-01T11:30:00+09:00" },
      { provider: "KORAIL", train_number: "KTX 001", departure_at: "2026-08-01T11:00:00+09:00" },
    ];
    expect(filterTimetables(form, items).map((item) => item.train_number))
      .toEqual(["KTX 001", "SRT 101"]);
  });

  it("returns every successful provider item in the selected range without a frontend result cap", async () => {
    const makeItems = (
      provider: "KORAIL" | "SRT",
      prefix: string,
      offset: number,
    ): Array<Record<string, unknown>> => Array.from({ length: 23 }, (_, index) => {
      const minutes = 10 * 60 + offset + index * 10;
      const time = `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
      return {
        provider: provider.toLowerCase(),
        train_number: `${prefix} ${String(index + 1).padStart(3, "0")}`,
        origin: "서울",
        destination: "부산",
        departure_at: `2026-08-01T${time}:00+09:00`,
        arrival_at: "2026-08-01T15:00:00+09:00",
      };
    });
    const results: Record<"korail" | "srt", Array<Record<string, unknown>>> = {
      korail: makeItems("KORAIL", "KTX", 0),
      srt: makeItems("SRT", "SRT", 5),
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => (
      jsonResponse(results[requestedProviderKey(input)])
    )));

    const result = await fetchTimetables({
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "14:00",
    });

    expect(result.trains).toHaveLength(46);
    expect(result.trains.map((train) => train.provider).filter((provider) => provider === "KORAIL"))
      .toHaveLength(23);
    expect(result.trains.map((train) => train.provider).filter((provider) => provider === "SRT"))
      .toHaveLength(23);
    expect(result.trains.map((train) => Date.parse(train.departure_at))).toEqual(
      [...result.trains]
        .map((train) => Date.parse(train.departure_at))
        .sort((left, right) => left - right),
    );
  });

  it("rejects a travel date that does not match the selected weekdays before requesting", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTimetables({
      providers: ["KORAIL"],
      origin: "서울",
      destination: "부산",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "12:00",
      selectedWeekdays: ["MON"],
    })).rejects.toThrow("반복 날짜는 아직 자동 생성하지 않습니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an incomplete station identity pair before requesting a timetable", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTimetables({
      provider: "KORAIL",
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: null,
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "12:00",
    })).rejects.toThrow("식별자를 다시 선택");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects missing official station identities before requesting a timetable", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTimetables({
      provider: "KORAIL",
      origin: "서울",
      destination: "부산",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "12:00",
    })).rejects.toThrow("식별자를 다시 선택");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an inverted timetable range", () => {
    expect(() => filterTimetables({
      providers: ["KORAIL"],
      date: "2026-08-01",
      timeFrom: "13:00",
      timeTo: "10:00",
    }, [])).toThrow("조회 시간 범위를 올바르게 선택해 주세요");
  });

  it("preserves a production timetable 503 as a provider-scoped error", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      detail: "TAGO service key is not configured",
    }, 503)));
    await expect(fetchTimetables({
      provider: "KORAIL",
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      time: "10:00",
    })).resolves.toMatchObject({
      trains: [],
      providerResults: {
        KORAIL: {
          status: "error",
          httpStatus: 503,
          message: expect.stringContaining("TAGO service key is not configured"),
        },
      },
    });
  });

  it("keeps one provider result when the other provider fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const provider = requestedProviderKey(input);
      if (provider === "srt") return jsonResponse({ detail: "SRT unavailable" }, 503);
      return jsonResponse([{
        provider: "korail",
        train_number: "KTX 001",
        origin: "서울",
        destination: "부산",
        departure_at: "2026-08-01T10:30:00+09:00",
        arrival_at: "2026-08-01T13:00:00+09:00",
        official_booking_url: "https://www.korail.com/ticket/search",
      }]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchTimetables({
      providers: ["KORAIL", "SRT"],
      origin: "서울",
      origin_node_id: "N-SEOUL",
      destination: "부산",
      destination_node_id: "N-BUSAN",
      date: "2026-08-01",
      timeFrom: "10:00",
      timeTo: "12:00",
    });
    expect(result.trains.map((train) => train.train_number)).toEqual(["KTX 001"]);
    expect(result.providerResults).toMatchObject({
      KORAIL: { status: "success" },
      SRT: { status: "error", httpStatus: 503 },
    });
  });
});
