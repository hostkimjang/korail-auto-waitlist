import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createLiveDataReloadCoordinator,
  type VisibilityTarget,
} from "../src/features/app/liveDataReloadCoordinator";

class FakeVisibilityTarget implements VisibilityTarget {
  visibilityState: DocumentVisibilityState = "visible";
  private readonly listeners = new Set<() => void>();

  addEventListener(_type: "visibilitychange", listener: () => void): void {
    this.listeners.add(listener);
  }

  removeEventListener(_type: "visibilitychange", listener: () => void): void {
    this.listeners.delete(listener);
  }

  setVisibility(state: DocumentVisibilityState): void {
    this.visibilityState = state;
    for (const listener of this.listeners) listener();
  }
}

describe("live data reload coordinator", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls only while visible and reloads immediately after returning to the tab", async () => {
    vi.useFakeTimers();
    const visibility = new FakeVisibilityTarget();
    const reload = vi.fn().mockResolvedValue(undefined);
    const coordinator = createLiveDataReloadCoordinator(reload, 50, {
      pollIntervalMs: 5_000,
      visibilityTarget: visibility,
    });

    coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(reload).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5_000);
    expect(reload).toHaveBeenCalledTimes(2);

    visibility.setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(reload).toHaveBeenCalledTimes(2);

    visibility.setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(reload).toHaveBeenCalledTimes(3);

    coordinator.dispose();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it("collapses hidden invalidations into one reload when the tab becomes visible", async () => {
    vi.useFakeTimers();
    const visibility = new FakeVisibilityTarget();
    const reload = vi.fn().mockResolvedValue(undefined);
    const coordinator = createLiveDataReloadCoordinator(reload, 50, {
      pollIntervalMs: 5_000,
      visibilityTarget: visibility,
    });

    coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(reload).toHaveBeenCalledOnce();

    visibility.setVisibility("hidden");
    coordinator.request("routine");
    coordinator.request("routine");
    coordinator.request("immediate");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(reload).toHaveBeenCalledOnce();

    visibility.setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(reload).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(50);
    expect(reload).toHaveBeenCalledTimes(2);

    coordinator.dispose();
  });

  it("folds routine invalidation bursts into the fixed recovery poll", async () => {
    vi.useFakeTimers();
    const visibility = new FakeVisibilityTarget();
    const reload = vi.fn().mockResolvedValue(undefined);
    const coordinator = createLiveDataReloadCoordinator(reload, 50, {
      pollIntervalMs: 5_000,
      visibilityTarget: visibility,
    });

    coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(reload).toHaveBeenCalledOnce();

    coordinator.request("routine");
    coordinator.request("routine");
    await vi.advanceTimersByTimeAsync(4_999);
    expect(reload).toHaveBeenCalledOnce();

    await vi.advanceTimersByTimeAsync(1);
    expect(reload).toHaveBeenCalledTimes(2);

    coordinator.request("routine");
    coordinator.request("routine");
    await vi.advanceTimersByTimeAsync(4_999);
    expect(reload).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(1);
    expect(reload).toHaveBeenCalledTimes(3);
    coordinator.dispose();
  });

  it("keeps immediate invalidations ahead of a pending routine poll", async () => {
    vi.useFakeTimers();
    const visibility = new FakeVisibilityTarget();
    const reload = vi.fn().mockResolvedValue(undefined);
    const coordinator = createLiveDataReloadCoordinator(reload, 50, {
      pollIntervalMs: 5_000,
      visibilityTarget: visibility,
    });

    coordinator.start();
    await vi.advanceTimersByTimeAsync(0);
    coordinator.request("routine");
    coordinator.request("immediate");

    await vi.advanceTimersByTimeAsync(49);
    expect(reload).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(reload).toHaveBeenCalledTimes(2);

    coordinator.dispose();
  });
});
