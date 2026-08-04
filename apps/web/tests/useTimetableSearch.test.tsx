import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RailProvider } from "../src/api/providerAccounts";
import type { NewWaitForm } from "../src/features/new-wait/newWaitForm";
import {
  type TimetableProviderResults,
  type TimetableRequestForm,
  type TimetableSearchResult,
  type TimetableTrainSnapshot,
  useTimetableSearch,
} from "../src/features/new-wait/useTimetableSearch";

interface TestTrain extends TimetableTrainSnapshot {
  name: string;
  timetable_source: "official" | "mock";
}

function form(overrides: Partial<NewWaitForm> = {}): NewWaitForm {
  return {
    provider: "KORAIL",
    providers: ["KORAIL", "SRT"],
    origin: "서울",
    origin_node_id: "N-SEOUL",
    destination: "부산",
    destination_node_id: "N-BUSAN",
    date: "2026-08-05",
    time: "12:00",
    timeEnd: "18:00",
    selectedWeekdays: ["수"],
    passengers: "1",
    seat: "일반실",
    channels: [],
    reservationPolicy: "notify_only",
    ...overrides,
  };
}

function train(provider: RailProvider, name: string, hour: string, source: "official" | "mock" = "official"): TestTrain {
  return {
    id: `${provider}:${name}:${hour}`,
    provider,
    departure_at: `2026-08-05T${hour}:00+09:00`,
    name,
    timetable_source: source,
  };
}

function success(provider: RailProvider, count: number): TimetableProviderResults {
  return { [provider]: { status: "success", provider, count } };
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolvePromise: ((value: T) => void) | undefined;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return {
    promise,
    resolve: (value) => resolvePromise?.(value),
  };
}

function hookOptions(
  currentForm: NewWaitForm,
  loadTimetables: (
    formValue: TimetableRequestForm,
    provider?: RailProvider,
  ) => Promise<TimetableSearchResult<TestTrain>>,
  demo = false,
) {
  return {
    active: true,
    demo,
    form: currentForm,
    loadTimetables,
    loadSeatStatus: vi.fn(async () => [] as TestTrain[]),
    loadCachedSnapshot: vi.fn(async () => null),
    loadDemoTimetables: vi.fn(() => [] as TestTrain[]),
    filterTimetables: vi.fn((_form: TimetableRequestForm, items: TestTrain[]) => items),
    mapTimetable: vi.fn((item: unknown) => {
      if (typeof item !== "object" || item === null || !("id" in item)) {
        throw new Error("invalid test train");
      }
      return item as TestTrain;
    }),
  };
}

describe("useTimetableSearch", () => {
  it("keeps a successful provider result while retrying only the failed provider", async () => {
    const korail = train("KORAIL", "KTX 101", "13:00");
    const srt = train("SRT", "SRT 303", "14:00");
    const loadTimetables = vi.fn(async (
      _form: TimetableRequestForm,
      provider?: RailProvider,
    ): Promise<TimetableSearchResult<TestTrain>> => provider === "SRT"
      ? { trains: [srt], providerResults: success("SRT", 1) }
      : {
        trains: [korail],
        providerResults: {
          ...success("KORAIL", 1),
          SRT: { status: "error", provider: "SRT", message: "temporary" },
        },
      });
    const options = hookOptions(form(), loadTimetables);
    const { result } = renderHook(() => useTimetableSearch(options));

    await waitFor(() => expect(result.current.state.providerResults.SRT?.status).toBe("error"));
    expect(result.current.trains).toEqual([korail]);
    expect(result.current.state.providerResults.SRT?.status).toBe("error");

    await act(() => result.current.retryProvider("SRT"));

    expect(loadTimetables).toHaveBeenLastCalledWith(expect.any(Object), "SRT");
    expect(result.current.trains).toEqual([korail, srt]);
    expect(result.current.state.providerResults).toMatchObject({
      KORAIL: { status: "success" },
      SRT: { status: "success" },
    });
  });

  it("ignores a late response after the timetable query changes", async () => {
    const oldResponse = deferred<TimetableSearchResult<TestTrain>>();
    const newTrain = train("KORAIL", "KTX NEW", "09:30");
    let callCount = 0;
    const loadTimetables = vi.fn(() => {
      callCount += 1;
      return callCount === 1
        ? oldResponse.promise
        : Promise.resolve({ trains: [newTrain], providerResults: success("KORAIL", 1) });
    });
    const baseForm = form({ providers: ["KORAIL"] });
    const options = hookOptions(baseForm, loadTimetables);
    const { result, rerender } = renderHook(
      ({ currentForm }) => useTimetableSearch({ ...options, form: currentForm }),
      { initialProps: { currentForm: baseForm } },
    );

    await waitFor(() => expect(loadTimetables).toHaveBeenCalledTimes(1));
    rerender({ currentForm: { ...baseForm, time: "09:00", timeEnd: "12:00" } });
    await waitFor(() => expect(result.current.trains).toEqual([newTrain]));

    await act(async () => {
      oldResponse.resolve({
        trains: [train("KORAIL", "KTX OLD", "13:30")],
        providerResults: success("KORAIL", 1),
      });
      await oldResponse.promise;
    });

    expect(result.current.trains).toEqual([newTrain]);
    expect(loadTimetables).toHaveBeenCalledTimes(2);
  });

  it("preserves demo provenance without calling the production loader", async () => {
    const demoTrain = train("KORAIL", "KTX DEMO", "13:00", "mock");
    const loadTimetables = vi.fn(async (): Promise<TimetableSearchResult<TestTrain>> => ({
      trains: [],
      providerResults: {},
    }));
    const options = hookOptions(form({ providers: ["KORAIL"] }), loadTimetables, true);
    options.loadDemoTimetables.mockReturnValue([demoTrain]);
    const { result } = renderHook(() => useTimetableSearch(options));

    await waitFor(() => expect(result.current.trains).toEqual([demoTrain]));

    expect(loadTimetables).not.toHaveBeenCalled();
    expect(result.current.trains[0]?.timetable_source).toBe("mock");
    expect(result.current.state.providerResults.KORAIL).toMatchObject({
      status: "success",
      count: 1,
    });
  });

  it("rejects a late seat refresh after the query changes without replacing trains", async () => {
    const oldTrain = train("KORAIL", "KTX 101", "13:00");
    const refreshedTrain = train("KORAIL", "KTX 101 REFRESHED", "13:00");
    const seatResponse = deferred<TestTrain[]>();
    const baseForm = form({ providers: ["KORAIL"] });
    const loadTimetables = vi.fn(async (): Promise<TimetableSearchResult<TestTrain>> => ({
      trains: [oldTrain],
      providerResults: success("KORAIL", 1),
    }));
    const options = hookOptions(baseForm, loadTimetables);
    options.loadSeatStatus.mockImplementation(() => seatResponse.promise);
    const { result, rerender } = renderHook(
      ({ currentForm }) => useTimetableSearch({ ...options, form: currentForm }),
      { initialProps: { currentForm: baseForm } },
    );
    await waitFor(() => expect(result.current.trains).toEqual([oldTrain]));

    let refreshPromise: Promise<TestTrain[]> | undefined;
    act(() => {
      refreshPromise = result.current.refreshProviderSeatStatus("KORAIL");
    });
    rerender({ currentForm: { ...baseForm, time: "09:00", timeEnd: "12:00" } });
    await act(async () => {
      seatResponse.resolve([refreshedTrain]);
      await expect(refreshPromise).rejects.toThrow("조회 조건이 변경되었습니다.");
    });

    expect(result.current.trains).toEqual([oldTrain]);
  });
});
