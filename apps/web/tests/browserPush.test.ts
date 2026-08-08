import { afterEach, describe, expect, it, vi } from "vitest";

import {
  connectBrowserPush,
  disconnectBrowserPush,
  readBrowserPushState,
  waitForServiceWorkerRegistration,
} from "../src/api/notifications";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function browserPushChannel(name: string): Record<string, unknown> {
  return {
    id: "web-push-1",
    kind: "web_push",
    name,
    enabled: true,
    configured: true,
    device_key: "A".repeat(43),
    active_device_count: 1,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

const VALID_PUBLIC_KEY = "BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("browser push connection", () => {
  it("fails with a clear error instead of waiting forever for service worker readiness", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("navigator", {
      serviceWorker: { ready: new Promise(() => undefined) },
    });

    const pending = waitForServiceWorkerRegistration(100);
    const assertion = expect(pending).rejects.toThrow(
      "알림 서비스를 준비하지 못했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.",
    );
    await vi.advanceTimersByTimeAsync(100);
    await assertion;
  });

  it("registers the service worker when local development has not registered it yet", async () => {
    const subscription = {
      endpoint: "https://push.example/subscription",
      toJSON: () => ({ endpoint: "https://push.example/subscription" }),
    };
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe: vi.fn().mockResolvedValue(subscription),
      },
    };
    const serviceWorker = {
      getRegistration: vi.fn().mockResolvedValue(undefined),
      register: vi.fn().mockResolvedValue(registration),
      ready: Promise.resolve(registration),
    };
    const requestPermission = vi.fn().mockResolvedValue("granted");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ public_key: VALID_PUBLIC_KEY }))
      .mockResolvedValueOnce(jsonResponse(browserPushChannel("이 브라우저")));
    vi.stubGlobal("navigator", { serviceWorker });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", { requestPermission });
    vi.stubGlobal("fetch", fetchMock);

    const result = await connectBrowserPush();

    expect(serviceWorker.register).toHaveBeenCalledWith("/sw.js");
    expect(requestPermission).toHaveBeenCalledOnce();
    expect(registration.pushManager.subscribe).toHaveBeenCalledWith(expect.objectContaining({
      userVisibleOnly: true,
      applicationServerKey: expect.any(Uint8Array),
    }));
    expect(result).toMatchObject({ id: "web-push-1", enabled: true });
  });

  it("reuses the current OS subscription and upserts it without replacing another device", async () => {
    const subscription = {
      endpoint: "https://push.example/existing",
      toJSON: () => ({ endpoint: "https://push.example/existing" }),
    };
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(subscription),
        subscribe: vi.fn(),
      },
    };
    const serviceWorker = {
      getRegistration: vi.fn().mockResolvedValue(registration),
      register: vi.fn(),
      ready: Promise.resolve(registration),
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ public_key: VALID_PUBLIC_KEY }))
      .mockResolvedValueOnce(jsonResponse(browserPushChannel("내 PC")));
    vi.stubGlobal("navigator", { serviceWorker });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", { permission: "granted", requestPermission: vi.fn().mockResolvedValue("granted") });
    vi.stubGlobal("fetch", fetchMock);

    await connectBrowserPush("내 PC");

    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/notifications/channels");
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
  });

  it("unsubscribes this device without requesting notification permission again", async () => {
    const unsubscribe = vi.fn().mockResolvedValue(true);
    const registration = {
      pushManager: {
        getSubscription: vi.fn()
          .mockResolvedValueOnce({ unsubscribe })
          .mockResolvedValueOnce(null),
      },
    };
    vi.stubGlobal("navigator", {
      serviceWorker: { getRegistration: vi.fn().mockResolvedValue(registration) },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", { permission: "granted", requestPermission: vi.fn() });

    const state = await disconnectBrowserPush();

    expect(unsubscribe).toHaveBeenCalledOnce();
    expect(Notification.requestPermission).not.toHaveBeenCalled();
    expect(state).toEqual({
      support: "supported",
      permission: "granted",
      subscribed: false,
      deviceKey: null,
    });
  });

  it("reports a denied OS permission separately from a missing subscription", async () => {
    vi.stubGlobal("navigator", {
      serviceWorker: { getRegistration: vi.fn().mockResolvedValue(undefined) },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", { permission: "denied" });

    await expect(readBrowserPushState()).resolves.toEqual({
      support: "supported",
      permission: "denied",
      subscribed: false,
      deviceKey: null,
    });
  });

  it("derives the stable current-device key from the local endpoint", async () => {
    const subscription = { endpoint: "https://push.example/current-device" };
    vi.stubGlobal("navigator", {
      serviceWorker: {
        getRegistration: vi.fn().mockResolvedValue({
          pushManager: { getSubscription: vi.fn().mockResolvedValue(subscription) },
        }),
      },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", { permission: "granted" });

    const state = await readBrowserPushState();

    expect(state).toMatchObject({
      support: "supported",
      permission: "granted",
      subscribed: true,
    });
    expect(state.deviceKey).toBe("5cQ_9aZZdP0Q1tDlMIyg-R4lca3Akm2IWJ4CQGdML6M");
  });
});
