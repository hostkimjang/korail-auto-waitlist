import { describe, expect, it } from "vitest";
import { buildTimetableQueryKey } from "../src/features/new-wait/timetableQueryKey";

const form = {
  providers: ["SRT", "KORAIL"],
  origin: " 대전 ",
  origin_node_id: "0010",
  destination: "서울",
  destination_node_id: "0001",
  date: "2026-07-30",
  time: "12:00",
  timeEnd: "18:00",
  passengers: "1",
};

describe("timetable query key", () => {
  it("changes when passenger count changes so an older response cannot overwrite it", () => {
    const onePassenger = buildTimetableQueryKey(form);
    const twoPassengers = buildTimetableQueryKey({ ...form, passengers: "2" });

    expect(onePassenger).not.toBe(twoPassengers);
    expect(JSON.parse(twoPassengers)).toMatchObject({
      providers: ["KORAIL", "SRT"],
      origin: "대전",
      passengerCount: 2,
    });
  });
});
