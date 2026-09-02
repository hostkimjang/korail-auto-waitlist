import { describe, expect, it } from "vitest";
import {
  rankedStationOptions,
  REVIEWED_STATION_SEARCH_EQUIVALENCES,
} from "../src/features/new-wait/stationSearch";

const stations = [
  { name: "상봉", nodeId: "N3", cityName: "서울특별시" },
  { name: "서울", nodeId: "N1", cityName: "서울특별시" },
  { name: "서서울", nodeId: "N2", cityName: "경기도" },
  { name: "서울강남", nodeId: "N4", cityName: "서울특별시" },
  { name: "수서", nodeId: "N5", cityName: "서울특별시" },
  { name: "울산(통도사)", nodeId: "N6", cityName: "울산광역시" },
  { name: "김천구미", nodeId: "N7", cityName: "경상북도" },
  { name: "여수EXPO", nodeId: "N8", cityName: "전라남도" },
  { name: "경주", nodeId: "N9", cityName: "경상북도" },
  { name: "진부(오대산)", nodeId: "N10", cityName: "강원특별자치도" },
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

  it("finds a reviewed parenthesized official station by either name component", () => {
    expect(rankedStationOptions(stations, "울산")[0]?.nodeId).toBe("N6");
    expect(rankedStationOptions(stations, "통도사")[0]?.nodeId).toBe("N6");
    expect(rankedStationOptions(stations, "울산역")[0]?.nodeId).toBe("N6");
    expect(rankedStationOptions(stations, "진부역")[0]?.nodeId).toBe("N10");
  });

  it("keeps the reviewed API equivalence mirror explicit and complete", () => {
    expect(REVIEWED_STATION_SEARCH_EQUIVALENCES).toEqual([
      ["김천(구미)", "김천구미"],
      ["여수엑스포", "여수EXPO"],
      ["신경주", "경주"],
      ["울산", "울산(통도사)"],
      ["진부", "진부(오대산)"],
    ]);
  });

  it.each([
    ["김천(구미)", "N7"],
    ["여수엑스포", "N8"],
    ["신경주", "N9"],
  ])("keeps the previous TAGO search name %s working", (query, nodeId) => {
    expect(rankedStationOptions(stations, query)[0]?.nodeId).toBe(nodeId);
  });
});
