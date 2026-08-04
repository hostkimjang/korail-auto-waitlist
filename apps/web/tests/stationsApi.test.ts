import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchStations as compatibilityFetchStations,
  mergeStationCatalogs as compatibilityMergeStationCatalogs,
} from "../src/api.js";
import { fetchStations, mergeStationCatalogs } from "../src/api/stations";

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
  it("keeps api.js compatibility exports identical to the TypeScript owners", () => {
    expect(compatibilityFetchStations).toBe(fetchStations);
    expect(compatibilityMergeStationCatalogs).toBe(mergeStationCatalogs);
  });

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
});
