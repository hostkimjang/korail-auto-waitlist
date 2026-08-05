import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchStations } from "../src/api/stations";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function catalog(provider: "korail" | "srt", stations: unknown[]): Record<string, unknown> {
  return {
    provider,
    source: "TAGO",
    retrieved_at: "2026-08-04T00:00:00Z",
    catalog_scope: "intercity_station_guide_intersection",
    provider_membership: "not_verified_by_source",
    note: "일반·고속열차 여정 선택에 적합한 역 목록입니다.",
    stations,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("station catalog API boundary", () => {
  it("loads both official catalogs for SRT and merges only identical node identities", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return provider === "korail"
        ? jsonResponse(catalog("korail", [
          { node_id: "N1", name: "서울", city_code: "11", city_name: "서울" },
          { node_id: "N2", name: "부산", city_code: "26", city_name: "부산" },
        ]))
        : jsonResponse(catalog("srt", [
          { node_id: "N1", name: "서울", city_code: "11", city_name: "서울" },
          { node_id: "N3", name: "부산", city_code: "26", city_name: "부산" },
        ]));
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchStations(["SRT"]);

    expect(fetchMock.mock.calls.map(([input]) => (
      new URL(String(input), "https://railwait.local").searchParams.get("provider")
    ))).toEqual(["korail", "srt"]);
    expect(result.stations.filter(({ name }) => name === "부산").map(({ nodeId }) => nodeId))
      .toEqual(["N2", "N3"]);
    expect(result.stations.find(({ nodeId }) => nodeId === "N1")).toMatchObject({
      catalogProviders: ["KORAIL", "SRT"],
      sources: ["TAGO"],
      providerMembershipVerified: false,
    });
    expect(result.providerMembershipVerified).toBe(false);
  });

  it("rejects malformed metadata and station DTOs instead of inventing identities", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      ...catalog("korail", [
        { node_id: "", name: "서울", city_code: "11", city_name: "서울" },
      ]),
      source: "unverified",
    })));

    await expect(fetchStations("KORAIL")).rejects.toThrow("역 목록 응답 형식");
  });

  it("fails the combined request when one provider is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return provider === "korail"
        ? jsonResponse(catalog("korail", [
          { node_id: "N1", name: "서울", city_code: "11", city_name: "서울" },
        ]))
        : jsonResponse({ detail: "SRT station catalog unavailable" }, 503);
    }));

    await expect(fetchStations(["KORAIL", "SRT"]))
      .rejects.toThrow("SRT station catalog unavailable");
  });

  it("queries each selected station catalog, merges shared node IDs and preserves distinct same-name nodes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return jsonResponse({
        provider,
        source: "TAGO",
        retrieved_at: "2026-07-29T00:00:00Z",
        catalog_scope: "intercity_station_guide_intersection",
        provider_membership: "not_verified_by_source",
        note: "일반·고속열차 여정 선택에 적합한 역 목록입니다.",
        stations: provider === "korail"
          ? [
            { node_id: "N1", name: "서울", city_code: "11", city_name: "서울" },
            { node_id: "N2", name: "부산", city_code: "26", city_name: "부산" },
          ]
          : [
            { node_id: "N1", name: "서울", city_code: "11", city_name: "서울" },
            { node_id: "N3", name: "수서", city_code: "11", city_name: "서울" },
            { node_id: "N4", name: "부산", city_code: "26", city_name: "부산" },
          ],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchStations(["KORAIL", "SRT"]);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([input]) => (
      new URL(String(input), "https://railwait.local").searchParams.get("provider")
    ))).toEqual(["korail", "srt"]);
    expect(result.stations.map((station) => station.name)).toEqual(["부산", "부산", "서울", "수서"]);
    expect(result.stations.filter((station) => station.name === "부산").map((station) => station.nodeId))
      .toEqual(["N2", "N4"]);
    expect(result.stations.find((station) => station.name === "서울")).toMatchObject({
      catalogProviders: ["KORAIL", "SRT"],
      providerMembershipVerified: false,
    });
    expect(result.providerMembershipVerified).toBe(false);
  });

  it("loads the combined official station catalog even when only SRT is selected", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      return jsonResponse({
        provider,
        source: "TAGO",
        retrieved_at: "2026-08-03T00:00:00Z",
        catalog_scope: "intercity_station_guide_intersection",
        provider_membership: "not_verified_by_source",
        note: "일반·고속열차 여정 선택에 적합한 역 목록입니다.",
        stations: provider === "korail"
          ? [{ node_id: "N1", name: "서울", city_code: "11", city_name: "서울" }]
          : [{ node_id: "N2", name: "수서", city_code: "11", city_name: "서울" }],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchStations(["SRT"]);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.stations.map((station) => station.name)).toEqual(["서울", "수서"]);
  });

  it("fails closed when one station catalog request or response is invalid", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const provider = new URL(String(input), "https://railwait.local").searchParams.get("provider");
      if (provider === "srt") return jsonResponse({ detail: "station provider unavailable" }, 503);
      return jsonResponse({
        provider,
        source: "TAGO",
        retrieved_at: "2026-07-29T00:00:00Z",
        catalog_scope: "intercity_station_guide_intersection",
        provider_membership: "not_verified_by_source",
        note: "일반·고속열차 여정 선택에 적합한 역 목록입니다.",
        stations: [{ node_id: "N1", name: "서울", city_code: "11", city_name: "서울" }],
      });
    }));
    await expect(fetchStations(["KORAIL", "SRT"])).rejects.toThrow("station provider unavailable");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      provider: "korail",
      source: "TAGO",
      retrieved_at: "2026-07-29T00:00:00Z",
      catalog_scope: "intercity_station_guide_intersection",
      provider_membership: "not_verified_by_source",
      note: "일반·고속열차 여정 선택에 적합한 역 목록입니다.",
      stations: [{ node_id: "", name: "서울", city_code: "11", city_name: "서울" }],
    })));
    await expect(fetchStations(["KORAIL"])).rejects.toThrow("불완전한 항목");
  });

  it.each([
    ["TAGO", "all_tago_train_stations", "not_verified_by_source"],
    ["TAGO", "mock", "not_verified_by_source"],
    ["TAGO", "all_tago_train_stations", "mock"],
    ["mock", "all_tago_train_stations", "mock"],
    ["mock", "mock", "not_verified_by_source"],
  ] as const)("rejects an invalid station catalog metadata tuple: %s/%s/%s", async (
    source,
    catalogScope,
    providerMembership,
  ) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      provider: "korail",
      source,
      retrieved_at: "2026-07-29T00:00:00Z",
      catalog_scope: catalogScope,
      provider_membership: providerMembership,
      stations: [{ node_id: "N1", name: "서울", city_code: "11", city_name: "서울" }],
    })));

    await expect(fetchStations(["KORAIL"])).rejects.toThrow("역 목록 응답 형식");
  });

  it("rejects a station catalog with missing scope or an empty station list", async () => {
    const payload: Record<string, unknown> = {
      provider: "korail",
      source: "TAGO",
      retrieved_at: "2026-07-29T00:00:00Z",
      provider_membership: "not_verified_by_source",
      stations: [{ node_id: "N1", name: "서울", city_code: "11", city_name: "서울" }],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    await expect(fetchStations(["KORAIL"])).rejects.toThrow("역 목록 응답 형식");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      ...payload,
      catalog_scope: "intercity_station_guide_intersection",
      stations: [],
    })));
    await expect(fetchStations(["KORAIL"])).rejects.toThrow("역 목록 응답 형식");
  });

  it("accepts the exact mock station metadata tuple", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      provider: "korail",
      source: "mock",
      retrieved_at: "2026-07-29T00:00:00Z",
      catalog_scope: "mock",
      provider_membership: "mock",
      stations: [{ node_id: "MOCK-N1", name: "서울", city_code: "11", city_name: "서울" }],
    })));

    await expect(fetchStations(["KORAIL"])).resolves.toMatchObject({
      stations: [{ nodeId: "MOCK-N1", name: "서울" }],
      catalogs: [{ source: "mock", catalogScope: "mock", providerMembership: "mock" }],
    });
  });
});
