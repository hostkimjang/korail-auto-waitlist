import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Reservations } from "../src/App";
import type { ReservationListWatch } from "../src/features/reservations/ReservationList";
import {
  openOfficialReservation,
  ReservationsPage,
} from "../src/features/reservations/ReservationsPage";
import type { ReservationWatchViewModel } from "../src/features/reservations/reservationViewModel";
import {
  buildKorailNavigationIntentUrl,
  buildSrtTicketIntentUrl,
  KORAIL_OFFICIAL_RESERVATION_URL,
  SRT_OFFICIAL_RESERVATION_URL,
} from "../src/features/official-handoff/officialAppIntentUrl";
import type { RailDeepLinkConfig } from "../src/features/official-handoff/officialAppIntentUrl";

const ANDROID_CHROME_USER_AGENT =
  "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36";

const VALIDATED_ALL_CONFIG: RailDeepLinkConfig = {
  korailBooking: {
    enabled: true,
    validatedAndroidAppVersion: "7.0.0+70000006",
  },
  korailTicket: {
    enabled: true,
    validatedAndroidAppVersion: "7.0.0+70000006",
  },
  srtMain: {
    enabled: true,
    validatedAndroidAppVersion: "2.0.41+150",
  },
  srtTicket: {
    enabled: true,
    validatedAndroidAppVersion: "2.0.41+150",
  },
};

function fakeOfficialWindow() {
  const intentAnchor = document.createElement("a");
  vi.spyOn(intentAnchor, "click").mockImplementation(() => undefined);
  vi.spyOn(intentAnchor, "remove").mockImplementation(() => undefined);
  return {
    open: vi.fn(),
    location: { assign: vi.fn() },
    document: {
      createElement: vi.fn((_tagName: "a") => intentAnchor),
      body: {
        appendChild: vi.fn((anchor: HTMLAnchorElement) => anchor),
      },
    },
    intentAnchor,
  };
}

const legacyWatch: ReservationListWatch = {
  id: "legacy-scheduled",
  status: "scheduled",
  statusLabel: "대기 등록됨",
  route: "서울 → 부산",
  train: "KTX 085",
  date: "8월 1일",
  departure: "14:11",
  payment_deadline: null,
  official_booking_url: "https://www.letskorail.com",
};
const legacyWatches: ReadonlyArray<ReservationListWatch> = [legacyWatch];

const watches: ReadonlyArray<ReservationWatchViewModel> = [
  {
    id: "scheduled",
    provider: "KORAIL",
    status: "scheduled",
    statusLabel: "대기 등록됨",
    route: "서울 → 부산",
    train: "KTX 085",
    date: "8월 1일",
    departure: "14:11",
    paymentDeadline: null,
    officialBookingUrl: "https://www.letskorail.com",
  },
  {
    id: "payment",
    provider: "SRT",
    status: "payment_required",
    statusLabel: "결제 필요",
    route: "수서 → 부산",
    train: "SRT 327",
    date: "8월 1일",
    departure: "14:30",
    paymentDeadline: null,
    officialBookingUrl: "https://etk.srail.kr",
  },
  {
    id: "done",
    provider: "KORAIL",
    status: "completed",
    statusLabel: "결제 완료",
    route: "서울 → 대전",
    train: "KTX 001",
    date: "7월 30일",
    departure: "09:00",
    paymentDeadline: null,
    officialBookingUrl: null,
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reservations page", () => {
  it("adapts the legacy App navigation contract to the concrete create action", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(
      <Reservations
        watches={legacyWatches}
        onNavigate={onNavigate}
      />,
    );

    expect(screen.getByRole("heading", { name: "서울 → 부산" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "새 대기" }));

    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("new");
  });

  it("does not render an official CTA for an unsafe legacy compatibility URL", () => {
    render(
      <Reservations
        watches={[{
          ...legacyWatch,
          official_booking_url: "https://attacker.example/ticket",
        }]}
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /공식 예매 열기/ })).toBeNull();
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
      "https://www.korail.com/ticket/search/general",
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
              paymentDeadline: "2026-08-01T23:59:59Z",
              officialBookingUrl: "https://etk.srail.kr",
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

  it("uses the validated SRT ticket route from payment history", () => {
    const officialWindow = fakeOfficialWindow();
    openOfficialReservation({
      id: "android-payment",
      provider: "SRT",
      status: "payment_required",
      statusLabel: "결제 필요",
      route: "수서 → 부산",
      train: "SRT 327",
      date: "8월 8일",
      departure: "14:30",
      paymentDeadline: null,
      officialBookingUrl: "https://etk.srail.kr",
    }, officialWindow, ANDROID_CHROME_USER_AGENT, VALIDATED_ALL_CONFIG);

    expect(officialWindow.intentAnchor.href).toBe(
      buildSrtTicketIntentUrl(SRT_OFFICIAL_RESERVATION_URL),
    );
    expect(officialWindow.intentAnchor.target).toBe("_blank");
    expect(officialWindow.intentAnchor.click).toHaveBeenCalledOnce();
    expect(officialWindow.location.assign).not.toHaveBeenCalled();
    expect(officialWindow.open).not.toHaveBeenCalled();
  });

  it("uses the validated Korail ticket route from payment history", () => {
    const officialWindow = fakeOfficialWindow();
    openOfficialReservation({
      id: "android-korail-payment",
      provider: "KORAIL",
      status: "payment_required",
      statusLabel: "결제 필요",
      route: "서울 → 부산",
      train: "KTX 085",
      date: "8월 8일",
      departure: "12:00",
      paymentDeadline: null,
      officialBookingUrl: "https://www.letskorail.com",
    }, officialWindow, ANDROID_CHROME_USER_AGENT, VALIDATED_ALL_CONFIG);

    expect(officialWindow.intentAnchor.href).toBe(
      buildKorailNavigationIntentUrl("ticket", KORAIL_OFFICIAL_RESERVATION_URL),
    );
    expect(officialWindow.intentAnchor.target).toBe("_blank");
    expect(officialWindow.intentAnchor.click).toHaveBeenCalledOnce();
    expect(officialWindow.location.assign).not.toHaveBeenCalled();
    expect(officialWindow.open).not.toHaveBeenCalled();
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
            paymentDeadline: null,
            officialBookingUrl: null,
          },
          {
            id: "completed",
            status: "completed",
            statusLabel: "결제 완료",
            route: "서울 → 대전",
            train: "KTX 001",
            date: "8월 1일",
            departure: "09:00",
            paymentDeadline: null,
            officialBookingUrl: null,
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
