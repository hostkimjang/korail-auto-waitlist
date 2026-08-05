import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import { ApiError } from "../../api/client";
import { subscribeToEvents } from "../../api/events";
import { delayUntilRefreshRotationEnds } from "../../shared/lib/refreshIndicator";
import { createReservationPolicyMutationGuard } from "../../shared/lib/reservationPolicyMutationGuard";
import { buildLiveReservationNotice } from "./liveReservationNotice";
import { createLiveDataReloadCoordinator, type LiveDataReloadCoordinator } from "./liveDataReloadCoordinator";
import type { AppNotificationInput } from "./notificationCenter";
import {
  buildAvailabilityLostToast,
  buildSeatFoundToast,
  buildWatchActionToast,
} from "./reservationToast";
import {
  detectSeatAvailabilityLostTransitions,
  detectSeatFoundTransitions,
  detectWatchActionTransitions,
  reconcileWatchSnapshots,
} from "./watchSnapshots";
import {
  mapLegacyWatchLifecycleSnapshot,
  type LegacyWatchSnapshot,
  type WatchLifecycleSnapshot,
} from "./watchLifecycleSnapshot";

const queuedReservationEventTypes: ReadonlySet<string> = new Set([
  "watch.reservation_attempted",
  "watch.reservation_result",
  "watch.reservation_result_requires_manual_check",
  "watch.payment_hold_ended_monitoring_resumed",
  "watch.payment_hold_ended_one_off_expired",
]);

interface WatchRefreshState {
  isRefreshing: boolean;
  lastRefreshedAt: Date | null;
}

interface UseWatchCollectionOptions<TWatch extends LegacyWatchSnapshot> {
  authenticated: boolean;
  demo: boolean;
  initialWatches: ReadonlyArray<TWatch>;
  pollIntervalSeconds: number;
  loadWatches: () => Promise<ReadonlyArray<TWatch>>;
  snapshotOf?: (watch: TWatch) => WatchLifecycleSnapshot;
  onAuthenticationExpired: () => void;
  onProviderAuthenticationTransition: () => void;
  pushNotifications: (notifications: ReadonlyArray<AppNotificationInput>) => void;
}

export interface WatchCollectionController<TWatch extends LegacyWatchSnapshot> {
  watches: ReadonlyArray<TWatch>;
  commitWatches: Dispatch<SetStateAction<ReadonlyArray<TWatch>>>;
  refreshState: WatchRefreshState;
  requestRefresh: () => void;
  beginReservationPolicyMutation: () => void;
  endReservationPolicyMutation: () => void;
}

interface RefreshAnimation {
  generation: number;
  startedAt: number;
  stopTimerId: number | null;
}

function eventType(value: unknown): string | null {
  if (typeof value !== "object" || value === null || !("event_type" in value)) return null;
  return typeof value.event_type === "string" ? value.event_type : null;
}

export function useWatchCollection<TWatch extends LegacyWatchSnapshot>({
  authenticated,
  demo,
  initialWatches,
  pollIntervalSeconds,
  loadWatches,
  snapshotOf,
  onAuthenticationExpired,
  onProviderAuthenticationTransition,
  pushNotifications,
}: UseWatchCollectionOptions<TWatch>): WatchCollectionController<TWatch> {
  const [watches, setWatches] = useState<ReadonlyArray<TWatch>>(() => initialWatches);
  const [refreshState, setRefreshState] = useState<WatchRefreshState>({
    isRefreshing: false,
    lastRefreshedAt: null,
  });
  const watchesRef = useRef<ReadonlyArray<TWatch>>(watches);
  const snapshotOfRef = useRef<(watch: TWatch) => WatchLifecycleSnapshot>(
    snapshotOf ?? mapLegacyWatchLifecycleSnapshot,
  );
  const pendingLiveReservationEventsRef = useRef<unknown[]>([]);
  const reloadCoordinatorRef = useRef<LiveDataReloadCoordinator | null>(null);
  const lifecycleEpochRef = useRef(0);
  const activeLifecycleEpochRef = useRef<number | null>(null);
  const reservationPolicyMutationGuardRef = useRef(createReservationPolicyMutationGuard());
  const refreshAnimationRef = useRef<RefreshAnimation>({
    generation: 0,
    startedAt: 0,
    stopTimerId: null,
  });

  useEffect(() => {
    snapshotOfRef.current = snapshotOf ?? mapLegacyWatchLifecycleSnapshot;
  }, [snapshotOf]);

  const commitWatches = useCallback<Dispatch<SetStateAction<ReadonlyArray<TWatch>>>>((updater) => {
    setWatches((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      watchesRef.current = next;
      return next;
    });
  }, []);

  const reloadWatches = useCallback(async (lifecycleEpoch: number): Promise<void> => {
    if (demo || activeLifecycleEpochRef.current !== lifecycleEpoch) return;
    const reservationPolicyMutationSnapshot = reservationPolicyMutationGuardRef.current.snapshot();
    const refreshAnimation = refreshAnimationRef.current;
    refreshAnimation.generation += 1;
    const refreshGeneration = refreshAnimation.generation;
    refreshAnimation.startedAt = performance.now();
    if (refreshAnimation.stopTimerId !== null) {
      window.clearTimeout(refreshAnimation.stopTimerId);
      refreshAnimation.stopTimerId = null;
    }
    setRefreshState((current) => ({ ...current, isRefreshing: true }));
    try {
      const watchItems = await loadWatches();
      if (activeLifecycleEpochRef.current !== lifecycleEpoch) return;
      if (!reservationPolicyMutationGuardRef.current.isCurrent(
        reservationPolicyMutationSnapshot,
      )) {
        // A policy PATCH crossed this GET. Its older snapshot must not overwrite
        // the newer ticket-level choice; the mutation schedules a fresh reload.
        return;
      }
      const previous = watchesRef.current;
      const previousSnapshots = previous.map((watch) => snapshotOfRef.current(watch));
      const nextSnapshots = watchItems.map((watch) => snapshotOfRef.current(watch));
      const transitions = detectSeatFoundTransitions(previousSnapshots, nextSnapshots);
      const availabilityLosses = detectSeatAvailabilityLostTransitions(
        previousSnapshots,
        nextSnapshots,
      );
      const actionTransitions = detectWatchActionTransitions(previousSnapshots, nextSnapshots);
      const pendingLiveEvents = pendingLiveReservationEventsRef.current;
      pendingLiveReservationEventsRef.current = [];
      const liveReservationNotices = pendingLiveEvents.flatMap((event) => {
        const notice = buildLiveReservationNotice(event, nextSnapshots);
        return notice === null ? [] : [notice];
      });
      const reconciled = reconcileWatchSnapshots(previous, watchItems);
      watchesRef.current = reconciled;
      setWatches(reconciled);
      setRefreshState((current) => ({ ...current, lastRefreshedAt: new Date() }));
      if (actionTransitions.some((item) => (
        item.status === "auth_required" || item.status === "authentication_recovered"
      ))) {
        onProviderAuthenticationTransition();
      }
      const liveNoticeSubjects = new Set(
        liveReservationNotices.map((notice) => notice.subjectKey),
      );
      const lifecycleNotices = actionTransitions
        .filter((item) => !liveNoticeSubjects.has(`watch:${item.id}`))
        .map(buildWatchActionToast);
      pushNotifications([
        ...transitions.map(buildSeatFoundToast),
        ...lifecycleNotices,
        ...availabilityLosses.map(buildAvailabilityLostToast),
        ...liveReservationNotices,
      ]);
    } catch (error: unknown) {
      if (
        activeLifecycleEpochRef.current === lifecycleEpoch
        && error instanceof ApiError
        && error.status === 401
      ) {
        onAuthenticationExpired();
      }
      throw error;
    } finally {
      if (activeLifecycleEpochRef.current === lifecycleEpoch) {
        const delay = delayUntilRefreshRotationEnds(
          refreshAnimation.startedAt,
          performance.now(),
        );
        refreshAnimation.stopTimerId = window.setTimeout(() => {
          if (refreshAnimationRef.current.generation !== refreshGeneration) return;
          refreshAnimationRef.current.stopTimerId = null;
          setRefreshState((current) => ({ ...current, isRefreshing: false }));
        }, delay);
      }
    }
  }, [
    demo,
    loadWatches,
    onAuthenticationExpired,
    onProviderAuthenticationTransition,
    pushNotifications,
  ]);

  const requestRefresh = useCallback((): void => {
    reloadCoordinatorRef.current?.request();
  }, []);

  const beginReservationPolicyMutation = useCallback((): void => {
    reservationPolicyMutationGuardRef.current.begin();
  }, []);

  const endReservationPolicyMutation = useCallback((): void => {
    reservationPolicyMutationGuardRef.current.end();
  }, []);

  useEffect(() => {
    if (!authenticated || demo) return undefined;
    const lifecycleEpoch = lifecycleEpochRef.current + 1;
    lifecycleEpochRef.current = lifecycleEpoch;
    activeLifecycleEpochRef.current = lifecycleEpoch;
    pendingLiveReservationEventsRef.current = [];
    const refreshAnimation = refreshAnimationRef.current;
    const coordinator = createLiveDataReloadCoordinator(() => reloadWatches(lifecycleEpoch), 50, {
      pollIntervalMs: pollIntervalSeconds * 1_000,
    });
    reloadCoordinatorRef.current = coordinator;
    const unsubscribe = subscribeToEvents(
      (event) => {
        if (activeLifecycleEpochRef.current !== lifecycleEpoch) return;
        const liveNotice = buildLiveReservationNotice(
          event,
          watchesRef.current.map((watch) => snapshotOfRef.current(watch)),
        );
        if (liveNotice !== null) {
          pushNotifications([liveNotice]);
        } else {
          const type = eventType(event);
          if (type !== null && queuedReservationEventTypes.has(type)) {
            pendingLiveReservationEventsRef.current.push(event);
          }
        }
        coordinator.request();
      },
      () => undefined,
    );
    coordinator.start();
    return () => {
      if (activeLifecycleEpochRef.current === lifecycleEpoch) {
        activeLifecycleEpochRef.current = null;
        pendingLiveReservationEventsRef.current = [];
      }
      if (reloadCoordinatorRef.current === coordinator) reloadCoordinatorRef.current = null;
      coordinator.dispose();
      unsubscribe();
      refreshAnimation.generation += 1;
      if (refreshAnimation.stopTimerId !== null) {
        window.clearTimeout(refreshAnimation.stopTimerId);
        refreshAnimation.stopTimerId = null;
      }
    };
  }, [authenticated, demo, pollIntervalSeconds, pushNotifications, reloadWatches]);

  useEffect(() => {
    const refreshAnimation = refreshAnimationRef.current;
    return () => {
      refreshAnimation.generation += 1;
      if (refreshAnimation.stopTimerId !== null) {
        window.clearTimeout(refreshAnimation.stopTimerId);
        refreshAnimation.stopTimerId = null;
      }
    };
  }, []);

  return {
    watches,
    commitWatches,
    refreshState,
    requestRefresh,
    beginReservationPolicyMutation,
    endReservationPolicyMutation,
  };
}
