import { describe, expect, expectTypeOf, it } from "vitest";

import { mapWatch as mapWatchFromCompatibilityApi } from "../src/api/watches";
import type {
  MappedWatch,
  ProjectedWatch,
  WatchReadModel,
} from "../src/api/watches";
import { mapWatch as mapWatchFromProjection } from "../src/api/watchProjection";

const legacySnakeOnlyFixture: MappedWatch = {
  id: "legacy-watch",
  provider: "KORAIL",
  status: "watching",
  candidates: [],
  payment_deadline: null,
  created_at: null,
  updated_at: null,
  official_booking_url: null,
  reservation_policy: "notify_only",
  train: "KTX 001",
  route: "서울 → 부산",
  departure: "09:00",
  arrival: "11:30",
  date: "8월 8일 (토)",
  statusLabel: "감시 중",
  seatClass: "standard",
  seatClassLabel: "일반실",
  seatEvidenceLabel: "일반실 · 확인 불가",
  registrationEvidenceLabel: "등록 근거 없음",
  activityLabel: "확인 전",
  lastCheckedAt: null,
  lastCheckedLabel: "최근 확인 기록 없음",
  origin: "서울",
  destination: "부산",
  travelDate: "2026-08-08",
  officialBookingUrl: null,
  operational: null,
  latestReservationAttempt: null,
  seatFoundObservation: null,
  reservationCandidateContexts: {},
  reservationPolicy: "notify_only",
  seatObservationMode: "balanced",
  focusedObservationIntervalSeconds: 25,
  nextCheckAt: null,
};

describe("watch projection compatibility exports", () => {
  it("keeps the legacy API mapper export as the exact projection function", () => {
    expect(mapWatchFromCompatibilityApi).toBe(mapWatchFromProjection);
  });

  it("keeps snake-only legacy object literals assignable to the public mapped type", () => {
    expect(legacySnakeOnlyFixture).not.toHaveProperty("paymentDeadline");
    expect(legacySnakeOnlyFixture).not.toHaveProperty("createdAt");
    expect(legacySnakeOnlyFixture).not.toHaveProperty("updatedAt");
  });

  it("types mapper-produced watches as both canonical and legacy-compatible", () => {
    expectTypeOf<ProjectedWatch>().toExtend<WatchReadModel>();
    expectTypeOf<ProjectedWatch>().toExtend<MappedWatch>();
  });
});
