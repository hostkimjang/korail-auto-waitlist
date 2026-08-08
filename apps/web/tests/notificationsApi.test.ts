import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  connectBrowserPush,
  createNotificationChannel,
  deleteNotificationChannel,
  fetchNotificationChannels,
  testNotificationChannel,
  updateNotificationChannel,
} from "../src/api/notifications";

const CHANNEL_DTO = {
  id: "channel-1",
  kind: "telegram",
  name: "운영 알림",
  enabled: true,
  configured: true,
  device_key: null,
  active_device_count: null,
  created_at: "2026-08-05T00:00:00Z",
  updated_at: "2026-08-05T00:01:00Z",
};

const CHANNEL = {
  id: "channel-1",
  kind: "telegram",
  name: "운영 알림",
  enabled: true,
  configured: true,
  deviceKey: null,
  activeDeviceCount: null,
  createdAt: "2026-08-05T00:00:00Z",
  updatedAt: "2026-08-05T00:01:00Z",
};

const VALID_PUBLIC_KEY = "BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

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

  it("lists channels with the shared credential contract and no CSRF header", async () => {
    const payload = [{
      ...CHANNEL_DTO,
      config: { bot_token: "must-not-cross-read-boundary" },
      arbitrary: "drop-me",
    }];
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchNotificationChannels()).resolves.toEqual([CHANNEL]);

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
        config: { bot_token: "token", chat_id: "123" },
        enabled: true,
      }),
      url: "/api/v1/notifications/channels",
      method: "POST",
      body: {
        kind: "telegram",
        name: "운영 알림",
        config: { bot_token: "token", chat_id: "123" },
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
      .mockResolvedValue(jsonResponse(CHANNEL_DTO, responseStatus));
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
    { ...CHANNEL_DTO, kind: "email" },
    { ...CHANNEL_DTO, id: "" },
    { ...CHANNEL_DTO, created_at: "2026-08-05 00:00:00" },
    { ...CHANNEL_DTO, updated_at: "not-a-time" },
    { ...CHANNEL_DTO, configured: "yes" },
    { ...CHANNEL_DTO, kind: "web_push", device_key: null, active_device_count: 1 },
    { ...CHANNEL_DTO, kind: "web_push", device_key: "short", active_device_count: 1 },
    {
      ...CHANNEL_DTO,
      kind: "web_push",
      device_key: "A".repeat(43),
      active_device_count: -1,
    },
  ])("rejects malformed channel DTOs without partially trusting the list", async (invalid) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([
      CHANNEL_DTO,
      invalid,
    ])));

    await expect(fetchNotificationChannels()).rejects.toThrow(
      "알림 채널 응답 형식을 확인할 수 없습니다.",
    );
  });

  it("rejects a non-array channel list", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(CHANNEL_DTO)));

    await expect(fetchNotificationChannels()).rejects.toThrow(
      "알림 채널 목록 응답 형식을 확인할 수 없습니다.",
    );
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
      : jsonResponse({ queued: true, event_id: "event-1" }, responseStatus);
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

  it.each([
    { queued: false, event_id: "event-1" },
    { queued: true },
    { queued: true, event_id: "  " },
  ])("rejects a test-send response that does not prove queueing", async (payload) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload, 202)));

    await expect(testNotificationChannel("channel-1")).rejects.toThrow(
      "시험 알림 응답 형식을 확인할 수 없습니다.",
    );
  });

  it("maps the queue event identity after validating a successful test-send response", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      queued: true,
      event_id: " event-1 ",
    }, 202)));

    await expect(testNotificationChannel("channel-1")).resolves.toEqual({
      queued: true,
      eventId: "event-1",
    });
  });

  it("rejects an invalid Web Push public-key payload at the unknown JSON boundary", async () => {
    vi.stubGlobal("navigator", {
      serviceWorker: { getRegistration: vi.fn() },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ key: "AQ" })));

    await expect(connectBrowserPush()).rejects.toThrow(
      "Web Push 공개키 응답을 확인할 수 없습니다.",
    );
  });

  it.each([
    { public_key: "" },
    { public_key: "not+base64url" },
    { public_key: "AQ" },
    { public_key: `A${VALID_PUBLIC_KEY.slice(1)}` },
  ])("rejects a malformed Web Push P-256 public key", async (payload) => {
    vi.stubGlobal("navigator", {
      serviceWorker: { getRegistration: vi.fn() },
    });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission: vi.fn().mockResolvedValue("granted"),
    });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload)));

    await expect(connectBrowserPush()).rejects.toThrow(
      "Web Push 공개키 응답을 확인할 수 없습니다.",
    );
  });

  it("reads the public key before creating the exact browser subscription channel", async () => {
    const callOrder: string[] = [];
    const subscription = {
      endpoint: "https://push.example/new-device",
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
      requestPermission: vi.fn().mockImplementation(async () => {
        callOrder.push("permission");
        return "granted";
      }),
    });
    const fetchMock = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => {
        callOrder.push("public-key");
        return jsonResponse({ public_key: VALID_PUBLIC_KEY });
      })
      .mockResolvedValueOnce(jsonResponse({
        ...CHANNEL_DTO,
        id: "push-1",
        kind: "web_push",
        name: "업무 PC",
        device_key: "A".repeat(43),
        active_device_count: 1,
      }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(connectBrowserPush("업무 PC")).resolves.toMatchObject({
      id: "push-1",
      kind: "web_push",
      name: "업무 PC",
    });

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
    expect(callOrder.slice(0, 2)).toEqual(["permission", "public-key"]);
  });

  it("stops before network and service-worker work when OS permission is denied", async () => {
    const getRegistration = vi.fn();
    const register = vi.fn();
    vi.stubGlobal("navigator", { serviceWorker: { getRegistration, register } });
    vi.stubGlobal("PushManager", class PushManager {});
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission: vi.fn().mockResolvedValue("denied"),
    });
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(connectBrowserPush()).rejects.toThrow("OS 알림 권한이 차단되어 있습니다.");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getRegistration).not.toHaveBeenCalled();
    expect(register).not.toHaveBeenCalled();
  });
});
