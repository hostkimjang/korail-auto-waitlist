import { describe, expect, it } from "vitest";

import { createDemoWatch } from "../src/fixtures/demoData";
import { mapWatch } from "../src/api/watches";
import {
  mapLegacyPaymentRequiredWatch,
  mapPaymentRequiredWatch,
} from "../src/features/home/paymentRequiredViewModel";

describe("payment-required view model", () => {
  it("uses seat assignments from the same payment-required candidate as the displayed train", () => {
    const source = mapWatch({
      id: "payment-multi",
      provider: "korail",
      origin: "서울",
      destination: "부산",
      travel_date: "2026-08-08",
      time_from: "10:00:00",
      time_to: "13:30:00",
      train_numbers: ["101", "203"],
      seat_class: "standard",
      status: "payment_required",
      candidates: [{
        id: "payment-candidate",
        train_number: "101",
        train_type: "KTX-산천",
        departure_at: "2026-08-08T10:00:00+09:00",
        arrival_at: "2026-08-08T12:30:00+09:00",
        seat_class: "standard",
        priority: 1,
        state: "payment_required",
        latest_reservation_attempt: {
          outcome: "payment_required",
          started_at: "2026-08-08T01:00:00Z",
          finished_at: "2026-08-08T01:00:03Z",
          retryable: false,
          manual_check_required: false,
          retry_condition: null,
          confirmation_outcome: "confirmed_payment_required",
          confirmation_observed_at: "2026-08-08T01:00:04Z",
          reconciliation_attempt_count: 2,
          next_reconcile_at: "2026-08-08T01:01:04Z",
          reserved_seats: [{ car_number: "3", seat_number: "12A" }],
        },
      }, {
        id: "newer-other-candidate",
        train_number: "203",
        train_type: "KTX-청룡",
        departure_at: "2026-08-08T11:00:00+09:00",
        arrival_at: "2026-08-08T13:30:00+09:00",
        seat_class: "standard",
        priority: 2,
        state: "reservation_attempted",
        latest_reservation_attempt: {
          outcome: "reserved",
          started_at: "2026-08-08T01:05:00Z",
          finished_at: "2026-08-08T01:05:03Z",
          retryable: false,
          manual_check_required: false,
          retry_condition: null,
          confirmation_outcome: "confirmed_paid",
          confirmation_observed_at: "2026-08-08T01:05:04Z",
          reconciliation_attempt_count: 6,
          next_reconcile_at: null,
          reserved_seats: [{ car_number: "8", seat_number: "4D" }],
        },
      }],
    });

    expect(source.latestReservationAttemptCandidateId).toBe("newer-other-candidate");
    expect(mapPaymentRequiredWatch(source)).toMatchObject({
      train: "101",
      trainType: "KTX-산천",
      reservedSeats: [{ carNumber: "3", seatNumber: "12A" }],
      confirmationOutcome: "confirmed_payment_required",
      confirmationObservedAt: "2026-08-08T01:00:04Z",
      reconciliationAttemptCount: 2,
      nextReconcileAt: "2026-08-08T01:01:04Z",
    });
  });

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
      trainType: null,
      origin: "서울",
      destination: "부산",
      route: "서울 → 부산",
      departure: "12:00",
      arrival: "14:30",
      date: "8월 8일 (토)",
      seatClassLabel: "일반실",
      reservedSeats: [],
      paymentDeadline: "2026-08-08T12:10:00+09:00",
      officialBookingUrl: "https://www.korail.com/ticket/search/general",
      confirmationOutcome: null,
      confirmationDiagnosticCode: null,
      confirmationObservedAt: null,
      reconciliationAttemptCount: 0,
      nextReconcileAt: null,
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
      confirmationOutcome: null,
      confirmationDiagnosticCode: null,
      confirmationObservedAt: null,
      reconciliationAttemptCount: 0,
      nextReconcileAt: null,
    });
  });
});
