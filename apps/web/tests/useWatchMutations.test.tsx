import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useWatchMutations,
  type UseWatchMutationsOptions,
  type WatchCancellationResult,
  type WatchMutationRecord,
} from "../src/features/app/useWatchMutations";

function watch(
  id: string,
  status: WatchMutationRecord["status"] = "watching",
  extra: Partial<WatchMutationRecord> = {},
): WatchMutationRecord {
  return {
    id,
    provider: "KORAIL",
    status,
    candidates: [],
    payment_deadline: null,
    created_at: null,
    updated_at: null,
    official_booking_url: null,
    reservation_policy: "notify_only",
    train: "KTX 001",
    route: "서울 → 부산",
    departure: "09:00",
    arrival: "11:30",
    date: "8월 4일 (화)",
    statusLabel: status,
    seatClass: "standard",
    seatClassLabel: "일반실",
    seatEvidenceLabel: "일반실 · 확인 불가",
    registrationEvidenceLabel: "등록 근거 없음",
    activityLabel: "확인 전",
    lastCheckedAt: null,
    lastCheckedLabel: "확인 전",
    origin: "서울",
    destination: "부산",
    travelDate: "2026-08-04",
    officialBookingUrl: null,
    operational: null,
    latestReservationAttempt: null,
    seatFoundObservation: null,
    reservationCandidateContexts: {},
    reservationPolicy: "notify_only",
    seatObservationMode: "balanced",
    focusedObservationIntervalSeconds: 10,
    nextCheckAt: null,
    ...extra,
  };
}

function options(
  overrides: Partial<UseWatchMutationsOptions> = {},
): UseWatchMutationsOptions {
  return {
    demo: false,
    watches: [watch("watch-1"), watch("watch-2")],
    commitWatches: vi.fn(),
    pushToast: vi.fn(),
    beginReservationPolicyMutation: vi.fn(),
    endReservationPolicyMutation: vi.fn(),
    requestWatchesRefresh: vi.fn(),
    pauseWatchRequest: vi.fn().mockResolvedValue(watch("watch-1", "paused")),
    startWatchRequest: vi.fn().mockResolvedValue(watch("watch-1", "watching")),
    cancelWatchRequest: vi.fn().mockResolvedValue(watch("watch-1", "expired")),
    updateWatchRequest: vi.fn().mockResolvedValue(watch("watch-1", "watching", {
      reservation_policy: "reserve_once_before_payment",
      reservationPolicy: "reserve_once_before_payment",
    })),
    deleteWatchRequest: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function renderMutationHarness(overrides: Partial<UseWatchMutationsOptions> = {}) {
  const dependencies = options(overrides);
  const hook = renderHook(() => {
    const [watches, commitWatches] = useState<ReadonlyArray<WatchMutationRecord>>(
      dependencies.watches,
    );
    const controller = useWatchMutations({
      ...dependencies,
      watches,
      commitWatches,
    });
    return { watches, controller };
  });
  return { ...hook, dependencies };
}

describe("useWatchMutations", () => {
  it("pauses and resumes a demo watch immutably without calling live requests", async () => {
    const first = watch("watch-1", "watching", { activityLabel: "first" });
    const second = watch("watch-2", "watching", { activityLabel: "second" });
    const { result, dependencies } = renderMutationHarness({
      demo: true,
      watches: [first, second],
    });

    await act(() => result.current.controller.pauseWatch("watch-1"));
    expect(result.current.watches[0]).toMatchObject({
      id: "watch-1",
      status: "paused",
      statusLabel: "일시정지",
      activityLabel: "first",
    });
    expect(result.current.watches[0]).not.toBe(first);
    expect(result.current.watches[1]).toBe(second);
    expect(dependencies.pauseWatchRequest).not.toHaveBeenCalled();
    expect(dependencies.pushToast).toHaveBeenLastCalledWith("대기를 일시정지했습니다.");

    const paused = result.current.watches[0];
    await act(() => result.current.controller.resumeWatch("watch-1"));
    expect(result.current.watches[0]).toMatchObject({ status: "watching", statusLabel: "감시 중" });
    expect(result.current.watches[0]).not.toBe(paused);
    expect(result.current.watches[1]).toBe(second);
    expect(dependencies.startWatchRequest).not.toHaveBeenCalled();
    expect(dependencies.pushToast).toHaveBeenLastCalledWith("대기를 재개했습니다.");
  });

  it("uses live pause and start responses as the exact replacement snapshots", async () => {
    const paused = watch("watch-1", "paused", { updated_at: "2026-08-04T01:00:00Z" });
    const resumed = watch("watch-1", "watching", { updated_at: "2026-08-04T01:01:00Z" });
    const pauseWatchRequest = vi.fn().mockResolvedValue(paused);
    const startWatchRequest = vi.fn().mockResolvedValue(resumed);
    const { result } = renderMutationHarness({ pauseWatchRequest, startWatchRequest });

    await act(() => result.current.controller.pauseWatch("watch-1"));
    expect(pauseWatchRequest).toHaveBeenCalledWith("watch-1");
    expect(result.current.watches[0]).toBe(paused);

    await act(() => result.current.controller.resumeWatch("watch-1"));
    expect(startWatchRequest).toHaveBeenCalledWith("watch-1");
    expect(result.current.watches[0]).toBe(resumed);
  });

  it("swallows pause, resume, and delete errors after forwarding their messages", async () => {
    const pauseWatchRequest = vi.fn().mockRejectedValue(new Error("pause failed"));
    const startWatchRequest = vi.fn().mockRejectedValue(new Error("resume failed"));
    const deleteWatchRequest = vi.fn().mockRejectedValue(new Error("delete failed"));
    const pushToast = vi.fn();
    const { result } = renderMutationHarness({
      pauseWatchRequest,
      startWatchRequest,
      deleteWatchRequest,
      pushToast,
    });
    const initial = result.current.watches;

    await expect(act(() => result.current.controller.pauseWatch("watch-1")))
      .resolves.toBeUndefined();
    await expect(act(() => result.current.controller.resumeWatch("watch-1")))
      .resolves.toBeUndefined();
    await expect(act(() => result.current.controller.deleteWatchRecord("watch-1")))
      .resolves.toBeUndefined();

    expect(result.current.watches).toBe(initial);
    expect(pushToast.mock.calls.map(([message]) => message)).toEqual([
      "pause failed",
      "resume failed",
      "delete failed",
    ]);
  });

  it("cancels demo and live watches with their exact results", async () => {
    const demoCurrent = watch("watch-1", "watching", { activityLabel: "demo" });
    const demo = renderMutationHarness({ demo: true, watches: [demoCurrent] });
    let demoResult: WatchCancellationResult | undefined;
    await act(async () => {
      demoResult = await demo.result.current.controller.cancelWatch("watch-1");
    });
    expect(demoResult).toEqual({
      ...demoCurrent,
      status: "expired",
      statusLabel: "만료",
    });
    expect(demo.result.current.watches[0]).toBe(demoResult);
    expect(demo.dependencies.cancelWatchRequest).not.toHaveBeenCalled();

    const liveResult = watch("watch-1", "expired", { updated_at: "2026-08-04T01:02:00Z" });
    const cancelWatchRequest = vi.fn().mockResolvedValue(liveResult);
    const live = renderMutationHarness({ cancelWatchRequest });
    let returned: WatchCancellationResult | undefined;
    await act(async () => {
      returned = await live.result.current.controller.cancelWatch("watch-1");
    });
    expect(returned).toBe(liveResult);
    expect(live.result.current.watches[0]).toBe(liveResult);
    expect(live.dependencies.pushToast).toHaveBeenLastCalledWith("대기를 취소했습니다.");
  });

  it("returns a typed demo cancellation without inserting an unknown watch", async () => {
    const dependencies = options({ demo: true, watches: [] });
    const { result } = renderHook(() => useWatchMutations(dependencies));
    let returned: WatchCancellationResult | undefined;

    await act(async () => {
      returned = await result.current.cancelWatch("missing-watch");
    });

    expect(returned).toEqual({
      id: "missing-watch",
      status: "expired",
      statusLabel: "만료",
    });
    expect(dependencies.commitWatches).not.toHaveBeenCalled();
    expect(dependencies.cancelWatchRequest).not.toHaveBeenCalled();
    expect(dependencies.pushToast).toHaveBeenCalledWith("대기를 취소했습니다.");
  });

  it("rethrows cancel errors after toast and leaves the snapshot unchanged", async () => {
    const failure = new Error("cancel failed");
    const pushToast = vi.fn();
    const { result } = renderMutationHarness({
      cancelWatchRequest: vi.fn().mockRejectedValue(failure),
      pushToast,
    });
    const initial = result.current.watches;

    await expect(result.current.controller.cancelWatch("watch-1")).rejects.toBe(failure);
    expect(pushToast).toHaveBeenCalledWith("cancel failed");
    expect(result.current.watches).toBe(initial);
  });

  it("deletes demo and live records only after the applicable request succeeds", async () => {
    const demo = renderMutationHarness({ demo: true });
    await act(() => demo.result.current.controller.deleteWatchRecord("watch-1"));
    expect(demo.dependencies.deleteWatchRequest).not.toHaveBeenCalled();
    expect(demo.result.current.watches.map((item) => item.id)).toEqual(["watch-2"]);

    const deleteWatchRequest = vi.fn().mockResolvedValue({ status: "deleted" });
    const live = renderMutationHarness({ deleteWatchRequest });
    await act(() => live.result.current.controller.deleteWatchRecord("watch-1"));
    expect(deleteWatchRequest).toHaveBeenCalledWith("watch-1");
    expect(live.result.current.watches.map((item) => item.id)).toEqual(["watch-2"]);
    expect(live.dependencies.pushToast).toHaveBeenLastCalledWith("대기 기록을 삭제했습니다.");
  });

  it("guards a live policy mutation and refreshes only after it settles", async () => {
    const order: string[] = [];
    let resolveUpdate: ((value: WatchMutationRecord) => void) | undefined;
    const update = new Promise<WatchMutationRecord>((resolve) => { resolveUpdate = resolve; });
    const updateWatchRequest = vi.fn(() => {
      order.push("request");
      return update;
    });
    const updated = watch("watch-1", "watching", {
      reservation_policy: "reserve_once_before_payment",
      reservationPolicy: "reserve_once_before_payment",
    });
    const { result, dependencies } = renderMutationHarness({
      beginReservationPolicyMutation: vi.fn(() => order.push("begin")),
      endReservationPolicyMutation: vi.fn(() => order.push("end")),
      requestWatchesRefresh: vi.fn(() => order.push("refresh")),
      pushToast: vi.fn((message) => order.push(`toast:${message}`)),
      updateWatchRequest,
    });
    let task: Promise<void> | undefined;

    act(() => {
      task = result.current.controller.changeReservationPolicy(
        "watch-1",
        "reserve_once_before_payment",
      );
    });
    expect(result.current.controller.reservationPolicyUpdatingIds.has("watch-1")).toBe(true);
    expect(order).toEqual(["begin", "request"]);
    expect(updateWatchRequest).toHaveBeenCalledWith("watch-1", {
      reservation_policy: "reserve_once_before_payment",
    });

    await act(async () => {
      resolveUpdate?.(updated);
      await task;
    });
    expect(result.current.watches[0]).toBe(updated);
    expect(result.current.controller.reservationPolicyUpdatingIds.has("watch-1")).toBe(false);
    expect(order).toEqual([
      "begin",
      "request",
      "toast:좌석 재발견마다 자동 예매하도록 변경했습니다. 같은 좌석 가용성 에피소드에서는 중복 요청하지 않으며 결제는 직접 진행합니다.",
      "end",
      "refresh",
    ]);
    expect(dependencies.endReservationPolicyMutation).toHaveBeenCalledOnce();
    expect(dependencies.requestWatchesRefresh).toHaveBeenCalledOnce();
  });

  it("always ends the policy guard, refreshes, and clears the updating id after errors", async () => {
    const order: string[] = [];
    const pushToast = vi.fn((message) => order.push(`toast:${message}`));
    const { result } = renderMutationHarness({
      beginReservationPolicyMutation: vi.fn(() => order.push("begin")),
      endReservationPolicyMutation: vi.fn(() => order.push("end")),
      requestWatchesRefresh: vi.fn(() => order.push("refresh")),
      pushToast,
      updateWatchRequest: vi.fn().mockRejectedValue(new Error("policy failed")),
    });

    await expect(act(() => result.current.controller.changeReservationPolicy(
      "watch-1",
      "notify_only",
    ))).resolves.toBeUndefined();

    expect(order).toEqual(["begin", "toast:policy failed", "end", "refresh"]);
    expect(result.current.controller.reservationPolicyUpdatingIds.size).toBe(0);
    expect(result.current.watches[0]?.reservationPolicy).toBe("notify_only");
  });

  it("updates demo policy locally and uses the latest demo watch snapshot", async () => {
    const first = watch("watch-1", "watching", { updated_at: "2026-08-04T01:00:00Z" });
    const latest = watch("watch-1", "watching", { updated_at: "2026-08-04T01:01:00Z" });
    const initial = options({ demo: false, watches: [first] });
    const { result, rerender } = renderHook(
      ({ value }) => useWatchMutations(value),
      { initialProps: { value: initial } },
    );
    const stablePause = result.current.pauseWatch;

    rerender({ value: initial });
    expect(result.current.pauseWatch).toBe(stablePause);

    const commitWatches = vi.fn();
    const latestOptions = options({
      demo: true,
      watches: [latest],
      commitWatches,
    });
    rerender({ value: latestOptions });
    await act(() => result.current.changeReservationPolicy("watch-1", "notify_only"));

    expect(latestOptions.updateWatchRequest).not.toHaveBeenCalled();
    expect(commitWatches).toHaveBeenCalledOnce();
    const updater = commitWatches.mock.calls[0]?.[0];
    expect(typeof updater).toBe("function");
    if (typeof updater === "function") {
      expect(updater([latest])).toEqual([{ ...latest, reservationPolicy: "notify_only" }]);
    }
    expect(latestOptions.pushToast).toHaveBeenCalledWith(
      "자동 예매를 끄고 좌석 감시와 알림만 유지합니다.",
    );
  });
});
