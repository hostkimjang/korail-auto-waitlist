import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import type { NewWaitPageProps } from "../src/features/new-wait/NewWaitPage";

const newWaitPageProbe = vi.hoisted(() => ({
  render: vi.fn<(props: NewWaitPageProps) => void>(),
}));

vi.mock("../src/features/new-wait/NewWaitPage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/features/new-wait/NewWaitPage")>();
  return {
    ...actual,
    NewWaitPage: (props: NewWaitPageProps): ReactElement => {
      newWaitPageProbe.render(props);
      return <div data-testid="new-wait-page-probe">새 대기 화면</div>;
    },
  };
});

import {
  Home,
  NewWait,
  PaymentHero,
  Reservations,
} from "../src/app/AppCompatibility";
import {
  Home as AppHome,
  NewWait as AppNewWait,
  OfficialHandoff as AppOfficialHandoff,
  PaymentHero as AppPaymentHero,
  Reservations as AppReservations,
  Settings,
  WatchRow as AppWatchRow,
  hasObservedSeatEvidence as appHasObservedSeatEvidence,
  isActiveWatch as appIsActiveWatch,
} from "../src/App";
import { hasObservedSeatEvidence } from "../src/domain/seatEvidence";
import { isActiveWatch } from "../src/features/app/watchSelectors";
import type { ActiveWatch } from "../src/features/home/ActiveWatchList";
import { WatchRow } from "../src/features/home/ActiveWatchList";
import { OfficialHandoff } from "../src/features/official-handoff/OfficialHandoff";
import type { SeatWatchRegistrationCompletion } from "../src/features/new-wait/useSeatWatchRegistration";
import { SettingsPage } from "../src/features/settings/SettingsPage";

function activeWatch(): ActiveWatch {
  return {
    id: "watch-one",
    provider: "KORAIL",
    route: "서울 → 부산",
    train: "KTX 085",
    date: "8월 1일 (토)",
    departure: "14:11",
    arrival: "16:52",
    status: "watching",
    statusLabel: "감시 중",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 매진 · 공식 관측",
    accountAuthStatus: "not_checked",
  };
}

describe("App compatibility facade", () => {
  it("preserves compatibility and direct re-export identities", () => {
    expect(AppHome).toBe(Home);
    expect(AppNewWait).toBe(NewWait);
    expect(AppPaymentHero).toBe(PaymentHero);
    expect(AppReservations).toBe(Reservations);
    expect(appIsActiveWatch).toBe(isActiveWatch);
    expect(Settings).toBe(SettingsPage);
    expect(AppWatchRow).toBe(WatchRow);
    expect(AppOfficialHandoff).toBe(OfficialHandoff);
    expect(appHasObservedSeatEvidence).toBe(hasObservedSeatEvidence);
  });

  it("keeps the Home navigation adapter arguments", async () => {
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

    const homePage = screen.getByRole("heading", { name: "지금 할 일" }).closest(".page");
    if (!(homePage instanceof HTMLElement)) throw new Error("홈 화면을 찾지 못했습니다.");
    const managementHero = homePage.querySelector(".watch-management-hero");
    const activeSection = homePage.querySelector(".active-section");
    if (!(managementHero instanceof HTMLElement)) throw new Error("대기 관리 영역을 찾지 못했습니다.");
    if (!(activeSection instanceof HTMLElement)) throw new Error("활동 중 대기 영역을 찾지 못했습니다.");
    await user.click(within(managementHero).getByRole("button", { name: "새 대기 만들기" }));
    await user.click(within(activeSection).getByRole("button", { name: "전체 내역 보기" }));
    await user.click(within(activeSection).getByRole("button", { name: "로그인 필요" }));

    expect(onNavigate).toHaveBeenNthCalledWith(1, "new");
    expect(onNavigate).toHaveBeenNthCalledWith(2, "reservations");
    expect(onNavigate).toHaveBeenNthCalledWith(3, "settings", "rail-accounts");
  });

  it("injects the canonical OfficialHandoff into the NewWait adapter", () => {
    const onComplete: SeatWatchRegistrationCompletion = async () => [];
    render(
      <NewWait
        demo={false}
        onComplete={onComplete}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByTestId("new-wait-page-probe").textContent).toBe("새 대기 화면");
    expect(newWaitPageProbe.render).toHaveBeenCalledWith(expect.objectContaining({
      demo: false,
      onComplete,
      officialHandoffComponent: OfficialHandoff,
    }));
  });

  it("keeps Reservations create and delete adapter callbacks", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    const onDelete = vi.fn();
    render(
      <Reservations
        watches={[{
          id: "legacy-expired",
          status: "expired",
          statusLabel: "만료",
          route: "서울 → 부산",
          train: "KTX 085",
          date: "8월 1일",
          departure: "14:11",
          payment_deadline: null,
          official_booking_url: null,
        }]}
        onNavigate={onNavigate}
        onDelete={onDelete}
      />,
    );

    await user.click(screen.getByRole("button", { name: "새 대기" }));
    await user.click(screen.getByRole("button", { name: /기록 삭제/ }));

    expect(onNavigate).toHaveBeenCalledOnce();
    expect(onNavigate).toHaveBeenCalledWith("new");
    expect(onDelete).toHaveBeenCalledOnce();
    expect(onDelete).toHaveBeenCalledWith("legacy-expired");
  });

  it("keeps PaymentHero as a single-watch zero-argument callback adapter", async () => {
    const user = userEvent.setup();
    const onOfficialPayment = vi.fn<() => void>();
    render(
      <PaymentHero
        watch={{
          id: "payment-one",
          provider: "SRT",
          train: "SRT 327",
          route: "수서 → 부산",
          departure: "10:42",
          arrival: "13:14",
          date: "8월 1일 (토)",
          payment_deadline: null,
          official_booking_url: "https://etk.srail.kr",
        }}
        onOfficialPayment={onOfficialPayment}
      />,
    );

    expect(screen.getByRole("heading", { name: "결제 대기 1건" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "공식 결제 열기" }));

    expect(onOfficialPayment).toHaveBeenCalledOnce();
    expect(onOfficialPayment).toHaveBeenCalledWith();
  });

  it("classifies every active status and rejects terminal or unknown statuses", () => {
    for (const status of [
      "draft",
      "scheduled",
      "watching",
      "official_waitlist",
      "seat_found",
      "reserving",
      "paused",
      "cooldown",
      "auth_required",
    ]) {
      expect(isActiveWatch({ status })).toBe(true);
    }
    for (const status of ["payment_required", "completed", "expired", "failed", "unknown"]) {
      expect(isActiveWatch({ status })).toBe(false);
    }
  });
});
