import { isWatchStatus, type WatchStatus } from "../../domain/watch";

export const PWA_NOTIFICATION_MESSAGE = "railwait:notification";

export interface PwaNotificationHint {
  type: typeof PWA_NOTIFICATION_MESSAGE;
  kind: "push" | "click";
  watchId: string;
  status: WatchStatus;
}

interface ServiceWorkerMessageTarget {
  addEventListener: (type: "message", listener: (event: MessageEvent<unknown>) => void) => void;
  removeEventListener: (type: "message", listener: (event: MessageEvent<unknown>) => void) => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function mapPwaNotificationHint(value: unknown): PwaNotificationHint | null {
  if (!isRecord(value) || value.type !== PWA_NOTIFICATION_MESSAGE) return null;
  if (value.kind !== "push" && value.kind !== "click") return null;
  if (typeof value.watchId !== "string") return null;
  const watchId = value.watchId.trim();
  if (!watchId || watchId.length > 128 || !isWatchStatus(value.status)) return null;
  return {
    type: PWA_NOTIFICATION_MESSAGE,
    kind: value.kind,
    watchId,
    status: value.status,
  };
}

function browserMessageTarget(): ServiceWorkerMessageTarget | undefined {
  return typeof navigator !== "undefined" && "serviceWorker" in navigator
    ? navigator.serviceWorker
    : undefined;
}

export function subscribeToPwaNotificationHints(
  onHint: (hint: PwaNotificationHint) => void,
  target: ServiceWorkerMessageTarget | undefined = browserMessageTarget(),
): () => void {
  if (target === undefined) return () => undefined;
  const handleMessage = (event: MessageEvent<unknown>): void => {
    const hint = mapPwaNotificationHint(event.data);
    if (hint !== null) onHint(hint);
  };
  target.addEventListener("message", handleMessage);
  return () => target.removeEventListener("message", handleMessage);
}
