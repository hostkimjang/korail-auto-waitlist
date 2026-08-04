import { readFileSync } from "node:fs";
import vm from "node:vm";
import { describe, expect, it, vi } from "vitest";

function loadServiceWorker() {
  const listeners = {};
  const showNotification = vi.fn().mockResolvedValue(undefined);
  const self = {
    addEventListener: (type, listener) => { listeners[type] = listener; },
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

describe("service worker push contract", () => {
  it("accepts the backend message and official booking URL fields", async () => {
    const { listeners, showNotification } = loadServiceWorker();
    let notification;
    listeners.push({
      data: { json: () => ({ status: "payment_required", message: "좌석을 확보했습니다.", official_booking_url: "https://etk.srail.kr" }) },
      waitUntil: (promise) => { notification = promise; },
    });
    await notification;

    expect(showNotification).toHaveBeenCalledWith("결제 필요", expect.objectContaining({
      body: "좌석을 확보했습니다.",
      data: { url: "https://etk.srail.kr" },
    }));
  });

  it("keeps explicit title, body and URL payloads compatible", async () => {
    const { listeners, showNotification } = loadServiceWorker();
    let notification;
    listeners.push({
      data: { json: () => ({ title: "직접 제목", body: "직접 본문", url: "/reservations" }) },
      waitUntil: (promise) => { notification = promise; },
    });
    await notification;

    expect(showNotification).toHaveBeenCalledWith("직접 제목", expect.objectContaining({
      body: "직접 본문",
      data: { url: "/reservations" },
    }));
  });
});
