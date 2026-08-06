import { describe, expect, it } from "vitest";

import { createDemoWatch, initialWatches } from "../src/fixtures/demoData";
import {
  advanceDemoCaptureLifecycle,
  buildDemoCaptureWatchStage,
} from "../src/features/app/useDemoCaptureLifecycle";

function automaticDemoWatch() {
  return createDemoWatch({
    id: "readme-demo-watch",
    provider: "KORAIL",
    train: "KTX 033",
    route: "서울 → 부산",
    origin: "서울",
    destination: "부산",
    departure: "13:18",
    arrival: "15:59",
    date: "7월 31일 (금)",
    travelDate: "2026-07-31",
    status: "watching",
    statusLabel: "감시 중",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 데모 좌석 상태",
    officialBookingUrl: "https://www.korail.com/ticket/search",
    reservationPolicy: "reserve_once_before_payment",
    candidates: [{
      train_number: "KTX 033",
      departure_at: "2026-07-31T13:18:00+09:00",
      arrival_at: "2026-07-31T15:59:00+09:00",
      seat_class: "standard",
      priority: 1,
    }],
  });
}

describe("README demo capture lifecycle", () => {
  it("keeps one mock journey coherent through seat discovery and reservation progress", () => {
    const base = automaticDemoWatch();
    const found = advanceDemoCaptureLifecycle([base, ...initialWatches], "seat_found");
    const seatFound = found.watches[0];
    expect(seatFound).toMatchObject({
      id: base.id,
      status: "seat_found",
      reservationPolicy: "reserve_once_before_payment",
      paymentDeadline: null,
      seatFoundObservation: {
        kind: "mock",
        source: "정식 앱 UX 벤치마크 데모",
      },
    });
    expect(found.notifications).toEqual([]);

    const reserving = advanceDemoCaptureLifecycle(found.watches, "reserving");
    expect(reserving.watches[0]).toMatchObject({
      id: base.id,
      train: base.train,
      route: base.route,
      status: "reserving",
      latestReservationAttempt: { outcome: "pending" },
      seatFoundObservation: null,
    });
    expect(reserving.notifications.map((notice) => notice.title)).toEqual([
      "예매를 진행하고 있습니다",
    ]);

    const payment = advanceDemoCaptureLifecycle(reserving.watches, "payment_required");
    expect(payment.watches[0]).toMatchObject({
      id: base.id,
      train: base.train,
      route: base.route,
      status: "payment_required",
      paymentDeadline: null,
      latestReservationAttempt: { outcome: "payment_required" },
    });
    expect(payment.notifications.map((notice) => notice.title)).toEqual([
      "결제 직전까지 예매되었습니다",
    ]);
  });

  it("rejects skipped stages and notify-only watches", () => {
    const base = automaticDemoWatch();
    expect(() => advanceDemoCaptureLifecycle([base], "reserving")).toThrow(
      "seat_found 상태의 자동 예매 대기 1건",
    );
    expect(() => buildDemoCaptureWatchStage(initialWatches[0]!, "seat_found")).toThrow(
      "자동 예매를 선택한 데모 대기",
    );
  });
});
