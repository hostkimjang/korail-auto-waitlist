import { useCallback, useState, type Dispatch, type SetStateAction } from "react";

import type { MappedWatch } from "../../api/watches";
import type { ReservationPolicy } from "../../domain/reservationPolicy";

export type WatchMutationRecord = MappedWatch;

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
  pushToast: (message: string) => void;
  beginReservationPolicyMutation: () => void;
  endReservationPolicyMutation: () => void;
  requestWatchesRefresh: () => void;
}

export interface WatchMutationController {
  pauseWatch: (id: string) => Promise<void>;
  resumeWatch: (id: string) => Promise<void>;
  cancelWatch: (id: string) => Promise<WatchCancellationResult>;
  changeReservationPolicy: (id: string, policy: ReservationPolicy) => Promise<void>;
  deleteWatchRecord: (id: string) => Promise<void>;
  reservationPolicyUpdatingIds: ReadonlySet<string>;
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

export function useWatchMutations({
  demo,
  watches,
  commitWatches,
  pushToast,
  beginReservationPolicyMutation,
  endReservationPolicyMutation,
  requestWatchesRefresh,
  pauseWatchRequest,
  startWatchRequest,
  cancelWatchRequest,
  updateWatchRequest,
  deleteWatchRequest,
}: UseWatchMutationsOptions): WatchMutationController {
  const [reservationPolicyUpdatingIds, setReservationPolicyUpdatingIds] = useState<Set<string>>(
    () => new Set(),
  );

  const pauseWatch = useCallback(async (id: string): Promise<void> => {
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
    }
  }, [commitWatches, demo, pauseWatchRequest, pushToast]);

  const resumeWatch = useCallback(async (id: string): Promise<void> => {
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
    }
  }, [commitWatches, demo, pushToast, startWatchRequest]);

  const cancelWatch = useCallback(async (id: string): Promise<WatchCancellationResult> => {
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
    }
  }, [cancelWatchRequest, commitWatches, demo, pushToast, watches]);

  const changeReservationPolicy = useCallback(async (
    id: string,
    reservationPolicy: ReservationPolicy,
  ): Promise<void> => {
    beginReservationPolicyMutation();
    setReservationPolicyUpdatingIds((items) => new Set(items).add(id));
    try {
      let updated: WatchMutationRecord | null;
      if (demo) {
        const current = watches.find((watch) => watch.id === id);
        updated = current ? { ...current, reservationPolicy } : null;
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
      endReservationPolicyMutation();
      requestWatchesRefresh();
      setReservationPolicyUpdatingIds((items) => {
        const next = new Set(items);
        next.delete(id);
        return next;
      });
    }
  }, [
    beginReservationPolicyMutation,
    commitWatches,
    demo,
    endReservationPolicyMutation,
    pushToast,
    requestWatchesRefresh,
    updateWatchRequest,
    watches,
  ]);

  const deleteWatchRecord = useCallback(async (id: string): Promise<void> => {
    try {
      if (!demo) await deleteWatchRequest(id);
      commitWatches((items) => items.filter((watch) => watch.id !== id));
      pushToast("대기 기록을 삭제했습니다.");
    } catch (error: unknown) {
      pushToast(errorMessage(error, "대기 기록을 삭제하지 못했습니다."));
    }
  }, [commitWatches, deleteWatchRequest, demo, pushToast]);

  return {
    pauseWatch,
    resumeWatch,
    cancelWatch,
    changeReservationPolicy,
    deleteWatchRecord,
    reservationPolicyUpdatingIds,
  };
}
