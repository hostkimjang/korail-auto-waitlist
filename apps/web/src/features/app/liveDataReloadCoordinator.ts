export interface LiveDataReloadCoordinator {
  start: () => void;
  request: () => void;
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
  let pending = false;
  let timer: number | null = null;
  let pollTimer: number | null = null;
  const pollIntervalMs = options.pollIntervalMs;
  const visibilityTarget = options.visibilityTarget
    ?? (typeof document === "undefined" ? undefined : document);

  const pollingEnabled = Number.isFinite(pollIntervalMs) && Number(pollIntervalMs) > 0;
  const isVisible = (): boolean => visibilityTarget?.visibilityState !== "hidden";

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
      void runReload();
    }, Number(pollIntervalMs));
  };

  const runReload = async (): Promise<void> => {
    if (!active) return;
    if (inFlight) {
      pending = true;
      return;
    }
    if (!isVisible()) {
      pending = true;
      return;
    }
    pending = false;
    clearPollTimer();
    inFlight = true;
    try {
      await reload();
    } catch {
      // Authentication expiry belongs to the supplied reload function. A
      // transient refresh failure keeps the last successful snapshot visible.
    } finally {
      inFlight = false;
      if (active && pending && isVisible()) {
        pending = false;
        timer = window.setTimeout(() => {
          timer = null;
          void runReload();
        }, burstDelayMs);
      } else if (isVisible()) {
        schedulePoll();
      }
    }
  };

  const request = (): void => {
    pending = true;
    if (!isVisible() || inFlight || timer !== null) return;
    timer = window.setTimeout(() => {
      timer = null;
      void runReload();
    }, burstDelayMs);
  };

  const handleVisibilityChange = (): void => {
    if (!active || !pollingEnabled) return;
    if (!isVisible()) {
      clearPollTimer();
      clearRequestTimer();
      return;
    }
    clearPollTimer();
    void runReload();
  };

  if (pollingEnabled) {
    visibilityTarget?.addEventListener("visibilitychange", handleVisibilityChange);
  }

  return {
    start: () => {
      if (!isVisible()) {
        pending = true;
        return;
      }
      void runReload();
    },
    request,
    dispose: () => {
      active = false;
      clearRequestTimer();
      clearPollTimer();
      pending = false;
      visibilityTarget?.removeEventListener("visibilitychange", handleVisibilityChange);
    },
  };
}
