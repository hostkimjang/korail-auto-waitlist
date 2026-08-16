import { describe, expect, it } from "vitest";

import { korailResultsFixture } from "../fixtures/korail-results.fixture";
import { parseKorailSnapshot, statusFromSeatBox } from "../src/parser";

describe("parseKorailSnapshot", () => {
  it("normalizes the rendered result fixture into the bridge payload", () => {
    expect(parseKorailSnapshot(korailResultsFixture)).toEqual({
      origin: "서울",
      destination: "부산",
      travel_date: "2026-08-03",
      passenger_count: 1,
      trains: [
        {
          train_number: "KTX101",
          departure_at: "2026-08-03T19:35:00+09:00",
          standard: "available",
          first: "limited",
        },
        {
          train_number: "KTX123",
          departure_at: "2026-08-03T20:10:00+09:00",
          standard: "sold_out",
          first: "waitlist_available",
        },
      ],
    });
  });

  it("fails closed for duplicate train numbers or a passenger count other than one", () => {
    expect(
      parseKorailSnapshot({
        ...korailResultsFixture,
        rows: [korailResultsFixture.rows[0]!, korailResultsFixture.rows[0]!],
      }),
    ).toBeNull();
    expect(parseKorailSnapshot({ ...korailResultsFixture, passengerCount: 2 })).toBeNull();
  });
});

describe("statusFromSeatBox", () => {
  it("preserves each explicit official-page seat status", () => {
    expect(statusFromSeatBox("특실 매진", ["sold_out"])).toBe("sold_out");
    expect(statusFromSeatBox("특실 매진임박", ["sold_out_soon"])).toBe("limited");
    expect(statusFromSeatBox("특실 예약대기", [])).toBe("waitlist_available");
    expect(statusFromSeatBox("일반실 입석+좌석", [])).toBe("standing_plus_seat");
    expect(statusFromSeatBox("일반실 입석 + 예매", [])).toBe("standing_plus_seat");
    expect(statusFromSeatBox("일반실 입석", [])).toBe("standing_only");
    expect(statusFromSeatBox("일반실 입석 예매", [])).toBe("standing_only");
    expect(statusFromSeatBox("좌석 없음", [])).toBe("not_offered");
    expect(statusFromSeatBox("-", [])).toBe("not_offered");
    expect(statusFromSeatBox("일반실 59,800원", [])).toBe("available");
    expect(statusFromSeatBox("일반실 예약", [])).toBe("available");
    expect(statusFromSeatBox("특실 예매 가능", [])).toBe("available");
  });

  it("uses actionable waitlist text before a generic sold-out marker", () => {
    expect(statusFromSeatBox("특실 매진 예약대기", ["sold_out"])).toBe("waitlist_available");
  });

  it("uses rendered standing text before a generic sold-out class", () => {
    expect(statusFromSeatBox("일반실 입석+좌석", ["sold_out"])).toBe("standing_plus_seat");
    expect(statusFromSeatBox("일반실 입석", ["sold_out"])).toBe("standing_only");
    expect(statusFromSeatBox("일반실 입석 예매", ["sold_out"])).toBe("standing_only");
  });

  it("fails closed for an unrecognized non-empty seat box", () => {
    expect(statusFromSeatBox("상태를 확인하세요", [])).toBeNull();
    expect(statusFromSeatBox("예매 불가", [])).toBeNull();
    expect(statusFromSeatBox("입석 없음", [])).toBe("not_offered");
    expect(statusFromSeatBox("입석 예매 불가", [])).toBeNull();
  });
});
