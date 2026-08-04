import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/client";
import type {
  BrowserPushState,
  NotificationChannel,
} from "../src/api/notifications";

const notificationApi = vi.hoisted(() => ({
  connectBrowserPush: vi.fn(),
  createNotificationChannel: vi.fn(),
  disconnectBrowserPush: vi.fn(),
  fetchNotificationChannels: vi.fn(),
  readBrowserPushState: vi.fn(),
  testNotificationChannel: vi.fn(),
  updateNotificationChannel: vi.fn(),
}));

vi.mock("../src/api/notifications", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/api/notifications")>();
  return { ...actual, ...notificationApi };
});

import { useNotificationChannelSettings } from "../src/features/settings/useNotificationChannelSettings";

const supportedState: BrowserPushState = {
  support: "supported",
  permission: "granted",
  subscribed: true,
};

function channel(
  kind: NotificationChannel["kind"] = "telegram",
  overrides: Partial<NotificationChannel> = {},
): NotificationChannel {
  return {
    id: `${kind}-one`,
    kind,
    name: kind === "web_push" ? "이 브라우저" : "내 알림",
    enabled: true,
    configured: true,
    createdAt: "2026-08-05T00:00:00Z",
    updatedAt: "2026-08-05T00:00:00Z",
    ...overrides,
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolvePromise: ((value: T) => void) | undefined;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return {
    promise,
    resolve: (value) => resolvePromise?.(value),
  };
}

describe("useNotificationChannelSettings", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    notificationApi.fetchNotificationChannels.mockResolvedValue([]);
    notificationApi.readBrowserPushState.mockResolvedValue(supportedState);
    notificationApi.disconnectBrowserPush.mockResolvedValue(supportedState);
    notificationApi.testNotificationChannel.mockResolvedValue({ queued: true, eventId: "event-one" });
  });

  it("loads channels and browser push state for an authenticated live session", async () => {
    const loaded = channel();
    notificationApi.fetchNotificationChannels.mockResolvedValue([loaded]);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await waitFor(() => expect(result.current.channels).toEqual([loaded]));
    expect(result.current.browserPushState).toEqual(supportedState);
    expect(onAuthenticationExpired).not.toHaveBeenCalled();
  });

  it("ignores a channel response after the hook is unmounted", async () => {
    const pending = deferred<NotificationChannel[]>();
    notificationApi.fetchNotificationChannels.mockReturnValue(pending.promise);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result, unmount } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await waitFor(() => expect(notificationApi.fetchNotificationChannels).toHaveBeenCalledOnce());
    unmount();
    await act(async () => {
      pending.resolve([channel()]);
      await pending.promise;
    });

    expect(result.current.channels).toEqual([]);
    expect(onAuthenticationExpired).not.toHaveBeenCalled();
  });

  it("expires authentication when the initial channel request returns 401", async () => {
    notificationApi.fetchNotificationChannels.mockRejectedValue(new ApiError("expired", 401));
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await waitFor(() => expect(onAuthenticationExpired).toHaveBeenCalledOnce());
  });

  it("refreshes browser push on focus and removes that listener on cleanup", async () => {
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { unmount } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await waitFor(() => expect(notificationApi.readBrowserPushState).toHaveBeenCalledOnce());
    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(notificationApi.readBrowserPushState).toHaveBeenCalledTimes(2));
    unmount();
    window.dispatchEvent(new Event("focus"));

    expect(notificationApi.readBrowserPushState).toHaveBeenCalledTimes(2);
  });

  it("falls back to unsupported when browser push state cannot be read", async () => {
    notificationApi.readBrowserPushState.mockRejectedValue(new Error("browser state unavailable"));
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await waitFor(() => expect(result.current.browserPushState).toEqual({
      support: "unsupported",
      permission: "default",
      subscribed: false,
    }));
  });

  it("creates and updates channels while keeping one channel per kind", async () => {
    const firstTelegram = channel("telegram", { id: "telegram-first" });
    const duplicateTelegram = channel("telegram", { id: "telegram-duplicate" });
    const discord = channel("discord_webhook", { id: "discord-one" });
    const created = channel("generic_webhook", { id: "generic-created", name: "일반 웹훅" });
    const updated = channel("telegram", { id: firstTelegram.id, name: "변경된 텔레그램" });
    notificationApi.fetchNotificationChannels.mockResolvedValue([
      firstTelegram,
      duplicateTelegram,
      discord,
    ]);
    notificationApi.createNotificationChannel.mockResolvedValue(created);
    notificationApi.updateNotificationChannel.mockResolvedValue(updated);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(result.current.channels).toEqual([
      firstTelegram,
      duplicateTelegram,
      discord,
    ]));

    await act(async () => result.current.saveChannel({
      kind: "generic_webhook",
      name: "일반 웹훅",
      config: { url: "https://example.test/hook" },
    }));

    expect(notificationApi.createNotificationChannel).toHaveBeenCalledWith({
      kind: "generic_webhook",
      name: "일반 웹훅",
      config: { url: "https://example.test/hook" },
      enabled: true,
    });
    expect(result.current.channels).toEqual([
      created,
      firstTelegram,
      duplicateTelegram,
      discord,
    ]);
    expect(pushToast).toHaveBeenLastCalledWith("알림 채널을 연결했습니다.");

    await act(async () => result.current.saveChannel({
      kind: "telegram",
      name: "변경된 텔레그램",
      config: { bot_token: "placeholder", chat_id: "placeholder" },
    }));

    expect(notificationApi.updateNotificationChannel).toHaveBeenCalledWith(firstTelegram.id, {
      name: "변경된 텔레그램",
      config: { bot_token: "placeholder", chat_id: "placeholder" },
      enabled: true,
    });
    expect(result.current.channels).toEqual([updated, created, discord]);
    expect(pushToast).toHaveBeenCalledTimes(2);
  });

  it("rethrows create errors after presenting a safe message", async () => {
    const failure = new Error("채널 생성 실패");
    notificationApi.createNotificationChannel.mockRejectedValue(failure);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(notificationApi.fetchNotificationChannels).toHaveBeenCalledOnce());

    await expect(result.current.saveChannel({
      kind: "telegram",
      name: "내 텔레그램",
      config: { bot_token: "placeholder", chat_id: "placeholder" },
    })).rejects.toBe(failure);

    expect(pushToast).toHaveBeenLastCalledWith("채널 생성 실패");
  });

  it("rethrows update errors and does not assume an unknown value has a message", async () => {
    const loaded = channel();
    notificationApi.fetchNotificationChannels.mockResolvedValue([loaded]);
    notificationApi.updateNotificationChannel.mockRejectedValue("malformed failure");
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(result.current.channels).toEqual([loaded]));

    await expect(result.current.saveChannel({
      kind: "telegram",
      name: "변경된 이름",
      config: { bot_token: "placeholder", chat_id: "placeholder" },
    })).rejects.toBe("malformed failure");

    expect(notificationApi.updateNotificationChannel).toHaveBeenCalledWith(loaded.id, {
      name: "변경된 이름",
      config: { bot_token: "placeholder", chat_id: "placeholder" },
      enabled: true,
    });
    expect(pushToast).toHaveBeenLastCalledWith("알림 채널을 연결하지 못했습니다.");
  });

  it("replaces only the selected live non-web channel without changing row order", async () => {
    const target = channel("telegram", { id: "telegram-target" });
    const sameKind = channel("telegram", { id: "telegram-other", name: "다른 텔레그램" });
    const discord = channel("discord_webhook", { id: "discord-one" });
    const updated = { ...target, enabled: false, updatedAt: "2026-08-05T01:00:00Z" };
    notificationApi.fetchNotificationChannels.mockResolvedValue([discord, target, sameKind]);
    notificationApi.updateNotificationChannel.mockResolvedValue(updated);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(result.current.channels).toEqual([discord, target, sameKind]));

    await act(async () => result.current.toggleChannel(target, false));

    expect(notificationApi.updateNotificationChannel).toHaveBeenCalledWith(target.id, {
      enabled: false,
    });
    expect(result.current.channels).toEqual([discord, updated, sameKind]);
  });

  it("enables live web push through the existing channel and then refreshes device state", async () => {
    const webPush = channel("web_push", { enabled: false });
    const otherWebPush = channel("web_push", { id: "web_push-two", name: "다른 기기" });
    const enabled = { ...webPush, enabled: true, updatedAt: "2026-08-05T01:00:00Z" };
    const refreshedState: BrowserPushState = {
      support: "supported",
      permission: "granted",
      subscribed: true,
    };
    const order: string[] = [];
    notificationApi.fetchNotificationChannels.mockResolvedValue([webPush, otherWebPush]);
    notificationApi.connectBrowserPush.mockImplementation(async () => {
      order.push("connect");
      return enabled;
    });
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(result.current.channels).toEqual([webPush, otherWebPush]));
    await waitFor(() => expect(notificationApi.readBrowserPushState).toHaveBeenCalledOnce());
    notificationApi.readBrowserPushState.mockImplementation(async () => {
      order.push("read");
      return refreshedState;
    });

    await act(async () => result.current.toggleChannel(webPush, true));

    expect(order).toEqual(["connect", "read"]);
    expect(notificationApi.connectBrowserPush).toHaveBeenCalledWith(webPush.name, webPush.id);
    expect(result.current.channels).toEqual([enabled, otherWebPush]);
    expect(result.current.browserPushState).toEqual(refreshedState);
  });

  it("disables a web push channel before removing its device subscription", async () => {
    const webPush = channel("web_push");
    const otherWebPush = channel("web_push", { id: "web_push-two", name: "다른 기기" });
    notificationApi.fetchNotificationChannels.mockResolvedValue([webPush, otherWebPush]);
    const disabled = { ...webPush, enabled: false };
    const order: string[] = [];
    notificationApi.updateNotificationChannel.mockImplementation(async () => {
      order.push("update");
      return disabled;
    });
    notificationApi.disconnectBrowserPush.mockImplementation(async () => {
      order.push("disconnect");
      return { ...supportedState, subscribed: false };
    });
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(result.current.channels).toEqual([webPush, otherWebPush]));
    await waitFor(() => expect(notificationApi.readBrowserPushState).toHaveBeenCalledOnce());
    notificationApi.readBrowserPushState.mockImplementation(async () => {
      order.push("read");
      return { ...supportedState, subscribed: false };
    });

    await act(async () => result.current.toggleChannel(webPush, false));

    expect(order).toEqual(["update", "disconnect", "read"]);
    expect(result.current.channels).toEqual([disabled, otherWebPush]);
    expect(result.current.browserPushState.subscribed).toBe(false);
  });

  it("fails closed when a web push test has no active device subscription", async () => {
    const webPush = channel("web_push");
    const inactiveState: BrowserPushState = {
      support: "supported",
      permission: "granted",
      subscribed: false,
    };
    notificationApi.readBrowserPushState.mockResolvedValue(inactiveState);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await act(async () => result.current.testChannel(webPush));

    expect(notificationApi.testNotificationChannel).not.toHaveBeenCalled();
    expect(pushToast).toHaveBeenLastCalledWith("이 기기의 OS 알림 구독을 먼저 켜 주세요.");
  });

  it("queues a live web push test after confirming its device subscription", async () => {
    const webPush = channel("web_push");
    notificationApi.readBrowserPushState.mockResolvedValue(supportedState);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));

    await act(async () => result.current.testChannel(webPush));

    expect(notificationApi.testNotificationChannel).toHaveBeenCalledWith(webPush.id);
    expect(result.current.browserPushState).toEqual(supportedState);
    expect(pushToast).toHaveBeenLastCalledWith(
      `${webPush.name} 시험 알림을 전송 대기열에 넣었습니다.`,
    );
  });

  it.each([
    {
      label: "new",
      loaded: new Array<NotificationChannel>(),
      expectedName: "이 브라우저",
      expectedId: null,
      expectedRemainder: new Array<NotificationChannel>(),
    },
    {
      label: "existing",
      loaded: [
        channel("web_push", { id: "web_push-existing", name: "기존 브라우저" }),
        channel("web_push", { id: "web_push-duplicate", name: "중복 브라우저" }),
        channel("telegram", { id: "telegram-one" }),
      ],
      expectedName: "기존 브라우저",
      expectedId: "web_push-existing",
      expectedRemainder: [channel("telegram", { id: "telegram-one" })],
    },
  ])("connects a $label live browser and collapses web push channels by kind", async ({
    loaded,
    expectedName,
    expectedId,
    expectedRemainder,
  }) => {
    const saved = channel("web_push", { id: "web_push-saved", name: expectedName });
    notificationApi.fetchNotificationChannels.mockResolvedValue(loaded);
    notificationApi.connectBrowserPush.mockResolvedValue(saved);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(notificationApi.fetchNotificationChannels).toHaveBeenCalledOnce());
    await waitFor(() => expect(result.current.channels).toEqual(loaded));

    await act(async () => result.current.connectWebPushChannel());

    expect(notificationApi.connectBrowserPush).toHaveBeenCalledWith(expectedName, expectedId);
    expect(result.current.channels).toEqual([saved, ...expectedRemainder]);
    expect(pushToast).toHaveBeenLastCalledWith("이 기기의 OS 알림을 연결했습니다.");
  });

  it("keeps the existing demo toggle, test-send, and connect paths local", async () => {
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: true,
      onAuthenticationExpired,
      pushToast,
    }));

    const telegram = channel("telegram", { id: "demo-telegram" });
    await act(async () => result.current.toggleChannel(telegram, false));
    await act(async () => result.current.testChannel(telegram));
    await act(async () => result.current.connectWebPushChannel());

    for (const request of Object.values(notificationApi)) {
      expect(request).not.toHaveBeenCalled();
    }
    expect(result.current.browserPushState).toEqual(supportedState);
  });

  it("resets every channel-owned state", async () => {
    const loaded = channel();
    notificationApi.fetchNotificationChannels.mockResolvedValue([loaded]);
    const onAuthenticationExpired = vi.fn();
    const pushToast = vi.fn();
    const { result } = renderHook(() => useNotificationChannelSettings({
      authenticated: true,
      demo: false,
      onAuthenticationExpired,
      pushToast,
    }));
    await waitFor(() => expect(result.current.channels).toEqual([loaded]));
    await waitFor(() => expect(result.current.browserPushState).toEqual(supportedState));

    act(() => result.current.reset());

    expect(result.current.channels).toEqual([]);
    expect(result.current.browserPushState).toEqual({
      support: "checking",
      permission: "default",
      subscribed: false,
    });
  });
});
