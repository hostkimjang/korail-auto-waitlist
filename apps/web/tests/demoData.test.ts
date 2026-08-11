import { describe, expect, it } from "vitest";

import {
  mapWatch,
  type MappedWatch,
  type WatchReadModel,
} from "../src/api/watches";
import {
  createDemoWatch,
  demoTimetablesForForm,
  initialWatches,
} from "../src/fixtures/demoData";

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
        train_type: "SRT",
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
      trainNumber: "SRT 327",
      trainType: "SRT",
      departureAt: "2026-08-04T10:42:00+09:00",
      arrivalAt: "2026-08-04T13:14:00+09:00",
      seatClass: "standard",
      train_number: "SRT 327",
      train_type: "SRT",
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

  it("sorts reverse candidate input like the production projection without duplicating arrays", () => {
    const candidateInputs = [{
      id: "candidate-later",
      train_number: "KTX 002",
      departure_at: "2026-08-08T11:00:00+09:00",
      arrival_at: "2026-08-08T13:30:00+09:00",
      seat_class: "first" as const,
      priority: 2,
    }, {
      id: "candidate-first",
      train_number: "KTX 001",
      departure_at: "2026-08-08T10:00:00+09:00",
      arrival_at: "2026-08-08T12:30:00+09:00",
      seat_class: "standard" as const,
      priority: 1,
    }];
    const demo = createDemoWatch({
      id: "demo-sorted",
      provider: "KORAIL",
      train: "KTX 001",
      route: "서울 → 부산",
      origin: "서울",
      destination: "부산",
      departure: "10:00",
      arrival: "12:30",
      date: "8월 8일 (토)",
      travelDate: "2026-08-08",
      status: "watching",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 데모 좌석 상태",
      candidates: candidateInputs,
    });
    const production = mapWatch({
      id: "production-sorted",
      provider: "korail",
      origin: "서울",
      destination: "부산",
      travel_date: "2026-08-08",
      time_from: "10:00:00",
      time_to: "13:30:00",
      train_numbers: ["KTX 001", "KTX 002"],
      seat_class: "standard",
      status: "watching",
      candidates: candidateInputs,
    });

    expect(demo.candidates.map((candidate) => candidate.id)).toEqual([
      "candidate-first",
      "candidate-later",
    ]);
    expect(demo.candidates.map((candidate) => candidate.id)).toEqual(
      production.candidates.map((candidate) => candidate.id),
    );
    expect(demo.candidates[0]?.trainNumber).toBe("KTX 001");
    expect(Object.keys(demo.reservationCandidateContexts)).toEqual([
      "candidate-first",
      "candidate-later",
    ]);
    const canonicalView: WatchReadModel = demo;
    const compatibilityView: MappedWatch = demo;
    expect(canonicalView.candidates).toBe(compatibilityView.candidates);
  });

  it("maps generated demo timetables through the canonical API boundary", () => {
    const items = demoTimetablesForForm({
      providers: ["KORAIL"],
      origin: "서울",
      destination: "부산",
      date: "2026-08-04",
      time: "12:00",
      timeEnd: "12:00",
    });
    const first = items.at(0);
    expect(first).toBeDefined();
    if (!first) throw new Error("생성된 데모 시간표가 필요합니다.");

    expect(first).toMatchObject({
      provider: "KORAIL",
      origin: "서울",
      destination: "부산",
      timetable_source: "mock",
      official_search_url: null,
    });
    expect(first.seat_classes).toHaveLength(2);
    expect(first.seat_classes.every((seat) => (
      seat.provenance.kind === "mock"
      && seat.registration_evidence_id === null
      && seat.registration_evidence_error === null
    ))).toBe(true);
  });
});
