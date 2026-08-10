import { afterEach, describe, expect, it, vi } from "vitest";

import { LIVE_EVENT_TYPES, subscribeToEvents } from "../src/api/events";

class FakeEventSource {
  static latest: FakeEventSource | undefined;

  readonly url: string;
  readonly options: EventSourceInit | undefined;
  readonly listeners = new Map<string, EventListener>();
  closed = false;
  onmessage: EventListener | null = null;
  onerror: ((error: unknown) => void) | null = null;

  constructor(url: string | URL, options?: EventSourceInit) {
    this.url = String(url);
    this.options = options;
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, listener);
  }

  emit(type: string, payload: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(payload) });
    this.listeners.get(type)?.(event);
  }

  emitRaw(type: string, data: string): void {
    this.listeners.get(type)?.(new MessageEvent(type, { data }));
  }

  close(): void {
    this.closed = true;
  }
}

function eventId(payload: unknown): unknown {
  if (typeof payload !== "object" || payload === null || !("id" in payload)) return undefined;
  return payload.id;
}

afterEach(() => {
  FakeEventSource.latest = undefined;
  vi.unstubAllGlobals();
});

describe("live event transport boundary", () => {
  it("opens the credentialed endpoint, binds every durable event, and closes exactly once", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const onError = vi.fn();

    const unsubscribe = subscribeToEvents(vi.fn(), onError);

    const source = FakeEventSource.latest;
    if (source === undefined) throw new Error("EventSource was not created");
    expect(source.url).toBe("/api/v1/events");
    expect(source.options).toEqual({ withCredentials: true });
    expect([...source.listeners.keys()]).toEqual(LIVE_EVENT_TYPES);
    expect(source.onmessage).toBeTypeOf("function");
    expect(source.onerror).toBe(onError);

    source.onerror?.(new Event("error"));
    expect(onError).toHaveBeenCalledOnce();
    expect(source.closed).toBe(false);

    unsubscribe();
    expect(source.closed).toBe(true);
  });

  it("filters replayed history, forwards current events, and reports malformed JSON", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const onEvent = vi.fn();
    const onError = vi.fn();

    subscribeToEvents(onEvent, onError, {
      subscribedAt: Date.parse("2026-07-31T00:00:00Z"),
    });

    const source = FakeEventSource.latest;
    if (source === undefined) throw new Error("EventSource was not created");
    source.emit("watch.created", { id: "old", created_at: "2026-07-30T23:59:59Z" });
    source.emit("watch.updated", { id: "current", created_at: "2026-07-31T00:00:00Z" });
    source.emit("watch.seat_observed", { id: "future", created_at: "2026-07-31T00:00:01Z" });
    source.emitRaw("notification.dispatch_requested", "{broken-json");

    expect(onEvent.mock.calls.map(([event]) => event)).toEqual([
      { id: "current", created_at: "2026-07-31T00:00:00Z" },
      { id: "future", created_at: "2026-07-31T00:00:01Z" },
    ]);
    expect(onError).toHaveBeenCalledOnce();
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(SyntaxError);
  });

  it("ignores replayed SSE history and forwards only events created after subscription", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const onEvent = vi.fn();
    const onError = vi.fn();

    const unsubscribe = subscribeToEvents(onEvent, onError, {
      subscribedAt: Date.parse("2026-07-31T00:00:00Z"),
    });
    const source = FakeEventSource.latest;
    if (source === undefined) throw new Error("EventSource was not created");
    source.emit("watch.created", { id: "old", created_at: "2026-07-30T23:59:59Z" });
    source.emit("watch.updated", { id: "current", created_at: "2026-07-31T00:00:00Z" });
    source.emit("watch.status_changed", { id: "future", created_at: "2026-07-31T00:00:01Z" });
    source.emit("watch.reservation_result", {
      id: "reservation",
      created_at: "2026-07-31T00:00:02Z",
    });
    source.emit("watch.reservation_progressed", {
      id: "reservation-progress",
      created_at: "2026-07-31T00:00:03Z",
    });

    expect(onEvent.mock.calls.map(([event]) => eventId(event)))
      .toEqual(["current", "future", "reservation", "reservation-progress"]);
    expect(onError).not.toHaveBeenCalled();
    unsubscribe();
    expect(source.closed).toBe(true);
  });
});
