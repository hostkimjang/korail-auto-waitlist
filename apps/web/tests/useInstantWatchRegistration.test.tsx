import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useInstantWatchRegistration } from "../src/features/new-wait/useInstantWatchRegistration";

describe("useInstantWatchRegistration", () => {
  it("stores the sole created watch id as an active seat registration", async () => {
    const { result } = renderHook(() => useInstantWatchRegistration());

    await act(async () => {
      await expect(result.current.register("train::standard", async () => ({ id: "watch-1" }))).resolves.toBe(true);
    });

    expect(result.current.getRegistrationState("train::standard")).toEqual({
      status: "active",
      watchId: "watch-1",
      reservationPolicy: "notify_only",
    });
    expect(result.current.successCount).toBe(1);
  });

  it("rejects duplicate creation while pending or active", async () => {
    const { result } = renderHook(() => useInstantWatchRegistration());
    let resolveTask: ((value: { id: string }) => void) | undefined;
    const task = vi.fn(() => new Promise<{ id: string }>((resolve) => { resolveTask = resolve; }));

    let firstAttempt: Promise<boolean> | undefined;
    act(() => {
      firstAttempt = result.current.register("train::standard", task);
    });
    expect(result.current.getRegistrationState("train::standard")).toEqual({ status: "pending" });

    await act(async () => {
      await expect(result.current.register("train::standard", task)).resolves.toBe(false);
    });
    expect(task).toHaveBeenCalledOnce();

    await act(async () => {
      resolveTask?.({ id: "watch-1" });
      await firstAttempt;
    });
    await act(async () => {
      await expect(result.current.register("train::standard", task)).resolves.toBe(false);
    });
    expect(task).toHaveBeenCalledOnce();
  });

  it("cancels an active registration once and returns it to idle", async () => {
    const { result } = renderHook(() => useInstantWatchRegistration());
    await act(async () => {
      await result.current.register("train::standard", async () => [{
        id: "watch-1",
        reservationPolicy: "reserve_once_before_payment",
      }]);
    });
    const cancelTask = vi.fn(async () => undefined);

    await act(async () => {
      await expect(result.current.cancel("train::standard", cancelTask)).resolves.toBe(true);
    });

    expect(cancelTask).toHaveBeenCalledWith("watch-1");
    expect(result.current.getRegistrationState("train::standard")).toEqual({ status: "idle" });
    expect(result.current.successCount).toBe(0);
  });

  it("cancels a hydrated DB registration with its persisted watch id", async () => {
    const { result } = renderHook(() => useInstantWatchRegistration());
    const cancelTask = vi.fn(async () => undefined);

    await act(async () => {
      await expect(result.current.cancel("train::standard", cancelTask, "watch-persisted")).resolves.toBe(true);
    });

    expect(cancelTask).toHaveBeenCalledWith("watch-persisted");
    expect(result.current.getRegistrationState("train::standard")).toEqual({ status: "idle" });
  });

  it("keeps the active watch id and a readable error when cancellation fails", async () => {
    const { result } = renderHook(() => useInstantWatchRegistration());
    await act(async () => {
      await result.current.register("train::standard", async () => ({ id: "watch-1" }));
    });

    await act(async () => {
      await expect(result.current.cancel("train::standard", async () => {
        throw new Error("취소 요청 실패");
      })).resolves.toBe(false);
    });

    expect(result.current.getRegistrationState("train::standard")).toEqual({
      status: "active",
      watchId: "watch-1",
      reservationPolicy: "notify_only",
      message: "취소 요청 실패",
    });
  });

  it("fails the registration when its task does not return exactly one watch", async () => {
    const { result } = renderHook(() => useInstantWatchRegistration());

    await act(async () => {
      await expect(result.current.register("train::standard", async () => [])).resolves.toBe(false);
    });

    expect(result.current.getRegistrationState("train::standard")).toEqual({
      status: "error",
      message: "대기 등록 결과가 정확히 하나여야 합니다.",
    });
  });
});
