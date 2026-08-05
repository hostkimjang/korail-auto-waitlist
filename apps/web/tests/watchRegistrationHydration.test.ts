import { describe, expect, it } from "vitest";

import {
  persistedSeatRegistration,
  persistedWatchSeatRegistration,
  resolvedSeatRegistration,
} from "../src/features/new-wait/watchRegistrationHydration";
import { createDemoWatch } from "../src/fixtures/demoData";

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
  it("hydrates typed read models from canonical candidate fields", () => {
    const projected = createDemoWatch({
      id: "canonical-watch",
      provider: "KORAIL",
      train: "KTX 033",
      route: "서울 → 부산",
      origin: "서울",
      destination: "부산",
      departure: "13:18",
      arrival: "15:59",
      date: "8월 1일 (토)",
      travelDate: "2026-08-01",
      status: "watching",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 확인 불가",
      reservationPolicy: "reserve_once_before_payment",
      candidates: [{
        train_number: "KTX 033",
        departure_at: "2026-08-01T04:18:00Z",
        arrival_at: "2026-08-01T06:59:00Z",
        seat_class: "standard",
        priority: 1,
      }],
    });
    const candidate = projected.candidates[0];
    if (!candidate) throw new Error("canonical candidate fixture가 필요합니다.");
    const canonicalWatch = {
      ...projected,
      candidates: [{
        ...candidate,
        train_number: "LEGACY ALIAS MUST NOT WIN",
        departure_at: "2026-08-01T05:18:00Z",
        seat_class: "first" as const,
      }],
    };

    expect(persistedWatchSeatRegistration([canonicalWatch], train, "standard")).toEqual({
      status: "active",
      watchId: "canonical-watch",
      reservationPolicy: "reserve_once_before_payment",
    });
  });

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
