import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Reservations } from "../src/App.jsx";
import type { ReservationListWatch } from "../src/features/reservations/ReservationList";
import { ReservationsPage } from "../src/features/reservations/ReservationsPage";

const watches: ReadonlyArray<ReservationListWatch> = [
  {
    id: "scheduled",
    status: "scheduled",
    statusLabel: "대기 등록됨",
    route: "서울 → 부산",
    train: "KTX 085",
    date: "8월 1일",
    departure: "14:11",
    official_booking_url: "https://www.letskorail.com",
  },
  {
    id: "payment",
    status: "payment_required",
    statusLabel: "결제 필요",
    route: "수서 → 부산",
    train: "SRT 327",
    date: "8월 1일",
    departure: "14:30",
    official_booking_url: "https://etk.srail.kr",
  },
  {
    id: "done",
    status: "completed",
    statusLabel: "결제 완료",
    route: "서울 → 대전",
    train: "KTX 001",
    date: "7월 30일",
    departure: "09:00",
    official_booking_url: null,
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reservations page", () => {
  it("adapts the legacy App navigation contract to the concrete create action", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<Reservations watches={[]} onNavigate={onNavigate} />);

    await user.click(screen.getByRole("button", { name: "새 대기" }));

    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("new");
  });

  it("calls the concrete create action exactly once", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    render(<ReservationsPage watches={[]} onCreate={onCreate} />);

    await user.click(screen.getByRole("button", { name: "새 대기" }));

    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("builds reservation counts and opens the selected official CTA securely", async () => {
    const user = userEvent.setup();
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<ReservationsPage watches={watches} onCreate={vi.fn()} />);

    expect(screen.getByText("진행 중").nextElementSibling?.textContent).toBe("1");
    expect(screen.getByText("결제 필요", {
      selector: ".reservation-summary span",
    }).nextElementSibling?.textContent).toBe("1");
    expect(screen.getByText("완료").nextElementSibling?.textContent).toBe("1");
    expect(screen.getByRole("button", { name: /결제 열기/ })).toBeTruthy();
    expect(screen.getAllByRole("article")[0]?.textContent).toContain("수서 → 부산");
    expect(screen.getByText("결제기한 미제공")).toBeTruthy();
    expect(openWindow).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /공식 예매 열기/ }));

    expect(openWindow).toHaveBeenCalledTimes(1);
    expect(openWindow).toHaveBeenCalledWith(
      "https://www.letskorail.com",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("does not count an elapsed provider deadline as payment waiting", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      render(
        <ReservationsPage
          watches={[
            {
              id: "elapsed-payment",
              status: "payment_required",
              statusLabel: "결제 필요",
              route: "대전 → 수서",
              train: "SRT 370",
              date: "8월 4일",
              departure: "22:06",
              payment_deadline: "2026-08-01T23:59:59Z",
              official_booking_url: "https://etk.srail.kr",
            },
          ]}
          onCreate={vi.fn()}
        />,
      );

      expect(screen.getByText("결제 필요", {
        selector: ".reservation-summary span",
      }).nextElementSibling?.textContent).toBe("0");
      expect(screen.getByText("기한 경과 확인").nextElementSibling?.textContent).toBe("1");
      expect(screen.getByRole("button", { name: /공식 확인 열기/ })).toBeTruthy();
      expect(screen.queryByRole("button", { name: /결제 열기/ })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows deleting only a deletable terminal record", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(
      <ReservationsPage
        watches={[
          {
            id: "expired",
            status: "expired",
            statusLabel: "만료",
            route: "서울 → 부산",
            train: "KTX 085",
            date: "8월 1일",
            departure: "14:11",
          },
          {
            id: "completed",
            status: "completed",
            statusLabel: "결제 완료",
            route: "서울 → 대전",
            train: "KTX 001",
            date: "8월 1일",
            departure: "09:00",
          },
        ]}
        onCreate={vi.fn()}
        onDelete={onDelete}
      />,
    );

    expect(screen.getAllByRole("button", { name: /기록 삭제/ })).toHaveLength(1);
    const expiredRow = screen.getByRole("heading", { name: "서울 → 부산" }).closest("article");
    expect(expiredRow).not.toBeNull();
    if (expiredRow) {
      await user.click(within(expiredRow).getByRole("button", { name: /기록 삭제/ }));
    }
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledWith("expired");
  });
});
