export type LiveDataReloadUrgency = "routine" | "immediate";

export interface LiveDataReloadCoordinator {
  start: () => void;
  request: (urgency: LiveDataReloadUrgency) => void;
  isVisible: () => boolean;
  dispose: () => void;
}

export interface VisibilityTarget {
  readonly visibilityState: DocumentVisibilityState;
  addEventListener: (type: "visibilitychange", listener: () => void) => void;
  removeEventListener: (type: "visibilitychange", listener: () => void) => void;
}

export interface LiveDataReloadOptions {
  pollIntervalMs?: number;
  visibilityTarget?: VisibilityTarget;
}

export function createLiveDataReloadCoordinator(
  reload: () => Promise<void>,
  burstDelayMs = 50,
  options: LiveDataReloadOptions = {},
): LiveDataReloadCoordinator {
  let active = true;
  let inFlight = false;
  let pendingUrgency: LiveDataReloadUrgency | null = null;
  let timer: number | null = null;
  let pollTimer: number | null = null;
  const pollIntervalMs = options.pollIntervalMs;
  const visibilityTarget = options.visibilityTarget
    ?? (typeof document === "undefined" ? undefined : document);

  const pollingEnabled = Number.isFinite(pollIntervalMs) && Number(pollIntervalMs) > 0;
  const isVisible = (): boolean => visibilityTarget?.visibilityState !== "hidden";

  const markPending = (urgency: LiveDataReloadUrgency): void => {
    if (urgency === "immediate" || pendingUrgency === null) pendingUrgency = urgency;
  };

  const clearPollTimer = (): void => {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
  };

  const clearRequestTimer = (): void => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  };

  const schedulePoll = (): void => {
    clearPollTimer();
    if (!active || !pollingEnabled || !isVisible()) return;
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      void runReload("routine");
    }, Number(pollIntervalMs));
  };

  const scheduleRequest = (urgency: LiveDataReloadUrgency): void => {
    if (!active || !isVisible() || timer !== null) return;
    timer = window.setTimeout(() => {
      timer = null;
      void runReload(urgency);
    }, burstDelayMs);
  };

  const runReload = async (triggerUrgency: LiveDataReloadUrgency): Promise<void> => {
    if (!active) return;
    if (inFlight) {
      markPending(triggerUrgency);
      return;
    }
    if (!isVisible()) {
      markPending(triggerUrgency);
      return;
    }
    pendingUrgency = null;
    clearRequestTimer();
    clearPollTimer();
    inFlight = true;
    try {
      await reload();
    } catch {
      // Authentication expiry belongs to the supplied reload function. A
      // transient refresh failure keeps the last successful snapshot visible.
    } finally {
      inFlight = false;
      if (active && isVisible()) {
        if (pendingUrgency === "immediate") {
          scheduleRequest("immediate");
        } else if (pollingEnabled) {
          // Routine invalidations share the next recovery poll. This bounds
          // canonical GET starts without delaying state-changing events.
          schedulePoll();
        } else if (pendingUrgency === "routine") {
          scheduleRequest("routine");
        }
      }
    }
  };

  const request = (urgency: LiveDataReloadUrgency): void => {
    markPending(urgency);
    if (!isVisible() || inFlight) return;
    if (urgency === "immediate") {
      scheduleRequest("immediate");
    } else if (pollingEnabled) {
      if (pollTimer === null) schedulePoll();
    } else {
      scheduleRequest("routine");
    }
  };

  const handleVisibilityChange = (): void => {
    if (!active) return;
    if (!isVisible()) {
      clearPollTimer();
      clearRequestTimer();
      return;
    }
    clearPollTimer();
    void runReload("immediate");
  };

  visibilityTarget?.addEventListener("visibilitychange", handleVisibilityChange);

  return {
    start: () => {
      if (!isVisible()) {
        markPending("immediate");
        return;
      }
      void runReload("immediate");
    },
    request,
    isVisible,
    dispose: () => {
      active = false;
      clearRequestTimer();
      clearPollTimer();
      pendingUrgency = null;
      visibilityTarget?.removeEventListener("visibilitychange", handleVisibilityChange);
    },
  };
}
