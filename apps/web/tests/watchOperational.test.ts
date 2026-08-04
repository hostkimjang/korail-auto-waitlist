import { afterEach, describe, expect, it, vi } from "vitest";

import { mapOperationalCandidate } from "../src/domain/watchOperational";

describe("mapOperationalCandidate", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a current booking-window fact only while its provenance is fresh", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T13:30:00Z"));

    expect(mapOperationalCandidate({
      operational_status: "scheduled",
      booking_window_status: "open",
      operational_observed_at: "2026-08-02T13:29:00Z",
      operational_fresh_until: "2026-08-02T13:31:00Z",
    })).toMatchObject({
      fresh: true,
      label: "예매창 열림",
    });
  });

  it("degrades an expired operational fact without repeating current-tense details", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T13:32:00Z"));

    const mapped = mapOperationalCandidate({
      operational_status: "delayed",
      booking_window_status: "open",
      delay_minutes: 12,
      estimated_departure_at: "2026-08-02T13:45:00Z",
      operational_observed_at: "2026-08-02T13:29:00Z",
      operational_fresh_until: "2026-08-02T13:31:00Z",
    });

    expect(mapped).toMatchObject({
      fresh: false,
      label: "운행·예매 상태 관측 만료 · 다시 확인 중",
    });
    expect(mapped?.label).not.toContain("예매창 열림");
    expect(mapped?.label).not.toContain("12분 지연");
  });

  it("does not invent an operational message from unknown or malformed input", () => {
    expect(mapOperationalCandidate(null)).toBeNull();
    expect(mapOperationalCandidate({
      operational_status: "raw-provider-value",
      booking_window_status: "unknown",
    })).toBeNull();
  });
});
