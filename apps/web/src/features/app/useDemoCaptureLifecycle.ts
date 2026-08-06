import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";

import type { WatchReadModel } from "../../api/watchProjection";
import { buildWatchActionToast } from "./reservationToast";
import { mapWatchLifecycleSnapshot } from "./watchLifecycleSnapshot";
import { detectWatchActionTransitions } from "./watchSnapshots";
import type { AppNotificationInput } from "./notificationCenter";

export type DemoCaptureLifecycleStage = "seat_found" | "reserving" | "payment_required";

const foundAt = "2026-07-30T05:33:00Z";
const startedAt = "2026-07-30T05:33:02Z";
const finishedAt = "2026-07-30T05:33:06Z";

const expectedStatus: Readonly<Record<DemoCaptureLifecycleStage, WatchReadModel["status"]>> = {
  seat_found: "watching",
  reserving: "seat_found",
  payment_required: "reserving",
};

interface DemoCaptureLifecycleAdvanceResult {
  watches: ReadonlyArray<WatchReadModel>;
  notifications: ReadonlyArray<AppNotificationInput>;
  watchId: string;
  stage: DemoCaptureLifecycleStage;
}

interface DemoCaptureLifecycleBridge {
  advance: (stage: DemoCaptureLifecycleStage) => {
    watchId: string;
    stage: DemoCaptureLifecycleStage;
  };
}

declare global {
  interface Window {
    __RAILWAIT_DEMO_CAPTURE__?: DemoCaptureLifecycleBridge;
  }
}

export function buildDemoCaptureWatchStage(
  watch: WatchReadModel,
  stage: DemoCaptureLifecycleStage,
): WatchReadModel {
  if (watch.reservationPolicy !== "reserve_once_before_payment") {
    throw new Error("자동 예매를 선택한 데모 대기만 예약 진행 장면으로 전환할 수 있습니다.");
  }

  if (stage === "seat_found") {
    const evidenceLabel = `${watch.seatClassLabel} · 예매 가능 · 데모 관측 14:33`;
    return {
      ...watch,
      status: "seat_found",
      statusLabel: "좌석 발견",
      seatEvidenceLabel: evidenceLabel,
      activityLabel: evidenceLabel,
      lastCheckedAt: foundAt,
      lastCheckedLabel: "최근 확인 14:33",
      updatedAt: foundAt,
      latestReservationAttempt: null,
      seatFoundObservation: {
        kind: "mock",
        source: "정식 앱 UX 벤치마크 데모",
        observedAt: foundAt,
        observedLabel: "최근 확인 14:33",
      },
    };
  }

  if (stage === "reserving") {
    return {
      ...watch,
      status: "reserving",
      statusLabel: "예매 진행 중",
      updatedAt: startedAt,
      seatFoundObservation: null,
      latestReservationAttempt: {
        outcome: "pending",
        startedAt,
        finishedAt: null,
        retryable: false,
        manualCheckRequired: false,
        retryCondition: null,
        paymentHoldEndedAt: null,
      },
    };
  }

  return {
    ...watch,
    status: "payment_required",
    statusLabel: "결제 필요",
    updatedAt: finishedAt,
    paymentDeadline: null,
    seatFoundObservation: null,
    latestReservationAttempt: {
      outcome: "payment_required",
      startedAt,
      finishedAt,
      retryable: false,
      manualCheckRequired: false,
      retryCondition: null,
      paymentHoldEndedAt: null,
    },
  };
}

export function advanceDemoCaptureLifecycle(
  watches: ReadonlyArray<WatchReadModel>,
  stage: DemoCaptureLifecycleStage,
): DemoCaptureLifecycleAdvanceResult {
  const currentStatus = expectedStatus[stage];
  const targets = watches.filter((watch) => (
    watch.status === currentStatus
    && watch.reservationPolicy === "reserve_once_before_payment"
  ));
  if (targets.length !== 1) {
    throw new Error(
      `예약 진행 데모는 ${currentStatus} 상태의 자동 예매 대기 1건이 필요합니다.`,
    );
  }
  const target = targets[0];
  if (!target) throw new Error("예약 진행 데모 대기를 찾지 못했습니다.");
  const updated = buildDemoCaptureWatchStage(target, stage);
  const next = [updated, ...watches.filter((watch) => watch.id !== target.id)];
  const transitions = detectWatchActionTransitions(
    [mapWatchLifecycleSnapshot(target)],
    [mapWatchLifecycleSnapshot(updated)],
  );
  return {
    watches: next,
    notifications: transitions.map(buildWatchActionToast),
    watchId: target.id,
    stage,
  };
}

interface UseDemoCaptureLifecycleOptions {
  enabled: boolean;
  watches: ReadonlyArray<WatchReadModel>;
  commitWatches: Dispatch<SetStateAction<ReadonlyArray<WatchReadModel>>>;
  dismissTimedNotifications: () => void;
  pushNotifications: (notifications: ReadonlyArray<AppNotificationInput>) => void;
}

export function useDemoCaptureLifecycle({
  enabled,
  watches,
  commitWatches,
  dismissTimedNotifications,
  pushNotifications,
}: UseDemoCaptureLifecycleOptions): void {
  const watchesRef = useRef(watches);

  useEffect(() => {
    watchesRef.current = watches;
  }, [watches]);

  useEffect(() => {
    if (!enabled) return undefined;
    const bridge: DemoCaptureLifecycleBridge = {
      advance: (stage) => {
        const result = advanceDemoCaptureLifecycle(watchesRef.current, stage);
        watchesRef.current = result.watches;
        if (stage === "seat_found") dismissTimedNotifications();
        commitWatches(result.watches);
        pushNotifications(result.notifications);
        return { watchId: result.watchId, stage: result.stage };
      },
    };
    window.__RAILWAIT_DEMO_CAPTURE__ = bridge;
    return () => {
      if (window.__RAILWAIT_DEMO_CAPTURE__ === bridge) {
        delete window.__RAILWAIT_DEMO_CAPTURE__;
      }
    };
  }, [commitWatches, dismissTimedNotifications, enabled, pushNotifications]);
}
