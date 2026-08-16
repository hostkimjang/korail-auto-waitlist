import { describe, expect, it } from "vitest";

import { createDemoWatch } from "../src/fixtures/demoData";
import type { WatchSnapshot } from "../src/features/app/watchSnapshots";
import {
  mapLegacyWatchLifecycleSnapshot,
  mapWatchLifecycleSnapshot,
} from "../src/features/app/watchLifecycleSnapshot";

describe("watch lifecycle snapshot", () => {
  it("projects the normalized watch into one typed camelCase lifecycle contract", () => {
    const source = {
      ...createDemoWatch({
        id: "lifecycle-one",
        provider: "KORAIL",
        train: "KTX 101",
        route: "서울 → 부산",
        origin: "서울",
        destination: "부산",
        departure: "12:00",
        arrival: "14:30",
        date: "8월 8일 (토)",
        travelDate: "2026-08-08",
        status: "payment_required",
        statusLabel: "결제 필요",
        seatClass: "standard",
        seatClassLabel: "일반실",
        seatEvidenceLabel: "일반실 · 임시 예약",
        officialBookingUrl: "https://www.korail.com/ticket/search/general",
      }),
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      payment_deadline: "2026-08-08T13:10:00+09:00",
      updatedAt: "2026-08-08T03:01:00Z",
      updated_at: "2026-08-08T04:01:00Z",
    };

    const snapshot = mapWatchLifecycleSnapshot(source);

    expect(snapshot).toMatchObject({
      id: "lifecycle-one",
      status: "payment_required",
      provider: "KORAIL",
      route: "서울 → 부산",
      train: "KTX 101",
      seatClassLabel: "일반실",
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      updatedAt: "2026-08-08T03:01:00Z",
      reservationPolicy: source.reservationPolicy,
      latestReservationAttempt: source.latestReservationAttempt,
      seatFoundObservation: source.seatFoundObservation,
      reservationCandidateContexts: source.reservationCandidateContexts,
    });
    expect(snapshot).not.toHaveProperty("payment_deadline");
    expect(snapshot).not.toHaveProperty("updated_at");
  });

  it("preserves normalized null lifecycle evidence", () => {
    const snapshot = mapWatchLifecycleSnapshot(createDemoWatch({
      id: "lifecycle-null",
      provider: "KORAIL",
      train: "KTX 101",
      route: "서울 → 부산",
      origin: "서울",
      destination: "부산",
      departure: "12:00",
      arrival: "14:30",
      date: "8월 8일 (토)",
      travelDate: "2026-08-08",
      status: "watching",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 매진",
      officialBookingUrl: "https://www.korail.com/ticket/search/general",
    }));

    expect(snapshot).toMatchObject({
      latestReservationAttempt: null,
      paymentDeadline: null,
      seatFoundObservation: null,
      updatedAt: null,
    });
  });

  it("preserves canonical reservation retry policy for terminal recovery", () => {
    const source = {
      ...createDemoWatch({
        id: "lifecycle-terminal",
        provider: "KORAIL",
        train: "KTX 053",
        route: "서울 → 대전",
        origin: "서울",
        destination: "대전",
        departure: "17:58",
        arrival: "18:57",
        date: "8월 13일 (목)",
        travelDate: "2026-08-13",
        status: "watching",
        statusLabel: "감시 중",
        seatClass: "standard",
        seatClassLabel: "일반실",
        seatEvidenceLabel: "일반실 · 매진",
        officialBookingUrl: "https://www.korail.com/ticket/search/general",
      }),
      latestReservationAttempt: {
        outcome: "not_available" as const,
        startedAt: "2026-08-12T13:56:21Z",
        finishedAt: "2026-08-12T13:56:23Z",
        retryable: true,
        manualCheckRequired: false,
        retryCondition: "new_availability_episode" as const,
        paymentHoldEndedAt: null,
      },
    };

    expect(mapWatchLifecycleSnapshot(source).latestReservationAttempt).toMatchObject({
      outcome: "not_available",
      retryable: true,
      retryCondition: "new_availability_episode",
    });
  });

  it("keeps the legacy loose snake_case snapshot contract behind an adapter", () => {
    const legacy: WatchSnapshot = {
      id: "legacy-lifecycle",
      status: "watching",
      latestReservationAttempt: {
        outcome: "not_available",
        retryable: true,
        confirmationOutcome: "inconclusive",
        confirmationDiagnosticCode: "future_diagnostic_code",
      },
      payment_deadline: "2026-08-08T12:10:00+09:00",
      updated_at: "2026-08-08T03:01:00Z",
    };

    expect(mapLegacyWatchLifecycleSnapshot(legacy)).toMatchObject({
      id: "legacy-lifecycle",
      status: "watching",
      provider: "철도",
      route: "여정 정보 없음",
      latestReservationAttempt: {
        outcome: "not_available",
        retryable: true,
        confirmationOutcome: "inconclusive",
        confirmationDiagnosticCode: "unspecified",
      },
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      updatedAt: "2026-08-08T03:01:00Z",
    });
  });

  it("preserves only a compatible legacy confirmed-absence retry fence", () => {
    const baseAttempt = {
      outcome: "unknown",
      manualCheckRequired: false,
      confirmationOutcome: "not_found",
      reconciliationResolution: "confirmed_absent",
    };
    const base: WatchSnapshot = {
      id: "legacy-confirmed-absent",
      status: "watching",
      latestReservationAttempt: baseAttempt,
    };

    expect(mapLegacyWatchLifecycleSnapshot({
      ...base,
      latestReservationAttempt: {
        ...baseAttempt,
        automaticReservationRetryFenceReason: "confirmed_absent_recovery_consumed",
      },
    }).latestReservationAttempt).toMatchObject({
      reconciliationResolution: "confirmed_absent",
      automaticReservationRetryFenceReason: "confirmed_absent_recovery_consumed",
    });
    expect(mapLegacyWatchLifecycleSnapshot({
      ...base,
      latestReservationAttempt: {
        ...baseAttempt,
        automaticReservationRetryFenceReason: "future_fence_reason",
      },
    }).latestReservationAttempt).toMatchObject({
      reconciliationResolution: "confirmed_absent",
      automaticReservationRetryFenceReason: null,
    });
  });

  it("trims only legacy candidate text while preserving provider and time fallback semantics", () => {
    const mapped = mapLegacyWatchLifecycleSnapshot({
      id: "legacy-text",
      status: "watching",
      provider: "",
      departure: "",
      reservationCandidateContexts: {
        candidate: {
          train: "  KTX 240  ",
          seatClassLabel: "   ",
        },
      },
    });

    expect(mapped.provider).toBe("");
    expect(mapped.departure).toBe("");
    expect(mapped.arrival).toBe("--:--");
    expect(mapped.reservationCandidateContexts).toEqual({
      candidate: { train: "KTX 240" },
    });
  });
});
