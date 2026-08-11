import { readFileSync } from "node:fs";
import vm from "node:vm";
import { describe, expect, it, vi } from "vitest";

interface ExtendableEventFixture {
  waitUntil(promise: Promise<unknown>): void;
}

interface WindowClientFixture {
  url: string;
  visibilityState: "hidden" | "visible";
  focus: ReturnType<typeof vi.fn>;
  postMessage: ReturnType<typeof vi.fn>;
  navigate: ReturnType<typeof vi.fn>;
}

function windowClient(
  url = "https://railwait.test/",
  visibilityState: WindowClientFixture["visibilityState"] = "visible",
): WindowClientFixture {
  const client: WindowClientFixture = {
    url,
    visibilityState,
    focus: vi.fn(),
    postMessage: vi.fn(),
    navigate: vi.fn(),
  };
  client.focus.mockResolvedValue(client);
  client.navigate.mockResolvedValue(client);
  return client;
}

function loadServiceWorker(clientList: WindowClientFixture[] = []) {
  const listeners: Partial<Record<string, unknown>> = {};
  const showNotification = vi
    .fn<(title: string, options?: NotificationOptions) => Promise<void>>()
    .mockResolvedValue(undefined);
  const openWindow = vi.fn().mockResolvedValue(null);
  const fetchRequest = vi.fn();
  const cachedResponses = new Map<string, unknown>();
  const cacheKey = (value: string | { url?: string }): string => (
    typeof value === "string" ? value : value.url ?? ""
  );
  const cache = {
    addAll: vi.fn().mockResolvedValue(undefined),
    match: vi.fn(async (key: string | { url?: string }) => cachedResponses.get(cacheKey(key))),
    put: vi.fn(async (key: string | { url?: string }, response: unknown) => {
      cachedResponses.set(cacheKey(key), response);
    }),
  };
  const cacheStorage = {
    open: vi.fn().mockResolvedValue(cache),
    match: vi.fn(async (key: string | { url?: string }) => cachedResponses.get(cacheKey(key))),
    keys: vi.fn().mockResolvedValue([]),
    delete: vi.fn().mockResolvedValue(true),
  };
  const clients = {
    claim: vi.fn(),
    matchAll: vi.fn().mockResolvedValue(clientList),
    openWindow,
  };
  const self = {
    addEventListener: (type: string, listener: unknown) => {
      listeners[type] = listener;
    },
    registration: {
      scope: "https://railwait.test/",
      showNotification,
      navigationPreload: { enable: vi.fn().mockResolvedValue(undefined) },
    },
    location: { origin: "https://railwait.test" },
    clients,
    skipWaiting: vi.fn(),
  };
  vm.runInNewContext(readFileSync("public/sw.js", "utf8"), {
    self,
    caches: cacheStorage,
    fetch: fetchRequest,
    URL,
    setTimeout: (...args: Parameters<typeof setTimeout>) => setTimeout(...args),
    clearTimeout: (id: Parameters<typeof clearTimeout>[0]) => clearTimeout(id),
  });
  return {
    listeners,
    showNotification,
    clients,
    openWindow,
    fetchRequest,
    cache,
    cacheStorage,
    cachedResponses,
  };
}

function requireListener(
  listeners: Partial<Record<string, unknown>>,
  type: "fetch" | "push" | "notificationclick",
): (event: unknown) => void {
  const listener = listeners[type];
  if (typeof listener !== "function") {
    throw new Error(`Service worker did not register a ${type} listener`);
  }
  return listener as (event: unknown) => void;
}

interface FetchRequestFixture {
  url: string;
  method: "GET";
  mode: "cors" | "navigate";
  destination: "" | "image" | "script" | "style";
}

async function dispatchFetch(
  listeners: Partial<Record<string, unknown>>,
  request: FetchRequestFixture,
  preloadResponse?: unknown,
): Promise<unknown> {
  let responseWork: Promise<unknown> | undefined;
  const lifetimeWork: Promise<unknown>[] = [];
  const event = {
    request,
    preloadResponse: Promise.resolve(preloadResponse),
    respondWith: (promise: Promise<unknown>) => {
      responseWork = promise;
    },
    waitUntil: (promise: Promise<unknown>) => {
      lifetimeWork.push(promise);
    },
  };
  requireListener(listeners, "fetch")(event);
  if (responseWork === undefined) throw new Error("Fetch listener did not call respondWith");
  const response = await responseWork;
  await Promise.all(lifetimeWork);
  return response;
}

function cacheableResponse(label: string) {
  const response = {
    label,
    ok: true,
    type: "basic",
    clone: vi.fn(),
  };
  response.clone.mockReturnValue(response);
  return response;
}

async function dispatchPush(
  listeners: Partial<Record<string, unknown>>,
  data: unknown,
  parseError?: Error,
): Promise<void> {
  let work: Promise<unknown> | undefined;
  const event: ExtendableEventFixture & { data: { json(): unknown } } = {
    data: {
      json: () => {
        if (parseError) throw parseError;
        return data;
      },
    },
    waitUntil: (promise) => {
      work = promise;
    },
  };
  requireListener(listeners, "push")(event);
  if (work === undefined) throw new Error("Push listener did not pass work to waitUntil");
  await work;
}

async function dispatchNotificationClick(
  listeners: Partial<Record<string, unknown>>,
  data: unknown,
): Promise<ReturnType<typeof vi.fn>> {
  let work: Promise<unknown> | undefined;
  const close = vi.fn();
  const event: ExtendableEventFixture & { notification: { data: unknown; close(): void } } = {
    notification: { data, close },
    waitUntil: (promise) => {
      work = promise;
    },
  };
  requireListener(listeners, "notificationclick")(event);
  if (work === undefined) throw new Error("Click listener did not pass work to waitUntil");
  await work;
  return close;
}

describe("service worker push contract", () => {
  it("keeps official booking URLs out of the main click target and alerts a visible app", async () => {
    const visible = windowClient();
    const hidden = windowClient("https://railwait.test/reservations", "hidden");
    const otherOrigin = windowClient("https://example.invalid/");
    const { listeners, showNotification, clients } = loadServiceWorker([
      visible,
      hidden,
      otherOrigin,
    ]);

    await dispatchPush(listeners, {
      watch_id: "watch-one",
      status: "payment_required",
      message: "좌석을 확보했습니다.",
      official_booking_url: "https://etk.srail.kr",
    });

    expect(showNotification).toHaveBeenCalledWith("결제 필요", expect.objectContaining({
      body: "좌석을 확보했습니다.",
      icon: "/icons/app-icon-any-512-v2.png",
      badge: "/icons/notification-badge-96.png",
      data: {
        url: "https://railwait.test/",
        watchId: "watch-one",
        status: "payment_required",
      },
      tag: "railwait-watch:watch-one",
      renotify: true,
      requireInteraction: true,
      vibrate: [200, 100, 200],
    }));
    expect(clients.matchAll).toHaveBeenCalledWith({
      type: "window",
      includeUncontrolled: true,
    });
    expect(visible.postMessage).toHaveBeenCalledWith({
      type: "railwait:notification",
      kind: "push",
      watchId: "watch-one",
      status: "payment_required",
    });
    expect(hidden.postMessage).not.toHaveBeenCalled();
    expect(otherOrigin.postMessage).not.toHaveBeenCalled();
  });

  it.each([
    ["scheduled", "공식 확인 대기"],
    ["watching", "열차 대기 확인"],
    ["official_waitlist", "공식 예약대기 가능"],
    ["seat_found", "좌석 발견"],
    ["reserving", "예매 진행 중"],
    ["payment_required", "결제 필요"],
    ["completed", "결제 완료"],
    ["cooldown", "잠시 후 다시 확인"],
    ["auth_required", "로그인 확인 필요"],
    ["expired", "대기 종료"],
    ["failed", "대기 확인 실패"],
  ])("maps %s to an actionable Korean title", async (status, title) => {
    const { listeners, showNotification } = loadServiceWorker();
    await dispatchPush(listeners, { watch_id: "watch-one", status });
    expect(showNotification).toHaveBeenCalledWith(title, expect.any(Object));
  });

  it("keeps reservation progress in the critical seat-discovery sequence", async () => {
    const { listeners, showNotification } = loadServiceWorker();

    await dispatchPush(listeners, {
      watch_id: "watch-one",
      status: "reserving",
      message: "좌석을 발견해 예매를 진행하고 있습니다.",
    });

    expect(showNotification).toHaveBeenCalledWith("예매 진행 중", expect.objectContaining({
      tag: "railwait-watch:watch-one",
      renotify: true,
      requireInteraction: true,
      vibrate: [200, 100, 200],
    }));
  });

  it("keeps a safe app-root URL but rejects non-app and external explicit URLs", async () => {
    const safeWorker = loadServiceWorker();
    await dispatchPush(safeWorker.listeners, {
      title: "직접 제목",
      body: "직접 본문",
      url: "/?from=push",
    });
    expect(safeWorker.showNotification).toHaveBeenCalledWith(
      "직접 제목",
      expect.objectContaining({
        body: "직접 본문",
        data: expect.objectContaining({
          url: "https://railwait.test/?from=push",
        }),
      }),
    );

    const nonAppWorker = loadServiceWorker();
    await dispatchPush(nonAppWorker.listeners, { url: "/api/v1/watches" });
    expect(nonAppWorker.showNotification).toHaveBeenCalledWith(
      "레일웨잇",
      expect.objectContaining({
        data: expect.objectContaining({ url: "https://railwait.test/" }),
      }),
    );

    const unsafeWorker = loadServiceWorker();
    await dispatchPush(unsafeWorker.listeners, { url: "https://evil.invalid/phish" });
    expect(unsafeWorker.showNotification).toHaveBeenCalledWith(
      "레일웨잇",
      expect.objectContaining({
        data: expect.objectContaining({ url: "https://railwait.test/" }),
      }),
    );
  });

  it("falls back to a generic notification when push JSON parsing fails", async () => {
    const visible = windowClient();
    const { listeners, showNotification } = loadServiceWorker([visible]);
    await dispatchPush(listeners, null, new SyntaxError("invalid JSON"));

    expect(showNotification).toHaveBeenCalledWith("레일웨잇", expect.objectContaining({
      body: "기차 대기 상태가 변경되었습니다.",
      data: {
        url: "https://railwait.test/",
        watchId: null,
        status: null,
      },
    }));
    expect(visible.postMessage).not.toHaveBeenCalled();
  });
});

describe("service worker app shell performance contract", () => {
  it("serves the network app shell so index and hashed bundles stay in one deployment", async () => {
    const cached = cacheableResponse("cached-shell");
    const refreshed = cacheableResponse("refreshed-shell");
    const worker = loadServiceWorker();
    worker.cachedResponses.set("/", cached);
    worker.fetchRequest.mockResolvedValue(refreshed);

    const response = await dispatchFetch(worker.listeners, {
      url: "https://railwait.test/?from=notification",
      method: "GET",
      mode: "navigate",
      destination: "",
    });

    expect(response).toBe(refreshed);
    expect(worker.fetchRequest).toHaveBeenCalledOnce();
    expect(worker.cache.put).toHaveBeenCalledWith("/", refreshed);
  });

  it("uses the cached app shell only when navigation network access fails", async () => {
    const cached = cacheableResponse("cached-shell");
    const worker = loadServiceWorker();
    worker.cachedResponses.set("/", cached);
    worker.fetchRequest.mockRejectedValue(new Error("offline"));

    const response = await dispatchFetch(worker.listeners, {
      url: "https://railwait.test/?from=notification",
      method: "GET",
      mode: "navigate",
      destination: "",
    });

    expect(response).toBe(cached);
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it("serves a cached static bundle while revalidating that exact asset", async () => {
    const request: FetchRequestFixture = {
      url: "https://railwait.test/assets/index-version.js",
      method: "GET",
      mode: "cors",
      destination: "script",
    };
    const cached = cacheableResponse("cached-script");
    const refreshed = cacheableResponse("refreshed-script");
    const worker = loadServiceWorker();
    worker.cachedResponses.set(request.url, cached);
    worker.fetchRequest.mockResolvedValue(refreshed);

    const response = await dispatchFetch(worker.listeners, request);

    expect(response).toBe(cached);
    expect(worker.cache.put).toHaveBeenCalledWith(request, refreshed);
  });

  it("does not intercept authenticated API requests", () => {
    const worker = loadServiceWorker();
    const respondWith = vi.fn();
    requireListener(worker.listeners, "fetch")({
      request: {
        url: "https://railwait.test/api/v1/auth/status",
        method: "GET",
        mode: "cors",
        destination: "",
      },
      respondWith,
      waitUntil: vi.fn(),
    });

    expect(respondWith).not.toHaveBeenCalled();
    expect(worker.fetchRequest).not.toHaveBeenCalled();
  });
});

describe("service worker notification click contract", () => {
  it("skips same-origin API and documentation clients when selecting an existing UI client", async () => {
    const apiClient = windowClient("https://railwait.test/api/v1/watches");
    const docsClient = windowClient("https://railwait.test/docs");
    const uiClient = windowClient("https://railwait.test/reservations");
    const { listeners, openWindow } = loadServiceWorker([apiClient, docsClient, uiClient]);

    await dispatchNotificationClick(listeners, {
      url: "https://railwait.test/",
      watchId: "watch-one",
      status: "seat_found",
    });

    expect(apiClient.focus).not.toHaveBeenCalled();
    expect(docsClient.focus).not.toHaveBeenCalled();
    expect(uiClient.focus).toHaveBeenCalledOnce();
    expect(uiClient.postMessage).toHaveBeenCalledWith(expect.objectContaining({
      kind: "click",
      watchId: "watch-one",
      status: "seat_found",
    }));
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("focuses and messages an existing same-origin PWA client", async () => {
    const existing = windowClient("https://railwait.test/settings");
    const { listeners, openWindow, clients } = loadServiceWorker([existing]);
    const close = await dispatchNotificationClick(listeners, {
      url: "https://railwait.test/",
      watchId: "watch-one",
      status: "seat_found",
    });

    expect(close).toHaveBeenCalledOnce();
    expect(clients.matchAll).toHaveBeenCalledWith({
      type: "window",
      includeUncontrolled: true,
    });
    expect(existing.focus).toHaveBeenCalledOnce();
    expect(existing.navigate).not.toHaveBeenCalled();
    expect(existing.postMessage).toHaveBeenCalledWith({
      type: "railwait:notification",
      kind: "click",
      watchId: "watch-one",
      status: "seat_found",
    });
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("navigates a background client that focus alone cannot bring to the foreground", async () => {
    const background = windowClient("https://railwait.test/reservations", "hidden");
    background.focus
      .mockResolvedValueOnce(background)
      .mockImplementationOnce(async () => {
        background.visibilityState = "visible";
        return background;
      });
    background.navigate.mockResolvedValue(background);
    const { listeners, openWindow } = loadServiceWorker([background]);

    await dispatchNotificationClick(listeners, {
      url: "/?from=notification",
      watchId: "watch-one",
      status: "seat_found",
    });

    expect(background.navigate).toHaveBeenCalledWith("https://railwait.test/?from=notification");
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("recovers inside the existing client when navigation succeeds after a focus failure", async () => {
    const stale = windowClient("https://railwait.test/settings");
    stale.visibilityState = "hidden";
    stale.focus
      .mockRejectedValueOnce(new Error("focus is not allowed"))
      .mockImplementationOnce(async () => {
        stale.visibilityState = "visible";
        return stale;
      });
    stale.navigate.mockResolvedValue(stale);
    const { listeners, openWindow } = loadServiceWorker([stale]);

    await dispatchNotificationClick(listeners, {
      url: "/",
      watchId: "watch-one",
      status: "payment_required",
    });

    expect(stale.navigate).toHaveBeenCalledWith("https://railwait.test/");
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("opens the app when focus remains wedged after navigation", async () => {
    vi.useFakeTimers();
    try {
      const wedged = windowClient("https://railwait.test/", "hidden");
      wedged.focus.mockReturnValue(new Promise(() => undefined));
      wedged.navigate.mockResolvedValue(wedged);
      const { listeners, openWindow } = loadServiceWorker([wedged]);

      let work: Promise<unknown> | undefined;
      requireListener(listeners, "notificationclick")({
        notification: {
          data: { url: "/", watchId: "watch-one", status: "seat_found" },
          close: vi.fn(),
        },
        waitUntil: (promise: Promise<unknown>) => {
          work = promise;
        },
      });
      await vi.runAllTimersAsync();
      await work;

      expect(wedged.navigate).toHaveBeenCalledWith("https://railwait.test/");
      expect(openWindow).toHaveBeenCalledWith("https://railwait.test/");
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens the app without a redundant second focus when no client is available", async () => {
    const opened = windowClient("https://railwait.test/");
    const { listeners, openWindow } = loadServiceWorker();
    openWindow.mockResolvedValue(opened);

    await dispatchNotificationClick(listeners, {
      url: "/?from=notification",
      watchId: "watch-one",
      status: "auth_required",
    });

    expect(openWindow).toHaveBeenCalledWith("https://railwait.test/?from=notification");
    expect(opened.focus).not.toHaveBeenCalled();
    expect(opened.postMessage).toHaveBeenCalledWith(expect.objectContaining({
      kind: "click",
      watchId: "watch-one",
      status: "auth_required",
    }));
  });

  it("opens a new window when both focus and navigation fail on a stale client", async () => {
    const stale = windowClient("https://railwait.test/");
    stale.focus.mockRejectedValue(new Error("client is closing"));
    stale.navigate.mockRejectedValue(new Error("client is closing"));
    const opened = windowClient("https://railwait.test/");
    const { listeners, openWindow } = loadServiceWorker([stale]);
    openWindow.mockResolvedValue(opened);

    await dispatchNotificationClick(listeners, {
      url: "/",
      watchId: "watch-one",
      status: "payment_required",
    });

    expect(stale.focus).toHaveBeenCalledOnce();
    expect(stale.navigate).toHaveBeenCalledOnce();
    expect(openWindow).toHaveBeenCalledWith("https://railwait.test/");
    expect(opened.postMessage).toHaveBeenCalledWith(expect.objectContaining({
      kind: "click",
      watchId: "watch-one",
      status: "payment_required",
    }));
  });

  it("opens the app when navigation succeeds but the client remains hidden after refocus", async () => {
    const background = windowClient("https://railwait.test/reservations", "hidden");
    const opened = windowClient("https://railwait.test/");
    const { listeners, openWindow } = loadServiceWorker([background]);
    openWindow.mockResolvedValue(opened);

    await dispatchNotificationClick(listeners, {
      url: "/?from=notification",
      watchId: "watch-one",
      status: "seat_found",
    });

    expect(background.focus).toHaveBeenCalledTimes(2);
    expect(background.navigate).toHaveBeenCalledWith("https://railwait.test/?from=notification");
    expect(openWindow).toHaveBeenCalledWith("https://railwait.test/?from=notification");
    expect(opened.postMessage).toHaveBeenCalledWith(expect.objectContaining({
      kind: "click",
      watchId: "watch-one",
      status: "seat_found",
    }));
  });

  it("never opens an unsafe notification URL", async () => {
    const { listeners, openWindow } = loadServiceWorker();
    await dispatchNotificationClick(listeners, {
      url: "javascript:alert(document.cookie)",
      watchId: "watch-one",
      status: "seat_found",
    });
    expect(openWindow).toHaveBeenCalledWith("https://railwait.test/");
  });
});
