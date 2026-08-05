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
      payment_deadline: "2026-08-08T12:10:00+09:00",
      updated_at: "2026-08-08T03:01:00Z",
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

  it("keeps the legacy loose snake_case snapshot contract behind an adapter", () => {
    const legacy: WatchSnapshot = {
      id: "legacy-lifecycle",
      status: "watching",
      payment_deadline: "2026-08-08T12:10:00+09:00",
      updated_at: "2026-08-08T03:01:00Z",
    };

    expect(mapLegacyWatchLifecycleSnapshot(legacy)).toMatchObject({
      id: "legacy-lifecycle",
      status: "watching",
      provider: "철도",
      route: "여정 정보 없음",
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      updatedAt: "2026-08-08T03:01:00Z",
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
