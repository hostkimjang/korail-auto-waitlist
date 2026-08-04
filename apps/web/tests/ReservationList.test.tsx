import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  ReservationList,
  sortReservationWatches,
  type ReservationListWatch,
} from "../src/features/reservations/ReservationList";

function watch(
  id: string,
  status: string,
  paymentDeadline: string | null = null,
): ReservationListWatch {
  return {
    id,
    status,
    statusLabel: status === "payment_required" ? "결제 필요" : "감시 중",
    route: `${id} 출발 → 도착`,
    train: `KTX ${id}`,
    date: "8월 2일",
    departure: "10:00",
    payment_deadline: paymentDeadline,
    official_booking_url: "https://www.korail.com/ticket/search/general",
  };
}

describe("reservation list", () => {
  it("places payment-required watches first and orders their real deadlines before missing ones", () => {
    const watching = watch("watching", "watching");
    const missing = watch("missing", "payment_required");
    const later = watch("later", "payment_required", "2099-08-02T10:20:00+09:00");
    const earlier = watch("earlier", "payment_required", "2099-08-02T10:10:00+09:00");

    expect(sortReservationWatches([watching, missing, later, earlier]).map((item) => item.id))
      .toEqual(["earlier", "later", "missing", "watching"]);

    render(
      <ReservationList
        watches={[watching, missing, later, earlier]}
        onCreate={vi.fn()}
        onOpenOfficial={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("article").map((article) => article.textContent))
      .toEqual([
        expect.stringContaining("earlier 출발"),
        expect.stringContaining("later 출발"),
        expect.stringContaining("missing 출발"),
        expect.stringContaining("watching 출발"),
      ]);
  });

  it("shows the exact missing-deadline label without fabricating a countdown", () => {
    const { container } = render(
      <ReservationList
        watches={[watch("missing", "payment_required")]}
        onCreate={vi.fn()}
        onOpenOfficial={vi.fn()}
      />,
    );

    expect(screen.getByText("결제기한 미제공")).toBeTruthy();
    expect(container.querySelector("time")).toBeNull();
    expect(screen.queryByText(/15분 내 결제/)).toBeNull();
  });

  it("opens an official page only after the user selects the matching payment row", async () => {
    const user = userEvent.setup();
    const onOpenOfficial = vi.fn();
    const payment = watch("payment", "payment_required");
    render(
      <ReservationList
        watches={[payment]}
        onCreate={vi.fn()}
        onOpenOfficial={onOpenOfficial}
      />,
    );

    expect(onOpenOfficial).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole("article")).getByRole("button", { name: /결제 열기/ }));
    expect(onOpenOfficial).toHaveBeenCalledWith(payment);
  });

  it("keeps an elapsed payment record for audit without presenting an active payment CTA", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      const onOpenOfficial = vi.fn();
      const elapsed = watch("elapsed", "payment_required", "2026-08-01T23:59:00Z");
      const { container } = render(
        <ReservationList
          watches={[elapsed]}
          onCreate={vi.fn()}
          onOpenOfficial={onOpenOfficial}
        />,
      );

      expect(screen.getByText("기한 경과 · 확인 필요")).toBeTruthy();
      expect(screen.getByText("결제기한 경과 · 공식 확인 필요")).toBeTruthy();
      expect(container.querySelector("time.countdown")).toBeNull();
      expect(screen.queryByRole("button", { name: /결제 열기/ })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: /공식 확인 열기/ }));
      expect(onOpenOfficial).toHaveBeenCalledWith(elapsed);
    } finally {
      vi.useRealTimers();
    }
  });
});
