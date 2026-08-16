import { API_ROOT } from "./client";

export type LiveEventHandler = (payload: unknown) => void;
export type LiveEventErrorHandler = (error: unknown) => void;

export interface LiveEventSubscriptionOptions {
  subscribedAt?: number;
}

export const LIVE_EVENT_TYPES = [
  "watch.created",
  "watch.updated",
  "watch.status_changed",
  "watch.seat_observed",
  "watch.reservation_attempted",
  "watch.reservation_progressed",
  "watch.reservation_result",
  "watch.reservation_reconciled",
  "watch.reservation_result_requires_manual_check",
  "watch.payment_hold_ended_monitoring_resumed",
  "watch.payment_hold_ended_one_off_expired",
  "watch.payment_completed",
  "notification.dispatch_requested",
] as const;

function parseLiveEvent(event: Event, onError: LiveEventErrorHandler): unknown | null {
  try {
    if (!("data" in event)) throw new TypeError("SSE event data is missing");
    return JSON.parse(String(event.data));
  } catch (error) {
    onError(error);
    return null;
  }
}

function isCurrentLiveEvent(payload: unknown, subscribedAt: number): boolean {
  if (typeof payload !== "object" || payload === null || !("created_at" in payload)) {
    return false;
  }
  const createdAt = Date.parse(String(payload.created_at ?? ""));
  return Number.isFinite(createdAt) && createdAt >= subscribedAt;
}

export function subscribeToEvents(
  onEvent: LiveEventHandler,
  onError: LiveEventErrorHandler,
  options: LiveEventSubscriptionOptions = {},
): () => void {
  // The durable outbox replays history on a connection without Last-Event-ID.
  // The REST snapshot is canonical, so only events created after subscription invalidate it.
  const subscribedAt = Number.isFinite(options.subscribedAt)
    ? options.subscribedAt ?? Date.now()
    : Date.now();
  const source = new EventSource(`${API_ROOT}/events`, { withCredentials: true });
  const handleEvent = (event: Event): void => {
    const payload = parseLiveEvent(event, onError);
    if (payload !== null && isCurrentLiveEvent(payload, subscribedAt)) onEvent(payload);
  };
  source.onmessage = handleEvent;
  for (const type of LIVE_EVENT_TYPES) source.addEventListener(type, handleEvent);
  source.onerror = onError;
  return () => source.close();
}
