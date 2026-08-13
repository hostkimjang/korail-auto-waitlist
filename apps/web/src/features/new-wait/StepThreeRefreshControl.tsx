import { ArrowsClockwise } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { delayUntilRefreshRotationEnds } from "../../shared/lib/refreshIndicator";
import { LIVE_DATA_RECONCILIATION_INTERVAL_SECONDS } from "../../shared/lib/liveDataSynchronization";

type RefreshTask = () => Promise<unknown> | unknown;

export interface StepThreeRefreshControlProps {
  intervalSeconds?: number;
  enabled?: boolean;
  onManualRefresh: RefreshTask;
  /**
   * The parent must keep this path cache-only. This component intentionally
   * has no provider or API dependency, so a timer cannot bypass provider
   * cooldown and protection policy by itself.
   */
  onAutomaticRefresh: RefreshTask;
  lastSynchronizedAt?: Date | null;
}

export function StepThreeRefreshControl({
  intervalSeconds = LIVE_DATA_RECONCILIATION_INTERVAL_SECONDS,
  enabled = true,
  onManualRefresh,
  onAutomaticRefresh,
  lastSynchronizedAt = null,
}: StepThreeRefreshControlProps) {
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isVisible, setIsVisible] = useState(() => documentIsVisible());
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(lastSynchronizedAt);
  const [refreshError, setRefreshError] = useState("");
  const mountedRef = useRef(true);
  const refreshingRef = useRef(false);
  const manualRefreshRef = useLatest(onManualRefresh);
  const automaticRefreshRef = useLatest(onAutomaticRefresh);
  const normalizedIntervalSeconds = normalizedInterval(intervalSeconds);

  useEffect(() => {
    setLastSyncedAt(lastSynchronizedAt);
  }, [lastSynchronizedAt]);

  useEffect(() => {
    const updateVisibility = () => setIsVisible(documentIsVisible());
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const runRefresh = useCallback(async (task: RefreshTask): Promise<boolean> => {
    if (!enabled || refreshingRef.current) return false;

    const startedAt = Date.now();
    refreshingRef.current = true;
    setIsRefreshing(true);
    setRefreshError("");
    let succeeded = false;
    let errorMessage = "";
    try {
      await task();
      succeeded = true;
    } catch {
      errorMessage = "새로고침에 실패했습니다. 마지막 동기화 결과를 유지합니다.";
    } finally {
      const remainingIndicatorTime = delayUntilRefreshRotationEnds(startedAt, Date.now());
      if (remainingIndicatorTime > 0) {
        await delay(remainingIndicatorTime);
      }
      refreshingRef.current = false;
      if (mountedRef.current) {
        if (succeeded) setLastSyncedAt(new Date());
        if (errorMessage) setRefreshError(errorMessage);
        setIsRefreshing(false);
      }
    }
    return succeeded;
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !isVisible) return undefined;
    const timer = window.setInterval(() => {
      void runRefresh(() => automaticRefreshRef.current());
    }, normalizedIntervalSeconds * 1_000);
    return () => window.clearInterval(timer);
  }, [automaticRefreshRef, enabled, isVisible, normalizedIntervalSeconds, runRefresh]);

  const lastSynchronizedLabel = lastSyncedAt === null
    ? "최근 갱신 --:--:--"
    : `최근 갱신 ${formatTime(lastSyncedAt)}`;

  return (
    <div className="step-three-refresh-control">
      <button
        type="button"
        className={isRefreshing ? "icon-button is-refreshing" : "icon-button"}
        aria-label="시간표 새로고침"
        aria-busy={isRefreshing}
        disabled={!enabled}
        onClick={() => void runRefresh(() => manualRefreshRef.current())}
      >
        <ArrowsClockwise className={isRefreshing ? "refresh-icon is-spinning" : "refresh-icon"} size={22} aria-hidden="true" />
      </button>
      <span role="status" aria-live="polite">
        {lastSynchronizedLabel}
      </span>
      {refreshError && <span role="alert">{refreshError}</span>}
    </div>
  );
}

function useLatest<T>(value: T): { current: T } {
  const reference = useRef(value);
  reference.current = value;
  return reference;
}

function normalizedInterval(value: number): number {
  return Number.isFinite(value) && value > 0
    ? value
    : LIVE_DATA_RECONCILIATION_INTERVAL_SECONDS;
}

function documentIsVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

function formatTime(value: Date): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(value);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
