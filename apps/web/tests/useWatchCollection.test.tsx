import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import {
  initialNotificationCenterState,
  notificationCenterReducer,
  pushNotifications as reduceNotifications,
  type AppNotificationInput,
  type NotificationCenterState,
} from "../src/features/app/notificationCenter";
import type { WatchLifecycleSnapshot } from "../src/features/app/watchLifecycleSnapshot";

const eventApi = vi.hoisted(() => ({
  subscribeToEvents: vi.fn((_handler: (event: unknown) => void) => () => undefined),
}));

const liveNoticeApi = vi.hoisted(() => ({
  buildLiveReservationNotice: vi.fn(),
}));

vi.mock("../src/api/events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/events")>();
  return { ...actual, subscribeToEvents: eventApi.subscribeToEvents };
});

vi.mock("../src/features/app/liveReservationNotice", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/features/app/liveReservationNotice")>();
  return {
    ...actual,
    buildLiveReservationNotice: (
      ...args: Parameters<typeof actual.buildLiveReservationNotice>
    ): ReturnType<typeof actual.buildLiveReservationNotice> => {
      liveNoticeApi.buildLiveReservationNotice(...args);
      return actual.buildLiveReservationNotice(...args);
    },
  };
});

import { useWatchCollection } from "../src/features/app/useWatchCollection";

interface TestWatch {
  id: string;
  status:
    | "watching"
    | "paused"
    | "seat_found"
    | "reserving"
    | "payment_required"
    | "completed"
    | "auth_required"
    | "expired";
  reservationPolicy: "notify_only" | "reserve_once_before_payment";
  recoveredUnknown?: boolean;
}

function snapshotOf(value: TestWatch): WatchLifecycleSnapshot {
  const hasAttempt = value.recoveredUnknown === true
    || ["reserving", "payment_required", "completed", "auth_required"].includes(value.status);
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
          outcome: value.recoveredUnknown
            ? "unknown" as const
            : value.status === "completed"
              ? "payment_required" as const
              : "pending" as const,
          startedAt: "2026-08-03T12:09:45Z",
          finishedAt: value.status === "reserving" ? null : "2026-08-03T12:09:48Z",
          retryable: false,
          manualCheckRequired: value.recoveredUnknown === true,
          retryCondition: null,
          progressStages: value.recoveredUnknown
            ? [{
              stage: "authenticated_session_ready" as const,
              occurredAt: "2026-08-03T12:09:46Z",
            }]
            : [],
          paymentHoldEndedAt: null,
        }
      : null,
    latestReservationAttemptCandidateId: hasAttempt ? "candidate" : null,
    paymentDeadline: null,
    reservationCandidateContexts: {
      candidate: {
        train: "KTX 085",
        seatClassLabel: "일반실",
        date: "8월 1일 (토)",
        departure: "14:11",
        arrival: "16:52",
      },
    },
    reservationPolicy: value.reservationPolicy,
    seatFoundObservation: value.status === "seat_found"
      ? { observedAt: "2026-08-01T03:45:00Z" }
      : null,
    updatedAt: value.status === "completed"
      ? "2026-08-03T12:10:01Z"
      : hasAttempt ? "2026-08-03T12:09:48Z" : null,
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

function reservationProgressedEvent(watchId: string, id: string): unknown {
  return {
    id,
    event_type: "watch.reservation_progressed",
    aggregate_id: watchId,
    created_at: "2026-08-03T12:09:46.200Z",
    payload: {
      watch_id: watchId,
      candidate_id: "candidate",
      attempt_id: "attempt-one",
      attempt_sequence: 1,
      seat_detected_at: null,
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
  };
}

describe("useWatchCollection", () => {
  afterEach(() => {
    vi.useRealTimers();
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
      {
        ...watch("reserve_once_before_payment", "watching"),
        id: "manual",
        recoveredUnknown: true,
      },
      {
        ...watch("reserve_once_before_payment", "expired"),
        id: "manual-expired",
        recoveredUnknown: true,
      },
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
      expect.objectContaining({ subjectKey: "watch:manual", kind: "manual_check" }),
      expect.objectContaining({ subjectKey: "watch:manual-expired", kind: "manual_check" }),
    ]));
    expect(first.pushNotifications.mock.calls.at(-1)?.[0]).toHaveLength(5);
    first.unmount();

    const remounted = mountCollection();
    await waitFor(() => expect(remounted.pushNotifications).toHaveBeenCalled());
    expect(remounted.pushNotifications.mock.calls.at(-1)?.[0]).toHaveLength(5);
    remounted.unmount();
  });

  it("replays progress received while the initial canonical snapshot is loading", async () => {
    const canonical = watch("reserve_once_before_payment", "watching");
    const initialLoad = deferred<ReadonlyArray<TestWatch>>();
    const loadWatches = vi.fn()
      .mockReturnValueOnce(initialLoad.promise)
      .mockResolvedValue([canonical]);
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
    const pushNotifications = vi.fn();
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
    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());

    act(() => onEvent?.(reservationProgressedEvent(canonical.id, "progress-during-load")));
    expect(pushNotifications).not.toHaveBeenCalled();

    await act(async () => {
      initialLoad.resolve([canonical]);
      await initialLoad.promise;
    });
    await waitFor(() => expect(pushNotifications).toHaveBeenCalledWith([
      expect.objectContaining({
        revisionKey: `watch:${canonical.id}:progress-during-load`,
        kind: "reserving",
      }),
    ]));
    unmount();
  });

  it("keeps canonical manual-check terminal ahead of queued initial progress", async () => {
    const canonical = {
      ...watch("reserve_once_before_payment", "watching"),
      recoveredUnknown: true,
    };
    const initialLoad = deferred<ReadonlyArray<TestWatch>>();
    const loadWatches = vi.fn().mockReturnValue(initialLoad.promise);
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
    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());

    act(() => onEvent?.(reservationProgressedEvent(canonical.id, "queued-progress")));
    await act(async () => {
      initialLoad.resolve([canonical]);
      await initialLoad.promise;
    });
    await waitFor(() => expect(notificationState.notices).toHaveLength(1));
    expect(notificationState.notices[0]).toMatchObject({
      subjectKey: `watch:${canonical.id}`,
      kind: "manual_check",
    });
    expect(pushNotifications.mock.calls.at(-1)?.[0]).toEqual([
      expect.objectContaining({ kind: "manual_check" }),
    ]);
    unmount();
  });

  it("reloads a stale snapshot and replays progress after its candidate context arrives", async () => {
    const stale = watch("reserve_once_before_payment", "watching");
    const fresh = { ...stale };
    const loadWatches = vi.fn()
      .mockResolvedValueOnce([stale])
      .mockResolvedValue([fresh]);
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
    const snapshotWithStaleCandidateContext = (value: TestWatch): WatchLifecycleSnapshot => {
      const snapshot = snapshotOf(value);
      return value === stale ? { ...snapshot, reservationCandidateContexts: {} } : snapshot;
    };
    const pushNotifications = vi.fn();
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const { unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [stale],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf: snapshotWithStaleCandidateContext,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());

    act(() => onEvent?.(reservationProgressedEvent(stale.id, "progress-after-stale")));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(pushNotifications).toHaveBeenCalledWith([
      expect.objectContaining({
        revisionKey: `watch:${stale.id}:progress-after-stale`,
        kind: "reserving",
      }),
    ]));
    unmount();
  });

  it("replaces a payment notice after the paid SSE event and lets the completion close", async () => {
    const initial = watch("reserve_once_before_payment", "watching");
    const reserving = watch("reserve_once_before_payment", "reserving");
    const payment = watch("reserve_once_before_payment", "payment_required");
    const completed = watch("reserve_once_before_payment", "completed");
    const loadWatches = vi.fn()
      .mockResolvedValueOnce([initial])
      .mockResolvedValueOnce([reserving])
      .mockResolvedValueOnce([payment])
      .mockResolvedValue([completed]);
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
        id: "progress-one",
        event_type: "watch.reservation_progressed",
        aggregate_id: initial.id,
        created_at: "2026-08-03T12:09:46.200Z",
        payload: {
          watch_id: initial.id,
          candidate_id: "candidate",
          attempt_id: "attempt-one",
          attempt_sequence: 1,
          seat_detected_at: null,
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
    expect(loadWatches).toHaveBeenCalledTimes(2);
    expect(notificationState.notices).toHaveLength(1);
    expect(notificationState.notices[0]).toMatchObject({
      revisionKey: `watch:${initial.id}:progress-one`,
      kind: "reserving",
    });
    expect(notificationState.notices[0]?.steps?.map((step) => step.label)).toEqual([
      "자동 예매 요청 시작",
      "로그인 세션 확인",
      "철도사 응답·공식 결과 대기",
    ]);

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

    act(() => {
      onEvent?.({
        id: "payment-completed-one",
        event_type: "watch.payment_completed",
        aggregate_id: initial.id,
        created_at: "2026-08-03T12:10:01Z",
        payload: {
          watch_id: initial.id,
          candidate_id: "candidate",
          terminal: true,
          status: "completed",
          from: "payment_required",
          to: "completed",
          reason: "untrusted-provider-text",
          message: "이 원문은 사용자 안내에 노출되면 안 됩니다.",
          automatic_reservation_retry: false,
        },
      });
    });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(4));
    expect(notificationState.notices).toHaveLength(1);
    expect(notificationState.notices[0]).toMatchObject({
      subjectKey: `watch:${initial.id}`,
      kind: "recovery",
      persistence: "timed",
      title: "결제가 완료되었습니다",
    });
    expect(notificationState.notices[0]?.description).not.toContain("원문");

    const completionId = notificationState.notices[0]?.id;
    expect(completionId).toBeDefined();
    if (completionId === undefined) throw new Error("completion notice was not created");
    notificationState = notificationCenterReducer(notificationState, {
      type: "dismiss",
      id: completionId,
    });
    expect(notificationState.notices).toEqual([]);
    expect(notificationState.dismissalLedger).toEqual([]);
    unmount();
  });

  it("keeps the measured terminal result authoritative across surrounding REST reloads", async () => {
    const initial = watch("reserve_once_before_payment", "watching");
    const payment = watch("reserve_once_before_payment", "payment_required");
    const loadWatches = vi.fn()
      .mockResolvedValueOnce([initial])
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
      initialWatches: [initial],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());

    act(() => onEvent?.({ event_type: "watch.status_changed" }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    expect(notificationState.notices[0]?.steps?.some((step) => (
      step.label === "로그인 세션 확인"
    ))).toBe(false);

    act(() => onEvent?.({
      id: "result-observed-246",
      event_type: "watch.reservation_result",
      aggregate_id: initial.id,
      created_at: "2026-08-10T13:01:21.646Z",
      payload: {
        watch_id: initial.id,
        candidate_id: "candidate",
        attempt_id: "attempt-246",
        attempt_sequence: 246,
        seat_detected_at: null,
        attempt_started_at: "2026-08-10T13:01:13.901Z",
        attempt_finished_at: "2026-08-10T13:01:21.618Z",
        outcome: "payment_required",
        progress_stages: [
          { stage: "authenticated_session_ready", occurred_at: "2026-08-10T13:01:18.506Z" },
          { stage: "target_rechecked", occurred_at: "2026-08-10T13:01:19.651Z" },
          { stage: "seat_selected", occurred_at: "2026-08-10T13:01:19.775Z" },
          { stage: "reservation_requested", occurred_at: "2026-08-10T13:01:19.799Z" },
        ],
      },
    }));
    expect(notificationState.notices[0]).toMatchObject({
      revisionKey: `watch:${initial.id}:result-observed-246`,
      kind: "payment_required",
      durationMs: 7_717,
    });
    expect(notificationState.notices[0]?.steps?.map((step) => step.label)).toEqual([
      "좌석 발견",
      "자동 예매 요청 시작",
      "로그인 세션 확인",
      "검색 결과·열차 재확인",
      "좌석 선택",
      "예약 요청",
      "공식 결과 확인",
      "공식 결제 필요",
    ]);

    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(3));
    expect(notificationState.notices[0]?.revisionKey)
      .toBe(`watch:${initial.id}:result-observed-246`);
    expect(notificationState.notices[0]?.steps?.some((step) => step.label === "예약 요청"))
      .toBe(true);
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
        snapshotOf: (value) => {
          const snapshot = snapshotOf(value);
          return {
            ...snapshot,
            train: trainLabel,
            reservationCandidateContexts: {
              ...snapshot.reservationCandidateContexts,
              candidate: {
                ...snapshot.reservationCandidateContexts.candidate,
                train: trainLabel,
              },
            },
          };
        },
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
        payload: { watch_id: initial.id, candidate_id: "candidate", outcome: "pending" },
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

  it("prunes a missing watch subject only after a successful canonical reload", async () => {
    const initial = watch("reserve_once_before_payment", "payment_required");
    const loadWatches = vi.fn().mockResolvedValue([]);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const pruneStaleNotificationSubjects = vi.fn();
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
      pruneStaleNotificationSubjects,
    }));

    await waitFor(() => expect(pruneStaleNotificationSubjects)
      .toHaveBeenCalledWith(["watch:watch-one"]));
    expect(result.current.watches).toEqual([]);
    expect(pushNotifications).toHaveBeenLastCalledWith([]);
    expect(onProviderAuthenticationTransition).not.toHaveBeenCalled();
    unmount();
  });

  it("folds repeated seat-observation SSE invalidations into the next recovery poll", async () => {
    const initial = watch("notify_only");
    const loadWatches = vi.fn().mockResolvedValue([initial]);
    const projectSnapshot = vi.fn(snapshotOf);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
    const { unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [initial],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf: projectSnapshot,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));
    await waitFor(() => expect(projectSnapshot).toHaveBeenCalled());
    const projectionCallsBeforeRoutineEvents = projectSnapshot.mock.calls.length;
    liveNoticeApi.buildLiveReservationNotice.mockClear();

    act(() => {
      onEvent?.({ event_type: "watch.seat_observed", aggregate_id: initial.id });
      onEvent?.({ event_type: "watch.seat_observed", aggregate_id: initial.id });
      onEvent?.({ event_type: "watch.seat_observed", aggregate_id: initial.id });
    });
    expect(liveNoticeApi.buildLiveReservationNotice).not.toHaveBeenCalled();

    await new Promise((resolve) => window.setTimeout(resolve, 100));
    expect(loadWatches).toHaveBeenCalledOnce();
    expect(projectSnapshot).toHaveBeenCalledTimes(projectionCallsBeforeRoutineEvents);

    unmount();
  });

  it("reloads an important status event without waiting for the routine poll", async () => {
    const initial = watch("notify_only");
    const loadWatches = vi.fn().mockResolvedValue([initial]);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
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

    act(() => onEvent?.({ event_type: "watch.status_changed", aggregate_id: initial.id }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));

    unmount();
  });

  it("defers hidden SSE interpretation and performs one canonical reload on resume", async () => {
    const initial = watch("reserve_once_before_payment", "watching");
    const loadWatches = vi.fn().mockResolvedValue([initial]);
    const projectSnapshot = vi.fn(snapshotOf);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
    const originalVisibility = Object.getOwnPropertyDescriptor(document, "visibilityState");
    const setVisibility = (state: DocumentVisibilityState): void => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: state,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    };
    const rendered = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [initial],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf: projectSnapshot,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));

    try {
      await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());
      const projectionCallsBeforeHiddenEvents = projectSnapshot.mock.calls.length;
      liveNoticeApi.buildLiveReservationNotice.mockClear();

      act(() => setVisibility("hidden"));
      act(() => {
        onEvent?.(reservationProgressedEvent(initial.id, "hidden-progress-one"));
        onEvent?.(reservationProgressedEvent(initial.id, "hidden-progress-two"));
        onEvent?.({ event_type: "watch.seat_observed", aggregate_id: initial.id });
      });

      expect(loadWatches).toHaveBeenCalledOnce();
      expect(projectSnapshot).toHaveBeenCalledTimes(projectionCallsBeforeHiddenEvents);
      expect(liveNoticeApi.buildLiveReservationNotice).not.toHaveBeenCalled();

      act(() => setVisibility("visible"));
      await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
      expect(liveNoticeApi.buildLiveReservationNotice).not.toHaveBeenCalled();
    } finally {
      rendered.unmount();
      if (originalVisibility === undefined) {
        Reflect.deleteProperty(document, "visibilityState");
      } else {
        Object.defineProperty(document, "visibilityState", originalVisibility);
      }
    }
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
    const pruneStaleNotificationSubjects = vi.fn();
    const { result, unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [watch("notify_only")],
      pollIntervalSeconds: 300,
      loadWatches,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
      pruneStaleNotificationSubjects,
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
    expect(pruneStaleNotificationSubjects).not.toHaveBeenCalled();

    act(() => {
      result.current.endReservationPolicyMutation();
      result.current.requestRefresh();
    });
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    expect(result.current.watches[0]?.reservationPolicy)
      .toBe("reserve_once_before_payment");
    expect(pruneStaleNotificationSubjects).not.toHaveBeenCalled();

    unmount();
  });

  it("does not let a pre-resume GET restore the paused badge after resume succeeds", async () => {
    const staleReload = deferred<ReadonlyArray<TestWatch>>();
    const resumed = watch("notify_only", "watching");
    const loadWatches = vi.fn()
      .mockReturnValueOnce(staleReload.promise)
      .mockResolvedValue([resumed]);
    const onAuthenticationExpired = vi.fn();
    const onProviderAuthenticationTransition = vi.fn();
    const pushNotifications = vi.fn();
    const { result, unmount } = renderHook(() => useWatchCollection({
      authenticated: true,
      demo: false,
      initialWatches: [watch("notify_only", "paused")],
      pollIntervalSeconds: 300,
      loadWatches,
      snapshotOf,
      onAuthenticationExpired,
      onProviderAuthenticationTransition,
      pushNotifications,
    }));

    await waitFor(() => expect(loadWatches).toHaveBeenCalledOnce());
    act(() => {
      result.current.beginWatchMutation();
      result.current.commitWatches([resumed]);
      result.current.endWatchMutation();
      result.current.requestRefresh();
    });

    await act(async () => {
      staleReload.resolve([watch("notify_only", "paused")]);
      await staleReload.promise;
    });
    expect(result.current.watches[0]?.status).toBe("watching");

    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    expect(result.current.watches[0]?.status).toBe("watching");
    unmount();
  });

  it("keeps a deleted watch hidden through a stale event reload until absence is canonical", async () => {
    const initial = watch("reserve_once_before_payment", "watching");
    const staleReload = deferred<ReadonlyArray<TestWatch>>();
    const loadWatches = vi.fn()
      .mockResolvedValueOnce([initial])
      .mockReturnValueOnce(staleReload.promise)
      .mockResolvedValue([]);
    let onEvent: ((event: unknown) => void) | undefined;
    eventApi.subscribeToEvents.mockImplementation((handler: (event: unknown) => void) => {
      onEvent = handler;
      return () => undefined;
    });
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
    act(() => result.current.commitWatchDeletion(initial.id));
    expect(result.current.watches).toEqual([]);

    act(() => onEvent?.({ event_type: "watch.updated", aggregate_id: initial.id }));
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(2));
    await act(async () => {
      staleReload.resolve([initial]);
      await staleReload.promise;
    });
    expect(result.current.watches).toEqual([]);

    act(() => result.current.requestRefresh());
    await waitFor(() => expect(loadWatches).toHaveBeenCalledTimes(3));
    expect(result.current.watches).toEqual([]);
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
