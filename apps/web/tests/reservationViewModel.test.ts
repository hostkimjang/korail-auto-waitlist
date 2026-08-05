import { describe, expect, it } from "vitest";

import { createDemoWatch } from "../src/fixtures/demoData";
import {
  mapLegacyReservationWatch,
  mapReservationWatch,
} from "../src/features/reservations/reservationViewModel";

describe("reservation view model", () => {
  it("maps a normalized watch into the camelCase reservation contract", () => {
    const source = {
      ...createDemoWatch({
        id: "reservation-1",
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
      official_booking_url: "https://attacker.invalid/pay",
    };

    const mapped = mapReservationWatch(source);

    expect(mapped).toEqual({
      id: "reservation-1",
      status: "payment_required",
      statusLabel: "결제 필요",
      route: "서울 → 부산",
      train: "KTX 101",
      date: "8월 8일 (토)",
      departure: "12:00",
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      officialBookingUrl: "https://www.korail.com/ticket/search/general",
    });
    expect(mapped).not.toHaveProperty("payment_deadline");
    expect(mapped).not.toHaveProperty("official_booking_url");
  });

  it("adapts optional snake_case fields and fails closed for an unknown legacy status", () => {
    const mapped = mapLegacyReservationWatch({
      id: "legacy-1",
      status: "future_status",
      statusLabel: "알 수 없는 상태",
      route: "수서 → 부산",
      train: "SRT 327",
      date: "8월 1일 (토)",
      departure: "10:42",
    });

    expect(mapped).toMatchObject({
      id: "legacy-1",
      status: "unknown",
      paymentDeadline: null,
      officialBookingUrl: null,
    });
  });

  it("keeps allowlisted legacy official URLs and rejects unsafe candidates", () => {
    const legacyWatch = {
      id: "legacy-official",
      status: "scheduled",
      statusLabel: "대기 등록됨",
      route: "서울 → 부산",
      train: "KTX 085",
      date: "8월 1일 (토)",
      departure: "14:11",
    };

    expect(mapLegacyReservationWatch({
      ...legacyWatch,
      official_booking_url: "https://www.letskorail.com/ticket/search/general",
    }).officialBookingUrl).toBe("https://www.letskorail.com/ticket/search/general");
    expect(mapLegacyReservationWatch({
      ...legacyWatch,
      official_booking_url: "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do",
    }).officialBookingUrl).toBe("https://etk.srail.kr/hpg/hra/01/selectScheduleList.do");

    for (const unsafeUrl of [
      "https://attacker.example/ticket",
      "https://example.invalid/ticket",
      "javascript:alert(1)",
      "not-a-url",
      "http://www.letskorail.com/ticket/search/general",
    ]) {
      expect(mapLegacyReservationWatch({
        ...legacyWatch,
        official_booking_url: unsafeUrl,
      }).officialBookingUrl).toBeNull();
    }
  });
});
