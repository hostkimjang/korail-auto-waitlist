import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  connectBrowserPush,
  createNotificationChannel,
  deleteNotificationChannel,
  disconnectBrowserPush,
  fetchNotificationChannels,
  readBrowserPushState,
  testNotificationChannel,
  updateNotificationChannel,
  waitForServiceWorkerRegistration,
} from "../src/api/notifications";
import {
  connectBrowserPush as connectBrowserPushFromCompatibilityEntry,
  createNotificationChannel as createNotificationChannelFromCompatibilityEntry,
  deleteNotificationChannel as deleteNotificationChannelFromCompatibilityEntry,
  disconnectBrowserPush as disconnectBrowserPushFromCompatibilityEntry,
  fetchNotificationChannels as fetchNotificationChannelsFromCompatibilityEntry,
  readBrowserPushState as readBrowserPushStateFromCompatibilityEntry,
  testNotificationChannel as testNotificationChannelFromCompatibilityEntry,
  updateNotificationChannel as updateNotificationChannelFromCompatibilityEntry,
  waitForServiceWorkerRegistration as waitForServiceWorkerRegistrationFromCompatibilityEntry,
} from "../src/api.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("notification transport boundary", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    Object.defineProperty(document, "cookie", {
      configurable: true,
      writable: true,
      value: "rail_csrf=notification-csrf",
    });
  });

  it("keeps api.js as an identity-preserving compatibility entry", () => {
    expect(fetchNotificationChannelsFromCompatibilityEntry).toBe(fetchNotificationChannels);
    expect(createNotificationChannelFromCompatibilityEntry).toBe(createNotificationChannel);
    expect(updateNotificationChannelFromCompatibilityEntry).toBe(updateNotificationChannel);
    expect(deleteNotificationChannelFromCompatibilityEntry).toBe(deleteNotificationChannel);
    expect(testNotificationChannelFromCompatibilityEntry).toBe(testNotificationChannel);
    expect(waitForServiceWorkerRegistrationFromCompatibilityEntry)
      .toBe(waitForServiceWorkerRegistration);
    expect(readBrowserPushStateFromCompatibilityEntry).toBe(readBrowserPushState);
    expect(disconnectBrowserPushFromCompatibilityEntry).toBe(disconnectBrowserPush);
    expect(connectBrowserPushFromCompatibilityEntry).toBe(connectBrowserPush);
  });

  it("lists channels with the shared credential contract and no CSRF header", async () => {
    const payload = [{ id: "push-1", kind: "web_push", enabled: true }];
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchNotificationChannels()).resolves.toEqual(payload);

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(url).toBe("/api/v1/notifications/channels");
    expect(options).toMatchObject({ method: "GET", credentials: "include" });
    expect(new Headers(options?.headers).has("X-CSRF-Token")).toBe(false);
  });

  it.each([
    {
      name: "create",
      action: () => createNotificationChannel({
        kind: "telegram",
        name: "운영 알림",
        config: { chat_id: "123" },
        enabled: true,
      }),
      url: "/api/v1/notifications/channels",
      method: "POST",
      body: {
        kind: "telegram",
        name: "운영 알림",
        config: { chat_id: "123" },
        enabled: true,
      },
      responseStatus: 201,
    },
    {
      name: "update",
      action: () => updateNotificationChannel("channel-1", { enabled: false }),
      url: "/api/v1/notifications/channels/channel-1",
      method: "PATCH",
      body: { enabled: false },
      responseStatus: 200,
    },
  ])("uses the exact $name URL, method, body, and CSRF contract", async ({
    action,
    url: expectedUrl,
    method,
    body,
    responseStatus,
  }) => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ id: "channel-1" }, responseStatus));
    vi.stubGlobal("fetch", fetchMock);

    await action();

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(url).toBe(expectedUrl);
    expect(options).toMatchObject({ method, credentials: "include" });
    expect(JSON.parse(String(options?.body))).toEqual(body);
    const headers = new Headers(options?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("notification-csrf");
  });

  it.each([
    ["delete", deleteNotificationChannel, "DELETE", "/api/v1/notifications/channels/channel-1", 204],
    ["test-send", testNotificationChannel, "POST", "/api/v1/notifications/channels/channel-1/test-send", 202],
  ] as const)("uses the exact %s action contract", async (
    _name,
    action,
    method,
    expectedUrl,
    responseStatus,
  ) => {
    const response = responseStatus === 204
      ? new Response(null, { status: responseStatus })
      : jsonResponse({ queued: true }, responseStatus);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await action("channel-1");

    const requestCall = fetchMock.mock.calls[0];
    expect(requestCall).toBeDefined();
    const [url, options] = requestCall ?? [];
    expect(url).toBe(expectedUrl);
    expect(options).toMatchObject({ method, credentials: "include" });
    expect(options?.body).toBeUndefined();
    expect(new Headers(options?.headers).get("X-CSRF-Token")).toBe("notification-csrf");
  });

  it("rejects an invalid Web Push public-key payload at the unknown JSON boundary", async () => {
    vi.stubGlobal("navigator", {
      serviceWorker: { getRegistration: vi.fn() },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", { permission: "default", requestPermission: vi.fn() });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ key: "AQ" })));

    await expect(connectBrowserPush()).rejects.toThrow(
      "Web Push 공개키 응답을 확인할 수 없습니다.",
    );
  });

  it("reads the public key before creating the exact browser subscription channel", async () => {
    const subscription = {
      toJSON: () => ({ endpoint: "https://push.example/new-device", keys: { auth: "auth" } }),
    };
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(subscription),
        subscribe: vi.fn(),
      },
    };
    vi.stubGlobal("navigator", {
      serviceWorker: {
        getRegistration: vi.fn().mockResolvedValue(registration),
        register: vi.fn(),
        ready: Promise.resolve(registration),
      },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", {
      permission: "granted",
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ public_key: "AQ" }))
      .mockResolvedValueOnce(jsonResponse({ id: "push-1", kind: "web_push" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await connectBrowserPush("업무 PC");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [publicKeyUrl, publicKeyOptions] = fetchMock.mock.calls[0] ?? [];
    expect(publicKeyUrl).toBe("/api/v1/notifications/web-push/public-key");
    expect(publicKeyOptions).toMatchObject({ method: "GET", credentials: "include" });
    expect(new Headers(publicKeyOptions?.headers).has("X-CSRF-Token")).toBe(false);

    const [channelUrl, channelOptions] = fetchMock.mock.calls[1] ?? [];
    expect(channelUrl).toBe("/api/v1/notifications/channels");
    expect(channelOptions).toMatchObject({ method: "POST", credentials: "include" });
    expect(new Headers(channelOptions?.headers).get("X-CSRF-Token"))
      .toBe("notification-csrf");
    expect(JSON.parse(String(channelOptions?.body))).toEqual({
      kind: "web_push",
      name: "업무 PC",
      config: {
        subscription_info: JSON.stringify({
          endpoint: "https://push.example/new-device",
          keys: { auth: "auth" },
        }),
      },
      enabled: true,
    });
    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
  });
});
