import { describe, expect, it } from "vitest";

import {
  accessibleTimeBoundary,
  displayTimeRange,
  halfHourBoundaryIndex,
  halfHourBoundaryValue,
  NEW_WAIT_TIME_PRESETS,
  SERVICE_DATE_END_TIME,
} from "../src/features/new-wait/timeRange";

describe("new wait time range contract", () => {
  it("covers midnight through morning in the dawn preset", () => {
    expect(NEW_WAIT_TIME_PRESETS[0]).toEqual({
      label: "새벽",
      start: "00:00",
      end: "09:00",
    });
  });

  it("keeps the evening end unambiguous internally and displays it as midnight", () => {
    expect(NEW_WAIT_TIME_PRESETS.at(-1)).toEqual({
      label: "저녁",
      start: "18:00",
      end: SERVICE_DATE_END_TIME,
    });
    expect(displayTimeRange("18:00", SERVICE_DATE_END_TIME)).toBe("18:00–00:00");
    expect(accessibleTimeBoundary(SERVICE_DATE_END_TIME)).toBe("익일 00:00");
    expect(halfHourBoundaryIndex(SERVICE_DATE_END_TIME)).toBe(48);
    expect(halfHourBoundaryValue(48)).toBe(SERVICE_DATE_END_TIME);
  });
});
