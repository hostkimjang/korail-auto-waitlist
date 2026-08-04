import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import type { ProviderAccount } from "../src/api/providerAccounts";
import { NewWaitPage, type NewWaitPageProps } from "../src/features/new-wait/NewWaitPage";
import { OfficialHandoff } from "../src/features/official-handoff/OfficialHandoff";

type OwnedNewWaitProps = Omit<NewWaitPageProps, "officialHandoffComponent">;
type TestUser = ReturnType<typeof userEvent.setup>;

function OwnedNewWait(props: OwnedNewWaitProps) {
  return <NewWaitPage {...props} officialHandoffComponent={OfficialHandoff} />;
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: unknown): URL {
  if (input instanceof Request) return new URL(input.url);
  if (input instanceof URL) return input;
  return new URL(typeof input === "string" ? input : String(input), "https://railwait.local");
}

function buttonElement(element: HTMLElement): HTMLButtonElement {
  if (!(element instanceof HTMLButtonElement)) {
    throw new Error("기대했던 button 요소가 아닙니다.");
  }
  return element;
}

function inputElement(element: HTMLElement): HTMLInputElement {
  if (!(element instanceof HTMLInputElement)) {
    throw new Error("기대했던 input 요소가 아닙니다.");
  }
  return element;
}

function requiredElement<T extends Element>(element: T | null, message: string): T {
  if (element === null) throw new Error(message);
  return element;
}

function stationCatalog(provider: string | null = "korail") {
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

function seoulDate(dayOffset = 1): string {
  const date = new Date(Date.now() + dayOffset * 24 * 60 * 60 * 1000);
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Seoul",
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes): string | undefined => (
    parts.find((part) => part.type === type)?.value
  );
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function koreanDateLabel(value: string): string {
  const [year = 0, month = 0, day = 0] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date(year, month - 1, day));
}

function seatWaitButton(trainName: string, seatName = "일반실로 대기"): HTMLButtonElement {
  const card = screen.getByRole("article", { name: trainName });
  const button = within(card).getByRole("button", { name: seatName });
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error("좌석 대기 행동이 button이 아닙니다.");
  }
  return button;
}

function expiredEvidenceConflict(): ApiError {
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

function observedSoldOutTimetable(
  travelDate: string,
  evidenceId: string,
  overrides: Readonly<Record<string, unknown>> = {},
) {
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

async function selectStation(user: TestUser, label: string, name: string): Promise<void> {
  const input = screen.getByRole("combobox", { name: label });
  if (!(input instanceof HTMLInputElement)) {
    throw new Error(`${label} 입력이 input이 아닙니다.`);
  }
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

describe("NewWaitPage behavior", () => {
  it("keeps the production wizard on train selection when the official timetable returns 503", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      return response({ detail: "TAGO service key is not configured" }, 503);
    }));
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    const timetableProviders: Array<string | null> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) {
        return response(stationCatalog(parsed.searchParams.get("provider")));
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
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("checkbox", { name: /^SRT/ }));
    await selectStation(user, "출발역", "대전");
    await selectStation(user, "도착역", "서울");
    await user.click(screen.getByRole("checkbox", { name: /^KTX/ }));
    await waitFor(() => {
      expect(inputElement(screen.getByRole("combobox", { name: "출발역" })).value).toBe("대전");
      expect(inputElement(screen.getByRole("combobox", { name: "도착역" })).value).toBe("서울");
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
    let resolveOldRetry: (response: Response) => void = () => undefined;
    const oldRetry = new Promise<Response>((resolve) => { resolveOldRetry = resolve; });
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
    const fetchMock = vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      timetableCalls += 1;
      if (timetableCalls === 1) return response({ detail: "temporary timetable error" }, 503);
      if (timetableCalls === 2) return oldRetry;
      return response([newTrain]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

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
      const params = requestUrl(url).searchParams;
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
    const timetableRequests: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
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
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    const timetableRequests: unknown[] = [];
    let stationRequests = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) {
        stationRequests += 1;
        return response(stationCatalog(parsed.searchParams.get("provider")));
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
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
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
    render(<OwnedNewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("checkbox", { name: /^SRT/ }));
    await user.click(screen.getByRole("checkbox", { name: /KTX · KORAIL/ }));
    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(false);
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
    const fetchMock = vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) {
        return response(stationCatalog(parsed.searchParams.get("provider")));
      }
      timetableCalls += 1;
      const morning = parsed.searchParams.get("departure_from")?.includes("T09:00");
      const seat = (seatClass: string, status: string, evidenceId: string) => ({
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
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
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
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const korail = screen.getByRole("checkbox", { name: /KTX · KORAIL/ });
    const srt = screen.getByRole("checkbox", { name: /^SRT/ });
    expect(korail.getAttribute("aria-checked")).toBe("true");
    expect(srt.getAttribute("aria-checked")).toBe("false");

    await user.click(korail);
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("운영사를 1개 이상 선택");

    await user.click(srt);
    await waitFor(() => expect(inputElement(screen.getByRole("combobox", { name: "출발역" })).disabled).toBe(false));
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(true);
    expect(screen.getByRole("note").textContent).toContain("운영사별 운행 여부를 증명하지 않으며");
  });

  it("fails closed without embedded station fallback when the production catalog fails", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(response({ detail: "TAGO station catalog unavailable" }, 503));
    vi.stubGlobal("fetch", fetchMock);

    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    const alert = requiredElement(
      (await screen.findByText("역 목록을 불러오지 못했습니다.", { selector: "strong" }))
        .closest('[role="alert"]'),
      "역 목록 오류 alert를 찾지 못했습니다.",
    );
    expect(alert.textContent).toContain("역 목록을 불러오지 못했습니다");
    expect(alert.textContent).not.toContain("TAGO");
    expect(inputElement(screen.getByRole("combobox", { name: "출발역" })).disabled).toBe(true);
    expect(inputElement(screen.getByRole("combobox", { name: "도착역" })).disabled).toBe(true);
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(true);

    await user.click(screen.getByRole("button", { name: "다시 불러오기" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("searches supplied stations with the combobox keyboard and swaps the route", async () => {
    const user = userEvent.setup();
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const origin = inputElement(screen.getByRole("combobox", { name: "출발역" }));
    const destination = inputElement(screen.getByRole("combobox", { name: "도착역" }));
    await user.clear(origin);
    await user.type(origin, "수서");
    expect(screen.getByRole("listbox", { name: "출발역 검색 가능한 역" })).toBeTruthy();
    const inlineError = screen.getByText("출발역을 제공된 역 목록에서 선택해 주세요.");
    expect(inlineError.className).toContain("station-field-error");
    expect(inlineError.getAttribute("role")).toBe("alert");
    expect(origin.getAttribute("aria-invalid")).toBe("true");
    expect(origin.getAttribute("aria-describedby")).toContain(inlineError.id);
    expect(document.querySelector(".journey-error")).toBeNull();
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(true);
    await user.keyboard("{Enter}");
    expect(origin.value).toBe("수서");
    expect(origin.getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByText("출발역을 제공된 역 목록에서 선택해 주세요.")).toBeNull();
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(false);

    const swap = screen.getByRole("button", { name: "출발역과 도착역 바꾸기" });
    expect(swap.closest(".route-swap-slot")).toBeTruthy();
    await user.click(swap);
    expect(origin.value).toBe("부산");
    expect(destination.value).toBe("수서");
    expect(buttonElement(screen.getByRole("button", { name: /다음/ })).disabled).toBe(false);
  });

  it("keeps untouched station validation quiet until the user interacts with a field", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      return response(stationCatalog(parsed.searchParams.get("provider")));
    }));
    render(<OwnedNewWait demo={false} onComplete={vi.fn()} onCancel={vi.fn()} />);

    const origin = inputElement(screen.getByRole("combobox", { name: "출발역" }));
    const destination = inputElement(screen.getByRole("combobox", { name: "도착역" }));
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
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    const origin = inputElement(screen.getByRole("combobox", { name: "출발역" }));
    await user.clear(origin);
    await user.type(origin, "수");
    const listbox = screen.getByRole("listbox", { name: "출발역 검색 가능한 역" });
    const options = within(listbox).getAllByRole("option");
    expect(options.every((option) => option.tabIndex === -1)).toBe(true);
    const firstOption = requiredElement(options[0] ?? null, "첫 역 검색 결과가 없습니다.");
    const firstOptionName = requiredElement(
      firstOption.querySelector("strong"),
      "첫 역 검색 결과의 이름이 없습니다.",
    ).textContent;
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox", { name: "출발역 검색 가능한 역" })).toBeNull();

    await user.keyboard("{ArrowDown}{Enter}");
    expect(origin.value).toBe(firstOptionName);
  });

  it("keeps the calendar and single weekday quick selection synchronized", async () => {
    const user = userEvent.setup();
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /오전.*09:00.*12:00/ }));
    const startSlider = inputElement(screen.getByRole("slider", { name: "출발 시작 시간" }));
    const endSlider = inputElement(screen.getByRole("slider", { name: "출발 종료 시간" }));
    expect(startSlider.value).toBe("18");
    expect(endSlider.value).toBe("24");
    expect(startSlider.getAttribute("aria-valuetext")).toBe("09:00부터");
    expect(endSlider.getAttribute("aria-valuetext")).toBe("12:00까지");
    expect(screen.queryByDisplayValue(/\d{2}:\d{2}/)).toBeNull();
  });

  it("registers standard and first class on the same train as independent waits", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn()
      .mockResolvedValueOnce([{ id: "watch-korail-033-standard" }])
      .mockResolvedValueOnce([{ id: "watch-korail-033-first" }]);
    render(<OwnedNewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

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
    render(<OwnedNewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

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
      <OwnedNewWait
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
    render(<OwnedNewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

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
    expect(requiredElement(
      standardPanel.querySelector(".seat-status-chip"),
      "좌석 상태 chip이 없습니다.",
    ).textContent).toBe("예매 가능");
    expect(requiredElement(
      standardPanel.querySelector(".seat-class-helper"),
      "좌석 도움말이 없습니다.",
    ).getAttribute("title")).toContain("공식 예매 화면");
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
  it("defaults a verified account to one-time reservation without overriding a manual notify choice", async () => {
    const user = userEvent.setup();
    const account: ProviderAccount = {
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
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) {
        return response(stationCatalog(parsed.searchParams.get("provider")));
      }
      return response([]);
    }));
    const { rerender } = render(
      <OwnedNewWait
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
      <OwnedNewWait
        demo={false}
        providerAccounts={[{ ...account }]}
        onComplete={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /알림만 받기/ }).getAttribute("aria-pressed"))
      .toBe("true");
  });
  it("shows every provider result in time order and preserves already registered waits after a range change", async () => {
    const user = userEvent.setup();
    render(<OwnedNewWait demo onComplete={vi.fn()} onCancel={vi.fn()} />);

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
    const rangeTools = requiredElement(
      screen.getByText("출발 시간 다시 조회").closest("fieldset"),
      "출발 시간 재조회 도구를 찾지 못했습니다.",
    );
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
    render(<OwnedNewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

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
  it("blocks duplicate clicks while one seat registration is pending", async () => {
    const user = userEvent.setup();
    let resolveRegistration: (value: Array<{ id: string }>) => void = () => undefined;
    const registration = new Promise<Array<{ id: string }>>((resolve) => {
      resolveRegistration = resolve;
    });
    const onComplete = vi.fn().mockReturnValue(registration);
    render(<OwnedNewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 033" });
    await user.click(seatWaitButton("KTX 033"));
    const pending = buttonElement(screen.getByRole("button", { name: "일반실 등록 중…" }));
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
    const fetchMock = vi.fn(async (url: string | URL | Request, options: RequestInit = {}) => {
      const parsed = requestUrl(url);
      if (parsed.pathname.endsWith("/stations")) return response(stationCatalog(parsed.searchParams.get("provider")));
      if (parsed.pathname.endsWith("/seat-status/refresh")) {
        refreshCalls += 1;
        expect(options.method).toBe("POST");
        return response([observedSoldOutTimetable(travelDate, newEvidenceId)]);
      }
      return response([observedSoldOutTimetable(travelDate, oldEvidenceId)]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<OwnedNewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

    await selectStation(user, "출발역", "서울");
    await selectStation(user, "도착역", "부산");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 901" });
    await user.click(seatWaitButton("KTX 901", "일반실 취소표 대기"));

    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
    expect(refreshCalls).toBe(1);
    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete.mock.calls[0]?.[0].train.seat_classes[0]?.registration_evidence_id)
      .toBe(oldEvidenceId);
    expect(onComplete.mock.calls[1]?.[0].train.seat_classes[0]?.registration_evidence_id)
      .toBe(newEvidenceId);
    await user.click(screen.getByRole("button", { name: "일반실 대기 취소" }));
    expect(onComplete).toHaveBeenCalledTimes(2);
  });

  it("does not retry creation when refreshing expired registration evidence fails", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    const onComplete = vi.fn().mockRejectedValue(expiredEvidenceConflict());
    let refreshCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
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
    render(<OwnedNewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

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
    expect(buttonElement(screen.getByRole("button", { name: "일반실 다시 등록" })).disabled)
      .toBe(false);
  });

  it("does not retry creation when the refreshed train identity changed", async () => {
    const user = userEvent.setup();
    const travelDate = seoulDate();
    const onComplete = vi.fn().mockRejectedValue(expiredEvidenceConflict());
    let refreshCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL | Request) => {
      const parsed = requestUrl(url);
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
    render(<OwnedNewWait demo={false} onComplete={onComplete} onCancel={vi.fn()} />);

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
    render(<OwnedNewWait demo onComplete={onComplete} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await screen.findByRole("article", { name: "KTX 033" });
    await user.click(seatWaitButton("KTX 033"));
    expect((await screen.findByRole("alert")).textContent).toContain("등록 요청이 실패했습니다.");
    const retry = buttonElement(screen.getByRole("button", { name: "일반실 다시 등록" }));
    expect(retry.disabled).toBe(false);
    expect(seatWaitButton("KTX 033", "특실로 대기").disabled).toBe(false);

    await user.click(retry);
    expect(await screen.findByRole("button", { name: "일반실 대기 취소" })).toBeTruthy();
    expect(onComplete).toHaveBeenCalledTimes(2);
  });
});
