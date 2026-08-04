import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, Home, NewWait, OfficialHandoff, PaymentHero, Reservations, SeatClassPanel, WatchRow, hasObservedSeatEvidence, isActiveWatch } from "../src/App.jsx";
import { ApiError, normalizeSeatClasses } from "../src/api.js";
import {
  AppToast,
  IMPORTANT_TOAST_AUTO_CLOSE_MS,
  TOAST_AUTO_CLOSE_MS,
} from "../src/shared/ui/AppToast";

function response(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

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

function stationCatalog(provider = "korail") {
  return {
    provider,
    source: "TAGO",
    retrieved_at: "2026-07-29T00:00:00Z",
    catalog_scope: "intercity_station_guide_intersection",
    provider_membership: "not_verified_by_source",
    note: "일반·고속열차 여정 선택에 적합한 역 목록입니다.",
    stations: [
      { node_id: "N-SEOUL", name: "서울", city_code: "11", city_name: "서울" },
      { node_id: "N-SUSEO", name: "수서", city_code: "11", city_name: "서울" },
      { node_id: "N-DAEJEON", name: "대전", city_code: "30", city_name: "대전" },
      { node_id: "N-BUSAN", name: "부산", city_code: "26", city_name: "부산" },
    ],
  };
}

function seoulDate(dayOffset = 1) {
  const date = new Date(Date.now() + dayOffset * 24 * 60 * 60 * 1000);
  const parts = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul" }).formatToParts(date);
  const value = (type) => parts.find((part) => part.type === type)?.value;
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function koreanDateLabel(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date(year, month - 1, day));
}

function seatWaitButton(trainName, seatName = "일반실로 대기") {
  const card = screen.getByRole("article", { name: trainName });
  return within(card).getByRole("button", { name: seatName });
}

function expiredEvidenceConflict() {
  const error = new ApiError(
    "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
    409,
    {
      detail: {
        code: "registration_evidence_conflict",
        reason: "expired",
        message: "좌석 등록 근거가 만료되었습니다. 좌석 상태를 다시 조회해 주세요.",
      },
    },
  );
  error.operation = "watch.create";
  return error;
}

function observedSoldOutTimetable(travelDate, evidenceId, overrides = {}) {
  return {
    provider: "korail",
    train_number: "KTX 901",
    train_type: "KTX",
    origin: "서울",
    destination: "부산",
    departure_at: `${travelDate}T14:30:00+09:00`,
    arrival_at: `${travelDate}T17:00:00+09:00`,
    official_booking_url: "https://www.korail.com/ticket/search",
    seat_classes: [{
      seat_class: "standard",
      status: "sold_out",
      provenance: {
        kind: "official_provider",
        source: "authorized-test",
        observed_at: `${travelDate}T12:34:00+09:00`,
      },
      registration_evidence_id: evidenceId,
      actions: [{ kind: "add_to_watch" }],
    }],
    ...overrides,
  };
}

async function selectStation(user, label, name) {
  const input = screen.getByRole("combobox", { name: label });
  await waitFor(() => expect(input.disabled).toBe(false));
  await user.clear(input);
  await user.type(input, name);
  const listbox = screen.getByRole("listbox", { name: `${label} 검색 가능한 역` });
  await user.click(within(listbox).getByRole("option", { name: new RegExp(`^${name}`) }));
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

  it("returns home to watch management when the only payment deadline elapsed", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      render(<Home
        watches={[]}
        paymentWatches={[{
          provider: "SRT",
          train: "SRT 370",
          route: "대전 → 수서",
          departure: "22:06",
          arrival: "23:12",
          date: "8월 4일",
          payment_deadline: "2026-08-01T23:59:59Z",
          official_booking_url: "https://etk.srail.kr",
        }]}
        onNavigate={vi.fn()}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onCancel={vi.fn()}
        onToast={vi.fn()}
      />);

      expect(screen.getByText("관심 열차 관리")).toBeTruthy();
      expect(screen.queryByRole("heading", { name: /결제 대기/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /공식 결제 열기/ })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
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

  it("keeps the production wizard on train selection when the official timetable returns 503", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      return response({ detail: "TAGO service key is not configured" }, 503);
    }));
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("공식 시간표 제공자가 응답하지 않습니다.");
    expect(alert.textContent).not.toContain("TAGO");
    expect(screen.getByRole("heading", { name: "공식 시간표에서 관심 열차를 고르세요" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "등록 완료" })).toBeNull();
  });

  it("keeps Seoul selected and queries the SRT live route when SRT is the only provider", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    const timetableProviders = [];
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) {
        return response(stationCatalog(parsed.searchParams.get("provider")));
      }
      if (parsed.pathname.endsWith("/korail-browser-snapshot-revision")) {
        return response({ revision: null });
      }
      if (!parsed.pathname.endsWith("/timetables")) return response([]);
      timetableProviders.push(parsed.searchParams.get("provider"));
      return response([{
        provider: "srt",
        train_number: "162",
        train_type: "SRT",
        origin: "대전",
        destination: "서울",
        departure_at: `${travelDate}T12:37:00+09:00`,
        arrival_at: `${travelDate}T13:47:00+09:00`,
      }]);
    }));
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("checkbox", { name: /^SRT/ }));
    await selectStation(user, "출발역", "대전");
    await selectStation(user, "도착역", "서울");
    await user.click(screen.getByRole("checkbox", { name: /^KTX/ }));
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "출발역" }).value).toBe("대전");
      expect(screen.getByRole("combobox", { name: "도착역" }).value).toBe("서울");
    });

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    expect(await screen.findByRole("article", { name: "162" })).toBeTruthy();
    expect(timetableProviders).toEqual(["srt"]);
  });

  it("discards a delayed provider retry after the timetable conditions change", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    let timetableCalls = 0;
    let resolveOldRetry;
    const oldRetry = new Promise((resolve) => { resolveOldRetry = resolve; });
    const oldTrain = {
      provider: "korail",
      train_number: "KTX OLD",
      origin: "서울",
      destination: "부산",
      departure_at: `${travelDate}T13:20:00+09:00`,
      arrival_at: `${travelDate}T16:00:00+09:00`,
    };
    const newTrain = {
      provider: "korail",
      train_number: "KTX NEW",
      origin: "서울",
      destination: "부산",
      departure_at: `${travelDate}T09:30:00+09:00`,
      arrival_at: `${travelDate}T12:10:00+09:00`,
    };
    const fetchMock = vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      if (parsed.pathname.endsWith("/korail-browser-snapshot-revision")) return response({ revision: null });
      timetableCalls += 1;
      if (timetableCalls === 1) return response({ detail: "temporary timetable error" }, 503);
      if (timetableCalls === 2) return oldRetry;
      return response([newTrain]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("alert");

    await user.click(screen.getByRole("button", { name: "이 운영사만 다시 조회" }));
    await waitFor(() => expect(timetableCalls).toBe(2));
    await user.click(screen.getByRole("button", { name: "오전 09:00부터 12:00까지" }));
    await user.click(screen.getByRole("button", { name: "범위 변경" }));

    expect(await screen.findByRole("article", { name: "KTX NEW" })).toBeTruthy();
    expect(screen.getByLabelText("시간표 조회 결과 요약").textContent).toContain("09:00–12:00");
    await act(async () => {
      resolveOldRetry(response([oldTrain]));
      await oldRetry;
    });
    expect(timetableCalls).toBe(3);
    expect(screen.queryByRole("article", { name: "KTX OLD" })).toBeNull();
    expect(screen.getByRole("article", { name: "KTX NEW" })).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/timetables")).map(([url]) => {
      const params = new URL(url, "https://railwait.local").searchParams;
      return [params.get("departure_from"), params.get("departure_to")];
    })).toEqual([
      [`${travelDate}T12:00:00+09:00`, `${travelDate}T18:00:00+09:00`],
      [`${travelDate}T12:00:00+09:00`, `${travelDate}T18:00:00+09:00`],
      [`${travelDate}T09:00:00+09:00`, `${travelDate}T12:00:00+09:00`],
    ]);
  });

  it("recovers from an empty late-night same-day timetable when the departure date changes", async () => {
    const user = userEvent.setup();
    const today = seoulDate(0);
    const tomorrow = seoulDate(1);
    const timetableRequests = [];
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      if (parsed.pathname.endsWith("/korail-browser-snapshot-revision")) return response({ revision: null });
      if (!parsed.pathname.endsWith("/timetables")) return response([]);
      const departureFrom = parsed.searchParams.get("departure_from");
      timetableRequests.push(departureFrom);
      if (departureFrom?.startsWith(today)) return response([]);
      return response([{
        provider: "korail",
        train_number: "KTX NEXT",
        train_type: "KTX",
        origin: "서울",
        destination: "부산",
        departure_at: `${tomorrow}T14:30:00+09:00`,
        arrival_at: `${tomorrow}T17:00:00+09:00`,
      }]);
    }));
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /가는 날:/ }));
    await user.click(screen.getByRole("button", { name: "오늘" }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    expect(await screen.findByText("선택한 날짜·시간 범위에 맞는 공식 열차가 없습니다.")).toBeTruthy();
    const dateGroup = screen.getByRole("group", { name: /출발일 변경/ });
    await user.click(within(dateGroup).getByRole("button", { name: /출발일:/ }));
    await user.click(screen.getByRole("button", { name: "내일" }));

    expect(await screen.findByRole("article", { name: "KTX NEXT" })).toBeTruthy();
    expect(screen.queryByText("선택한 날짜·시간 범위에 맞는 공식 열차가 없습니다.")).toBeNull();
    expect(timetableRequests).toEqual([
      `${today}T12:00:00+09:00`,
      `${tomorrow}T12:00:00+09:00`,
    ]);
  });

  it("keeps the selected station identities when a failed same-day query moves to tomorrow", async () => {
    const user = userEvent.setup();
    const today = seoulDate(0);
    const tomorrow = seoulDate(1);
    const timetableRequests = [];
    let stationRequests = 0;
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) {
        stationRequests += 1;
        return response(stationCatalog(parsed.searchParams.get("provider")));
      }
      if (parsed.pathname.endsWith("/korail-browser-snapshot-revision")) {
        return response({ revision: null });
      }
      if (!parsed.pathname.endsWith("/timetables")) return response([]);
      const request = {
        origin: parsed.searchParams.get("origin"),
        destination: parsed.searchParams.get("destination"),
        originNodeId: parsed.searchParams.get("origin_node_id"),
        destinationNodeId: parsed.searchParams.get("destination_node_id"),
        departureFrom: parsed.searchParams.get("departure_from"),
      };
      timetableRequests.push(request);
      if (request.departureFrom?.startsWith(today)) {
        return response({ detail: "temporary timetable failure" }, 503);
      }
      return response([{
        provider: "korail",
        train_number: "KTX NEXT",
        train_type: "KTX",
        origin: "서울",
        destination: "부산",
        departure_at: `${tomorrow}T14:30:00+09:00`,
        arrival_at: `${tomorrow}T17:00:00+09:00`,
      }]);
    }));
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /가는 날:/ }));
    await user.click(screen.getByRole("button", { name: "오늘" }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    expect((await screen.findByRole("alert")).textContent).toContain("공식 시간표 제공자가 응답하지 않습니다.");
    const dateGroup = screen.getByRole("group", { name: /출발일 변경/ });
    await user.click(within(dateGroup).getByRole("button", { name: /출발일:/ }));
    await user.click(screen.getByRole("button", { name: "내일" }));

    expect(await screen.findByRole("article", { name: "KTX NEXT" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(stationRequests).toBe(1);
    expect(timetableRequests).toEqual([
      {
        origin: "서울",
        destination: "부산",
        originNodeId: "N-SEOUL",
        destinationNodeId: "N-BUSAN",
        departureFrom: `${today}T12:00:00+09:00`,
      },
      {
        origin: "서울",
        destination: "부산",
        originNodeId: "N-SEOUL",
        destinationNodeId: "N-BUSAN",
        departureFrom: `${tomorrow}T12:00:00+09:00`,
      },
    ]);
  });

  it("uses the selected production timetable train for the creation contract", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue(undefined);
    const travelDate = seoulDate();
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      return response([{
        provider: "srt",
        train_number: "SRT 327",
        origin: "서울",
        destination: "부산",
        departure_at: `${travelDate}T14:30:00+09:00`,
        arrival_at: `${travelDate}T16:58:00+09:00`,
        official_booking_url: "https://etk.srail.kr",
        seat_classes: [
          {
            seat_class: "standard",
            status: "unknown",
            provenance: { kind: "not_observed", reason: "provider_access_restricted" },
            registration_evidence_id: "20000000-0000-4000-8000-000000000327",
            actions: [{ kind: "add_to_watch" }, { kind: "official_check", url: "https://etk.srail.kr" }],
          },
          {
            seat_class: "first",
            status: "unknown",
            provenance: { kind: "not_observed", reason: "unsupported_route" },
            registration_evidence_id: "20000000-0000-4000-8000-000000000328",
            actions: [{ kind: "add_to_watch" }, { kind: "official_check", url: "https://etk.srail.kr" }],
          },
        ],
      }]);
    }));
    render(<NewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("checkbox", { name: /^SRT/ }));
    await user.click(screen.getByRole("checkbox", { name: /KTX · KORAIL/ }));
    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(false);
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "SRT 327" });
    const resultCard = screen.getByRole("article", { name: "SRT 327" });
    expect(within(resultCard).getByText("조회 제한")).toBeTruthy();
    expect(within(resultCard).getByText("구간 미지원")).toBeTruthy();
    expect(within(resultCard).queryByText("확인 필요")).toBeNull();
    expect(within(resultCard).getByText("운영사가 현재 좌석 조회를 제한해 상태를 가져오지 못했습니다.")).toBeTruthy();
    expect(within(resultCard).queryByText("예약 가능")).toBeNull();
    expect(within(resultCard).queryByText("매진")).toBeNull();
    expect(within(resultCard).queryByText("예매 불가")).toBeNull();
    expect(document.body.textContent).not.toContain("TAGO");
    expect(document.body.textContent).not.toContain("안전 보조 모드");
    expect(within(resultCard).queryByRole("button", { name: /관심 열차에 추가/ })).toBeNull();
    expect(within(resultCard).queryByRole("button", { name: /좌석 상태 입력/ })).toBeNull();
    expect(within(resultCard).queryByRole("button", { name: /일반실 공식 좌석 확인/ })).toBeNull();
    expect(within(resultCard).queryByRole("button", { name: /특실 공식 좌석 확인/ })).toBeNull();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("automatically refreshes server-side seat status when the timetable range changes", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    let timetableCalls = 0;
    const fetchMock = vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) {
        return response(stationCatalog(parsed.searchParams.get("provider")));
      }
      timetableCalls += 1;
      const morning = parsed.searchParams.get("departure_from")?.includes("T09:00");
      const seat = (seatClass, status, evidenceId) => ({
        seat_class: seatClass,
        status,
        registration_evidence_id: evidenceId,
        provenance: {
          kind: "official_provider",
          source: "authorized-test",
          observed_at: "2026-07-30T01:23:45Z",
        },
        actions: status === "sold_out"
          ? [{ kind: "add_to_watch" }]
          : [{ kind: "official_check", url: "https://www.korail.com/ticket/search" }],
      });
      return response([{
        provider: "korail",
        train_number: morning ? "KTX 027" : "KTX 026",
        train_type: "KTX",
        origin: "서울",
        destination: "부산",
        departure_at: `${travelDate}T${morning ? "09:30" : "14:30"}:00+09:00`,
        arrival_at: `${travelDate}T${morning ? "12:00" : "17:00"}:00+09:00`,
        official_booking_url: "https://www.korail.com/ticket/search",
        seat_classes: [
          seat("standard", "sold_out", "10000000-0000-4000-8000-000000000026"),
          seat("first", "limited", "10000000-0000-4000-8000-000000000027"),
        ],
      }]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    const initialCard = await screen.findByRole("article", { name: "KTX 026" });
    expect(within(initialCard).getByText("매진")).toBeTruthy();
    expect(within(initialCard).getByText("매진 임박")).toBeTruthy();
    expect(screen.getByText("좌석 상태 자동 반영 완료")).toBeTruthy();
    expect(screen.queryByText(/확장 프로그램/)).toBeNull();
    expect(timetableCalls).toBe(1);

    await user.click(screen.getByRole("button", { name: "오전 09:00부터 12:00까지" }));
    await user.click(screen.getByRole("button", { name: "적용·재조회" }));
    expect(await screen.findByRole("article", { name: "KTX 027" })).toBeTruthy();
    expect(timetableCalls).toBe(2);
    const timetableRequests = fetchMock.mock.calls.filter(([url]) => String(url).includes("/timetables"));
    expect(timetableRequests).toHaveLength(2);
  });

  it("shows observed official status but blocks add-to-watch when registration evidence is missing", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      return response([{
        provider: "korail",
        train_number: "KTX 901",
        origin: "서울",
        destination: "부산",
        departure_at: `${travelDate}T14:30:00+09:00`,
        arrival_at: `${travelDate}T17:00:00+09:00`,
        official_booking_url: "https://www.korail.com/ticket/search",
        seat_classes: [{
          seat_class: "standard",
          status: "available",
          provenance: {
            kind: "official_provider",
            source: "authorized-test",
            observed_at: `${travelDate}T12:34:00+09:00`,
          },
          actions: [{ kind: "add_to_watch" }],
        }],
      }]);
    }));
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    const card = await screen.findByRole("article", { name: "KTX 901" });
    const standardSeat = within(card).getByRole("region", { name: "KTX 901 일반실" });
    expect(within(card).getByText("예매 가능")).toBeTruthy();
    expect(within(standardSeat).getByRole("note").textContent).toContain("대기 등록 근거");
    expect(within(card).queryByRole("button", { name: /일반실로 대기/ })).toBeNull();
  });

  it("uses accessible operator cards and blocks step one when none is selected", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const korail = screen.getByRole("checkbox", { name: /KTX · KORAIL/ });
    const srt = screen.getByRole("checkbox", { name: /^SRT/ });
    expect(korail.getAttribute("aria-checked")).toBe("true");
    expect(srt.getAttribute("aria-checked")).toBe("false");

    await user.click(korail);
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("운영사를 1개 이상 선택");

    await user.click(srt);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "출발역" }).disabled).toBe(false));
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(true);
    expect(screen.getByRole("note").textContent).toContain("운영사별 운행 여부를 증명하지 않으며");
  });

  it("fails closed without embedded station fallback when the production catalog fails", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(response({ detail: "TAGO station catalog unavailable" }, 503));
    vi.stubGlobal("fetch", fetchMock);

    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    const alert = (await screen.findByText("역 목록을 불러오지 못했습니다.", { selector: "strong" })).closest('[role="alert"]');
    expect(alert.textContent).toContain("역 목록을 불러오지 못했습니다");
    expect(alert.textContent).not.toContain("TAGO");
    expect(screen.getByRole("combobox", { name: "출발역" }).disabled).toBe(true);
    expect(screen.getByRole("combobox", { name: "도착역" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(true);

    await user.click(screen.getByRole("button", { name: "다시 불러오기" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("searches supplied stations with the combobox keyboard and swaps the route", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const origin = screen.getByRole("combobox", { name: "출발역" });
    const destination = screen.getByRole("combobox", { name: "도착역" });
    await user.clear(origin);
    await user.type(origin, "수서");
    expect(screen.getByRole("listbox", { name: "출발역 검색 가능한 역" })).toBeTruthy();
    const inlineError = screen.getByText("출발역을 제공된 역 목록에서 선택해 주세요.");
    expect(inlineError.className).toContain("station-field-error");
    expect(inlineError.getAttribute("role")).toBe("alert");
    expect(origin.getAttribute("aria-invalid")).toBe("true");
    expect(origin.getAttribute("aria-describedby")).toContain(inlineError.id);
    expect(document.querySelector(".journey-error")).toBeNull();
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(true);
    await user.keyboard("{Enter}");
    expect(origin.value).toBe("수서");
    expect(origin.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByText("출발역을 제공된 역 목록에서 선택해 주세요.")).toBeNull();
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(false);

    const swap = screen.getByRole("button", { name: "출발역과 도착역 바꾸기" });
    expect(swap.closest(".route-swap-slot")).toBeTruthy();
    await user.click(swap);
    expect(origin.value).toBe("부산");
    expect(destination.value).toBe("수서");
    expect(screen.getByRole("button", { name: /다음/ }).disabled).toBe(false);
  });

  it("keeps untouched station validation quiet until the user interacts with a field", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      return response(stationCatalog(parsed.searchParams.get("provider")));
    }));
    render(<NewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    const origin = screen.getByRole("combobox", { name: "출발역" });
    const destination = screen.getByRole("combobox", { name: "도착역" });
    await waitFor(() => expect(origin.disabled).toBe(false));
    expect(origin.getAttribute("aria-invalid")).toBe("false");
    expect(destination.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByText("출발역을 제공된 역 목록에서 선택해 주세요.")).toBeNull();
    expect(screen.queryByText("도착역을 제공된 역 목록에서 선택해 주세요.")).toBeNull();

    await user.click(origin);
    await user.tab();
    expect(origin.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("출발역을 제공된 역 목록에서 선택해 주세요.")).toBeTruthy();
    expect(destination.getAttribute("aria-invalid")).toBe("false");
  });

  it("opens a closed station combobox on the first keyboard option without skipping it", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const origin = screen.getByRole("combobox", { name: "출발역" });
    await user.clear(origin);
    await user.type(origin, "수");
    const listbox = screen.getByRole("listbox", { name: "출발역 검색 가능한 역" });
    const options = within(listbox).getAllByRole("option");
    expect(options.every((option) => option.tabIndex === -1)).toBe(true);
    const firstOptionName = options[0].querySelector("strong").textContent;
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox", { name: "출발역 검색 가능한 역" })).toBeNull();

    await user.keyboard("{ArrowDown}{Enter}");
    expect(origin.value).toBe(firstOptionName);
  });

  it("keeps the calendar and single weekday quick selection synchronized", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /가는 날:/ }));
    const calendar = screen.getByRole("dialog", { name: "가는 날 선택" });
    await user.click(within(calendar).getByRole("button", { name: "오늘" }));

    const weekdayGroup = screen.getByRole("group", { name: /출발 요일 빠른 선택/ });
    const current = within(weekdayGroup).getByRole("button", { pressed: true }).textContent;
    const target = current === "월" ? "화" : "월";
    await user.click(within(weekdayGroup).getByRole("button", { name: target }));
    expect(within(weekdayGroup).getByRole("button", { pressed: true }).textContent).toBe(target);
    expect(screen.getByRole("button", { name: new RegExp(`가는 날:.*${target}`) })).toBeTruthy();
  });

  it("keeps keyboard focus inside the calendar and restores it on Escape", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: /가는 날:/ });
    await user.click(trigger);
    const calendar = screen.getByRole("dialog", { name: "가는 날 선택" });
    expect(calendar.getAttribute("aria-modal")).toBe("true");
    await waitFor(() => expect(calendar.contains(document.activeElement)).toBe(true));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "가는 날 선택" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("selects a custom time range preset without native time inputs", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /오전.*09:00.*12:00/ }));
    const startSlider = screen.getByRole("slider", { name: "출발 시작 시간" });
    const endSlider = screen.getByRole("slider", { name: "출발 종료 시간" });
    expect(startSlider.value).toBe("18");
    expect(endSlider.value).toBe("24");
    expect(startSlider.getAttribute("aria-valuetext")).toBe("09:00부터");
    expect(endSlider.getAttribute("aria-valuetext")).toBe("12:00까지");
    expect(screen.queryByDisplayValue(/\d{2}:\d{2}/, { selector: "input[type='time']" })).toBeNull();
  });

  it("registers standard and first class on the same train as independent waits", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn()
      .mockResolvedValueOnce([{ id: "watch-korail-033-standard" }])
      .mockResolvedValueOnce([{ id: "watch-korail-033-first" }]);
    render(<NewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 033" });
    await user.click(seatWaitButton("KTX 033"));
    await screen.findByRole("button", { name: "일반실 대기 취소" });
    await user.click(seatWaitButton("KTX 033", "특실로 대기"));
    await screen.findByRole("button", { name: "특실 대기 취소" });

    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete.mock.calls.map(([contract]) => contract.selectedTrains[0].selected_seat_class)).toEqual(["standard", "first"]);
    expect(screen.getByRole("button", { name: "일반실 대기 취소" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "특실 대기 취소" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("list", { name: "등록된 열차와 좌석 등급" })).toBeNull();
  });

  it("keeps both seat classes and the registered cancel action accessible at 320px", async () => {
    vi.stubGlobal("innerWidth", 320);
    window.dispatchEvent(new Event("resize"));
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue([{ id: "watch-korail-033-standard" }]);
    render(<NewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    const card = await screen.findByRole("article", { name: "KTX 033" });
    const standardPanel = within(card).getByRole("region", { name: "KTX 033 일반실" });
    const firstPanel = within(card).getByRole("region", { name: "KTX 033 특실" });

    expect(window.innerWidth).toBe(320);
    expect(within(standardPanel).getByText("예매 가능")).toBeTruthy();
    expect(within(firstPanel).getByText("예매 가능")).toBeTruthy();
    expect(within(standardPanel).getByRole("button", { name: "일반실로 대기" })).toBeTruthy();
    expect(within(firstPanel).getByRole("button", { name: "특실로 대기" })).toBeTruthy();

    await user.click(within(standardPanel).getByRole("button", { name: "일반실로 대기" }));

    const cancelButton = await within(standardPanel).findByRole("button", { name: "일반실 대기 취소" });
    expect(card.dataset.registrationCount).toBe("1");
    expect(card.classList.contains("has-active-registration")).toBe(true);
    expect(within(card).getByText("대기 등록 1건")).toBeTruthy();
    expect(standardPanel.dataset.registrationState).toBe("active");
    expect(standardPanel.classList.contains("is-registered")).toBe(true);
    const registrationStatus = within(standardPanel).getByRole("status");
    expect(registrationStatus.textContent).toContain("대기 등록됨");
    expect(registrationStatus.textContent).toContain("좌석 변화를 감시 중");
    expect(cancelButton.classList.contains("seat-action-cancel")).toBe(true);
    expect(cancelButton.getAttribute("aria-pressed")).toBe("true");

    expect(firstPanel.dataset.registrationState).toBe("idle");
    expect(firstPanel.classList.contains("is-registered")).toBe(false);
    expect(within(firstPanel).queryByRole("status")).toBeNull();
    expect(within(firstPanel).getByRole("button", { name: "특실로 대기" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("cancels the exact created watch when the active seat button is pressed again", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue([{ id: "watch-korail-033-standard" }]);
    const onCancelWatch = vi.fn().mockResolvedValue({
      id: "watch-korail-033-standard",
      status: "expired",
    });
    render(
      <NewWait
        demo
        onComplete={onComplete}
        onCancelWatch={onCancelWatch}
        onCancel={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 033" });

    await user.click(seatWaitButton("KTX 033"));
    const cancelButton = await screen.findByRole("button", { name: "일반실 대기 취소" });
    expect(cancelButton.getAttribute("aria-pressed")).toBe("true");
    expect(onComplete).toHaveBeenCalledOnce();

    await user.click(cancelButton);
    expect(onCancelWatch).toHaveBeenCalledOnce();
    expect(onCancelWatch).toHaveBeenCalledWith("watch-korail-033-standard");
    await waitFor(() => expect(seatWaitButton("KTX 033").getAttribute("aria-pressed")).toBe("false"));
    expect(screen.queryByRole("button", { name: "일반실 대기 취소" })).toBeNull();
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("shows factual demo timetable metadata without a production confirmation action", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn()
      .mockResolvedValueOnce([{ id: "watch-korail-033-standard" }])
      .mockResolvedValueOnce([{ id: "watch-srt-327-first" }]);
    render(<NewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("checkbox", { name: /^SRT/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    expect(await screen.findAllByText("데모 시간표")).toHaveLength(20);
    expect(screen.queryByText("TAGO 공식 시간표")).toBeNull();
    expect(screen.getAllByText("성인 59,800원")).toHaveLength(10);
    expect(screen.getAllByText("데모 좌석 상태")).toHaveLength(40);
    expect(screen.getAllByText("매진").length).toBeGreaterThan(0);
    expect(screen.getAllByText("예약대기 가능").length).toBeGreaterThan(0);
    expect(screen.queryByText("매진 · 예약대기 가능")).toBeNull();
    expect(document.body.textContent).not.toContain("TAGO");
    const standardPanel = within(screen.getByRole("article", { name: "KTX 033" })).getByRole("region", { name: "KTX 033 일반실" });
    expect(standardPanel.querySelector(".seat-status-chip").textContent).toBe("예매 가능");
    expect(standardPanel.querySelector(".seat-class-helper").getAttribute("title")).toContain("공식 예매 화면");
    expect(within(standardPanel).getAllByRole("button").every((button) => button.classList.contains("compact"))).toBe(true);
    expect(within(standardPanel).getByRole("button", { name: "KTX 033 일반실 공식 예매 전 안내 열기" }).className).toContain("button-primary");
    expect(screen.queryByRole("button", { name: /공식 페이지에서 확인한 좌석 상태 입력/ })).toBeNull();
    expect(within(screen.getByRole("article", { name: "SRT 327" })).getByText("서울")).toBeTruthy();

    const soldOutPanel = within(screen.getByRole("article", { name: "KTX 085" })).getByRole("region", { name: "KTX 085 특실" });
    expect(within(soldOutPanel).getByRole("button", { name: "특실 취소표 대기" }).className).toContain("button-primary");
    expect(within(soldOutPanel).queryByRole("button", { name: /공식 .* 확인 전 안내 열기/ })).toBeNull();

    const standingPanel = within(screen.getByRole("article", { name: "SRT 327" })).getByRole("region", { name: "SRT 327 일반실" });
    expect(within(standingPanel).getByText("입석+좌석")).toBeTruthy();
    expect(within(standingPanel).getByRole("button", { name: "SRT 327 일반실 공식 예매 전 안내 열기" })).toBeTruthy();
    const waitlistPanel = within(screen.getByRole("article", { name: "SRT 327" })).getByRole("region", { name: "SRT 327 특실" });
    expect(within(waitlistPanel).getByRole("button", { name: "SRT 327 특실 공식 예약대기 전 안내 열기" }).className).toContain("button-primary");
    expect(within(waitlistPanel).getByRole("button", { name: "특실 예약대기" }).className).toContain("button-secondary");

    await user.click(seatWaitButton("KTX 033"));
    expect(within(screen.getByRole("article", { name: "KTX 085" })).getByRole("button", { name: "특실 취소표 대기" })).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "SRT 327" })).getByRole("button", { name: "특실 예약대기" })).toBeTruthy();
    await user.click(seatWaitButton("SRT 327", "특실 예약대기"));
    expect(await screen.findByRole("button", { name: "특실 대기 취소" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
    expect(screen.getAllByText("대기 등록 1건")).toHaveLength(2);
    expect(screen.queryByRole("list", { name: "등록된 열차와 좌석 등급" })).toBeNull();
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

  it("defaults a verified account to one-time reservation without overriding a manual notify choice", async () => {
    const user = userEvent.setup();
    const account = {
      provider: "KORAIL",
      configured: true,
      enabled: true,
      loginMethod: "phone",
      maskedLoginId: "0*********6",
      credentialVersion: 1,
      lastAuthStatus: "authenticated",
      lastAuthenticatedAt: "2026-08-01T12:00:00Z",
      updatedAt: "2026-08-01T12:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) {
        return response(stationCatalog(parsed.searchParams.get("provider")));
      }
      return response([]);
    }));
    const { rerender } = render(
      <NewWait
        demo={false}
        providerAccounts={[account]}
        onComplete={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));

    expect(screen.getByRole("button", { name: /자동 예매/ }).getAttribute("aria-pressed"))
      .toBe("true");
    await user.click(screen.getByRole("button", { name: /알림만 받기/ }));
    rerender(
      <NewWait
        demo={false}
        providerAccounts={[{ ...account }]}
        onComplete={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /알림만 받기/ }).getAttribute("aria-pressed"))
      .toBe("true");
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

  it("shows every provider result in time order and preserves already registered waits after a range change", async () => {
    const user = userEvent.setup();
    render(<NewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("checkbox", { name: /^SRT/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    const afternoonSummary = await screen.findByLabelText("시간표 조회 결과 요약");
    expect(afternoonSummary.textContent).toContain("12:00–18:00");
    expect(afternoonSummary.textContent).toContain("총 20개 열차 · KORAIL 10 · SRT 10");
    const afternoonCards = screen.getAllByRole("article");
    expect(afternoonCards).toHaveLength(20);
    expect(afternoonCards.slice(0, 4).map((card) => card.querySelector(".train-result-header strong")?.textContent)).toEqual([
      "KTX 33",
      "SRT 327",
      "KTX 85",
      "SRT 329",
    ]);

    await user.click(seatWaitButton("KTX 033"));
    await user.click(screen.getByRole("button", { name: "오전 09:00부터 12:00까지" }));
    const rangeTools = screen.getByText("출발 시간 다시 조회").closest("fieldset");
    expect(within(rangeTools).getByRole("status").textContent).toContain("변경한 시간 범위를 적용");
    await user.click(within(rangeTools).getByRole("button", { name: "적용·재조회" }));

    const morningSummary = await screen.findByLabelText("시간표 조회 결과 요약");
    expect(morningSummary.textContent).toContain("09:00–12:00");
    expect(morningSummary.textContent).toContain("총 10개 열차 · KORAIL 5 · SRT 5");
    expect(screen.getAllByRole("article")).toHaveLength(10);
    expect(screen.queryByRole("article", { name: "KTX 107" })).toBeNull();
    expect(seatWaitButton("KTX 033").getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByRole("button", { name: "등록 완료" })).toBeNull();
  });

  it("changes the actual departure date from the Step 3 calendar, requeries, and clears the prior seat selection", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn().mockResolvedValue([{ id: "watch-korail-033-standard" }]);
    render(<NewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    const initialCard = await screen.findByRole("article", { name: "KTX 033" });
    await user.click(seatWaitButton("KTX 033"));
    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();

    const dateGroup = screen.getByRole("group", { name: /출발일 변경/ });
    expect(within(dateGroup).queryByRole("button", { name: /요일로 날짜 이동/ })).toBeNull();
    await user.click(within(dateGroup).getByRole("button", { name: /출발일:/ }));
    const calendar = screen.getByRole("dialog", { name: "시간표 출발일 선택" });
    const targetDate = seoulDate(2);
    const targetDateLabel = koreanDateLabel(targetDate);
    await user.click(within(calendar).getByRole("button", { name: targetDateLabel }));

    await waitFor(() => expect(within(dateGroup).getByRole("button", { name: `출발일: ${targetDateLabel}` })).toBeTruthy());
    await waitFor(() => expect(screen.getByRole("article", { name: "KTX 033" })).not.toBe(initialCard));
    expect(seatWaitButton("KTX 033").getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByRole("list", { name: "등록된 열차와 좌석 등급" })).toBeNull();
    expect(screen.queryByRole("button", { name: "등록 완료" })).toBeNull();
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

  it("blocks duplicate clicks while one seat registration is pending", async () => {
    const user = userEvent.setup();
    let resolveRegistration;
    const registration = new Promise((resolve) => { resolveRegistration = resolve; });
    const onComplete = vi.fn().mockReturnValue(registration);
    render(<NewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 033" });
    await user.click(seatWaitButton("KTX 033"));
    const pending = screen.getByRole("button", { name: "일반실 등록 중…" });
    expect(pending.disabled).toBe(true);
    expect(pending.getAttribute("aria-busy")).toBe("true");
    await user.click(pending);
    expect(onComplete).toHaveBeenCalledOnce();

    resolveRegistration([{ id: "watch-korail-033-standard" }]);
    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
  });

  it("refreshes expired registration evidence once and retries creation once with the exact refreshed seat", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    const oldEvidenceId = "10000000-0000-4000-8000-000000000901";
    const newEvidenceId = "20000000-0000-4000-8000-000000000901";
    const onComplete = vi.fn()
      .mockRejectedValueOnce(expiredEvidenceConflict())
      .mockResolvedValueOnce([{ id: "watch-korail-901-standard" }]);
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (url, options = {}) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      if (parsed.pathname.endsWith("/seat-status/refresh")) {
        refreshCalls += 1;
        expect(options.method).toBe("POST");
        return response([observedSoldOutTimetable(travelDate, newEvidenceId)]);
      }
      return response([observedSoldOutTimetable(travelDate, oldEvidenceId)]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 901" });
    await user.click(seatWaitButton("KTX 901", "일반실 취소표 대기"));

    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
    expect(refreshCalls).toBe(1);
    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete.mock.calls[0][0].train.seat_classes[0].registration_evidence_id).toBe(oldEvidenceId);
    expect(onComplete.mock.calls[1][0].train.seat_classes[0].registration_evidence_id).toBe(newEvidenceId);
    await user.click(screen.getByRole("button", { name: "일반실 대기 취소" }));
    expect(onComplete).toHaveBeenCalledTimes(2);
  });

  it("does not retry creation when refreshing expired registration evidence fails", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    const onComplete = vi.fn().mockRejectedValue(expiredEvidenceConflict());
    let refreshCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      if (parsed.pathname.endsWith("/seat-status/refresh")) {
        refreshCalls += 1;
        return response({ detail: "upstream unavailable" }, 503);
      }
      return response([observedSoldOutTimetable(
        travelDate,
        "10000000-0000-4000-8000-000000000901",
      )]);
    }));
    render(<NewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 901" });
    await user.click(seatWaitButton("KTX 901", "일반실 취소표 대기"));

    expect((await screen.findByText(
      /좌석 상태를 다시 확인하지 못해 등록하지 않았습니다/,
      { selector: ".seat-registration-error" },
    )).textContent).toContain("좌석 상태를 다시 확인하지 못해 등록하지 않았습니다");
    expect(refreshCalls).toBe(1);
    expect(onComplete).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "일반실 다시 등록" }).disabled).toBe(false);
  });

  it("does not retry creation when the refreshed train identity changed", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    const onComplete = vi.fn().mockRejectedValue(expiredEvidenceConflict());
    let refreshCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const parsed = new URL(url, "https://railwait.local");
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      if (parsed.pathname.endsWith("/seat-status/refresh")) {
        refreshCalls += 1;
        return response([observedSoldOutTimetable(
          travelDate,
          "20000000-0000-4000-8000-000000000902",
          { train_number: "KTX 902" },
        )]);
      }
      return response([observedSoldOutTimetable(
        travelDate,
        "10000000-0000-4000-8000-000000000901",
      )]);
    }));
    render(<NewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 901" });
    await user.click(seatWaitButton("KTX 901", "일반실 취소표 대기"));

    expect((await screen.findByRole("alert")).textContent).toContain("재조회한 열차가 기존 선택과 달라 등록하지 않았습니다");
    expect(refreshCalls).toBe(1);
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("shows a seat-local failure and allows only that registration to retry", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn()
      .mockRejectedValueOnce(new Error("등록 요청이 실패했습니다."))
      .mockResolvedValueOnce([{ id: "watch-korail-033-standard" }]);
    render(<NewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 033" });
    await user.click(seatWaitButton("KTX 033"));
    expect((await screen.findByRole("alert")).textContent).toContain("등록 요청이 실패했습니다.");
    const retry = screen.getByRole("button", { name: "일반실 다시 등록" });
    expect(retry.disabled).toBe(false);
    expect(seatWaitButton("KTX 033", "특실로 대기").disabled).toBe(false);

    await user.click(retry);
    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
    expect(onComplete).toHaveBeenCalledTimes(2);
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

  it("builds reservation counts and official CTAs from the watches", () => {
    const watches = [
      { id: "scheduled", status: "scheduled", statusLabel: "대기 등록됨", route: "서울 → 부산", train: "KTX 085", date: "8월 1일", departure: "14:11", official_booking_url: "https://www.letskorail.com" },
      { id: "payment", status: "payment_required", statusLabel: "결제 필요", route: "수서 → 부산", train: "SRT 327", date: "8월 1일", departure: "14:30", official_booking_url: "https://etk.srail.kr" },
      { id: "done", status: "completed", statusLabel: "결제 완료", route: "서울 → 대전", train: "KTX 001", date: "7월 30일", departure: "09:00", official_booking_url: null },
    ];
    render(<Reservations watches={watches} onNavigate={vi.fn()} />);

    expect(screen.getByText("진행 중").nextElementSibling.textContent).toBe("1");
    expect(screen.getByText("결제 필요", { selector: ".reservation-summary span" }).nextElementSibling.textContent).toBe("1");
    expect(screen.getByText("완료").nextElementSibling.textContent).toBe("1");
    expect(screen.getByRole("button", { name: /공식 예매 열기/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /결제 열기/ })).toBeTruthy();
    expect(screen.getAllByRole("article")[0].textContent).toContain("수서 → 부산");
    expect(screen.getByText("결제기한 미제공")).toBeTruthy();
  });

  it("does not count an elapsed provider deadline as payment waiting", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-02T00:00:00Z"));
    try {
      render(<Reservations watches={[
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
      ]} onNavigate={vi.fn()} />);

      expect(screen.getByText("결제 필요", { selector: ".reservation-summary span" })
        .nextElementSibling.textContent).toBe("0");
      expect(screen.getByText("기한 경과 확인").nextElementSibling.textContent).toBe("1");
      expect(screen.getByRole("button", { name: /공식 확인 열기/ })).toBeTruthy();
      expect(screen.queryByRole("button", { name: /결제 열기/ })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows deleting only deletable terminal records", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(<Reservations watches={[{ id: "expired", status: "expired", statusLabel: "만료", route: "서울 → 부산", train: "KTX 085", date: "8월 1일", departure: "14:11" }]} onNavigate={vi.fn()} onDelete={onDelete} />);

    await user.click(screen.getByRole("button", { name: /기록 삭제/ }));
    expect(onDelete).toHaveBeenCalledWith("expired");
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
