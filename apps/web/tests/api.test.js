import { beforeEach, describe, expect, it, vi } from "vitest";
import { loginWithPassword, registerAdmin } from "../src/api/auth";
import { subscribeToEvents } from "../src/api/events";

function response(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("API integration contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(document, "cookie", { writable: true, value: "rail_csrf=csrf-token" });
  });

  it("uses the dedicated administrator registration endpoint without a bootstrap header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ authenticated: true }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await registerAdmin("admin", "x".repeat(16));
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/register");
    expect(options.headers.has("X-Bootstrap-Token")).toBe(false);
    expect(JSON.parse(options.body)).toEqual({ username: "admin", password: expect.any(String) });
  });

  it("uses the administrator password login endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ authenticated: true }));
    vi.stubGlobal("fetch", fetchMock);

    await loginWithPassword("admin", "x".repeat(16));
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/login");
    expect(JSON.parse(options.body)).toEqual({ username: "admin", password: expect.any(String) });
  });

  it("ignores replayed SSE history and forwards only events created after subscription", () => {
    const originalEventSource = globalThis.EventSource;
    class FakeEventSource {
      static latest = null;

      constructor(url, options) {
        this.url = url;
        this.options = options;
        this.listeners = new Map();
        this.closed = false;
        FakeEventSource.latest = this;
      }

      addEventListener(type, listener) {
        this.listeners.set(type, listener);
      }

      emit(type, payload) {
        this.listeners.get(type)?.({ data: JSON.stringify(payload) });
      }

      close() {
        this.closed = true;
      }
    }
    globalThis.EventSource = FakeEventSource;
    const onEvent = vi.fn();
    const onError = vi.fn();

    try {
      const unsubscribe = subscribeToEvents(onEvent, onError, {
        subscribedAt: Date.parse("2026-07-31T00:00:00Z"),
      });
      const source = FakeEventSource.latest;
      source.emit("watch.created", { id: "old", created_at: "2026-07-30T23:59:59Z" });
      source.emit("watch.updated", { id: "current", created_at: "2026-07-31T00:00:00Z" });
      source.emit("watch.status_changed", { id: "future", created_at: "2026-07-31T00:00:01Z" });
      source.emit("watch.reservation_result", { id: "reservation", created_at: "2026-07-31T00:00:02Z" });

      expect(onEvent.mock.calls.map(([event]) => event.id)).toEqual(["current", "future", "reservation"]);
      expect(onError).not.toHaveBeenCalled();
      unsubscribe();
      expect(source.closed).toBe(true);
    } finally {
      globalThis.EventSource = originalEventSource;
    }
  });
});
