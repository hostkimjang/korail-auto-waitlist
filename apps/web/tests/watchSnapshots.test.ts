import { describe, expect, it } from "vitest";

import type { LatestReservationAttempt } from "../src/domain/reservationAttempt";
import type { WatchStatus } from "../src/domain/watch";
import {
  detectSeatAvailabilityLostTransitions,
  detectSeatFoundTransitions,
  detectWatchActionTransitions,
  hydrateCurrentWatchActionTransitions,
  reconcileWatchSnapshots,
  type WatchSnapshot,
} from "../src/features/app/watchSnapshots";
import type { WatchLifecycleSnapshot } from "../src/features/app/watchLifecycleSnapshot";

function attempt(
  values: Partial<LatestReservationAttempt>,
): LatestReservationAttempt {
  return {
    outcome: "pending",
    startedAt: "2026-08-03T12:09:45Z",
    finishedAt: null,
    retryable: false,
    manualCheckRequired: false,
    retryCondition: null,
    paymentHoldEndedAt: null,
    ...values,
  };
}

function watch(id: string, status: WatchStatus): WatchLifecycleSnapshot {
  return {
    id,
    status,
    provider: "KORAIL",
    train: `KTX ${id}`,
    route: "서울 → 부산",
    seatClassLabel: "일반실",
    date: "8월 3일 (월)",
    departure: "14:35",
    arrival: "15:39",
    latestReservationAttempt: null,
    paymentDeadline: null,
    reservationCandidateContexts: {},
    reservationPolicy: "notify_only",
    seatFoundObservation: status === "seat_found"
      ? {
        kind: "official_provider",
        source: "korail-pydoll-reservation",
        observedAt: "2026-07-31T12:00:00+09:00",
        observedLabel: "최근 확인 12:00",
      }
      : null,
    updatedAt: null,
  };
}

describe("watch snapshot reconciliation", () => {
  it("keeps every public transition detector compatible with legacy snapshots", () => {
    const watching: WatchSnapshot = {
      id: "legacy-transition",
      status: "watching",
      provider: "KORAIL",
      train: "KTX 085",
    };
    const seatFound: WatchSnapshot = {
      ...watching,
      status: "seat_found",
      seatFoundObservation: { observedAt: "2026-08-01T03:45:00Z" },
    };
    const reserving: WatchSnapshot = {
      ...watching,
      status: "reserving",
      updated_at: "2026-08-01T03:46:00Z",
    };

    expect(detectSeatFoundTransitions([watching], [seatFound]))
      .toMatchObject([{ id: "legacy-transition" }]);
    expect(detectSeatAvailabilityLostTransitions([seatFound], [watching]))
      .toMatchObject([{ id: "legacy-transition" }]);
    expect(detectWatchActionTransitions([seatFound], [reserving]))
      .toMatchObject([{ id: "legacy-transition", status: "reserving" }]);
  });

  it("ignores initial seat-found rows and reports only later watching-family transitions", () => {
    const initial = [watch("old", "seat_found"), watch("one", "watching"), watch("two", "scheduled")];
    expect(detectSeatFoundTransitions([], initial)).toEqual([]);

    const next = [watch("old", "seat_found"), watch("one", "seat_found"), watch("two", "seat_found")];
    expect(detectSeatFoundTransitions(initial, next).map((item) => item.id)).toEqual(["one", "two"]);
    expect(detectSeatFoundTransitions(next, next)).toEqual([]);
  });

  it("does not announce an automatic seat discovery before a reservation attempt is claimed", () => {
    const previous = [watch("auto", "watching")];
    const next: WatchLifecycleSnapshot[] = [{
      ...watch("auto", "seat_found"),
      reservationPolicy: "reserve_once_before_payment",
    }];

    expect(detectSeatFoundTransitions(previous, next)).toEqual([]);
  });

  it("preserves unchanged watch and array identities", () => {
    const previous = [watch("one", "watching"), watch("two", "watching")];
    const same = previous.map((item) => ({ ...item }));
    const unchanged = reconcileWatchSnapshots(previous, same);
    expect(unchanged).toBe(previous);

    const changed = reconcileWatchSnapshots(previous, [watch("one", "watching"), watch("two", "seat_found")]);
    expect(changed).not.toBe(previous);
    expect(changed[0]).toBe(previous[0]);
    expect(changed[1]).not.toBe(previous[1]);
  });

  it("reports an availability loss only on the actionable-to-unavailable edge", () => {
    const available: WatchLifecycleSnapshot = {
      ...watch("one", "seat_found"),
      seatFoundObservation: {
        kind: "official_provider",
        source: "korail-pydoll-reservation",
        observedAt: "2026-07-31T12:00:00+09:00",
        observedLabel: "최근 확인 12:00",
      },
    };
    const unavailable = watch("one", "watching");

    expect(detectSeatAvailabilityLostTransitions([], [available])).toEqual([]);
    expect(detectSeatAvailabilityLostTransitions([available], [unavailable])).toMatchObject([{
      id: "one",
      train: "KTX one",
      seatClassLabel: "일반실",
      date: "8월 3일 (월)",
      departure: "14:35",
      arrival: "15:39",
    }]);
    expect(detectSeatAvailabilityLostTransitions(
      [available],
      [{ ...unavailable, status: "reserving" }],
    )).toEqual([]);
    expect(detectSeatAvailabilityLostTransitions([unavailable], [unavailable])).toEqual([]);
  });

  it("reports only later reservation and action-required status edges", () => {
    const previous = [watch("reserve", "seat_found"), watch("pay", "reserving"), watch("auth", "watching")];
    const next = [watch("reserve", "reserving"), watch("pay", "payment_required"), watch("auth", "auth_required")];

    expect(detectWatchActionTransitions([], next)).toEqual([]);
    expect(detectWatchActionTransitions(previous, next).map((item) => item.status))
      .toEqual(["reserving", "payment_required", "auth_required"]);
    expect(detectWatchActionTransitions(next, next)).toEqual([]);
  });

  it("hydrates only current actionable reservation states and keeps seat-found as baseline", () => {
    const reserving = {
      ...watch("reserve", "reserving"),
      latestReservationAttempt: attempt({ startedAt: "2026-08-03T12:09:45Z" }),
    };
    const payment = {
      ...watch("pay", "payment_required"),
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
      }),
    };
    const auth = {
      ...watch("auth", "auth_required"),
      updatedAt: "2026-08-03T12:10:00Z",
    };

    expect(hydrateCurrentWatchActionTransitions([
      watch("watching", "watching"),
      watch("found", "seat_found"),
      reserving,
      payment,
      auth,
    ])).toMatchObject([
      { id: "reserve", status: "reserving", revision: "reserving:2026-08-03T12:09:45Z" },
      { id: "pay", status: "payment_required", revision: "payment_required:2026-08-03T12:09:48Z" },
      { id: "auth", status: "auth_required", revision: "auth_required:2026-08-03T12:10:00Z" },
    ]);
  });

  it("uses the actual lifecycle stage timestamp instead of an older seat observation", () => {
    const previous = [watch("reserve-time", "seat_found")];
    const reserving = {
      ...watch("reserve-time", "reserving"),
      updatedAt: "2026-08-03T12:09:46Z",
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
      }),
    };

    expect(detectWatchActionTransitions(previous, [reserving]))
      .toMatchObject([{ status: "reserving", revisionAt: "2026-08-03T12:09:45Z" }]);
  });

  it("does not infer a reservation result from a generic return to monitoring", () => {
    const previous = [watch("resume", "reserving")];
    const next = [watch("resume", "watching")];

    expect(detectWatchActionTransitions(previous, next)).toEqual([]);
  });

  it("reports authentication recovery when an auth-required watch resumes monitoring", () => {
    const previous = [watch("resume", "auth_required")];
    const recovered = {
      ...watch("resume", "watching"),
      updatedAt: "2026-08-03T12:12:00Z",
      latestReservationAttempt: attempt({
        startedAt: "2026-08-03T11:00:00Z",
        finishedAt: "2026-08-03T11:00:03Z",
      }),
    };

    expect(detectWatchActionTransitions(previous, [watch("resume", "scheduled")]))
      .toMatchObject([{ id: "resume", status: "authentication_recovered" }]);
    expect(detectWatchActionTransitions(previous, [recovered]))
      .toMatchObject([{
        id: "resume",
        status: "authentication_recovered",
        revisionAt: "2026-08-03T12:12:00Z",
      }]);
  });

  it("reports payment-hold end only with the server-confirmed hold-ended marker", () => {
    const previous = [watch("payment", "payment_required")];
    const resumedWithoutEvidence = [watch("payment", "watching")];
    const holdEndedAt = "2026-08-03T12:20:01Z";
    const confirmedHoldEnded: WatchLifecycleSnapshot = {
      ...watch("payment", "watching"),
      latestReservationAttempt: attempt({
        outcome: "payment_required",
        startedAt: "2026-08-03T12:09:45Z",
        finishedAt: "2026-08-03T12:09:48Z",
        paymentHoldEndedAt: holdEndedAt,
        paymentHoldEndReason: "confirmed_payment_deadline_elapsed",
      }),
    };
    const resumed = [confirmedHoldEnded];
    const oneOffExpired: WatchLifecycleSnapshot[] = [{
      ...confirmedHoldEnded,
      status: "expired",
    }];

    expect(detectWatchActionTransitions(previous, resumedWithoutEvidence)).toEqual([]);
    expect(detectWatchActionTransitions(previous, resumed)).toMatchObject([{
      id: "payment",
      status: "payment_hold_ended",
      automaticReservationRetry: true,
      paymentHoldEndReason: "confirmed_payment_deadline_elapsed",
      revisionAt: holdEndedAt,
    }]);
    expect(detectWatchActionTransitions(previous, oneOffExpired)).toMatchObject([{
      id: "payment",
      status: "payment_hold_ended",
      automaticReservationRetry: false,
      revisionAt: holdEndedAt,
    }]);
  });
});
