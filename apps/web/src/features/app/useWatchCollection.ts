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
import {
  notificationLifecyclePhasePriority,
  type AppNotificationInput,
} from "./notificationCenter";
import { subscribeToPwaNotificationHints } from "./pwaNotificationBridge";
import {
  buildAvailabilityLostToast,
  buildSeatFoundToast,
  buildWatchActionToast,
} from "./reservationToast";
import {
  detectSeatAvailabilityLostTransitions,
  detectSeatFoundTransitions,
  detectWatchActionTransitions,
  hydrateCurrentWatchActionTransitions,
  reconcileWatchSnapshots,
} from "./watchSnapshots";
import {
  mapLegacyWatchLifecycleSnapshot,
  type LegacyWatchSnapshot,
  type WatchLifecycleSnapshot,
} from "./watchLifecycleSnapshot";

const queuedReservationEventTypes: ReadonlySet<string> = new Set([
  "watch.reservation_attempted",
  "watch.reservation_progressed",
  "watch.reservation_result",
  "watch.reservation_result_requires_manual_check",
  "watch.payment_hold_ended_monitoring_resumed",
  "watch.payment_hold_ended_one_off_expired",
  "watch.payment_completed",
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
  commitWatchDeletion: (watchId: string) => void;
  refreshState: WatchRefreshState;
  requestRefresh: () => void;
  beginWatchMutation: () => void;
  endWatchMutation: () => void;
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
  const hasCanonicalSnapshotRef = useRef(false);
  const lifecycleEpochRef = useRef(0);
  const activeLifecycleEpochRef = useRef<number | null>(null);
  const watchMutationGuardRef = useRef(createReservationPolicyMutationGuard());
  const deletedWatchIdsRef = useRef(new Set<string>());
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

  const commitWatchDeletion = useCallback((watchId: string): void => {
    deletedWatchIdsRef.current.add(watchId);
    commitWatches((current) => current.filter((watch) => watch.id !== watchId));
  }, [commitWatches]);

  const reloadWatches = useCallback(async (lifecycleEpoch: number): Promise<void> => {
    if (demo || activeLifecycleEpochRef.current !== lifecycleEpoch) return;
    const watchMutationSnapshot = watchMutationGuardRef.current.snapshot();
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
      if (!watchMutationGuardRef.current.isCurrent(
        watchMutationSnapshot,
      )) {
        // A watch mutation crossed this GET. Its older snapshot must not overwrite
        // the mutation response; every mutation schedules a fresh canonical reload.
        return;
      }
      const deletedWatchIds = deletedWatchIdsRef.current;
      const visibleWatchItems = watchItems.filter((watch) => !deletedWatchIds.has(watch.id));
      for (const watchId of deletedWatchIds) {
        if (!watchItems.some((watch) => watch.id === watchId)) deletedWatchIds.delete(watchId);
      }
      const isInitialCanonicalSnapshot = !hasCanonicalSnapshotRef.current;
      hasCanonicalSnapshotRef.current = true;
      const previous = watchesRef.current;
      const previousSnapshots = previous.map((watch) => snapshotOfRef.current(watch));
      const nextSnapshots = visibleWatchItems.map((watch) => snapshotOfRef.current(watch));
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
      const reconciled = reconcileWatchSnapshots(previous, visibleWatchItems);
      watchesRef.current = reconciled;
      setWatches(reconciled);
      setRefreshState((current) => ({ ...current, lastRefreshedAt: new Date() }));
      if (actionTransitions.some((item) => (
        item.status === "auth_required" || item.status === "authentication_recovered"
      ))) {
        onProviderAuthenticationTransition();
      }
      const liveNoticesBySubject = new Map(
        liveReservationNotices.map((notice) => [notice.subjectKey, notice]),
      );
      const liveNoticeDominates = (notice: AppNotificationInput): boolean => {
        const subjectKey = notice.subjectKey ?? notice.key;
        if (subjectKey === undefined) return false;
        const liveNotice = liveNoticesBySubject.get(subjectKey);
        if (liveNotice === undefined) return false;
        const canonicalPhase = notificationLifecyclePhasePriority(notice.kind ?? "generic");
        const livePhase = notificationLifecyclePhasePriority(liveNotice.kind ?? "generic");
        if (canonicalPhase !== livePhase) return livePhase > canonicalPhase;
        if (canonicalPhase >= 2) return false;
        const canonicalRevisionAt = Date.parse(notice.revisionAt ?? "");
        const liveRevisionAt = Date.parse(liveNotice.revisionAt ?? "");
        if (Number.isFinite(canonicalRevisionAt) && Number.isFinite(liveRevisionAt)) {
          return liveRevisionAt >= canonicalRevisionAt;
        }
        return true;
      };
      const hydratedNotices = isInitialCanonicalSnapshot
        ? hydrateCurrentWatchActionTransitions(nextSnapshots)
            .map(buildWatchActionToast)
            .filter((notice) => !liveNoticeDominates(notice))
        : [];
      const lifecycleNotices = actionTransitions
        .map(buildWatchActionToast)
        .filter((notice) => !liveNoticeDominates(notice));
      const canonicalNoticesBySubject = new Map(
        [...hydratedNotices, ...lifecycleNotices].flatMap((notice) => {
          const subjectKey = notice.subjectKey ?? notice.key;
          return subjectKey === undefined ? [] : [[subjectKey, notice] as const];
        }),
      );
      const selectedLiveReservationNotices = liveReservationNotices.filter((liveNotice) => {
        const subjectKey = liveNotice.subjectKey ?? liveNotice.key;
        if (subjectKey === undefined) return true;
        const canonicalNotice = canonicalNoticesBySubject.get(subjectKey);
        if (canonicalNotice === undefined) return true;
        const canonicalPhase = notificationLifecyclePhasePriority(
          canonicalNotice.kind ?? "generic",
        );
        const livePhase = notificationLifecyclePhasePriority(liveNotice.kind ?? "generic");
        return canonicalPhase < livePhase;
      });
      pushNotifications([
        ...hydratedNotices,
        ...transitions.map(buildSeatFoundToast),
        ...lifecycleNotices,
        ...availabilityLosses.map(buildAvailabilityLostToast),
        ...selectedLiveReservationNotices,
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

  const beginWatchMutation = useCallback((): void => {
    watchMutationGuardRef.current.begin();
  }, []);

  const endWatchMutation = useCallback((): void => {
    watchMutationGuardRef.current.end();
  }, []);

  const beginReservationPolicyMutation = beginWatchMutation;
  const endReservationPolicyMutation = endWatchMutation;

  useEffect(() => {
    if (!authenticated) {
      hasCanonicalSnapshotRef.current = false;
      deletedWatchIdsRef.current.clear();
    }
  }, [authenticated]);

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
        const type = eventType(event);
        const liveNotice = buildLiveReservationNotice(
          event,
          watchesRef.current.map((watch) => snapshotOfRef.current(watch)),
        );
        if (liveNotice !== null) {
          pushNotifications([liveNotice]);
          if (type === "watch.reservation_progressed") return;
        } else {
          if (type !== null && queuedReservationEventTypes.has(type)) {
            pendingLiveReservationEventsRef.current.push(event);
          }
        }
        coordinator.request();
      },
      () => undefined,
    );
    const unsubscribePwaNotificationHints = subscribeToPwaNotificationHints(() => {
      coordinator.request();
    });
    coordinator.start();
    return () => {
      if (activeLifecycleEpochRef.current === lifecycleEpoch) {
        activeLifecycleEpochRef.current = null;
        pendingLiveReservationEventsRef.current = [];
      }
      if (reloadCoordinatorRef.current === coordinator) reloadCoordinatorRef.current = null;
      coordinator.dispose();
      unsubscribe();
      unsubscribePwaNotificationHints();
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
    commitWatchDeletion,
    refreshState,
    requestRefresh,
    beginWatchMutation,
    endWatchMutation,
    beginReservationPolicyMutation,
    endReservationPolicyMutation,
  };
}
