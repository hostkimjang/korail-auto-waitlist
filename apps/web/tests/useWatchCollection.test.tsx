import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import type { WatchSnapshot } from "../src/features/app/watchSnapshots";

const eventApi = vi.hoisted(() => ({
  subscribeToEvents: vi.fn((_handler: (event: unknown) => void) => () => undefined),
}));

vi.mock("../src/api/events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/events")>();
  return { ...actual, subscribeToEvents: eventApi.subscribeToEvents };
});

import { useWatchCollection } from "../src/features/app/useWatchCollection";

interface TestWatch extends WatchSnapshot {
  reservationPolicy: "notify_only" | "reserve_once_before_payment";
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolvePromise: ((value: T) => void) | undefined;
  let rejectPromise: ((reason: unknown) => void) | undefined;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return {
    promise,
    resolve: (value) => resolvePromise?.(value),
    reject: (reason) => rejectPromise?.(reason),
  };
}

function watch(reservationPolicy: TestWatch["reservationPolicy"]): TestWatch {
  return {
    id: "watch-one",
    status: "watching",
    reservationPolicy,
  };
}

describe("useWatchCollection", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("rejects a canonical GET snapshot that crosses a reservation-policy mutation", async () => {
    const staleReload = deferred<ReadonlyArray<TestWatch>>();
    const loadWatches = vi.fn()
      .mockReturnValueOnce(staleReload.promise)
      .mockResolvedValue([watch("reserve_once_before_payment")]);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const { result, unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [watch("notify_only")],
      pollIntervalSeconds: 300,
      loadWatches,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));

    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(1));
    act(() => {
      result.current.beginReservationPolicyMutation();
      result.current.commitWatches([watch("reserve_once_before_payment")]);
    });
    await act(async () => {
      staleReload.resolve([watch("notify_only")]);
      await staleReload.promise;
    });

    expect(result.current.watches[0]?.reservationPolicy)
      .toBe("reserve_once_before_payment");

    act(() => {
      result.current.endReservationPolicyMutation();
      result.current.requestRefresh();
    });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    expect(result.current.watches[0]?.reservationPolicy)
      .toBe("reserve_once_before_payment");

    unmount();
  });

  it("does not let a disposed poll lifecycle overwrite the current canonical snapshot", async () => {
    const staleReload = deferred<ReadonlyArray<TestWatch>>();
    const currentReload = deferred<ReadonlyArray<TestWatch>>();
    const loadWatches = vi.fn()
      .mockReturnValueOnce(staleReload.promise)
      .mockReturnValueOnce(currentReload.promise);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const { result, rerender, unmount } = renderHook(
      ({ pollIntervalSeconds }) => useWatchCollection({
        authenticated: true,
        demo: false,
        initialWatches: [watch("notify_only")],
        pollIntervalSeconds,
        loadWatches,
        onAuthenticationExpired,
        onProviderAuthenticationTransition,
        pushNotifications,
      }),
      { initialProps: { pollIntervalSeconds: 300 } },
    );

    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(1));
    rerender({ pollIntervalSeconds: 299 });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));

    await act(async () => {
      currentReload.resolve([watch("reserve_once_before_payment")]);
      await currentReload.promise;
    });
    await waitFor(() => {
      expect(result.current.watches[0]?.reservationPolicy)
        .toBe("reserve_once_before_payment");
    });
    expect(pushNotifications).toHaveBeenCalledTimes(1);
    expect(pushNotifications).toHaveBeenLastCalledWith([]);
    pushNotifications.mockClear();

    await act(async () => {
      staleReload.resolve([watch("notify_only")]);
      await staleReload.promise;
    });

    expect(result.current.watches[0]?.reservationPolicy)
      .toBe("reserve_once_before_payment");
    expect(pushNotifications).not.toHaveBeenCalled();
    expect(onAuthenticationExpired).not.toHaveBeenCalled();
    unmount();
  });

  it("isolates queued SSE events and stale authentication failures across logout and login", async () => {
    const staleReload = deferred<ReadonlyArray<TestWatch>>();
    const currentReload = deferred<ReadonlyArray<TestWatch>>();
    const handlers: Array<(event: unknown) => void> = [];
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      handlers.push(handler);
      return () => undefined;
    });
    const loadWatches = vi.fn()
      .mockReturnValueOnce(staleReload.promise)
      .mockReturnValueOnce(currentReload.promise);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const { result, rerender, unmount } = renderHook(
      ({ authenticated }) => useWatchCollection({
        authenticated,
        demo: false,
        initialWatches: [] as TestWatch[],
        pollIntervalSeconds: 300,
        loadWatches,
        onAuthenticationExpired,
        onProviderAuthenticationTransition,
        pushNotifications,
      }),
      { initialProps: { authenticated: true } },
    );

    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(1));
    act(() => {
      handlers[0]?.({
        id: "old-result",
        event_type: "watch.reservation_result",
        aggregate_id: "watch-one",
        created_at: "2026-08-04T00:00:00Z",
        payload: {
          watch_id: "watch-one",
          outcome: "not_available",
          retryable: true,
          manual_check_required: false,
          retry_condition: "new_availability_episode",
        },
      });
    });
    rerender({ authenticated: false });
    await act(async () => {
      staleReload.reject(new ApiError("old session expired", 401));
      await staleReload.promise.catch(() => undefined);
    });
    expect(onAuthenticationExpired).not.toHaveBeenCalled();

    rerender({ authenticated: true });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    await act(async () => {
      currentReload.resolve([watch("reserve_once_before_payment")]);
      await currentReload.promise;
    });
    await waitFor(() => {
      expect(result.current.watches[0]?.reservationPolicy)
        .toBe("reserve_once_before_payment");
    });

    expect(pushNotifications).toHaveBeenCalledTimes(1);
    expect(pushNotifications).toHaveBeenLastCalledWith([]);
    expect(onProviderAuthenticationTransition).not.toHaveBeenCalled();
    unmount();
  });
});
