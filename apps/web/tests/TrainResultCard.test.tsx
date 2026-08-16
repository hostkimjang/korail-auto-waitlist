import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { mapTimetable } from "../src/api/timetables";
import { TrainResultCard } from "../src/features/new-wait/TrainResultCard";
import { OfficialHandoff } from "../src/features/official-handoff/OfficialHandoff";

function train() {
  return mapTimetable({
    provider: "korail",
    train_number: "KTX 026",
    train_type: "KTX",
    origin: "대전",
    destination: "서울",
    departure_at: "2026-08-04T12:00:00+09:00",
    arrival_at: "2026-08-04T13:04:00+09:00",
    adult_fare: 23_700,
    fare_currency: "KRW",
    timetable_source: "official_provider",
    timetable_retrieved_at: "2026-08-04T02:55:00Z",
    official_booking_url: "https://www.korail.com/ticket/search",
    official_search_url: null,
    seat_classes: [
      {
        seat_class: "standard",
        status: "sold_out",
        fare: 23_700,
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-04T02:55:00Z",
        },
        registration_evidence_id: "evidence-standard",
        actions: [{ kind: "add_to_watch", url: null }],
      },
      {
        seat_class: "first",
        status: "sold_out",
        fare: 33_200,
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-04T02:55:00Z",
        },
        registration_evidence_id: "evidence-first",
        actions: [{ kind: "add_to_watch", url: null }],
      },
    ],
  });
}

describe("TrainResultCard", () => {
  it("preserves per-seat registration states, labels, actions, and card metadata", async () => {
    const user = userEvent.setup();
    const onChooseSeat = vi.fn();
    const timetable = train();

    render(<TrainResultCard
      train={timetable}
      registrationBySeat={{
        standard: {
          status: "active",
          watchId: "watch-standard",
          reservationPolicy: "reserve_once_before_payment",
        },
        first: { status: "error", message: "등록을 다시 시도해 주세요." },
      }}
      onChooseSeat={onChooseSeat}
      officialHandoffComponent={OfficialHandoff}
      automaticReservationEnabled={false}
    />);

    const card = screen.getByRole("article", { name: "KTX 026" });
    expect(within(card).getByText("성인 23,700원")).toBeTruthy();
    expect(within(card).getByText("공식 시간표")).toBeTruthy();
    expect(within(card).getByText("대기 등록 1건")).toBeTruthy();
    expect(within(card).getByText("좌석 재발견마다 자동 예매 · 결제 전 중단")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "일반실 대기 취소" })
      .getAttribute("aria-pressed")).toBe("true");
    expect(within(card).getByRole("alert").textContent).toContain("등록을 다시 시도해 주세요.");

    await user.click(within(card).getByRole("button", { name: "특실 다시 등록" }));
    expect(onChooseSeat).toHaveBeenCalledWith(timetable.id, "first");
  });

  it("labels an unrecognized timetable source without promoting it to official", () => {
    const timetable = mapTimetable({
      provider: "srt",
      train_number: "SRT 327",
      train_type: "SRT",
      origin: "수서",
      destination: "부산",
      departure_at: "2026-08-04T13:00:00+09:00",
      arrival_at: "2026-08-04T15:30:00+09:00",
      adult_fare: 52_600,
      fare_currency: "KRW",
      timetable_source: "untrusted",
      timetable_retrieved_at: "invalid",
      official_booking_url: "https://etk.srail.kr/main.do",
      seat_classes: [],
    });

    render(<TrainResultCard
      train={timetable}
      registrationBySeat={{}}
      onChooseSeat={vi.fn()}
      officialHandoffComponent={OfficialHandoff}
      automaticReservationEnabled={false}
    />);

    expect(screen.getByText("시간표 출처 미확인")).toBeTruthy();
    expect(screen.getByText("시간표 업데이트 시각 미제공")).toBeTruthy();
    expect(screen.queryByText("공식 시간표")).toBeNull();
  });

  it("offers official standing booking and cancellation-seat waiting without automatic booking", async () => {
    const user = userEvent.setup();
    const onChooseSeat = vi.fn();
    const timetable = mapTimetable({
      provider: "korail",
      train_number: "KTX 223",
      train_type: "KTX-산천",
      origin: "서울",
      destination: "대전",
      departure_at: "2026-08-15T22:08:00+09:00",
      arrival_at: "2026-08-15T23:07:00+09:00",
      adult_fare: 23_700,
      fare_currency: "KRW",
      timetable_source: "official_provider",
      timetable_retrieved_at: "2026-08-15T12:20:00Z",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "standing_only",
        fare: 23_700,
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-15T12:20:00Z",
        },
        registration_evidence_id: "evidence-standing-only",
        actions: [
          { kind: "official_check", url: "https://www.korail.com/ticket/search" },
          { kind: "add_to_watch", url: null },
        ],
      }],
    });

    render(<TrainResultCard
      train={timetable}
      registrationBySeat={{}}
      onChooseSeat={onChooseSeat}
      officialHandoffComponent={OfficialHandoff}
      automaticReservationEnabled
    />);

    const panel = screen.getByLabelText("KTX 223 일반실");
    expect(within(panel).getByText("입석만 가능")).toBeTruthy();
    expect(within(panel).getByRole("button", {
      name: "KTX 223 일반실 공식 예매 전 안내 열기",
    })).toBeTruthy();
    const waitButton = within(panel).getByRole("button", { name: "일반실 취소좌석 대기" });
    expect(within(panel).queryByRole("button", { name: "일반실 자동 예매" })).toBeNull();

    await user.click(waitButton);
    expect(onChooseSeat).toHaveBeenCalledWith(timetable.id, "standard");
  });

  it("skips equal snapshots and rerenders when the train identity changes", () => {
    const timetable = mapTimetable({
      provider: "korail",
      train_number: "KTX 028",
      train_type: "KTX",
      origin: "대전",
      destination: "서울",
      departure_at: "2026-08-04T14:00:00+09:00",
      arrival_at: "2026-08-04T15:04:00+09:00",
      adult_fare: 23_700,
      fare_currency: "KRW",
      timetable_source: "official_provider",
      timetable_retrieved_at: "2026-08-04T03:00:00Z",
      official_booking_url: "https://www.korail.com/ticket/search",
      seat_classes: [{
        seat_class: "standard",
        status: "available",
        fare: 23_700,
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-04T03:00:00Z",
        },
        registration_evidence_id: "evidence-standard",
        actions: [{
          kind: "official_check",
          url: "https://www.korail.com/ticket/search",
        }],
      }],
    });
    const handoff = vi.fn(() => null);
    const onChooseSeat = vi.fn();
    const { rerender } = render(<TrainResultCard
      train={timetable}
      registrationBySeat={{}}
      onChooseSeat={onChooseSeat}
      officialHandoffComponent={handoff}
      automaticReservationEnabled={false}
    />);
    expect(handoff).toHaveBeenCalledOnce();

    rerender(<TrainResultCard
      train={timetable}
      registrationBySeat={{}}
      onChooseSeat={onChooseSeat}
      officialHandoffComponent={handoff}
      automaticReservationEnabled={false}
    />);
    expect(handoff).toHaveBeenCalledOnce();

    rerender(<TrainResultCard
      train={{ ...timetable }}
      registrationBySeat={{}}
      onChooseSeat={onChooseSeat}
      officialHandoffComponent={handoff}
      automaticReservationEnabled={false}
    />);
    expect(handoff).toHaveBeenCalledTimes(2);
  });
});
