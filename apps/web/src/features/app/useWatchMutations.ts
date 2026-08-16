import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from "react";

import type { WatchReadModel } from "../../api/watches";
import type { ManualRearmReason } from "../../domain/reservationAttempt";
import type { ReservationPolicy } from "../../domain/reservationPolicy";

export type WatchMutationRecord = WatchReadModel;

export interface MissingDemoWatchCancellation {
  id: string;
  status: "expired";
  statusLabel: string;
}

export type WatchCancellationResult = WatchMutationRecord | MissingDemoWatchCancellation;

type CommitWatches = Dispatch<
  SetStateAction<ReadonlyArray<WatchMutationRecord>>
>;

interface WatchMutationRequests {
  pauseWatchRequest: (id: string) => Promise<WatchMutationRecord>;
  startWatchRequest: (id: string) => Promise<WatchMutationRecord>;
  cancelWatchRequest: (id: string) => Promise<WatchMutationRecord>;
  rearmReservationRequest: (
    id: string,
    reason: ManualRearmReason,
  ) => Promise<WatchMutationRecord>;
  updateWatchRequest: (
    id: string,
    payload: { reservation_policy: ReservationPolicy },
  ) => Promise<WatchMutationRecord>;
  deleteWatchRequest: (id: string) => Promise<unknown>;
}

export interface UseWatchMutationsOptions extends WatchMutationRequests {
  demo: boolean;
  watches: ReadonlyArray<WatchMutationRecord>;
  commitWatches: CommitWatches;
  commitWatchDeletion: (watchId: string) => void;
  pushToast: (message: string) => void;
  beginWatchMutation: () => void;
  endWatchMutation: () => void;
  requestWatchesRefresh: () => void;
}

export interface WatchMutationController {
  pauseWatch: (id: string) => Promise<void>;
  resumeWatch: (id: string) => Promise<void>;
  cancelWatch: (id: string) => Promise<WatchCancellationResult>;
  rearmReservation: (id: string, reason: ManualRearmReason) => Promise<void>;
  changeReservationPolicy: (id: string, policy: ReservationPolicy) => Promise<void>;
  deleteWatchRecord: (id: string) => Promise<void>;
  reservationPolicyUpdatingIds: ReadonlySet<string>;
  watchMutationPendingIds: ReadonlySet<string>;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function replaceWatch(
  watches: ReadonlyArray<WatchMutationRecord>,
  id: string,
  replacement: WatchMutationRecord,
): ReadonlyArray<WatchMutationRecord> {
  return watches.map((watch) => watch.id === id ? replacement : watch);
}

function withReservationPolicy(
  watch: WatchMutationRecord,
  reservationPolicy: ReservationPolicy,
): WatchMutationRecord & { reservation_policy: ReservationPolicy } {
  return {
    ...watch,
    reservationPolicy,
    reservation_policy: reservationPolicy,
  };
}

export function useWatchMutations({
  demo,
  watches,
  commitWatches,
  commitWatchDeletion,
  pushToast,
  beginWatchMutation,
  endWatchMutation,
  requestWatchesRefresh,
  pauseWatchRequest,
  startWatchRequest,
  cancelWatchRequest,
  rearmReservationRequest,
  updateWatchRequest,
  deleteWatchRequest,
}: UseWatchMutationsOptions): WatchMutationController {
  const [reservationPolicyUpdatingIds, setReservationPolicyUpdatingIds] = useState<Set<string>>(
    () => new Set(),
  );
  const pendingWatchIdsRef = useRef<Set<string>>(new Set());
  const [watchMutationPendingIds, setWatchMutationPendingIds] = useState<Set<string>>(
    () => new Set(),
  );

  const beginMutation = useCallback((id: string): boolean => {
    if (pendingWatchIdsRef.current.has(id)) return false;
    pendingWatchIdsRef.current.add(id);
    setWatchMutationPendingIds(new Set(pendingWatchIdsRef.current));
    beginWatchMutation();
    return true;
  }, [beginWatchMutation]);

  const endMutation = useCallback((id: string): void => {
    pendingWatchIdsRef.current.delete(id);
    setWatchMutationPendingIds(new Set(pendingWatchIdsRef.current));
    endWatchMutation();
    requestWatchesRefresh();
  }, [endWatchMutation, requestWatchesRefresh]);

  const pauseWatch = useCallback(async (id: string): Promise<void> => {
    if (!beginMutation(id)) return;
    try {
      if (demo) {
        commitWatches((items) => items.map((watch) => watch.id === id
          ? { ...watch, status: "paused", statusLabel: "일시정지" }
          : watch));
      } else {
        const updated = await pauseWatchRequest(id);
        commitWatches((items) => replaceWatch(items, id, updated));
      }
      pushToast("대기를 일시정지했습니다.");
    } catch (error: unknown) {
      pushToast(errorMessage(error, "대기를 일시정지하지 못했습니다."));
    } finally {
      endMutation(id);
    }
  }, [
    beginMutation,
    commitWatches,
    demo,
    endMutation,
    pauseWatchRequest,
    pushToast,
  ]);

  const resumeWatch = useCallback(async (id: string): Promise<void> => {
    if (!beginMutation(id)) return;
    try {
      if (demo) {
        commitWatches((items) => items.map((watch) => watch.id === id
          ? { ...watch, status: "watching", statusLabel: "감시 중" }
          : watch));
      } else {
        const updated = await startWatchRequest(id);
        commitWatches((items) => replaceWatch(items, id, updated));
      }
      pushToast("대기를 재개했습니다.");
    } catch (error: unknown) {
      pushToast(errorMessage(error, "대기를 재개하지 못했습니다."));
    } finally {
      endMutation(id);
    }
  }, [
    beginMutation,
    commitWatches,
    demo,
    endMutation,
    pushToast,
    startWatchRequest,
  ]);

  const cancelWatch = useCallback(async (id: string): Promise<WatchCancellationResult> => {
    if (!beginMutation(id)) {
      return watches.find((watch) => watch.id === id) ?? {
        id,
        status: "expired",
        statusLabel: "만료",
      };
    }
    try {
      if (demo) {
        const current = watches.find((watch) => watch.id === id);
        if (!current) {
          const missing: MissingDemoWatchCancellation = {
            id,
            status: "expired",
            statusLabel: "만료",
          };
          pushToast("대기를 취소했습니다.");
          return missing;
        }
        const updated: WatchMutationRecord = {
          ...current,
          status: "expired",
          statusLabel: "만료",
        };
        commitWatches((items) => replaceWatch(items, id, updated));
        pushToast("대기를 취소했습니다.");
        return updated;
      }

      const updated = await cancelWatchRequest(id);
      commitWatches((items) => replaceWatch(items, id, updated));
      pushToast("대기를 취소했습니다.");
      return updated;
    } catch (error: unknown) {
      pushToast(errorMessage(error, "대기를 취소하지 못했습니다."));
      throw error;
    } finally {
      endMutation(id);
    }
  }, [
    beginMutation,
    cancelWatchRequest,
    commitWatches,
    demo,
    endMutation,
    pushToast,
    watches,
  ]);

  const changeReservationPolicy = useCallback(async (
    id: string,
    reservationPolicy: ReservationPolicy,
  ): Promise<void> => {
    if (!beginMutation(id)) return;
    setReservationPolicyUpdatingIds((items) => new Set(items).add(id));
    try {
      let updated: WatchMutationRecord | null;
      if (demo) {
        const current = watches.find((watch) => watch.id === id);
        updated = current ? withReservationPolicy(current, reservationPolicy) : null;
      } else {
        updated = await updateWatchRequest(id, {
          reservation_policy: reservationPolicy,
        });
      }
      if (updated) commitWatches((items) => replaceWatch(items, id, updated));
      pushToast(reservationPolicy === "reserve_once_before_payment"
        ? "좌석 재발견마다 자동 예매하도록 변경했습니다. 같은 좌석 가용성 에피소드에서는 중복 요청하지 않으며 결제는 직접 진행합니다."
        : "자동 예매를 끄고 좌석 감시와 알림만 유지합니다.");
    } catch (error: unknown) {
      pushToast(errorMessage(error, "대기 실행 방식을 변경하지 못했습니다."));
    } finally {
      endMutation(id);
      setReservationPolicyUpdatingIds((items) => {
        const next = new Set(items);
        next.delete(id);
        return next;
      });
    }
  }, [
    beginMutation,
    commitWatches,
    demo,
    endMutation,
    pushToast,
    updateWatchRequest,
    watches,
  ]);

  const rearmReservation = useCallback(async (
    id: string,
    reason: ManualRearmReason,
  ): Promise<void> => {
    if (!beginMutation(id)) return;
    try {
      if (demo) {
        commitWatches((items) => items.map((watch) => watch.id !== id
          ? watch
          : {
            ...watch,
            latestReservationAttempt: watch.latestReservationAttempt === null
              ? null
              : { ...watch.latestReservationAttempt, manualRearmAvailable: false },
          }));
      } else {
        const updated = await rearmReservationRequest(id, reason);
        commitWatches((items) => replaceWatch(items, id, updated));
      }
      pushToast("자동 예매 재시작을 확인했습니다. 좌석을 다시 관측한 뒤 가능하면 한 번 시도합니다.");
    } catch (error: unknown) {
      pushToast(errorMessage(error, "자동 예매를 다시 시작하지 못했습니다."));
      throw error;
    } finally {
      endMutation(id);
    }
  }, [
    beginMutation,
    commitWatches,
    demo,
    endMutation,
    pushToast,
    rearmReservationRequest,
  ]);

  const deleteWatchRecord = useCallback(async (id: string): Promise<void> => {
    if (!beginMutation(id)) return;
    try {
      if (!demo) await deleteWatchRequest(id);
      commitWatchDeletion(id);
      pushToast("대기 기록을 삭제했습니다.");
    } catch (error: unknown) {
      pushToast(errorMessage(error, "대기 기록을 삭제하지 못했습니다."));
    } finally {
      endMutation(id);
    }
  }, [
    beginMutation,
    commitWatchDeletion,
    deleteWatchRequest,
    demo,
    endMutation,
    pushToast,
  ]);

  return {
    pauseWatch,
    resumeWatch,
    cancelWatch,
    rearmReservation,
    changeReservationPolicy,
    deleteWatchRecord,
    reservationPolicyUpdatingIds,
    watchMutationPendingIds,
  };
}
