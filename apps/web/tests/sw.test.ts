import { readFileSync } from "node:fs";
import vm from "node:vm";
import { describe, expect, it, vi } from "vitest";

interface PushEventFixture {
  data: { json(): unknown };
  waitUntil(promise: Promise<unknown>): void;
}

function loadServiceWorker() {
  const listeners: Partial<Record<string, unknown>> = {};
  const showNotification = vi
    .fn<(title: string, options?: NotificationOptions) => Promise<void>>()
    .mockResolvedValue(undefined);
  const self = {
    addEventListener: (type: string, listener: unknown) => {
      listeners[type] = listener;
    },
    registration: { showNotification },
    clients: { claim: vi.fn() },
    skipWaiting: vi.fn(),
  };
  vm.runInNewContext(readFileSync("public/sw.js", "utf8"), {
    self,
    caches: {},
    clients: { openWindow: vi.fn() },
    fetch: vi.fn(),
    URL,
  });
  return { listeners, showNotification };
}

function requirePushListener(listeners: Partial<Record<string, unknown>>) {
  const pushListener = listeners.push;
  if (typeof pushListener !== "function") {
    throw new Error("Service worker did not register a push listener");
  }
  return pushListener;
}

async function dispatchPush(listeners: Partial<Record<string, unknown>>, data: unknown): Promise<void> {
  const pushListener = requirePushListener(listeners);
  let notification: Promise<unknown> | undefined;
  const event: PushEventFixture = {
    data: { json: () => data },
    waitUntil: (promise) => {
      notification = promise;
    },
  };

  pushListener(event);
  if (notification === undefined) {
    throw new Error("Push listener did not pass a notification promise to waitUntil");
  }
  await notification;
}

describe("service worker push contract", () => {
  it("accepts the backend message and official booking URL fields", async () => {
    const { listeners, showNotification } = loadServiceWorker();
    await dispatchPush(listeners, {
      status: "payment_required",
      message: "좌석을 확보했습니다.",
      official_booking_url: "https://etk.srail.kr",
    });

    expect(showNotification).toHaveBeenCalledWith("결제 필요", expect.objectContaining({
      body: "좌석을 확보했습니다.",
      data: { url: "https://etk.srail.kr" },
    }));
  });

  it("keeps explicit title, body and URL payloads compatible", async () => {
    const { listeners, showNotification } = loadServiceWorker();
    await dispatchPush(listeners, {
      title: "직접 제목",
      body: "직접 본문",
      url: "/reservations",
    });

    expect(showNotification).toHaveBeenCalledWith("직접 제목", expect.objectContaining({
      body: "직접 본문",
      data: { url: "/reservations" },
    }));
  });
});
