import { describe, expect, it } from "vitest";

import {
  persistedSeatRegistration,
  resolvedSeatRegistration,
} from "../src/features/new-wait/watchRegistrationHydration";

const train = {
  provider: "KORAIL",
  train_number: "KTX 033",
  departure_at: "2026-08-01T13:18:00+09:00",
};

function watch(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "watch-033-standard",
    provider: "KORAIL",
    status: "watching",
    reservationPolicy: "reserve_once_before_payment",
    candidates: [{
      train_number: "KTX 033",
      departure_at: "2026-08-01T04:18:00Z",
      seat_class: "standard",
      priority: 1,
    }],
    ...overrides,
  };
}

describe("persisted seat registration hydration", () => {
  it("hydrates an active DB watch by provider, train, departure instant, and seat class", () => {
    expect(persistedSeatRegistration([watch()], train, "standard")).toEqual({
      status: "active",
      watchId: "watch-033-standard",
      reservationPolicy: "reserve_once_before_payment",
    });
  });

  it("does not hydrate a near match or a terminal watch", () => {
    expect(persistedSeatRegistration([watch({
      candidates: [{
        train_number: "KTX 033",
        departure_at: "2026-08-01T04:18:00Z",
        seat_class: "first",
      }],
    })], train, "standard")).toBeNull();
    expect(persistedSeatRegistration([watch({ status: "expired" })], train, "standard")).toBeNull();
    expect(persistedSeatRegistration([watch({
      candidates: [{
        train_number: "KTX 033",
        departure_at: "not-a-time",
        seat_class: "standard",
      }],
    })], train, "standard")).toBeNull();
  });

  it("keeps local pending, cancelling, and error state ahead of hydrated state", () => {
    expect(resolvedSeatRegistration({ status: "pending" }, [watch()], train, "standard")).toEqual({ status: "pending" });
    expect(resolvedSeatRegistration(
      { status: "cancelling", watchId: "watch-local" },
      [watch()],
      train,
      "standard",
    )).toEqual({ status: "cancelling", watchId: "watch-local" });
    expect(resolvedSeatRegistration(
      { status: "error", message: "등록 실패" },
      [watch()],
      train,
      "standard",
    )).toEqual({ status: "error", message: "등록 실패" });
  });

  it("normalizes the API snake-case policy while restoring an existing registration", () => {
    expect(persistedSeatRegistration([
      watch({
        reservationPolicy: undefined,
        reservation_policy: "reserve_once_before_payment",
      }),
    ], train, "standard")).toEqual({
      status: "active",
      watchId: "watch-033-standard",
      reservationPolicy: "reserve_once_before_payment",
    });
    expect(persistedSeatRegistration([
      watch({ reservationPolicy: undefined, reservation_policy: "pay_automatically" }),
    ], train, "standard")).toMatchObject({ reservationPolicy: "notify_only" });
  });
});
