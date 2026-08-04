import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, Home, NewWait, OfficialHandoff, PaymentHero, WatchRow, hasObservedSeatEvidence, isActiveWatch } from "../src/App.jsx";
import { normalizeSeatClasses } from "../src/api/seatClasses";
import { SeatClassPanel } from "../src/features/new-wait/TrainResultCard";
import {
  AppToast,
  IMPORTANT_TOAST_AUTO_CLOSE_MS,
  TOAST_AUTO_CLOSE_MS,
} from "../src/shared/ui/AppToast";

function strictKorailSearchUrl() {
  const params = new URLSearchParams({
    srtCheckYn: "N", ebizCrossCheck: "N", adjStnScdlOfrFlg: "N",
    adjStnScdlOfrFlg2: "N", rtYn: "N", txtMenuId: "11", radJobId: "1",
    searchType: "GENERAL", txtGoStart: "서울", txtGoEnd: "부산",
    txtGoStartCode: "0001", txtGoEndCode: "0020", txtGoAbrdDt: "20260801",
    txtGoHour: "140000", txtPsgFlg_1: "1", txtPsgFlg_2: "0",
    txtPsgFlg_3: "0", txtPsgFlg_4: "0", txtPsgFlg_5: "0", txtPsgFlg_8: "0",
    selGoSeat1: "015", txtSeatAttCd_4: "015", txtTrnGpCd: "100",
    tkTripChgQryFlg: "Y", txtWkndUseFlg: "Y",
  });
  return `https://www.korail.com/ticket/search/list?${params.toString()}`;
}

function seatWaitButton(trainName, seatName = "일반실로 대기") {
  const card = screen.getByRole("article", { name: trainName });
  return within(card).getByRole("button", { name: seatName });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RailWait responsive core flow", () => {
  it.each([
    "대기를 일시정지했습니다.",
    "대기를 일시정지하지 못했습니다.",
    "KTX 001 일반실 좌석을 찾았습니다.",
    "KTX 001 일반실 자동 예매를 진행하고 있습니다.",
  ])("keeps every global toast message readable for thirty seconds: %s", (message) => {
    vi.useFakeTimers();
    try {
      const onClose = vi.fn();
      render(<AppToast notice={{ id: `toast-${message}`, title: message }} onClose={onClose} />);

      act(() => { vi.advanceTimersByTime(TOAST_AUTO_CLOSE_MS - 1); });
      expect(onClose).not.toHaveBeenCalled();

      act(() => { vi.advanceTimersByTime(1); });
      expect(onClose).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets a user dismiss a global toast before its automatic timeout", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<AppToast notice={{ id: "toast-dismiss", title: "대기를 등록했습니다." }} onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "알림 닫기" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows detailed reservation progress for sixty seconds without resetting on a parent rerender", () => {
    vi.useFakeTimers();
    try {
      const onClose = vi.fn();
      const notice = {
        id: "reservation-progress",
        title: "예매를 진행하고 있습니다",
        meta: "KORAIL · KTX 038 · 일반실",
        description: "8월 3일 (월) · 대전 → 서울 · 14:35 → 15:39",
        steps: [
          { label: "좌석 발견", state: "completed" },
          { label: "공식 예매 요청", state: "active" },
          { label: "결과 확인", state: "pending" },
        ],
      };
      const view = render(<AppToast notice={notice} onClose={onClose} />);
      expect(screen.getByText(notice.meta)).toBeTruthy();
      expect(screen.getByText(notice.description)).toBeTruthy();
      expect(screen.getByRole("list", { name: "예매 진행 단계" })).toBeTruthy();

      act(() => { vi.advanceTimersByTime(30_000); });
      view.rerender(<AppToast notice={notice} onClose={() => onClose()} />);
      act(() => { vi.advanceTimersByTime(IMPORTANT_TOAST_AUTO_CLOSE_MS - 30_001); });
      expect(onClose).not.toHaveBeenCalled();
      act(() => { vi.advanceTimersByTime(1); });
      expect(onClose).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders user-confirmed seat states only while their fixed-source evidence is fresh", () => {
    const monotonic = vi.spyOn(performance, "now").mockReturnValue(1_000);
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2099-01-01T00:00:00Z"));
    const [confirmed] = normalizeSeatClasses({
      provider: "korail",
      seat_classes: [{
        seat_class: "standard",
        status: "sold_out",
        provenance: {
          kind: "user_confirmed_official_page",
          source: "official-page-user-confirmation",
          observed_at: "2020-01-01T00:00:00Z",
          fresh_until: "2020-01-01T00:05:00Z",
        },
      }],
    });

    expect(hasObservedSeatEvidence(confirmed)).toBe(true);
    monotonic.mockReturnValue(300_999);
    expect(hasObservedSeatEvidence(confirmed)).toBe(true);
    monotonic.mockReturnValue(301_000);
    expect(hasObservedSeatEvidence(confirmed)).toBe(false);
    expect(hasObservedSeatEvidence({
      provenance: { ...confirmed.provenance, fresh_until: confirmed.provenance.observed_at },
    })).toBe(false);
    expect(hasObservedSeatEvidence({
      provenance: { ...confirmed.provenance, source: "arbitrary-client-source" },
    })).toBe(false);
  });

  it("changes an authenticated active ticket between monitoring and one-time reservation", async () => {
    const user = userEvent.setup();
    const onChangeReservationPolicy = vi.fn();
    const watch = {
      id: "watch-policy-1",
      provider: "KORAIL",
      train: "KTX 26",
      route: "대전 → 서울",
      departure: "12:00",
      arrival: "13:04",
      date: "8월 3일 (월)",
      status: "watching",
      statusLabel: "감시 중",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 매진 · 공식 관측",
      reservationPolicy: "notify_only",
      accountAuthStatus: "authenticated",
    };

    const { rerender } = render(
      <WatchRow
        watch={watch}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onChangeReservationPolicy={onChangeReservationPolicy}
      />,
    );

    const policySwitch = screen.getByRole("switch", {
      name: "KTX 26 일반실 좌석 재발견마다 자동 예매 설정",
    });
    expect(policySwitch.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText("감시만")).toBeTruthy();
    await user.click(policySwitch);
    expect(onChangeReservationPolicy).toHaveBeenCalledWith(
      watch.id,
      "reserve_once_before_payment",
    );

    rerender(
      <WatchRow
        watch={{ ...watch, reservationPolicy: "reserve_once_before_payment" }}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onChangeReservationPolicy={onChangeReservationPolicy}
      />,
    );
    expect(screen.getByRole("switch", {
      name: "KTX 26 일반실 좌석 재발견마다 자동 예매 설정",
    }).getAttribute("aria-checked"))
      .toBe("true");
    expect(screen.getByText("좌석 재발견마다 자동 예매")).toBeTruthy();
  });

  it("requires a verified rail account before enabling automatic reservation", () => {
    render(
      <WatchRow
        watch={{
          id: "watch-policy-2",
          provider: "SRT",
          train: "SRT 312",
          route: "대전 → 수서",
          departure: "09:12",
          arrival: "10:17",
          date: "8월 3일 (월)",
          status: "watching",
          statusLabel: "감시 중",
          seatClass: "standard",
          seatClassLabel: "일반실",
          seatEvidenceLabel: "일반실 · 매진 · 공식 관측",
          reservationPolicy: "notify_only",
          accountAuthStatus: "not_checked",
        }}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onChangeReservationPolicy={vi.fn()}
        onOpenRailAccounts={vi.fn()}
      />,
    );

    expect(screen.getByRole("switch", {
      name: "SRT 312 일반실 좌석 재발견마다 자동 예매 설정",
    }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "로그인 필요" })).toBeTruthy();
  });

  it("locks the policy switch once a reservation attempt is in progress", () => {
    render(
      <WatchRow
        watch={{
          id: "watch-policy-reserving",
          provider: "KORAIL",
          train: "KTX 101",
          route: "서울 → 부산",
          departure: "10:00",
          arrival: "12:30",
          date: "8월 3일 (월)",
          status: "reserving",
          statusLabel: "예매 진행 중",
          seatClass: "standard",
          seatClassLabel: "일반실",
          seatEvidenceLabel: "일반실 · 예매 가능 · 공식 관측",
          reservationPolicy: "reserve_once_before_payment",
          accountAuthStatus: "authenticated",
        }}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onChangeReservationPolicy={vi.fn()}
      />,
    );

    const policySwitch = screen.getByRole("switch", {
      name: "KTX 101 일반실 좌석 재발견마다 자동 예매 설정",
    });
    expect(policySwitch.disabled).toBe(true);
    expect(policySwitch.title).toContain("예약 시도가 시작된 뒤");
  });

  it("describes password authentication without legacy Passkey copy", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getAllByRole("button", { name: "설정" })[0]);
    await user.click(screen.getByRole("button", { name: /보안/ }));

    expect(screen.getByText("관리자 ID·비밀번호 로그인 활성화")).toBeTruthy();
    expect(screen.getByText("비밀번호는 Argon2id 단방향 해시로 저장됩니다.")).toBeTruthy();
    expect(screen.queryByText(/Passkey|복구 코드/i)).toBeNull();
  });

  it("shows payment-required state separately from active monitoring", () => {
    render(<App />);

    expect(screen.getAllByText("결제 필요").length).toBeGreaterThan(0);
    expect(screen.getByText("결제기한 미제공")).toBeTruthy();
    expect(screen.getByRole("button", { name: /공식 결제 열기/ })).toBeTruthy();
    expect(screen.getByText("활동 중인 대기")).toBeTruthy();
  });

  it("does not invent a payment deadline when the API provides null", () => {
    render(<PaymentHero watch={{
      provider: "KORAIL",
      train: "KTX 085",
      origin: "서울",
      destination: "부산",
      departure: "14:11",
      arrival: "16:52",
      date: "8월 1일",
      payment_deadline: null,
      official_booking_url: "https://www.letskorail.com",
    }} onOfficialPayment={() => {}} />);

    expect(screen.getByText("결제기한 미제공")).toBeTruthy();
    expect(screen.queryByText("--:--:--")).toBeNull();
    expect(screen.queryByText(/분 남음/)).toBeNull();
  });

  it("registers a selected seat immediately and updates the active wait list without a confirmation step", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getAllByRole("button", { name: "새 대기" })[0]);
    expect(screen.getByRole("heading", { name: "어디로 떠나세요?" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /다음/ }));
    expect(screen.getByRole("heading", { name: "어떤 좌석을 찾을까요?" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /다음/ }));
    expect(screen.getByRole("heading", { name: "공식 시간표에서 관심 열차를 고르세요" })).toBeTruthy();

    await screen.findByRole("article", { name: "KTX 033" });
    const train = seatWaitButton("KTX 033");
    expect(screen.queryByRole("button", { name: "등록 완료" })).toBeNull();
    expect(screen.queryByRole("list", { name: "등록된 열차와 좌석 등급" })).toBeNull();
    expect(screen.getByRole("button", { name: "시간표 새로고침" })).toBeTruthy();
    await user.click(train);
    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: "홈" })[0]);
    expect(screen.getByText("활동 중인 대기")).toBeTruthy();
    expect(screen.getByLabelText("전체 2건 모두 표시 중")).toBeTruthy();

    await user.click(screen.getAllByRole("button", { name: "새 대기" })[0]);
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    const persistedCancel = await screen.findByRole("button", { name: "일반실 대기 취소" });
    expect(persistedCancel.getAttribute("aria-pressed")).toBe("true");
    expect(persistedCancel.closest(".seat-class-panel").className).toContain("is-selected");

    await user.click(persistedCancel);
    await waitFor(() => expect(seatWaitButton("KTX 033").getAttribute("aria-pressed")).toBe("false"));
  });

  it("keeps the legacy NewWait export as the concrete official handoff adapter", () => {
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "새 대기 만들기" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /KTX · KORAIL/ })).toBeTruthy();
  });

  it("does not render actions for a not-offered seat class", () => {
    const train = {
      id: "KORAIL:KTX 404:2026-08-01T14:11:00+09:00",
      provider: "KORAIL",
      name: "KTX 404",
      origin: "서울",
      destination: "부산",
      departure: "14:11",
      arrival: "16:52",
      departure_at: "2026-08-01T14:11:00+09:00",
      seat_classes: [],
    };
    const seat = {
      seat_class: "first",
      status: "not_offered",
      provenance: { kind: "mock", source: "mock", observed_at: "2026-08-01T00:00:00Z" },
      actions: [{ kind: "official_check", url: "https://www.korail.com/ticket/search" }],
    };

    render(<SeatClassPanel train={train} seat={seat} registration={{ status: "idle" }} onChooseSeat={vi.fn()} onRetryProvider={vi.fn()} />);

    expect(screen.getByText("미운영")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not promote an official action without a string URL to the fallback handoff", () => {
    render(<SeatClassPanel
      train={{
        id: "KORAIL:26:2026-08-02T12:00:00+09:00",
        provider: "KORAIL",
        name: "KTX 26",
        origin: "대전",
        destination: "서울",
        departure: "12:00",
        arrival: "13:04",
      }}
      seat={{
        seat_class: "standard",
        status: "available",
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-01T12:53:00Z",
        },
        actions: [{ kind: "official_check" }],
      }}
      registration={{ status: "idle" }}
      onChooseSeat={vi.fn()}
      officialHandoffComponent={OfficialHandoff}
    />);

    expect(screen.queryByRole("button", { name: /공식 예매 전 안내/ })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("registers an authenticated available seat for one-time reservation instead of handoff", async () => {
    const user = userEvent.setup();
    const onChooseSeat = vi.fn();
    const train = {
      id: "KORAIL:26:2026-08-02T12:00:00+09:00",
      provider: "KORAIL",
      name: "KTX 26",
      origin: "대전",
      destination: "서울",
      departure: "12:00",
      arrival: "13:04",
    };
    const seat = {
      seat_class: "standard",
      status: "available",
      provenance: {
        kind: "official_provider",
        source: "korail-official-page-browser",
        observed_at: "2026-08-01T12:53:00Z",
      },
      actions: [
        { kind: "official_check", url: "https://www.korail.com/ticket/search" },
        { kind: "add_to_watch" },
      ],
    };

    render(<SeatClassPanel
      train={train}
      seat={seat}
      registration={{ status: "idle" }}
      onChooseSeat={onChooseSeat}
      automaticReservationEnabled
    />);

    await user.click(screen.getByRole("button", { name: "일반실 자동 예매" }));
    expect(onChooseSeat).toHaveBeenCalledWith(train.id, "standard");
    expect(screen.queryByRole("button", { name: /공식 예매 전 안내/ })).toBeNull();
  });


  it("shows the persisted one-time policy when an active seat registration is restored", () => {
    render(<SeatClassPanel
      train={{
        id: "KORAIL:26:2026-08-02T12:00:00+09:00",
        provider: "KORAIL",
        name: "KTX 26",
        origin: "대전",
        destination: "서울",
        departure: "12:00",
        arrival: "13:04",
      }}
      seat={{
        seat_class: "standard",
        status: "sold_out",
        provenance: {
          kind: "official_provider",
          source: "korail-official-page-browser",
          observed_at: "2026-08-01T12:53:00Z",
        },
        actions: [{ kind: "add_to_watch" }],
      }}
      registration={{
        status: "active",
        watchId: "watch-26-standard",
        reservationPolicy: "reserve_once_before_payment",
      }}
      onChooseSeat={vi.fn()}
      automaticReservationEnabled
    />);

    expect(screen.getByText("좌석 재발견마다 자동 예매 · 결제 전 중단")).toBeTruthy();
    expect(screen.queryByText("자동 결제")).toBeNull();
  });


  it("hands an official check off without claiming seat or reservation success", async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn().mockResolvedValue(true);
    const train = {
      id: "KORAIL:KTX 085:2026-08-01T14:11:00+09:00",
      provider: "KORAIL",
      name: "KTX 085",
      origin: "서울",
      destination: "부산",
      departure: "14:11",
      departure_at: "2026-08-01T14:11:00+09:00",
      official_booking_url: "https://example.invalid/untrusted",
    };
    window.open.mockClear();
    const { container } = render(<div className="app-shell"><OfficialHandoff train={train} onCopy={onCopy} /></div>);

    const trigger = screen.getByRole("button", { name: "KTX 085 공식 좌석 확인 전 안내 열기" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "KTX 085 공식 좌석 확인 전 안내" });

    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(within(dialog).getByLabelText("선택 열차 요약").textContent).toContain("서울 → 부산");
    expect(dialog.textContent).toContain("좌석 상태는 아직 확인되지 않았습니다");
    expect(dialog.textContent).toContain("좌석 확보나 예약 성공 상태로 바뀌지 않");
    expect(dialog.textContent).toContain("새 탭에서 열립니다");
    expect(dialog.textContent).toContain("접근 제한 화면이 나타나면 자동 재시도하지 않습니다");
    expect(dialog.textContent).toContain("나중에 공식 앱이나 홈페이지에서 직접 확인");
    expect(container.querySelector(".app-shell").inert).toBe(true);

    await user.click(within(dialog).getByRole("button", { name: "여정 복사" }));
    expect(onCopy).toHaveBeenCalledWith(train);
    expect(within(dialog).getByRole("status").textContent).toContain("여정 정보를 복사했습니다");

    await user.click(within(dialog).getByRole("button", { name: /공식 페이지 열기/ }));
    expect(window.open).toHaveBeenCalledWith("https://www.korail.com/ticket/search/general", "_blank", "noopener,noreferrer");
    expect(screen.getByRole("dialog", { name: "KTX 085 공식 좌석 확인 전 안내" })).toBeTruthy();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });

  it("uses the SRT official entry point and traps focus until Escape restores the trigger", async () => {
    const user = userEvent.setup();
    const train = {
      id: "SRT:SRT 327:2026-08-01T14:30:00+09:00",
      provider: "SRT",
      name: "SRT 327",
      origin: "수서",
      destination: "부산",
      departure: "14:30",
      departure_at: "2026-08-01T14:30:00+09:00",
    };
    window.open.mockClear();
    const { container } = render(<div className="app-shell"><OfficialHandoff train={train} onCopy={vi.fn()} /></div>);

    const trigger = screen.getByRole("button", { name: "SRT 327 공식 좌석 확인 전 안내 열기" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "SRT 327 공식 좌석 확인 전 안내" });
    const close = within(dialog).getByRole("button", { name: "공식 좌석 확인 안내 닫기" });
    const official = within(dialog).getByRole("button", { name: /공식 페이지 열기/ });
    await waitFor(() => expect(document.activeElement).toBe(close));

    await user.tab({ shift: true });
    expect(document.activeElement).toBe(official);
    await user.tab();
    expect(document.activeElement).toBe(close);

    await user.click(official);
    expect(window.open).toHaveBeenCalledWith(
      "https://etk.srail.kr/hpg/hra/01/selectScheduleList.do?pageId=TK0101010000",
      "_blank",
      "noopener,noreferrer",
    );

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "SRT 327 공식 좌석 확인 전 안내" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(container.querySelector(".app-shell").inert).toBe(false);
    expect(container.querySelector(".app-shell").getAttribute("aria-hidden")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });

  it("opens a server-built strict KORAIL search URL with conditions prefilled", async () => {
    const user = userEvent.setup();
    const searchUrl = strictKorailSearchUrl();
    const train = {
      id: "KORAIL:KTX 085:2026-08-01T14:11:00+09:00",
      provider: "KORAIL",
      name: "KTX 085",
      origin: "서울",
      destination: "부산",
      departure: "14:11",
      arrival: "16:52",
      departure_at: "2026-08-01T14:11:00+09:00",
      official_search_url: searchUrl,
    };
    window.open.mockClear();
    render(<OfficialHandoff train={train} onCopy={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "KTX 085 공식 좌석 확인 전 안내 열기" }));
    const dialog = screen.getByRole("dialog", { name: "KTX 085 공식 좌석 확인 전 안내" });
    expect(dialog.textContent).toContain("선택한 여정 조건을 공식 검색 화면에 미리 입력합니다");
    expect(dialog.textContent).toContain("특정 열차 선택·좌석 확보·예매 성공을 뜻하지 않습니다");

    await user.click(within(dialog).getByRole("button", { name: /조건 입력하고 공식 페이지 열기/ }));
    expect(window.open).toHaveBeenCalledWith(searchUrl, "_blank", "noopener,noreferrer");
  });

  it("labels a mock seat-found handoff as demo data", async () => {
    const user = userEvent.setup();
    const train = {
      id: "MOCK:KTX 001:2026-08-01T09:00:00+09:00",
      provider: "KORAIL",
      name: "KTX 001",
      origin: "서울",
      destination: "부산",
      departure: "09:00",
      arrival: "11:40",
      departure_at: "2026-08-01T09:00:00+09:00",
    };

    render(
      <OfficialHandoff
        train={train}
        selectedSeatClass="standard"
        onCopy={vi.fn()}
        triggerLabel="예매"
        seatFoundObservation={{
          kind: "mock",
          observedAt: null,
          observedLabel: "최근 확인 기록 없음",
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "KTX 001 일반실 예매 전 안내 열기" }));
    const dialog = screen.getByRole("dialog", { name: "KTX 001 공식 예매 안내" });
    expect(dialog.textContent).toContain("좌석 발견 상태는 UX 검증용 데모입니다");
    expect(dialog.textContent).toContain("실제 좌석이나 예매 가능 상태를 뜻하지 않습니다");
    expect(dialog.textContent).not.toContain("좌석 가능 상태가 최근 관측되었습니다");
  });

  it("uses only provider-owned official hosts and describes the selected seat provenance", async () => {
    const user = userEvent.setup();
    const train = {
      id: "KORAIL:KTX 033:2026-08-01T13:18:00+09:00",
      provider: "KORAIL",
      name: "KTX 033",
      origin: "서울",
      destination: "부산",
      departure: "13:18",
      arrival: "15:59",
      departure_at: "2026-08-01T13:18:00+09:00",
      seat_classes: [
        { seat_class: "standard", provenance: { kind: "official_provider", source: "authorized-test", observed_at: "2026-08-01T00:00:00+09:00" } },
        { seat_class: "first", provenance: { kind: "not_observed", reason: "public_api_not_available" } },
      ],
    };
    window.open.mockClear();
    render(<OfficialHandoff train={train} selectedSeatClass="first" onCopy={vi.fn()} actionUrl="https://evil.example/phishing" />);

    await user.click(screen.getByRole("button", { name: "KTX 033 특실 공식 좌석 확인 전 안내 열기" }));
    const dialog = screen.getByRole("dialog", { name: "KTX 033 공식 좌석 확인 전 안내" });
    expect(dialog.textContent).toContain("좌석 상태는 아직 확인되지 않았습니다");
    expect(dialog.textContent).not.toContain("허가된 좌석 출처의 관측값입니다");
    await user.click(within(dialog).getByRole("button", { name: /공식 페이지 열기/ }));
    expect(window.open).toHaveBeenCalledWith("https://www.korail.com/ticket/search/general", "_blank", "noopener,noreferrer");
  });

  it("keeps a readable journey summary inside the dialog when clipboard copy fails", async () => {
    const user = userEvent.setup();
    const train = {
      id: "KORAIL:KTX 033:2026-08-01T13:18:00+09:00",
      provider: "KORAIL",
      name: "KTX 033",
      origin: "서울",
      destination: "부산",
      departure: "13:18",
      arrival: "15:59",
      departure_at: "2026-08-01T13:18:00+09:00",
    };
    render(<OfficialHandoff train={train} onCopy={vi.fn().mockResolvedValue(false)} />);

    await user.click(screen.getByRole("button", { name: "KTX 033 공식 좌석 확인 전 안내 열기" }));
    const dialog = screen.getByRole("dialog", { name: "KTX 033 공식 좌석 확인 전 안내" });
    await user.click(within(dialog).getByRole("button", { name: "여정 복사" }));

    const alert = within(dialog).getByRole("alert");
    expect(alert.textContent).toContain("자동 복사에 실패했습니다");
    expect(alert.textContent).toContain("2026-08-01 / 서울 → 부산 / KTX 033 / 13:18 출발");
  });

  it("keeps the readable journey summary when clipboard copy rejects", async () => {
    const user = userEvent.setup();
    const train = {
      id: "KORAIL:KTX 033:2026-08-01T13:18:00+09:00",
      provider: "KORAIL",
      name: "KTX 033",
      origin: "서울",
      destination: "부산",
      departure: "13:18",
      arrival: "15:59",
      departure_at: "2026-08-01T13:18:00+09:00",
    };
    render(<OfficialHandoff train={train} onCopy={vi.fn().mockRejectedValue(new Error("clipboard denied"))} />);

    await user.click(screen.getByRole("button", { name: "KTX 033 공식 좌석 확인 전 안내 열기" }));
    const dialog = screen.getByRole("dialog", { name: "KTX 033 공식 좌석 확인 전 안내" });
    await user.click(within(dialog).getByRole("button", { name: "여정 복사" }));

    const alert = within(dialog).getByRole("alert");
    expect(alert.textContent).toContain("자동 복사에 실패했습니다");
    expect(alert.textContent).toContain("2026-08-01 / 서울 → 부산 / KTX 033 / 13:18 출발");
  });

  it("ignores a stale clipboard result after the dialog is closed and reopened", async () => {
    const user = userEvent.setup();
    let resolveFirstCopy;
    const firstCopy = new Promise((resolve) => {
      resolveFirstCopy = resolve;
    });
    const onCopy = vi.fn()
      .mockReturnValueOnce(firstCopy)
      .mockResolvedValueOnce(true);
    const train = {
      id: "KORAIL:KTX 033:2026-08-01T13:18:00+09:00",
      provider: "KORAIL",
      name: "KTX 033",
      origin: "서울",
      destination: "부산",
      departure: "13:18",
      arrival: "15:59",
      departure_at: "2026-08-01T13:18:00+09:00",
    };
    render(<OfficialHandoff train={train} onCopy={onCopy} />);

    const trigger = screen.getByRole("button", { name: "KTX 033 공식 좌석 확인 전 안내 열기" });
    await user.click(trigger);
    let dialog = screen.getByRole("dialog", { name: "KTX 033 공식 좌석 확인 전 안내" });
    await user.click(within(dialog).getByRole("button", { name: "여정 복사" }));
    expect(within(dialog).getByRole("button", { name: "복사 중…" }).disabled).toBe(true);
    expect(screen.getAllByRole("button", { name: "공식 좌석 확인 안내 닫기" })).toHaveLength(1);

    await user.click(within(dialog).getByRole("button", { name: "공식 좌석 확인 안내 닫기" }));
    await user.click(trigger);
    dialog = screen.getByRole("dialog", { name: "KTX 033 공식 좌석 확인 전 안내" });
    await user.click(within(dialog).getByRole("button", { name: "여정 복사" }));
    expect((await within(dialog).findByRole("status")).textContent).toContain("여정 정보를 복사했습니다");

    resolveFirstCopy(false);
    await waitFor(() => expect(within(dialog).getByRole("status").textContent).toContain("여정 정보를 복사했습니다"));
    expect(within(dialog).queryByRole("alert")).toBeNull();
  });


  it("renders actual status details and only offers pause for pausable watches", () => {
    const base = { id: "watch-1", provider: "KORAIL", route: "서울 → 부산", train: "KTX 085", date: "8월 1일", departure: "14:11", arrival: "16:52", seatClass: "standard", seatClassLabel: "일반실", seatEvidenceLabel: "일반실 · 연동 안 됨" };
    const { rerender } = render(<WatchRow watch={{ ...base, status: "scheduled", statusLabel: "대기 등록됨" }} onPause={vi.fn()} onResume={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(/대기 등록됨/).closest(".status-pill")?.className).toContain("status-scheduled");
    expect(screen.getByText("일반실 · 연동 안 됨")).toBeTruthy();
    expect(screen.getByRole("button", { name: "대기 일시정지" })).toBeTruthy();

    rerender(<WatchRow watch={{ ...base, status: "completed", statusLabel: "결제 완료" }} onPause={vi.fn()} onResume={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "대기 일시정지" })).toBeNull();
    expect(isActiveWatch({ status: "completed" })).toBe(false);
    expect(isActiveWatch({ status: "scheduled" })).toBe(true);
  });

  it("offers resume for paused watches and labels cancellation accurately", async () => {
    const user = userEvent.setup();
    const onResume = vi.fn();
    const onCancel = vi.fn();
    const watch = { id: "paused", provider: "KORAIL", route: "서울 → 부산", train: "KTX 085", date: "8월 1일", departure: "14:11", arrival: "16:52", status: "paused", statusLabel: "일시정지", seatClass: "first", seatClassLabel: "특실", seatEvidenceLabel: "특실 · 등록 근거 없음" };
    render(<WatchRow watch={watch} onPause={vi.fn()} onResume={onResume} onCancel={onCancel} />);

    await user.click(screen.getByRole("button", { name: "대기 재개" }));
    await user.click(screen.getByRole("button", { name: "대기 취소" }));
    expect(onResume).toHaveBeenCalledWith("paused");
    expect(onCancel).toHaveBeenCalledWith("paused");
    expect(screen.queryByRole("button", { name: "대기 삭제" })).toBeNull();
  });

  it("opens the existing official handoff from a seat-found home row only", async () => {
    const user = userEvent.setup();
    const seatFound = {
      id: "seat-found",
      provider: "KORAIL",
      origin: "대전",
      destination: "부산",
      route: "대전 → 부산",
      train: "KTX 085",
      travelDate: "2026-08-01",
      date: "8월 1일 (토)",
      departure: "13:05",
      arrival: "14:42",
      status: "seat_found",
      statusLabel: "좌석 발견",
      seatClass: "standard",
      seatClassLabel: "일반실",
      seatEvidenceLabel: "일반실 · 예매 가능 · 공식 관측 12:34",
      officialBookingUrl: "https://evil.example/untrusted",
      seatFoundObservation: {
        kind: "official_provider",
        observedAt: "2026-08-01T03:45:00Z",
        observedLabel: "최근 확인 12:45",
      },
    };
    const watching = {
      ...seatFound,
      id: "watching",
      train: "KTX 087",
      status: "watching",
      statusLabel: "감시 중",
    };
    window.open.mockClear();

    render(
      <div className="app-shell">
        <Home
          watches={[seatFound, watching]}
          paymentWatch={null}
          onNavigate={vi.fn()}
          onPause={vi.fn()}
          onResume={vi.fn()}
          onCancel={vi.fn()}
          onToast={vi.fn()}
        />
      </div>,
    );

    const trigger = screen.getByRole("button", { name: "KTX 085 일반실 예매 전 안내 열기" });
    expect(screen.getAllByText("예매", { selector: "button" })).toHaveLength(1);
    expect(trigger.className).toContain("watch-booking-button");
    expect(trigger.className).toContain("compact");

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "KTX 085 공식 예매 안내" });
    expect(within(dialog).getByLabelText("선택 열차 요약").textContent).toContain("대전 → 부산");
    expect(within(dialog).getByLabelText("선택 열차 요약").textContent).toContain("8월 1일 (토) · 13:05 → 14:42");
    expect(within(dialog).getByLabelText("선택 열차 요약").textContent).toContain("KTX 85 · 일반실");
    expect(dialog.textContent).toContain("좌석 가능 상태가 최근 관측되었습니다");
    expect(dialog.textContent).toContain("일반실 기준 · 최근 확인 12:45");
    expect(dialog.textContent).not.toContain("예매 가능 상태로 확정");
    expect(within(dialog).getByRole("button", { name: "여정 복사" })).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: /공식 페이지 열기/ }));
    expect(window.open).toHaveBeenCalledWith("https://www.korail.com/ticket/search/general", "_blank", "noopener,noreferrer");
  });

  it("navigates from the app shell to the reservations page", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getAllByRole("button", { name: "내 예약" })[0]);

    const reservationsHeading = screen.getByRole("heading", { name: "내 예약", level: 1 });
    expect(reservationsHeading).toBeTruthy();
    expect(within(reservationsHeading.closest(".page")).getByRole("button", { name: "새 대기" })).toBeTruthy();
  });

  it("keeps system diagnostics outside the consumer home screen", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.queryByRole("heading", { name: "로그·진행 상태" })).toBeNull();
    await user.click(screen.getAllByRole("button", { name: "설정" })[0]);
    await user.click(screen.getByRole("button", { name: /로그·진행 상태/ }));
    expect(screen.getByRole("heading", { name: "로그·진행 상태" })).toBeTruthy();
    expect(screen.getByText("데모 데이터")).toBeTruthy();
    expect(screen.queryByText(/실험 자동화/)).toBeNull();
  });
});
