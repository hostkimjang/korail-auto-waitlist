import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import {
  initialNotificationCenterState,
  pushNotifications as reduceNotifications,
  type AppNotificationInput,
  type NotificationCenterState,
} from "../src/features/app/notificationCenter";
import type { WatchLifecycleSnapshot } from "../src/features/app/watchLifecycleSnapshot";

const eventApi = vi.hoisted(() => ({
  subscribeToEvents: vi.fn((_handler: (event: unknown) => void) => () => undefined),
}));

vi.mock("../src/api/events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/events")>();
  return { ...actual, subscribeToEvents: eventApi.subscribeToEvents };
});

import { useWatchCollection } from "../src/features/app/useWatchCollection";

interface TestWatch {
  id: string;
  status: "watching" | "seat_found" | "reserving" | "payment_required" | "auth_required";
  reservationPolicy: "notify_only" | "reserve_once_before_payment";
}

function snapshotOf(value: TestWatch): WatchLifecycleSnapshot {
  const hasAttempt = ["reserving", "payment_required", "auth_required"].includes(value.status);
  return {
    id: value.id,
    status: value.status,
    provider: "KORAIL",
    route: "서울 → 부산",
    train: "KTX 085",
    seatClassLabel: "일반실",
    date: "8월 1일 (토)",
    departure: "14:11",
    arrival: "16:52",
    latestReservationAttempt: hasAttempt
      ? {
          startedAt: "2026-08-03T12:09:45Z",
          finishedAt: value.status === "reserving" ? null : "2026-08-03T12:09:48Z",
          paymentHoldEndedAt: null,
        }
      : null,
    paymentDeadline: null,
    reservationCandidateContexts: {},
    reservationPolicy: value.reservationPolicy,
    seatFoundObservation: value.status === "seat_found"
      ? { observedAt: "2026-08-01T03:45:00Z" }
      : null,
    updatedAt: hasAttempt ? "2026-08-03T12:09:48Z" : null,
  };
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

function watch(
  reservationPolicy: TestWatch["reservationPolicy"],
  status: TestWatch["status"] = "watching",
): TestWatch {
  return {
    id: "watch-one",
    status,
    reservationPolicy,
  };
}

describe("useWatchCollection", () => {
  afterEach(() => {
    vi.clearAllMocks();
    eventApi.subscribeToEvents.mockImplementation((_handler) => () => undefined);
  });

  it("projects lifecycle snapshots without replacing an unchanged actual watch", async () => {
    const initial = watch("notify_only");
    const loadWatches = vi.fn().mockResolvedValue([{ ...initial }]);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const { result, unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [initial],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));

    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());
    await waitFor(() => expect(result.current.watches[0]).toBe(initial));

    unmount();
  });

  it("hydrates actionable canonical states on each mount while keeping seat-found as baseline", async () => {
    const canonical = [
      { ...watch("reserve_once_before_payment", "reserving"), id: "reserving" },
      { ...watch("reserve_once_before_payment", "payment_required"), id: "payment" },
      { ...watch("reserve_once_before_payment", "auth_required"), id: "auth" },
      { ...watch("notify_only", "seat_found"), id: "seat" },
    ];
    const mountCollection = () => {
      const pushNotifications = vi.fn();
      const loadWatches = vi.fn().mockResolvedValue(canonical);
      const onAuthenticationExpired = vi.fn();
      const onProviderAuthenticationTransition = vi.fn();
      const rendered = renderHook(() => useWatchCollection({
        authenticated: true,
        demo: false,
        initialWatches: [] as TestWatch[],
        pollIntervalSeconds: 300,
        loadWatches,
        snapshotOf,
        onAuthenticationExpired,
        onProviderAuthenticationTransition,
        pushNotifications,
      }));
      return { ...rendered, pushNotifications };
    };

    const first = mountCollection();
    await waitFor(() => expect(first.pushNotifications).toHaveBeenCalled());
    expect(first.pushNotifications).toHaveBeenLastCalledWith(expect.arrayContaining([
      expect.objectContaining({ subjectKey: "watch:reserving", kind: "reserving" }),
      expect.objectContaining({ subjectKey: "watch:payment", kind: "payment_required" }),
      expect.objectContaining({ subjectKey: "watch:auth", kind: "auth_required" }),
    ]));
    expect(first.pushNotifications.mock.calls.at(-1)?.[0]).toHaveLength(3);
    first.unmount();

    const remounted = mountCollection();
    await waitFor(() => expect(remounted.pushNotifications).toHaveBeenCalled());
    expect(remounted.pushNotifications.mock.calls.at(-1)?.[0]).toHaveLength(3);
    remounted.unmount();
  });

  it("keeps one same-watch notice through SSE, canonical reserving, and terminal result", async () => {
    const initial = watch("reserve_once_before_payment", "watching");
    const reserving = watch("reserve_once_before_payment", "reserving");
    const payment = watch("reserve_once_before_payment", "payment_required");
    const loadWatches = vi.fn()
      .mockResolvedValueOnce([initial])
      .mockResolvedValueOnce([reserving])
      .mockResolvedValue([payment]);
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
    let notificationState: NotificationCenterState = initialNotificationCenterState;
    const pushNotifications = vi.fn((inputs: ReadonlyArray<AppNotificationInput>) => {
      notificationState = reduceNotifications(notificationState, inputs);
    });
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const { unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [] as TestWatch[],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(1));

    act(() => {
      onEvent?.({
        id: "attempt-one",
        event_type: "watch.reservation_attempted",
        aggregate_id: initial.id,
        created_at: "2026-08-03T12:09:45Z",
        payload: { watch_id: initial.id, outcome: "pending" },
      });
    });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    expect(notificationState.notices).toHaveLength(1);
    expect(notificationState.notices[0]).toMatchObject({
      subjectKey: `watch:${initial.id}`,
      kind: "reserving",
      persistence: "sticky",
    });

    act(() => {
      onEvent?.({
        id: "result-one",
        event_type: "watch.reservation_result",
        aggregate_id: initial.id,
        created_at: "2026-08-03T12:09:48Z",
        payload: {
          watch_id: initial.id,
          attempt_started_at: "2026-08-03T12:09:45Z",
          attempt_finished_at: "2026-08-03T12:09:48Z",
          outcome: "payment_required",
        },
      });
    });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(3));
    expect(notificationState.notices).toHaveLength(1);
    expect(notificationState.notices[0]).toMatchObject({
      subjectKey: `watch:${initial.id}`,
      kind: "payment_required",
    });
    unmount();
  });

  it("keeps one subscription and uses the latest inline projector for SSE and REST", async () => {
    const initial = watch("notify_only");
    const firstReload = deferred<ReadonlyArray<TestWatch>>();
    const seatFound = watch("notify_only", "seat_found");
    const loadWatches = vi.fn()
      .mockReturnValueOnce(firstReload.promise)
      .mockResolvedValue([seatFound]);
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const { rerender, unmount } = renderHook(
      ({ trainLabel }) => useWatchCollection({
        authenticated: true,
        demo: false,
        initialWatches: [initial],
        pollIntervalSeconds: 300,
        loadWatches,
        snapshotOf: (value) => ({ ...snapshotOf(value), train: trainLabel }),
        onAuthenticationExpired,
        onProviderAuthenticationTransition,
        pushNotifications,
      }),
      { initialProps: { trainLabel: "OLD 085" } },
    );

    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());
    expect(eventApi.subscribeToEvents).toHaveBeenCalledOnce();

    rerender({ trainLabel: "LATEST 085" });

    expect(loadWatches).toHaveBeenCalledOnce();
    expect(eventApi.subscribeToEvents).toHaveBeenCalledOnce();

    act(() => {
      onEvent?.({
        id: "inline-attempt",
        event_type: "watch.reservation_attempted",
        aggregate_id: initial.id,
        created_at: "2026-08-01T03:44:59Z",
        payload: { watch_id: initial.id, outcome: "pending" },
      });
    });
    expect(pushNotifications).toHaveBeenCalledWith([
      expect.objectContaining({
        kind: "reserving",
        meta: "KORAIL · LATEST 085 · 일반실",
      }),
    ]);

    await act(async () => {
      firstReload.resolve([seatFound]);
      await firstReload.promise;
    });
    await waitFor(() => expect(pushNotifications.mock.calls.flatMap(([items]) => items))
      .toContainEqual(expect.objectContaining({
        kind: "seat_found",
        meta: "KORAIL · LATEST 085 · 일반실",
      })));

    expect(eventApi.subscribeToEvents).toHaveBeenCalledOnce();
    unmount();
  });

  it("coalesces duplicate PWA notification hints and removes the listener on cleanup", async () => {
    const initial = watch("notify_only");
    const loadWatches = vi.fn()
      .mockResolvedValueOnce([initial])
      .mockResolvedValue([watch("notify_only", "seat_found")]);
    let onPwaNotification: ((event: MessageEvent<unknown>) => void) | undefined;
    const addEventListener = vi.fn(
      (_type: "message", listener: (event: MessageEvent<unknown>) => void) => {
        onPwaNotification = listener;
      },
    );
    const removeEventListener = vi.fn();
    const originalServiceWorker = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { addEventListener, removeEventListener },
    });
    const pushNotifications = vi.fn();
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const { unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [initial],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());

    act(() => {
      const data = {
        type: "railwait:notification",
        kind: "push",
        watchId: "watch-one",
        status: "seat_found",
      };
      onPwaNotification?.({ data } as MessageEvent<unknown>);
      onPwaNotification?.({ data } as MessageEvent<unknown>);
    });

    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(pushNotifications.mock.calls.flatMap(([items]) => items))
      .toContainEqual(expect.objectContaining({ kind: "seat_found" })));
    expect(loadWatches).toHaveBeenCalledTimes(2);

    unmount();
    expect(removeEventListener).toHaveBeenCalledWith("message", onPwaNotification);
    if (originalServiceWorker === undefined) {
      Reflect.deleteProperty(navigator, "serviceWorker");
    } else {
      Object.defineProperty(navigator, "serviceWorker", originalServiceWorker);
    }
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
        snapshotOf,
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
        snapshotOf,
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
