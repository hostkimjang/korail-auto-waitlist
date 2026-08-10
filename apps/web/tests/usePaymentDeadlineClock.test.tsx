import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePaymentDeadlineClock } from "../src/hooks/usePaymentDeadlineClock";

const originalVisibilityDescriptor = Object.getOwnPropertyDescriptor(document, "visibilityState");

function setVisibility(value: "hidden" | "visible"): void {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePaymentDeadlineClock", () => {
  afterEach(() => {
    vi.useRealTimers();
    if (originalVisibilityDescriptor) {
      Object.defineProperty(document, "visibilityState", originalVisibilityDescriptor);
    }
  });

  it("stops ticking while hidden and catches up immediately after becoming visible", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-10T10:00:00Z"));
    setVisibility("visible");
    const { result } = renderHook(() => usePaymentDeadlineClock(["2026-08-10T10:10:00Z"]));
    const initial = result.current;

    act(() => setVisibility("hidden"));
    act(() => vi.advanceTimersByTime(5_000));
    expect(result.current).toBe(initial);

    act(() => setVisibility("visible"));
    expect(result.current).toBe(initial + 5_000);
  });
});
