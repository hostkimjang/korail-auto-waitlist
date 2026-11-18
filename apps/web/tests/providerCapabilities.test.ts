import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  automaticReservationAvailableFor,
  mapAutomaticReservationAvailability,
  type AutomaticReservationAvailability,
} from "../src/api/providerCapabilities";
import { useAutomaticReservationAvailability } from "../src/features/app/useAutomaticReservationAvailability";

function capability(provider: string, reservationOnce: unknown, overrides: Record<string, unknown> = {}) {
  return {
    provider,
    timetable: true,
    official_booking_link: true,
    official_waitlist_link: false,
    seat_monitoring: true,
    reservation_once: reservationOnce,
    experimental: false,
    enabled: true,
    note: null,
    ...overrides,
  };
}

describe("automatic reservation availability", () => {
  it("reads only the official provider entries of the capability list", () => {
    expect(mapAutomaticReservationAvailability([
      capability("korail", true),
      capability("srt", false),
      capability("mock", true),
      capability("korail", false, { timetable: false, experimental: true }),
      capability("srt", false, { timetable: false, experimental: true }),
    ])).toEqual({ KORAIL: true, SRT: false });
  });

  it.each([
    ["not a list", { korail: true }],
    ["missing provider", [capability("korail", true)]],
    ["duplicate provider", [capability("korail", true), capability("korail", true), capability("srt", true)]],
    ["non-boolean switch", [capability("korail", "true"), capability("srt", true)]],
  ])("rejects %s", (_name, payload) => {
    expect(() => mapAutomaticReservationAvailability(payload)).toThrow();
  });

  it("is unknown before the server answers and for providers without a switch", () => {
    expect(automaticReservationAvailableFor(null, "KORAIL")).toBeNull();
    expect(automaticReservationAvailableFor({ KORAIL: false, SRT: true }, "KORAIL")).toBe(false);
    expect(automaticReservationAvailableFor({ KORAIL: false, SRT: true }, "MOCK")).toBeNull();
  });

  it("loads after sign-in, reloads on navigation and keeps the last answer on a failed reload", async () => {
    const answers: Array<AutomaticReservationAvailability | Error> = [
      { KORAIL: false, SRT: false },
      new Error("temporarily unavailable"),
      { KORAIL: true, SRT: true },
    ];
    const load = vi.fn(async () => {
      const next = answers.shift();
      if (next === undefined || next instanceof Error) throw next ?? new Error("unexpected load");
      return next;
    });
    const { result, rerender } = renderHook(
      ({ authenticated, view }) => useAutomaticReservationAvailability({
        authenticated,
        demo: false,
        revalidateKey: view,
        load,
      }),
      { initialProps: { authenticated: false, view: "home" } },
    );
    expect(result.current).toBeNull();
    expect(load).not.toHaveBeenCalled();

    rerender({ authenticated: true, view: "home" });
    await waitFor(() => expect(result.current).toEqual({ KORAIL: false, SRT: false }));
    rerender({ authenticated: true, view: "new" });
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    await act(async () => undefined);
    expect(result.current).toEqual({ KORAIL: false, SRT: false });
    rerender({ authenticated: true, view: "home" });
    await waitFor(() => expect(result.current).toEqual({ KORAIL: true, SRT: true }));
    rerender({ authenticated: false, view: "home" });
    expect(result.current).toBeNull();
  });

  it("lets the demo show automatic reservation without a server", () => {
    const load = vi.fn();
    const { result } = renderHook(() => useAutomaticReservationAvailability({
      authenticated: false,
      demo: true,
      load,
    }));
    expect(result.current).toEqual({ KORAIL: true, SRT: true });
    expect(load).not.toHaveBeenCalled();
  });
});
