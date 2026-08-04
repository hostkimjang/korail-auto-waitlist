import { describe, expect, it } from "vitest";
import { rankedStationOptions } from "../src/features/new-wait/stationSearch";

const stations = [
  { name: "상봉", nodeId: "N3", cityName: "서울특별시" },
  { name: "서울", nodeId: "N1", cityName: "서울특별시" },
  { name: "서서울", nodeId: "N2", cityName: "경기도" },
  { name: "서울강남", nodeId: "N4", cityName: "서울특별시" },
  { name: "수서", nodeId: "N5", cityName: "서울특별시" },
];

describe("rankedStationOptions", () => {
  it("prioritizes exact, prefix, and contained station-name matches before city matches", () => {
    expect(rankedStationOptions(stations, "서울").map((station) => station.name)).toEqual([
      "서울",
      "서울강남",
      "서서울",
      "상봉",
      "수서",
    ]);
  });

  it("normalizes unicode width and whitespace before ranking", () => {
    expect(rankedStationOptions(stations, "  서　울 ")[0]?.name).toBe("서울");
  });

  it("preserves the catalog order for an empty query", () => {
    expect(rankedStationOptions(stations, "")).toEqual(stations);
  });
});
