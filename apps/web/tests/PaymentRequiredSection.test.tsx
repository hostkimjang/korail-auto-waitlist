import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  PaymentRequiredSection,
  sortPaymentRequiredWatches,
  type PaymentRequiredWatch,
} from "../src/features/home/PaymentRequiredSection";

function paymentWatch(id: string, deadline: string | null): PaymentRequiredWatch {
  return {
    id,
    provider: "KORAIL",
    train: `KTX ${id}`,
    route: "서울 → 부산",
    departure: "10:00",
    arrival: "12:30",
    date: "8월 2일",
    payment_deadline: deadline,
    official_booking_url: "https://www.korail.com/ticket/search/general",
  };
}

describe("payment required section", () => {
  it("shows every urgent payment row and orders actual provider deadlines first", () => {
    const later = paymentWatch("later", "2099-08-02T10:20:00+09:00");
    const unknown = paymentWatch("unknown", null);
    const earlier = paymentWatch("earlier", "2099-08-02T10:10:00+09:00");

    expect(sortPaymentRequiredWatches([later, unknown, earlier]).map((item) => item.id))
      .toEqual(["earlier", "later", "unknown"]);
    render(<PaymentRequiredSection watches={[later, unknown, earlier]} onOpenPayment={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "결제 대기 3건" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /공식 결제 열기/ })).toHaveLength(3);
    expect(screen.getByText("결제기한 미제공")).toBeTruthy();
  });

  it("opens the exact urgent watch selected by the user", async () => {
    const user = userEvent.setup();
    const onOpenPayment = vi.fn();
    const watch = paymentWatch("one", null);
    render(<PaymentRequiredSection watches={[watch]} onOpenPayment={onOpenPayment} />);

    await user.click(screen.getByRole("button", { name: /공식 결제 열기/ }));
    expect(onOpenPayment).toHaveBeenCalledWith(watch);
  });

  it("updates the countdown each second only when the provider supplied an absolute deadline", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      const { container } = render(
        <PaymentRequiredSection
          watches={[paymentWatch("timed", "2026-08-02T00:00:05Z")]}
          onOpenPayment={vi.fn()}
        />,
      );

      expect(container.querySelector("time")?.textContent).toBe("00:00:05");
      act(() => vi.advanceTimersByTime(1_000));
      expect(container.querySelector("time")?.textContent).toBe("00:00:04");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not create a countdown for a missing or timezone-naive deadline", () => {
    const { container } = render(
      <PaymentRequiredSection
        watches={[paymentWatch("missing", null), paymentWatch("naive", "2026-08-02T09:00:00")]}
        onOpenPayment={vi.fn()}
      />,
    );

    expect(screen.getAllByText("결제기한 미제공")).toHaveLength(2);
    expect(container.querySelector("time")).toBeNull();
    expect(screen.queryByText(/15분 내 결제/)).toBeNull();
  });

  it("removes an elapsed deadline from urgent payment without hiding missing deadlines", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      render(
        <PaymentRequiredSection
          watches={[
            paymentWatch("timed", "2026-08-02T00:00:02Z"),
            paymentWatch("missing", null),
            paymentWatch("elapsed", "2026-08-01T23:59:59Z"),
          ]}
          onOpenPayment={vi.fn()}
          emptyState={<p>긴급 결제 없음</p>}
        />,
      );

      expect(screen.getByRole("heading", { name: "결제 대기 2건" })).toBeTruthy();
      expect(screen.queryByText("KTX elapsed")).toBeNull();
      act(() => vi.advanceTimersByTime(2_000));
      expect(screen.getByRole("heading", { name: "결제 대기 1건" })).toBeTruthy();
      expect(screen.getByText("KTX missing")).toBeTruthy();
      expect(screen.queryByText("KTX timed")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows its fallback when every provider deadline has elapsed", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      render(
        <PaymentRequiredSection
          watches={[paymentWatch("elapsed", "2026-08-01T23:59:59Z")]}
          onOpenPayment={vi.fn()}
          emptyState={<p>긴급 결제 없음</p>}
        />,
      );

      expect(screen.getByText("긴급 결제 없음")).toBeTruthy();
      expect(screen.queryByRole("heading", { name: /결제 대기/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /공식 결제 열기/ })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
