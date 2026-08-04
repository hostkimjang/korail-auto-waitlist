import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activeWatchHandoffTrain,
  renderHomeSeatFoundAction,
} from "../src/app/HomeSeatFoundOfficialHandoff";
import type { ActiveWatch } from "../src/features/home/ActiveWatchList";

function activeWatch(overrides: Partial<ActiveWatch> = {}): ActiveWatch {
  return {
    id: "seat-found-one",
    provider: "KORAIL",
    route: "대전 → 부산",
    train: "KTX 085",
    date: "8월 1일 (토)",
    departure: "13:05",
    arrival: "14:42",
    status: "seat_found",
    statusLabel: "좌석 발견",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 예매 가능 · 공식 관측 12:45",
    origin: "대전",
    destination: "부산",
    travelDate: "2026-08-01",
    officialBookingUrl: "https://www.korail.com/ticket/search/general",
    seatFoundObservation: {
      kind: "official_provider",
      observedAt: "2026-08-01T03:45:00Z",
      observedLabel: "최근 확인 12:45",
    },
    ...overrides,
  };
}

describe("HomeSeatFoundOfficialHandoff", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("maps an active watch into the exact official handoff train contract", () => {
    expect(activeWatchHandoffTrain(activeWatch())).toEqual({
      id: "active-watch-seat-found-one",
      provider: "KORAIL",
      origin: "대전",
      destination: "부산",
      name: "KTX 085",
      date: "8월 1일 (토)",
      departure_at: "2026-08-01T13:05:00+09:00",
      departure: "13:05",
      arrival: "14:42",
      seat_classes: [],
    });
  });

  it("falls back to the route and leaves departure timestamp empty without a travel date", () => {
    const mapped = activeWatchHandoffTrain(activeWatch({
      route: "용산 → 광주송정",
      origin: "",
      destination: "",
      travelDate: "",
    }));

    expect(mapped.origin).toBe("용산");
    expect(mapped.destination).toBe("광주송정");
    expect(mapped.departure_at).toBe("");
  });

  it("forwards the seat CTA and connects journey copy to the mapped train", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const watch = activeWatch();
    const { container } = render(
      <div className="app-shell">{renderHomeSeatFoundAction(watch)}</div>,
    );

    const trigger = screen.getByRole("button", {
      name: "KTX 085 일반실 예매 전 안내 열기",
    });
    expect(trigger.className).toBe("button button-primary compact watch-booking-button");

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "KTX 085 공식 예매 안내" });
    expect(within(dialog).getByLabelText("선택 열차 요약").textContent)
      .toContain("대전 → 부산");
    expect(dialog.textContent).toContain("일반실 기준 · 최근 확인 12:45");
    const shell = container.querySelector(".app-shell");
    if (!(shell instanceof HTMLElement)) throw new Error("앱 셸을 찾지 못했습니다.");
    expect(shell.inert).toBe(true);

    await user.click(within(dialog).getByRole("button", { name: "여정 복사" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      "2026-08-01 / 대전 → 부산 / KTX 085 / 13:05 출발",
    ));
    expect(within(dialog).getByRole("status").textContent).toContain("여정 정보를 복사했습니다");
  });
});
