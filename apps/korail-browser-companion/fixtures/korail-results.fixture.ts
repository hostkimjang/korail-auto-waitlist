import type { ParserInput } from "../src/parser";

export const korailResultsFixture: ParserInput = {
  travelDate: "2026-08-03",
  passengerCount: 1,
  rows: [
    {
      trainNumber: "KTX 101",
      origin: "서울",
      destination: "부산",
      departureTime: "19:35",
      standardText: "일반실 59,800원",
      firstText: "특실 매진임박",
      standardClassNames: ["price_box"],
      firstClassNames: ["price_box", "sold_out_soon"],
    },
    {
      trainNumber: "KTX 123",
      origin: "서울",
      destination: "부산",
      departureTime: "20:10",
      standardText: "일반실 매진",
      firstText: "특실 예약대기",
      standardClassNames: ["price_box", "sold_out"],
      firstClassNames: ["price_box"],
    },
  ],
};
