import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Home } from "../src/App";
import type { ActiveWatch } from "../src/features/home/ActiveWatchList";
import {
  HomePage,
  openHomeOfficialPayment,
  type HomePageProps,
} from "../src/features/home/HomePage";
import type { PaymentRequiredViewModel } from "../src/features/home/paymentRequiredViewModel";
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

function activeWatch(overrides: Partial<ActiveWatch> = {}): ActiveWatch {
  return {
    id: "watch-home",
    provider: "KORAIL",
    route: "서울 → 부산",
    train: "KTX 085",
    date: "8월 8일 (토)",
    departure: "12:00",
    arrival: "14:30",
    status: "watching",
    statusLabel: "감시 중",
    accountAuthStatus: "not_checked",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 매진 · 공식 관측 12:00",
    reservationPolicy: "notify_only",
    ...overrides,
  };
}

function homePageProps(overrides: Partial<HomePageProps> = {}): HomePageProps {
  return {
    watches: [],
    paymentWatches: [],
    onCreate: vi.fn(),
    onViewReservations: vi.fn(),
    onOpenRailAccounts: vi.fn(),
    onPause: vi.fn(),
    onResume: vi.fn(),
    onCancel: vi.fn(),
    onToast: vi.fn(),
    renderSeatFoundAction: vi.fn(() => null),
    ...overrides,
  };
}

function managementHero(): HTMLElement {
  const status = screen.getByText("관심 열차 관리");
  const hero = status.closest("section");
  if (!(hero instanceof HTMLElement)) throw new Error("watch management hero is missing");
  return hero;
}

function activeSection(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "활동 중인 대기" });
  const section = heading.closest("section");
  if (!(section instanceof HTMLElement)) throw new Error("active watch section is missing");
  return section;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("home page", () => {
  it("routes the three concrete Home actions exactly once", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    const onViewReservations = vi.fn();
    const onOpenRailAccounts = vi.fn();
    render(
      <HomePage
        {...homePageProps({
          watches: [activeWatch()],
          onCreate,
          onViewReservations,
          onOpenRailAccounts,
        })}
      />,
    );

    await user.click(within(managementHero()).getByRole("button", { name: "새 대기 만들기" }));
    await user.click(within(activeSection()).getByRole("button", { name: "전체 내역 보기" }));
    await user.click(screen.getByRole("button", { name: "로그인 필요" }));

    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onViewReservations).toHaveBeenCalledTimes(1);
    expect(onOpenRailAccounts).toHaveBeenCalledTimes(1);
  });

  it("fails closed with a readable toast when the official payment URL is missing", () => {
    const onToast = vi.fn();
    const officialWindow = fakeOfficialWindow();
    const payment: PaymentRequiredViewModel = {
      id: "missing-url",
      provider: "KORAIL",
      train: "KTX 085",
      origin: null,
      destination: null,
      route: "서울 → 부산",
      departure: "12:00",
      arrival: "14:30",
      date: "8월 8일",
      seatClassLabel: null,
      paymentDeadline: null,
      officialBookingUrl: null,
    };

    openHomeOfficialPayment(payment, onToast, officialWindow);

    expect(onToast).toHaveBeenCalledTimes(1);
    expect(onToast).toHaveBeenCalledWith("공식 예매 주소를 확인할 수 없습니다.");
    expect(officialWindow.open).not.toHaveBeenCalled();
    expect(officialWindow.location.assign).not.toHaveBeenCalled();
  });

  it("opens a valid official payment URL only after the user action", async () => {
    const user = userEvent.setup();
    const onToast = vi.fn();
    const openWindow = vi.spyOn(window, "open").mockImplementation(() => null);
    const payment: PaymentRequiredViewModel = {
      id: "valid-url",
      provider: "SRT",
      train: "SRT 370",
      origin: null,
      destination: null,
      route: "대전 → 수서",
      departure: "22:06",
      arrival: "23:12",
      date: "8월 8일",
      seatClassLabel: null,
      paymentDeadline: null,
      officialBookingUrl: "https://etk.srail.kr",
    };
    render(
      <HomePage
        {...homePageProps({ paymentWatches: [payment], onToast })}
      />,
    );

    expect(openWindow).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /공식 결제 열기/ }));

    expect(onToast).toHaveBeenCalledTimes(1);
    expect(onToast).toHaveBeenCalledWith("공식 결제 화면을 새 창에서 엽니다.");
    expect(openWindow).toHaveBeenCalledTimes(1);
    expect(openWindow).toHaveBeenCalledWith(
      SRT_OFFICIAL_RESERVATION_URL,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("uses the validated SRT ticket route for a payment-required watch", () => {
    const onToast = vi.fn();
    const officialWindow = fakeOfficialWindow();
    const payment: PaymentRequiredViewModel = {
      id: "android-srt",
      provider: "SRT",
      train: "SRT 370",
      origin: "대전",
      destination: "수서",
      route: "대전 → 수서",
      departure: "22:06",
      arrival: "23:12",
      date: "8월 8일",
      seatClassLabel: "일반실",
      paymentDeadline: null,
      officialBookingUrl: "https://etk.srail.kr",
    };

    openHomeOfficialPayment(
      payment,
      onToast,
      officialWindow,
      ANDROID_CHROME_USER_AGENT,
      VALIDATED_ALL_CONFIG,
    );

    expect(officialWindow.intentAnchor.href).toBe(
      buildSrtTicketIntentUrl(SRT_OFFICIAL_RESERVATION_URL),
    );
    expect(officialWindow.intentAnchor.target).toBe("_blank");
    expect(officialWindow.intentAnchor.click).toHaveBeenCalledOnce();
    expect(officialWindow.location.assign).not.toHaveBeenCalled();
    expect(officialWindow.open).not.toHaveBeenCalled();
    expect(onToast).toHaveBeenCalledWith(
      "공식 앱 열기를 시도합니다. 연결되지 않으면 외부 브라우저에서 공식 홈페이지를 엽니다.",
    );
  });

  it("uses the validated Korail ticket route for a payment-required watch", () => {
    const onToast = vi.fn();
    const officialWindow = fakeOfficialWindow();
    const payment: PaymentRequiredViewModel = {
      id: "android-korail",
      provider: "KORAIL",
      train: "KTX 085",
      origin: "서울",
      destination: "부산",
      route: "서울 → 부산",
      departure: "12:00",
      arrival: "14:30",
      date: "8월 8일",
      seatClassLabel: "일반실",
      paymentDeadline: null,
      officialBookingUrl: "https://www.letskorail.com",
    };

    openHomeOfficialPayment(
      payment,
      onToast,
      officialWindow,
      ANDROID_CHROME_USER_AGENT,
      VALIDATED_ALL_CONFIG,
    );

    expect(officialWindow.intentAnchor.href).toBe(
      buildKorailNavigationIntentUrl("ticket", KORAIL_OFFICIAL_RESERVATION_URL),
    );
    expect(officialWindow.intentAnchor.target).toBe("_blank");
    expect(officialWindow.intentAnchor.click).toHaveBeenCalledOnce();
    expect(officialWindow.location.assign).not.toHaveBeenCalled();
    expect(officialWindow.open).not.toHaveBeenCalled();
    expect(onToast).toHaveBeenCalledWith(
      "공식 앱 열기를 시도합니다. 연결되지 않으면 외부 브라우저에서 공식 홈페이지를 엽니다.",
    );
  });

  it("returns to watch management when the only payment deadline elapsed", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    render(
      <HomePage
        {...homePageProps({
          paymentWatches: [{
            id: "elapsed",
            provider: "SRT",
            train: "SRT 370",
            origin: null,
            destination: null,
            route: "대전 → 수서",
            departure: "22:06",
            arrival: "23:12",
            date: "8월 4일",
            seatClassLabel: null,
            paymentDeadline: "2026-08-01T23:59:59Z",
            officialBookingUrl: "https://etk.srail.kr",
          }],
        })}
      />,
    );

    expect(screen.getByText("관심 열차 관리")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /결제 대기/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /공식 결제 열기/ })).toBeNull();
  });

  it("renders the injected action for a seat-found watch only", () => {
    const seatFound = activeWatch({
      id: "seat-found",
      status: "seat_found",
      statusLabel: "좌석 발견",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-08-05T03:00:00Z",
        observedLabel: "최근 확인 12:00",
      },
    });
    const watching = activeWatch({ id: "watching" });
    const renderSeatFoundAction = vi.fn((watch: ActiveWatch) => (
      <button type="button">{watch.id} 공식 행동</button>
    ));
    render(
      <HomePage
        {...homePageProps({
          watches: [seatFound, watching],
          renderSeatFoundAction,
        })}
      />,
    );

    expect(renderSeatFoundAction).toHaveBeenCalledTimes(1);
    expect(renderSeatFoundAction).toHaveBeenCalledWith(seatFound);
    expect(screen.getByRole("button", { name: "seat-found 공식 행동" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "watching 공식 행동" })).toBeNull();
  });

  it("keeps the legacy Home navigation adapter behavior", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(
      <Home
        watches={[activeWatch()]}
        onNavigate={onNavigate}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onToast={vi.fn()}
      />,
    );

    await user.click(within(managementHero()).getByRole("button", { name: "새 대기 만들기" }));
    await user.click(within(activeSection()).getByRole("button", { name: "전체 내역 보기" }));
    await user.click(screen.getByRole("button", { name: "로그인 필요" }));

    expect(onNavigate).toHaveBeenCalledTimes(3);
    expect(onNavigate).toHaveBeenNthCalledWith(1, "new");
    expect(onNavigate).toHaveBeenNthCalledWith(2, "reservations");
    expect(onNavigate).toHaveBeenNthCalledWith(3, "settings", "rail-accounts");
  });

  it("keeps the legacy single paymentWatch adapter behavior", () => {
    render(
      <Home
        watches={[]}
        paymentWatch={{
          provider: "KORAIL",
          train: "KTX 085",
          route: "서울 → 부산",
          departure: "12:00",
          arrival: "14:30",
          date: "8월 8일",
          payment_deadline: null,
          official_booking_url: "https://www.letskorail.com",
        }}
        onNavigate={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onToast={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "결제 대기 1건" })).toBeTruthy();
    expect(screen.getByText("KTX 085")).toBeTruthy();
  });

  it("keeps the legacy optional refresh callback contract", async () => {
    const user = userEvent.setup();
    const onRefreshWatches = vi.fn();
    const commonProps = {
      watches: [activeWatch()],
      onNavigate: vi.fn(),
      onPause: vi.fn(),
      onResume: vi.fn(),
      onCancel: vi.fn(),
      onToast: vi.fn(),
    };
    const { rerender } = render(<Home {...commonProps} />);
    const refreshName = "활동 중인 대기 새로고침";

    expect((screen.getByRole("button", { name: refreshName }) as HTMLButtonElement).disabled).toBe(true);

    rerender(<Home {...commonProps} onRefreshWatches={onRefreshWatches} />);
    await user.click(screen.getByRole("button", { name: refreshName }));

    expect(onRefreshWatches).toHaveBeenCalledTimes(1);
  });
});
