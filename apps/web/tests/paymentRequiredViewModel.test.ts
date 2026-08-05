import { describe, expect, it } from "vitest";

import { createDemoWatch } from "../src/fixtures/demoData";
import {
  mapLegacyPaymentRequiredWatch,
  mapPaymentRequiredWatch,
} from "../src/features/home/paymentRequiredViewModel";

describe("payment-required view model", () => {
  it("maps the normalized watch into a camelCase Home contract", () => {
    const source = {
      ...createDemoWatch({
        id: "payment-1",
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
    };

    const mapped = mapPaymentRequiredWatch(source);

    expect(mapped).toEqual({
      id: "payment-1",
      provider: "KORAIL",
      train: "KTX 101",
      origin: "서울",
      destination: "부산",
      route: "서울 → 부산",
      departure: "12:00",
      arrival: "14:30",
      date: "8월 8일 (토)",
      seatClassLabel: "일반실",
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      officialBookingUrl: "https://www.korail.com/ticket/search/general",
    });
    expect(mapped).not.toHaveProperty("payment_deadline");
    expect(mapped).not.toHaveProperty("official_booking_url");
  });

  it("preserves nulls and the stable legacy identity fallback", () => {
    const mapped = mapLegacyPaymentRequiredWatch({
      provider: "SRT",
      train: "SRT 327",
      route: "수서 → 부산",
      departure: "10:42",
      arrival: "13:14",
      date: "8월 1일 (토)",
      payment_deadline: null,
      official_booking_url: null,
    });

    expect(mapped).toMatchObject({
      id: "SRT-SRT 327-8월 1일 (토)-10:42",
      origin: null,
      destination: null,
      seatClassLabel: null,
      paymentDeadline: null,
      officialBookingUrl: null,
    });
  });
});
