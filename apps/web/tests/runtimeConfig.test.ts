import { describe, expect, it } from "vitest";

import {
  resolveDemoCaptureReservationLifecycle,
  resolveDemoMode,
} from "../src/shared/lib/runtimeConfig";

describe("runtime configuration", () => {
  it("allows demo data only in development and honors the explicit opt-out", () => {
    expect(resolveDemoMode(true, undefined)).toBe(true);
    expect(resolveDemoMode(true, "true")).toBe(true);
    expect(resolveDemoMode(true, "false")).toBe(false);
    expect(resolveDemoMode(false, undefined)).toBe(false);
    expect(resolveDemoMode(false, "true")).toBe(false);
  });

  it("enables the reservation lifecycle driver only for an explicit development capture", () => {
    expect(resolveDemoCaptureReservationLifecycle(
      true,
      true,
      "reservation-lifecycle",
    )).toBe(true);
    expect(resolveDemoCaptureReservationLifecycle(false, true, "reservation-lifecycle")).toBe(false);
    expect(resolveDemoCaptureReservationLifecycle(true, false, "reservation-lifecycle")).toBe(false);
    expect(resolveDemoCaptureReservationLifecycle(true, true, undefined)).toBe(false);
  });
});
