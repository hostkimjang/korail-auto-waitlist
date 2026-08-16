import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  PaymentRequiredSection,
  sortPaymentRequiredWatches,
} from "../src/features/home/PaymentRequiredSection";
import type { PaymentRequiredViewModel } from "../src/features/home/paymentRequiredViewModel";

function paymentWatch(id: string, deadline: string | null): PaymentRequiredViewModel {
  return {
    id,
    provider: "KORAIL",
    train: `KTX ${id}`,
    origin: null,
    destination: null,
    route: "서울 → 부산",
    departure: "10:00",
    arrival: "12:30",
    date: "8월 2일",
    seatClassLabel: null,
    paymentDeadline: deadline,
    officialBookingUrl: "https://www.korail.com/ticket/search/general",
  };
}

describe("payment required section", () => {
  it("shows only provider-confirmed train type and assigned seats", () => {
    const confirmed: PaymentRequiredViewModel = {
      ...paymentWatch("193", null),
      train: "193",
      trainType: "KTX-산천",
      seatClassLabel: "일반실",
      reservedSeats: [
        { carNumber: "5", seatNumber: "8A" },
        { carNumber: "5호차", seatNumber: "8B" },
      ],
    };
    const unconfirmed = paymentWatch("unknown-seat", null);
    render(
      <PaymentRequiredSection
        watches={[confirmed, unconfirmed]}
        onOpenPayment={vi.fn()}
      />,
    );

    expect(screen.getByText("KTX-산천 · 193")).toBeTruthy();
    expect(screen.getByText("예약 좌석 5호차 8A, 5호차 8B")).toBeTruthy();
    expect(screen.getAllByText(/예약 좌석/)).toHaveLength(1);
  });

  it("shows official confirmation evidence without exposing paid rows as payment actions", () => {
    render(
      <PaymentRequiredSection
        watches={[
          {
            ...paymentWatch("required", null),
            confirmationOutcome: "confirmed_payment_required",
            confirmationObservedAt: "2026-08-03T12:13:00Z",
            reconciliationAttemptCount: 2,
            nextReconcileAt: "2026-08-03T12:14:00Z",
          },
          { ...paymentWatch("missing", null), confirmationOutcome: "not_found" },
          {
            ...paymentWatch("unclear", null),
            confirmationOutcome: "inconclusive",
            confirmationDiagnosticCode: "official_record_ambiguous",
          },
          { ...paymentWatch("login", null), confirmationOutcome: "auth_required" },
          { ...paymentWatch("blocked", null), confirmationOutcome: "provider_blocked" },
          { ...paymentWatch("paid", null), confirmationOutcome: "confirmed_paid" },
        ]}
        onOpenPayment={vi.fn()}
      />,
    );

    expect(screen.getByText(/공식 내역에서 결제 대기 확인.*확인 21:13.*공식 재확인 2\/6회.*다음 21:14/)).toBeTruthy();
    expect(screen.getByText("공식 내역에서 대상 예약을 찾지 못함 · 결제 상태는 확정하지 않음")).toBeTruthy();
    expect(screen.getByText(
      "공식 내역에서 이번 예매 시도와 정확히 일치하는 항목을 하나로 구분하지 못했습니다.",
    )).toBeTruthy();
    expect(screen.getByText("공식 내역 확인에 로그인 필요")).toBeTruthy();
    expect(screen.getByText("운영사 제한으로 공식 내역 확인 불가")).toBeTruthy();
    expect(screen.queryByText("공식 내역에서 결제 완료 확인")).toBeNull();
    expect(screen.queryByText("KTX paid")).toBeNull();
    expect(screen.getAllByRole("button", { name: /공식 결제 열기/ })).toHaveLength(5);
    expect(screen.queryByText(/결제 실패/)).toBeNull();
  });

  it("fails closed when confirmed-paid evidence is the only payment-required row", () => {
    const onOpenPayment = vi.fn();
    render(
      <PaymentRequiredSection
        watches={[{ ...paymentWatch("paid", null), confirmationOutcome: "confirmed_paid" }]}
        onOpenPayment={onOpenPayment}
        emptyState={<p>긴급 결제 없음</p>}
      />,
    );

    expect(screen.getByText("긴급 결제 없음")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /공식 결제 열기/ })).toBeNull();
    expect(screen.queryByText("결제 필요")).toBeNull();
    expect(onOpenPayment).not.toHaveBeenCalled();
  });

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

  it("shows the warm ticket-list refresh guidance only for SRT payments", () => {
    const srtPayment: PaymentRequiredViewModel = {
      ...paymentWatch("srt", null),
      provider: "SRT",
      train: "SRT 327",
      officialBookingUrl: "https://etk.srail.kr",
    };
    const { rerender } = render(
      <PaymentRequiredSection watches={[srtPayment]} onOpenPayment={vi.fn()} />,
    );

    expect(screen.getByRole("note").textContent).toContain(
      "하단 ‘승차권 확인’을 한 번 더 눌러 목록을 갱신하세요.",
    );

    rerender(
      <PaymentRequiredSection
        watches={[paymentWatch("korail", null)]}
        onOpenPayment={vi.fn()}
      />,
    );
    expect(screen.queryByRole("note")).toBeNull();
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
