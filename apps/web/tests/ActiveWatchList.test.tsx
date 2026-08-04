import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ActiveWatchList, type ActiveWatch } from "../src/features/home/ActiveWatchList";

function watch(index: number): ActiveWatch {
  return {
    id: `watch-${index}`,
    provider: index % 2 === 0 ? "KORAIL" : "SRT",
    route: `서울 → 부산 ${index}`,
    train: `열차 ${index}`,
    date: "8월 1일",
    departure: "12:00",
    arrival: "14:30",
    status: "watching",
    statusLabel: "감시 중",
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: `일반실 · 매진 · 공식 관측 12:${String(index).padStart(2, "0")}`,
  };
}

describe("ActiveWatchList", () => {
  it("does not expose the retired per-watch balanced and focused interval controls", () => {
    render(
      <ActiveWatchList
        watches={[watch(1)]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByRole("combobox", { name: /취소표 관측 모드/ })).toBeNull();
    expect(screen.queryByText("균형")).toBeNull();
    expect(screen.queryByText("집중")).toBeNull();
  });

  it("keeps the policy switch in a dedicated action area after status details", () => {
    const automatic: ActiveWatch = {
      ...watch(1),
      reservationPolicy: "reserve_once_before_payment",
      accountAuthStatus: "authenticated",
    };

    render(
      <ActiveWatchList
        watches={[automatic]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onChangeReservationPolicy={vi.fn()}
      />,
    );

    const row = screen.getByRole("article");
    const state = row.querySelector(".watch-state");
    const actions = row.querySelector(".row-actions");
    const policy = row.querySelector(".watch-policy-control");
    const toggle = screen.getByRole("switch", {
      name: "열차 1 일반실 좌석 재발견마다 자동 예매 설정",
    });
    expect(state).not.toBeNull();
    expect(actions).not.toBeNull();
    expect(policy?.parentElement).toBe(actions);
    expect((state?.compareDocumentPosition(actions as Node) ?? 0) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(policy?.textContent).toContain("좌석 재발견마다 자동 예매");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("renders every active watch without a hidden display cap", async () => {
    const user = userEvent.setup();
    const watches = Array.from({ length: 25 }, (_, index) => watch(index + 1));
    const onPause = vi.fn();
    const onViewAll = vi.fn();
    const onRefresh = vi.fn();
    const lastRefreshedAt = new Date(2026, 6, 31, 18, 35, 42);

    render(
      <ActiveWatchList
        watches={watches}
        onCreate={vi.fn()}
        onViewAll={onViewAll}
        onRefresh={onRefresh}
        lastRefreshedAt={lastRefreshedAt}
        onPause={onPause}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("전체 25건 모두 표시 중")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("최근 갱신 18:35:42");
    expect(screen.getAllByRole("article")).toHaveLength(25);
    expect(screen.getByText("열차 25 · 8월 1일")).toBeTruthy();
    expect(screen.getAllByText("최근 확인 기록 없음")).toHaveLength(25);
    expect(document.querySelectorAll(".status-pill .status-dot")).toHaveLength(25);

    await user.click(screen.getByRole("button", { name: "전체 내역 보기" }));
    expect(onViewAll).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "활동 중인 대기 새로고침" }));
    expect(onRefresh).toHaveBeenCalledOnce();

    const lastWatch = screen.getByText("열차 25 · 8월 1일").closest("article");
    expect(lastWatch).not.toBeNull();
    if (lastWatch) {
      await user.click(within(lastWatch).getByRole("button", { name: "대기 일시정지" }));
    }
    expect(onPause).toHaveBeenCalledWith("watch-25");
  });

  it("exposes refresh progress without replacing the fixed-width timestamp", () => {
    render(
      <ActiveWatchList
        watches={[]}
        isRefreshing
        lastRefreshedAt={new Date(2026, 6, 31, 9, 7, 5)}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onRefresh={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const refreshButton = screen.getByRole("button", { name: "활동 중인 대기 새로고침" });
    expect(refreshButton.getAttribute("aria-busy")).toBe("true");
    expect(refreshButton.querySelector(".active-refresh-icon.is-spinning")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("최근 갱신 09:07:05");
  });

  it("gives same-train standard and first-class watches distinct visible evidence text", () => {
    const standard = watch(1);
    const first: ActiveWatch = {
      ...watch(2),
      id: "watch-first",
      provider: standard.provider,
      route: standard.route,
      train: standard.train,
      seatClass: "first",
      seatClassLabel: "특실",
      seatEvidenceLabel: "특실 · 예매 가능 · 공식 페이지에서 직접 확인 12:34",
    };

    render(
      <ActiveWatchList
        watches={[standard, first]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("일반실 · 매진 · 공식 관측 12:01")).toBeTruthy();
    expect(screen.getByText("특실 · 예매 가능 · 공식 페이지에서 직접 확인 12:34")).toBeTruthy();
  });

  it("renders a booking action only for seat-found watches", async () => {
    const user = userEvent.setup();
    const watching = watch(1);
    const seatFound: ActiveWatch = {
      ...watch(2),
      status: "seat_found",
      statusLabel: "좌석 발견",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-07-31T03:45:00Z",
        observedLabel: "최근 확인 12:45",
      },
    };
    const onBook = vi.fn();

    render(
      <ActiveWatchList
        watches={[watching, seatFound]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        renderSeatFoundAction={(item) => (
          <button type="button" onClick={() => onBook(item.id)}>예매</button>
        )}
      />,
    );

    const bookingButton = screen.getByRole("button", { name: "예매" });
    expect(bookingButton.closest("article")?.textContent).toContain("열차 2");
    expect(screen.getAllByRole("button", { name: "예매" })).toHaveLength(1);
    expect(screen.getByText("좌석 발견 · 감시 계속")).toBeTruthy();

    await user.click(bookingButton);
    expect(onBook).toHaveBeenCalledOnce();
    expect(onBook).toHaveBeenCalledWith("watch-2");
  });

  it("removes the booking action when the current observation is no longer actionable", () => {
    const staleSeatFound: ActiveWatch = {
      ...watch(2),
      status: "seat_found",
      statusLabel: "좌석 발견",
      seatEvidenceLabel: "일반실 · 매진 · 최근 관측 12:46",
      registrationEvidenceLabel: "일반실 · 예매 가능 · 공식 관측 12:34",
      seatFoundObservation: null,
    };

    render(
      <ActiveWatchList
        watches={[staleSeatFound]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        renderSeatFoundAction={() => <button type="button">예매</button>}
      />,
    );

    expect(screen.queryByRole("button", { name: "예매" })).toBeNull();
    expect(screen.getByText("일반실 · 매진 · 최근 관측 12:46")).toBeTruthy();
    expect(screen.getByText("등록 당시 일반실 · 예매 가능 · 공식 관측 12:34")).toBeTruthy();
  });

  it("keeps a failed seat-acquisition attempt visible with its safe retry condition", () => {
    const attempted: ActiveWatch = {
      ...watch(2),
      status: "seat_found",
      statusLabel: "좌석 발견",
      reservationPolicy: "reserve_once_before_payment",
      accountAuthStatus: "authenticated",
      latestReservationAttempt: {
        outcome: "not_available",
        startedAt: "2026-08-02T13:04:43Z",
        finishedAt: "2026-08-02T13:05:07Z",
        retryable: true,
        manualCheckRequired: false,
        retryCondition: "new_availability_episode",
        paymentHoldEndedAt: null,
      },
    };

    render(
      <ActiveWatchList
        watches={[attempted]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText(
      "예매 시도 · 좌석 확보 실패 · 감시 계속 · 22:05:07 · 매진 후 좌석이 다시 열리면 자동 예매",
    )).toBeTruthy();
    expect(screen.getByText("좌석 재발견마다 자동 예매")).toBeTruthy();
    expect(screen.getByRole("switch", {
      name: "열차 2 일반실 좌석 재발견마다 자동 예매 설정",
    }).getAttribute("aria-checked")).toBe("true");
  });

  it("distinguishes manual confirmation, account recheck, pending, and held outcomes", () => {
    const outcomes: ActiveWatch[] = [
      {
        ...watch(1),
        latestReservationAttempt: {
          outcome: "unknown",
          startedAt: "2026-08-02T13:10:00Z",
          finishedAt: "2026-08-02T13:11:00Z",
          retryable: false,
          manualCheckRequired: true,
          retryCondition: null,
          paymentHoldEndedAt: null,
        },
      },
      {
        ...watch(2),
        latestReservationAttempt: {
          outcome: "auth_required",
          startedAt: "2026-08-02T13:12:00Z",
          finishedAt: "2026-08-02T13:13:00Z",
          retryable: true,
          manualCheckRequired: false,
          retryCondition: "provider_account_reverified",
          paymentHoldEndedAt: null,
        },
      },
      {
        ...watch(3),
        latestReservationAttempt: {
          outcome: "pending",
          startedAt: "2026-08-02T13:14:00Z",
          finishedAt: null,
          retryable: false,
          manualCheckRequired: false,
          retryCondition: null,
          paymentHoldEndedAt: null,
        },
      },
      {
        ...watch(4),
        latestReservationAttempt: {
          outcome: "payment_required",
          startedAt: "2026-08-02T13:15:00Z",
          finishedAt: "2026-08-02T13:16:00Z",
          retryable: false,
          manualCheckRequired: false,
          retryCondition: null,
          paymentHoldEndedAt: null,
        },
      },
    ];

    render(
      <ActiveWatchList
        watches={outcomes}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText(
      "예매 시도 결과 확인 필요 · 22:11:00 · 공식 예매 내역을 확인해 주세요",
    )).toBeTruthy();
    expect(screen.getByText("예매 시도 · 철도 계정 재확인 필요 · 22:13:00")).toBeTruthy();
    expect(screen.getByText("예매 시도 중 · 22:14:00")).toBeTruthy();
    expect(screen.getByText("좌석 임시 확보 · 결제 필요 · 22:16:00")).toBeTruthy();
  });

  it("shows a finalized ended hold as monitoring for a new availability episode", () => {
    const endedHold: ActiveWatch = {
      ...watch(4),
      status: "seat_found",
      statusLabel: "좌석 발견",
      reservationPolicy: "reserve_once_before_payment",
      accountAuthStatus: "authenticated",
      latestReservationAttempt: {
        outcome: "payment_required",
        startedAt: "2026-08-02T08:20:00Z",
        finishedAt: "2026-08-02T08:22:00Z",
        retryable: true,
        manualCheckRequired: false,
        retryCondition: "new_availability_episode",
        paymentHoldEndedAt: "2026-08-02T08:24:00Z",
      },
    };

    render(
      <ActiveWatchList
        watches={[endedHold]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("이전 결제 보류 종료 · 매진 후 재발견 대기")).toBeTruthy();
    expect(screen.getByText(
      "결제 보류 종료 확인 · 감시 계속 · 17:24:00 · 매진 후 좌석이 다시 열리면 자동 예매",
    )).toBeTruthy();
    expect(screen.queryByText(/좌석 임시 확보 · 결제 필요/)).toBeNull();
  });

  it("keeps a seat-found hold fail-closed without the finalized end marker", () => {
    const unresolvedHold: ActiveWatch = {
      ...watch(4),
      status: "seat_found",
      statusLabel: "좌석 발견",
      latestReservationAttempt: {
        outcome: "payment_required",
        startedAt: "2026-08-02T08:20:00Z",
        finishedAt: "2026-08-02T08:22:00Z",
        retryable: true,
        manualCheckRequired: false,
        retryCondition: "new_availability_episode",
        paymentHoldEndedAt: null,
      },
    };

    render(
      <ActiveWatchList
        watches={[unresolvedHold]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("좌석 발견 · 감시 계속")).toBeTruthy();
    expect(screen.getByText("좌석 임시 확보 · 결제 필요 · 17:22:00")).toBeTruthy();
    expect(screen.queryByText(/결제 보류 종료 확인/)).toBeNull();
  });

  it("explains an auth-required watch and keeps its controls beside the rail-account CTA", async () => {
    const user = userEvent.setup();
    const onOpenRailAccounts = vi.fn();
    const onPause = vi.fn();
    const onCancel = vi.fn();
    const authRequired: ActiveWatch = {
      ...watch(1),
      status: "auth_required",
      statusLabel: "로그인 필요",
      provider: "KORAIL",
      accountAuthStatus: "authenticated",
    };

    render(
      <ActiveWatchList
        watches={[authRequired]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={onPause}
        onResume={vi.fn()}
        onCancel={onCancel}
        onOpenRailAccounts={onOpenRailAccounts}
      />,
    );

    const row = screen.getByRole("article");
    expect(row.classList.contains("is-auth-required")).toBe(true);
    expect(within(row).getByText("대기 확인 필요")).toBeTruthy();
    expect(within(row).getByText("계정 확인됨 · 이전 인증 상태 확인")).toBeTruthy();
    expect(row.querySelector(".watch-auth-warning")).toBeNull();
    expect(row.querySelector(".watch-auth-line")).toBeTruthy();

    await user.click(within(row).getByRole("button", { name: "철도 계정" }));
    await user.click(within(row).getByRole("button", { name: "대기 일시정지" }));
    await user.click(within(row).getByRole("button", { name: "대기 취소" }));

    expect(onOpenRailAccounts).toHaveBeenCalledOnce();
    expect(onPause).toHaveBeenCalledWith("watch-1");
    expect(onCancel).toHaveBeenCalledWith("watch-1");
  });

  it("does not briefly claim that login is required while the latest account status is loading", () => {
    const authRequired: ActiveWatch = {
      ...watch(1),
      status: "auth_required",
      statusLabel: "로그인 필요",
      accountAuthStatus: null,
    };

    render(
      <ActiveWatchList
        watches={[authRequired]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const row = screen.getByRole("article");
    expect(within(row).getByText("계정 상태 확인 중")).toBeTruthy();
    expect(within(row).getByText("철도 계정 상태를 확인하고 있습니다")).toBeTruthy();
    expect(within(row).queryByText("로그인 필요")).toBeNull();
  });

  it("keeps a provider-blocked watch in automatic re-verification without exposing a login-required action", async () => {
    const user = userEvent.setup();
    const onOpenRailAccounts = vi.fn();
    const providerBlocked: ActiveWatch = {
      ...watch(1),
      status: "auth_required",
      statusLabel: "로그인 필요",
      accountAuthStatus: "provider_blocked",
      reservationPolicy: "notify_only",
    };

    render(
      <ActiveWatchList
        watches={[providerBlocked]}
        onCreate={vi.fn()}
        onViewAll={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onOpenRailAccounts={onOpenRailAccounts}
        onChangeReservationPolicy={vi.fn()}
      />,
    );

    const row = screen.getByRole("article");
    expect(within(row).getByText("운영사 제한 · 자동 재확인 대기")).toBeTruthy();
    expect(within(row).getByText("저장된 계정으로 운영사 세션을 자동 재확인 중")).toBeTruthy();
    expect(within(row).queryByText("로그인 필요")).toBeNull();
    expect(within(row).getByRole("button", { name: "운영사 상태" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "운영사 상태 확인" })).toBeTruthy();

    const policySwitch = within(row).getByRole("switch", {
      name: "열차 1 일반실 좌석 재발견마다 자동 예매 설정",
    });
    expect(policySwitch.getAttribute("aria-checked")).toBe("false");
    expect((policySwitch as HTMLButtonElement).disabled).toBe(true);

    await user.click(within(row).getByRole("button", { name: "운영사 상태" }));
    expect(onOpenRailAccounts).toHaveBeenCalledOnce();
  });
});
