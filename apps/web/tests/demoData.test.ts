import { describe, expect, it } from "vitest";

import type { MappedWatch } from "../src/api/watches";
import { createDemoWatch, initialWatches } from "../src/fixtures/demoData";

function canonicalWatch(value: MappedWatch): MappedWatch {
  return value;
}

describe("demo watch fixtures", () => {
  it("keeps the initial demo collection on the canonical mapped-watch contract", () => {
    const firstWatch = initialWatches.at(0);
    expect(firstWatch).toBeDefined();
    if (!firstWatch) throw new Error("초기 데모 작업 fixture가 필요합니다.");
    const watch = canonicalWatch(firstWatch);

    expect(watch).toMatchObject({
      id: "watch-ktx-483",
      provider: "KORAIL",
      status: "watching",
      reservation_policy: "notify_only",
      reservationPolicy: "notify_only",
      origin: "용산",
      destination: "광주송정",
      travelDate: "2026-07-31",
      candidates: [],
    });
    expect(watch.latestReservationAttempt).toBeNull();
    expect(watch.seatFoundObservation).toBeNull();
  });

  it("builds wizard demo results with synchronized policy, URL, and candidate fields", () => {
    const watch = createDemoWatch({
      id: "watch-demo",
      provider: "SRT",
      train: "SRT 327",
      route: "수서 → 부산",
      origin: "수서",
      destination: "부산",
      departure: "10:42",
      arrival: "13:14",
      date: "8월 4일 (화)",
      travelDate: "2026-08-04",
      status: "watching",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 데모 좌석 상태",
      officialBookingUrl: "https://etk.srail.kr",
      reservationPolicy: "reserve_once_before_payment",
      candidates: [{
        train_number: "SRT 327",
        departure_at: "2026-08-04T10:42:00+09:00",
        arrival_at: "2026-08-04T13:14:00+09:00",
        seat_class: "standard",
        priority: 1,
      }],
    });

    expect(canonicalWatch(watch)).toMatchObject({
      reservation_policy: "reserve_once_before_payment",
      reservationPolicy: "reserve_once_before_payment",
      official_booking_url: "https://etk.srail.kr",
      officialBookingUrl: "https://etk.srail.kr",
    });
    expect(watch.candidates).toEqual([{
      id: "watch-demo:candidate:1",
      train_number: "SRT 327",
      departure_at: "2026-08-04T10:42:00+09:00",
      arrival_at: "2026-08-04T13:14:00+09:00",
      seat_class: "standard",
      priority: 1,
    }]);
    expect(watch.reservationCandidateContexts["watch-demo:candidate:1"]).toEqual({
      train: "SRT 327",
      seatClassLabel: "일반실",
      date: "8월 4일 (화)",
      departure: "10:42",
      arrival: "13:14",
    });
  });
});
