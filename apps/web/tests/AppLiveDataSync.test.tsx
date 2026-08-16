import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const liveApi = vi.hoisted(() => ({
  fetchWatches: vi.fn(),
  fetchNotificationChannels: vi.fn(),
  getAuthStatus: vi.fn(),
  subscribeToEvents: vi.fn(),
}));

const providerAccountsApi = vi.hoisted(() => ({
  fetchProviderAccounts: vi.fn(),
  saveProviderAccount: vi.fn(),
  deleteProviderAccount: vi.fn(),
}));

vi.mock("../src/shared/lib/runtimeConfig", () => ({ DEMO_MODE: false }));

vi.mock("../src/api/watches", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/watches")>();
  return { ...actual, fetchWatches: liveApi.fetchWatches };
});

vi.mock("../src/api/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/auth")>();
  return { ...actual, getAuthStatus: liveApi.getAuthStatus };
});

vi.mock("../src/api/notifications", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/notifications")>();
  return { ...actual, fetchNotificationChannels: liveApi.fetchNotificationChannels };
});

vi.mock("../src/api/events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/events")>();
  return { ...actual, subscribeToEvents: liveApi.subscribeToEvents };
});

vi.mock("../src/api/uiPreferences", () => ({
  DEFAULT_SEAT_OBSERVATION_INTERVAL_SECONDS: 5,
  MIN_SEAT_OBSERVATION_INTERVAL_SECONDS: 1,
  MAX_SEAT_OBSERVATION_INTERVAL_SECONDS: 600,
  fetchUiPreferences: vi.fn().mockResolvedValue({
    seatObservationIntervalSeconds: 5,
    updatedAt: "2026-07-31T00:00:00Z",
  }),
  updateUiPreferences: vi.fn(),
}));

vi.mock("../src/api/providerAccounts", () => ({
  fetchProviderAccounts: providerAccountsApi.fetchProviderAccounts,
  saveProviderAccount: providerAccountsApi.saveProviderAccount,
  deleteProviderAccount: providerAccountsApi.deleteProviderAccount,
}));

import { App } from "../src/App";

describe("App live data synchronization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    liveApi.getAuthStatus.mockResolvedValue({
      configured: true,
      authenticated: true,
      registration_allowed: false,
    });
    liveApi.fetchNotificationChannels.mockResolvedValue([]);
    providerAccountsApi.fetchProviderAccounts.mockResolvedValue([]);
  });

  it("uses the live collection outside history and reloads the full collection for reservations", async () => {
    liveApi.fetchWatches.mockResolvedValue([]);
    liveApi.subscribeToEvents.mockReturnValue(() => undefined);
    render(<App />);

    await waitFor(() => {
      expect(liveApi.fetchWatches).toHaveBeenCalledWith({ view: "live" });
    });
    expect(liveApi.subscribeToEvents).toHaveBeenCalledTimes(1);

    const newWaitButton = screen.getAllByRole("button", { name: "새 대기" })[0];
    if (!newWaitButton) throw new Error("새 대기 탐색 버튼을 찾지 못했습니다.");
    fireEvent.click(newWaitButton);
    await screen.findByRole("heading", { name: "어디로 떠나세요?" });
    await act(async () => Promise.resolve());
    expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1);
    expect(liveApi.subscribeToEvents).toHaveBeenCalledTimes(1);

    const reservationsButton = screen.getAllByRole("button", { name: "내 예약" })[0];
    if (!reservationsButton) throw new Error("내 예약 탐색 버튼을 찾지 못했습니다.");
    fireEvent.click(reservationsButton);
    await screen.findByRole("heading", { name: "내 예약", level: 1 });
    await waitFor(() => {
      expect(liveApi.fetchWatches).toHaveBeenCalledWith({ view: "all" });
    });
    expect(liveApi.subscribeToEvents).toHaveBeenCalledTimes(2);

    const homeButton = screen.getAllByRole("button", { name: "홈" })[0];
    if (!homeButton) throw new Error("홈 탐색 버튼을 찾지 못했습니다.");
    fireEvent.click(homeButton);
    await screen.findByText("활동 중인 대기");
    await waitFor(() => {
      expect(liveApi.fetchWatches).toHaveBeenLastCalledWith({ view: "live" });
    });
    expect(liveApi.subscribeToEvents).toHaveBeenCalledTimes(3);
  });

  it("opens the existing notification center from the mobile bell even when empty", async () => {
    liveApi.fetchWatches.mockResolvedValue([]);
    liveApi.subscribeToEvents.mockReturnValue(() => undefined);
    render(<App />);

    const bell = await screen.findByRole("button", { name: "실시간 알림 0건 열기" });
    expect(bell.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(bell);

    expect(bell.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("region", { name: "실시간 알림" })).toBeTruthy();
    expect(screen.getByText("새 실시간 알림이 없습니다.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "실시간 알림 0건 닫기" }));
    expect(screen.queryByRole("region", { name: "실시간 알림" })).toBeNull();
  });

  it("coalesces an SSE burst into one canonical data reload", async () => {
    let onEvent: (() => void) | undefined;
    liveApi.fetchWatches.mockResolvedValue([]);
    liveApi.subscribeToEvents.mockImplementation((handler: () => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);

    expect(await screen.findByText("활동 중인 대기")).toBeTruthy();
    await waitFor(() => {
      expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1);
      expect(liveApi.fetchNotificationChannels).toHaveBeenCalledTimes(1);
    });

    act(() => {
      for (let index = 0; index < 100; index += 1) onEvent?.();
    });

    await waitFor(() => {
      expect(liveApi.fetchWatches).toHaveBeenCalledTimes(2);
      expect(liveApi.fetchNotificationChannels).toHaveBeenCalledTimes(1);
    });
    await new Promise((resolve) => window.setTimeout(resolve, 100));
    expect(liveApi.fetchWatches).toHaveBeenCalledTimes(2);
    expect(liveApi.fetchNotificationChannels).toHaveBeenCalledTimes(1);
  });

  it("reuses the watches coordinator and keeps a fast refresh visible for one rotation", async () => {
    liveApi.fetchWatches.mockResolvedValue([]);
    liveApi.subscribeToEvents.mockReturnValue(() => undefined);

    render(<App />);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1));

    const refreshButton = screen.getByRole("button", { name: "활동 중인 대기 새로고침" });
    await waitFor(
      () => expect(refreshButton.getAttribute("aria-busy")).toBe("false"),
      { timeout: 1_500 },
    );
    fireEvent.click(refreshButton);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(2));
    expect(refreshButton.getAttribute("aria-busy")).toBe("true");
    expect(liveApi.fetchNotificationChannels).toHaveBeenCalledTimes(1);

    await new Promise((resolve) => window.setTimeout(resolve, 300));
    expect(refreshButton.getAttribute("aria-busy")).toBe("true");
    await waitFor(
      () => expect(refreshButton.getAttribute("aria-busy")).toBe("false"),
      { timeout: 1_200 },
    );
    expect(screen.getByRole("status").textContent).toMatch(/^최근 갱신 \d{2}:\d{2}:\d{2}$/);
  });

  it("queues only later watching-to-seat-found transitions and ignores initial matches", async () => {
    let onEvent: (() => void) | undefined;
    const watch = (id: string, status: string, train: string) => ({
      id,
      provider: "KORAIL",
      route: "서울 → 부산",
      train,
      date: "8월 1일 (토)",
      departure: "12:00",
      arrival: "14:30",
      status,
      statusLabel: status === "seat_found" ? "좌석 발견" : "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 매진 · 공식 관측 12:00",
      lastCheckedAt: null,
      lastCheckedLabel: "최근 확인 기록 없음",
      seatFoundObservation: status === "seat_found" ? {
        kind: "official_provider",
        observedAt: "2026-07-31T03:45:00Z",
        observedLabel: "최근 확인 12:45",
      } : null,
    });
    const initial = [
      watch("existing", "seat_found", "KTX 001"),
      watch("first", "watching", "KTX 003"),
      watch("second", "scheduled", "KTX 005"),
    ];
    const transitioned = [
      watch("existing", "seat_found", "KTX 001"),
      watch("first", "seat_found", "KTX 003"),
      watch("second", "seat_found", "KTX 005"),
    ];
    liveApi.fetchWatches.mockResolvedValueOnce(initial).mockResolvedValue(transitioned);
    liveApi.subscribeToEvents.mockImplementation((handler: () => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("region", { name: "실시간 알림" })).toBeNull();

    act(() => { onEvent?.(); });
    const center = await screen.findByRole("region", { name: "실시간 알림" });
    fireEvent.click(within(center).getByRole("button", { name: "실시간 알림 펼치기" }));
    expect(within(center).getByText("좌석 발견").nextElementSibling?.textContent).toBe("2건");
    expect(center.textContent).toContain("KTX 003");
    fireEvent.click(within(center).getByRole("button", { name: "추가 1건 보기" }));
    expect(center.textContent).toContain("KTX 005");
    fireEvent.click(within(center).getByRole("button", { name: "좌석 발견 2건 모두 닫기" }));
    expect(within(center).getByText("새 실시간 알림이 없습니다.")).toBeTruthy();

    act(() => { onEvent?.(); });
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(3));
    expect(within(center).getByText("새 실시간 알림이 없습니다.")).toBeTruthy();
  });

  it("removes a stale booking action and announces the availability loss once", async () => {
    let onEvent: (() => void) | undefined;
    const available = {
      id: "watch-one",
      provider: "KORAIL",
      route: "대전 → 서울",
      train: "KTX 022",
      date: "8월 1일 (토)",
      departure: "10:58",
      arrival: "11:53",
      status: "seat_found",
      statusLabel: "좌석 발견",
      seatClass: "first",
      seatClassLabel: "특실",
      seatEvidenceLabel: "특실 · 예매 가능 · 최근 관측 12:45",
      lastCheckedAt: "2026-07-31T03:45:00Z",
      lastCheckedLabel: "최근 확인 12:45",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-07-31T03:45:00Z",
        observedLabel: "최근 확인 12:45",
      },
    };
    const soldOut = {
      ...available,
      status: "watching",
      statusLabel: "감시 중",
      seatEvidenceLabel: "특실 · 매진 · 최근 관측 12:46",
      lastCheckedAt: "2026-07-31T03:46:00Z",
      lastCheckedLabel: "최근 확인 12:46",
      seatFoundObservation: null,
    };
    liveApi.fetchWatches
      .mockResolvedValueOnce([available])
      .mockResolvedValue([soldOut]);
    liveApi.subscribeToEvents.mockImplementation((handler: () => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);
    expect(await screen.findByRole("button", { name: /예매 전 안내 열기/ })).toBeTruthy();

    act(() => { onEvent?.(); });
    const center = await screen.findByRole("region", { name: "실시간 알림" });
    fireEvent.click(within(center).getByRole("button", { name: "실시간 알림 펼치기" }));
    expect(within(center).getByText("예매 가능 좌석이 사라져 다시 감시 중입니다"))
      .toBeTruthy();
    expect(within(center).getByText("KORAIL · KTX 022 · 특실")).toBeTruthy();
    expect(within(center).getByText("8월 1일 (토) · 대전 → 서울 · 10:58 → 11:53"))
      .toBeTruthy();
    expect(screen.queryByRole("button", { name: /예매 전 안내 열기/ })).toBeNull();

    act(() => { onEvent?.(); });
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(3));
    expect(within(center).getAllByText("예매 가능 좌석이 사라져 다시 감시 중입니다"))
      .toHaveLength(1);
  });

  it("shows truthful reservation steps and replaces failure with a monitoring-resumed notice", async () => {
    let onEvent: ((event?: unknown) => void) | undefined;
    const base = {
      id: "reservation-progress",
      provider: "KORAIL",
      route: "대전 → 서울",
      train: "KTX 038",
      date: "8월 3일 (월)",
      departure: "14:35",
      arrival: "15:39",
      statusLabel: "좌석 발견",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 예매 가능 · 공식 관측 14:30",
      lastCheckedAt: "2026-08-03T05:30:00Z",
      lastCheckedLabel: "최근 확인 14:30",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-08-03T05:30:00Z",
        observedLabel: "최근 확인 14:30",
      },
      reservationCandidateContexts: {
        "candidate-attempted": {
          train: "KTX 240",
          seatClassLabel: "특실",
          date: "8월 3일 (월)",
          departure: "15:11",
          arrival: "16:22",
        },
      },
    };
    const seatFound = { ...base, status: "seat_found" };
    const reserving = {
      ...base,
      status: "reserving",
      statusLabel: "예매 진행 중",
      seatFoundObservation: null,
    };
    const resumed = {
      ...base,
      status: "watching",
      statusLabel: "감시 중",
      seatFoundObservation: null,
    };
    liveApi.fetchWatches
      .mockResolvedValueOnce([seatFound])
      .mockResolvedValueOnce([reserving])
      .mockResolvedValue([resumed]);
    liveApi.subscribeToEvents.mockImplementation((handler: (event?: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1));

    act(() => { onEvent?.(); });
    const center = await screen.findByRole("region", { name: "실시간 알림" });
    fireEvent.click(within(center).getByRole("button", { name: "실시간 알림 펼치기" }));
    expect(within(center).getByText("예매를 진행하고 있습니다")).toBeTruthy();
    expect(within(center).getByText("KORAIL · KTX 038 · 일반실")).toBeTruthy();
    expect(within(center).getByText(
      "8월 3일 (월) · 대전 → 서울 · 14:35 → 15:39 · 세부 단계는 철도사 결과 수신 후 표시됩니다.",
    ))
      .toBeTruthy();
    expect(within(center).getByText("자동 예매 요청 시작")).toBeTruthy();
    expect(within(center).getByText("철도사 응답·공식 결과 대기")).toBeTruthy();

    act(() => { onEvent?.({ event_type: "watch.status_changed" }); });
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(3));
    expect(screen.queryByText("예매에 실패해 다시 감시 중입니다")).toBeNull();

    act(() => {
      onEvent?.({
        event_type: "watch.reservation_result",
        aggregate_id: "reservation-progress",
        payload: {
          watch_id: "reservation-progress",
          candidate_id: "candidate-attempted",
          outcome: "not_available",
          retryable: true,
          manual_check_required: false,
          retry_condition: "new_availability_episode",
        },
      });
    });
    expect(await within(center).findByText("좌석이 사라져 다시 감시 중입니다")).toBeTruthy();
    expect(within(center).getByText("KORAIL · KTX 240 · 특실")).toBeTruthy();
    expect(within(center).getByText(/8월 3일 \(월\) · 대전 → 서울 · 15:11 → 16:22/))
      .toBeTruthy();
    expect(within(center).getByText("감시·재예매 대기")).toBeTruthy();
    expect(within(center).getByText(/좌석이 다시 확인되면 예매를 다시 시도합니다/))
      .toBeTruthy();
  });

  it("removes a stale sticky booking action when the live canonical list drops its watch", async () => {
    let onEvent: ((event?: unknown) => void) | undefined;
    const reserving = {
      id: "stale-reservation-progress",
      provider: "KORAIL",
      route: "대전 → 서울",
      train: "KTX 038",
      date: "8월 3일 (월)",
      departure: "14:35",
      arrival: "15:39",
      status: "reserving",
      statusLabel: "예매 진행 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 예매 진행 중",
      lastCheckedAt: "2026-08-03T05:30:00Z",
      lastCheckedLabel: "최근 확인 14:30",
      seatFoundObservation: null,
    };
    liveApi.fetchWatches
      .mockResolvedValueOnce([reserving])
      .mockResolvedValue([]);
    liveApi.subscribeToEvents.mockImplementation((handler: (event?: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1));
    const center = await screen.findByRole("region", { name: "실시간 알림" });
    fireEvent.click(within(center).getByRole("button", { name: "실시간 알림 펼치기" }));
    expect(within(center).getByText("예매를 진행하고 있습니다")).toBeTruthy();

    act(() => onEvent?.({ event_type: "watch.status_changed" }));
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(within(center).queryByText("예매를 진행하고 있습니다")).toBeNull();
    });
    expect(within(center).getByText("새 실시간 알림이 없습니다.")).toBeTruthy();
    expect(screen.queryByText("결제가 완료되었습니다")).toBeNull();
    expect(screen.queryByText("예매에 실패했습니다")).toBeNull();
    expect(screen.queryByText("예매 결과를 직접 확인해야 합니다")).toBeNull();
  });

  it("shows fast attempted and result SSE stages even when REST never exposes reserving", async () => {
    let onEvent: ((event?: unknown) => void) | undefined;
    const watching = {
      id: "fast-reservation",
      provider: "KORAIL",
      route: "대전 → 서울",
      train: "9248",
      date: "8월 4일 (화)",
      departure: "17:50",
      arrival: "18:58",
      status: "watching",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 매진 · 공식 관측 21:09",
      seatFoundObservation: null,
      reservationPolicy: "reserve_once_before_payment",
      reservationCandidateContexts: {
        candidate: {
          train: "9248",
          seatClassLabel: "일반실",
          date: "8월 4일 (화)",
          departure: "17:50",
          arrival: "18:58",
        },
      },
    };
    liveApi.fetchWatches.mockResolvedValue([watching]);
    liveApi.subscribeToEvents.mockImplementation((handler: (event?: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1));

    act(() => {
      onEvent?.({
        id: "attempt-fast",
        event_type: "watch.reservation_attempted",
        aggregate_id: watching.id,
        created_at: "2026-08-03T12:09:45Z",
        payload: {
          watch_id: watching.id,
          candidate_id: "candidate",
          outcome: "pending",
          seat_detected_at: "2026-08-03T12:09:44.500Z",
          attempt_started_at: "2026-08-03T12:09:45Z",
        },
      });
    });
    expect(await screen.findByText("예매를 진행하고 있습니다")).toBeTruthy();
    const center = screen.getByRole("region", { name: "실시간 알림" });
    fireEvent.click(within(center).getByRole("button", { name: "자세히" }));
    expect(screen.getByText("자동 예매 요청 시작").closest("li")?.textContent)
      .toContain("21:09:45감지 후 0.5초");
    expect(screen.queryByLabelText("예매 작업 시간")).toBeNull();

    act(() => {
      onEvent?.({
        id: "progress-fast",
        event_type: "watch.reservation_progressed",
        aggregate_id: watching.id,
        created_at: "2026-08-03T12:09:46.200Z",
        payload: {
          watch_id: watching.id,
          candidate_id: "candidate",
          attempt_id: "attempt-fast-id",
          attempt_sequence: 1,
          seat_detected_at: "2026-08-03T12:09:44.500Z",
          attempt_started_at: "2026-08-03T12:09:45Z",
          stage: "authenticated_session_ready",
          occurred_at: "2026-08-03T12:09:46.100Z",
          progress_stages: [
            {
              stage: "authenticated_session_ready",
              occurred_at: "2026-08-03T12:09:46.100Z",
            },
          ],
        },
      });
    });
    expect(await screen.findByText("로그인 세션 확인")).toBeTruthy();
    expect(screen.getByText("로그인 세션 확인").closest("li")?.textContent)
      .toContain("21:09:46이전 단계 후 1.1초");
    expect(screen.getByText("철도사 응답·공식 결과 대기").closest("li")?.className)
      .toContain("toast-step-active");
    expect(screen.queryByText("객실 등급 선택")).toBeNull();
    expect(screen.queryByLabelText("예매 작업 시간")).toBeNull();

    const canonicalReadsBeforeResult = liveApi.fetchWatches.mock.calls.length;
    act(() => {
      onEvent?.({
        id: "result-observed-246",
        event_type: "watch.reservation_result",
        aggregate_id: watching.id,
        created_at: "2026-08-10T13:01:21.646Z",
        payload: {
          watch_id: watching.id,
          candidate_id: "candidate",
          attempt_id: "attempt-246",
          attempt_sequence: 246,
          seat_detected_at: null,
          attempt_started_at: "2026-08-10T13:01:13.901Z",
          attempt_finished_at: "2026-08-10T13:01:21.618Z",
          outcome: "payment_required",
          progress_stages: [
            {
              stage: "authenticated_session_ready",
              occurred_at: "2026-08-10T13:01:18.506Z",
            },
            { stage: "target_rechecked", occurred_at: "2026-08-10T13:01:19.651Z" },
            { stage: "seat_selected", occurred_at: "2026-08-10T13:01:19.775Z" },
            { stage: "reservation_requested", occurred_at: "2026-08-10T13:01:19.799Z" },
          ],
        },
      });
    });
    expect(await screen.findByText("결제 직전까지 예매되었습니다")).toBeTruthy();
    expect(screen.queryByText("예매를 진행하고 있습니다")).toBeNull();
    expect(screen.getByText("자동 예매 요청 시작").closest("li")?.textContent)
      .toContain("22:01:13");
    expect(screen.getByText("로그인 세션 확인").closest("li")?.textContent)
      .toContain("22:01:18이전 단계 후 4.6초");
    expect(screen.getByText("검색 결과·열차 재확인").closest("li")?.textContent)
      .toContain("22:01:19이전 단계 후 1.1초");
    expect(screen.getByText("객실 등급 선택").closest("li")?.textContent)
      .toContain("22:01:19이전 단계 후 0.1초");
    expect(screen.getByText("예약 요청").closest("li")?.textContent)
      .toContain("22:01:19이전 단계 후 0.0초");
    expect(screen.getByText("공식 결과 확인").closest("li")?.textContent)
      .toContain("22:01:21이전 단계 후 1.8초");
    expect(screen.getByLabelText("예매 작업 시간").textContent)
      .toContain("시작 22:01:13전체 7.7초");
    expect(screen.getByText("공식 결제 필요").closest("li")?.textContent)
      .toContain("22:01:21");
    await waitFor(() => {
      expect(liveApi.fetchWatches.mock.calls.length).toBeGreaterThan(canonicalReadsBeforeResult);
    });
    expect(screen.getByText("예약 요청")).toBeTruthy();
  });

  it("replaces a persistent auth-required notice after login recovery resumes monitoring", async () => {
    let onEvent: (() => void) | undefined;
    const base = {
      id: "auth-recovery",
      provider: "KORAIL",
      route: "대전 → 서울",
      train: "KTX 055",
      date: "8월 2일 (일)",
      departure: "18:13",
      arrival: "19:17",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 매진 · 공식 관측 17:00",
      seatFoundObservation: null,
    };
    liveApi.fetchWatches
      .mockResolvedValueOnce([{ ...base, status: "watching", statusLabel: "감시 중" }])
      .mockResolvedValueOnce([{ ...base, status: "auth_required", statusLabel: "로그인 필요" }])
      .mockResolvedValue([{ ...base, status: "watching", statusLabel: "감시 중" }]);
    liveApi.subscribeToEvents.mockImplementation((handler: () => void) => {
      onEvent = handler;
      return () => undefined;
    });

    render(<App />);
    await waitFor(() => expect(liveApi.fetchWatches).toHaveBeenCalledTimes(1));

    act(() => { onEvent?.(); });
    expect(await screen.findByText("로그인 확인이 필요합니다")).toBeTruthy();
    const center = screen.getByRole("region", { name: "실시간 알림" });
    fireEvent.click(within(center).getByRole("button", { name: "자세히" }));
    expect(screen.getByText(/로그인 확인 후 감시를 재개합니다/)).toBeTruthy();

    act(() => { onEvent?.(); });
    expect(await screen.findByText("로그인 확인이 완료되어 감시를 재개합니다")).toBeTruthy();
    expect(screen.queryByText("로그인 확인이 필요합니다")).toBeNull();
    await waitFor(() => expect(providerAccountsApi.fetchProviderAccounts).toHaveBeenCalledTimes(3));
  });

  it("uses the latest authenticated provider-account status without changing an auth-required watch", async () => {
    const authRequired = {
      id: "auth-required",
      provider: "KORAIL",
      route: "서울 → 부산",
      train: "KTX 085",
      date: "8월 2일 (일)",
      departure: "14:11",
      arrival: "16:52",
      status: "auth_required",
      statusLabel: "로그인 필요",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 등록 당시 매진",
      seatFoundObservation: null,
    };
    liveApi.fetchWatches.mockResolvedValue([authRequired]);
    liveApi.subscribeToEvents.mockReturnValue(() => undefined);
    providerAccountsApi.fetchProviderAccounts.mockResolvedValue([{
      provider: "KORAIL",
      configured: true,
      enabled: true,
      loginMethod: "membership_number",
      maskedLoginId: "de***",
      credentialVersion: 1,
      lastAuthStatus: "authenticated",
      lastAuthenticatedAt: "2026-08-01T15:00:00Z",
      updatedAt: "2026-08-01T15:00:00Z",
    }]);

    render(<App />);

    expect(await screen.findByText("계정 확인됨 · 이전 인증 상태 확인")).toBeTruthy();
    expect(screen.getByText("대기 확인 필요")).toBeTruthy();
    expect(screen.queryByText("이 대기에는 이전 인증 실패 기록이 남아 있습니다. 철도 계정과 대기 상태를 확인하세요.")).toBeNull();
  });
});
