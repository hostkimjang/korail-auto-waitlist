import { ApiError, request } from "./client";

export type BrowserPushSupport = "supported" | "unsupported" | "insecure";

export interface BrowserPushState {
  support: BrowserPushSupport;
  permission: NotificationPermission;
  subscribed: boolean;
}

export async function fetchNotificationChannels(): Promise<unknown> {
  return request("/notifications/channels");
}

export async function createNotificationChannel(payload: unknown): Promise<unknown> {
  return request("/notifications/channels", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateNotificationChannel(
  id: string,
  payload: unknown,
): Promise<unknown> {
  return request(`/notifications/channels/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteNotificationChannel(id: string): Promise<unknown> {
  return request(`/notifications/channels/${id}`, { method: "DELETE" });
}

export async function testNotificationChannel(id: string): Promise<unknown> {
  return request(`/notifications/channels/${id}/test-send`, { method: "POST" });
}

export async function waitForServiceWorkerRegistration(
  timeoutMs = 8_000,
): Promise<ServiceWorkerRegistration> {
  let timeoutId: number | undefined;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise<ServiceWorkerRegistration>((_resolve, reject) => {
        timeoutId = window.setTimeout(() => {
          reject(new ApiError(
            "알림 서비스를 준비하지 못했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.",
          ));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timeoutId !== undefined) window.clearTimeout(timeoutId);
  }
}

function browserPushSupported(): boolean {
  return typeof navigator !== "undefined"
    && "serviceWorker" in navigator
    && typeof window !== "undefined"
    && "PushManager" in window
    && "Notification" in window;
}

function requireBrowserPushSupport(): void {
  if (!browserPushSupported()) {
    throw new ApiError("이 브라우저는 OS 알림을 지원하지 않습니다.");
  }
  if (window.isSecureContext === false) {
    throw new ApiError("OS 알림은 HTTPS 또는 이 기기의 localhost 주소에서만 사용할 수 있습니다.");
  }
}

export async function readBrowserPushState(): Promise<BrowserPushState> {
  if (!browserPushSupported()) {
    return { support: "unsupported", permission: "default", subscribed: false };
  }
  if (window.isSecureContext === false) {
    return { support: "insecure", permission: Notification.permission, subscribed: false };
  }
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = registration
    ? await registration.pushManager.getSubscription()
    : null;
  return {
    support: "supported",
    permission: Notification.permission,
    subscribed: subscription !== null,
  };
}

export async function disconnectBrowserPush(): Promise<BrowserPushState> {
  requireBrowserPushSupport();
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = registration
    ? await registration.pushManager.getSubscription()
    : null;
  if (subscription) await subscription.unsubscribe();
  return readBrowserPushState();
}

function publicKeyFrom(payload: unknown): string {
  if (
    typeof payload === "object"
    && payload !== null
    && "public_key" in payload
    && typeof payload.public_key === "string"
  ) {
    return payload.public_key;
  }
  throw new ApiError("Web Push 공개키 응답을 확인할 수 없습니다.");
}

function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const base64 = value
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(value.length / 4) * 4, "=");
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

export async function connectBrowserPush(
  name = "이 브라우저",
  existingChannelId: string | null = null,
): Promise<unknown> {
  requireBrowserPushSupport();
  const publicKey = publicKeyFrom(await request("/notifications/web-push/public-key"));
  const existingRegistration = await navigator.serviceWorker.getRegistration();
  if (!existingRegistration) await navigator.serviceWorker.register("/sw.js");
  const registration = await waitForServiceWorkerRegistration();
  const permission = await Notification.requestPermission();
  if (permission === "denied") {
    throw new ApiError(
      "OS 알림 권한이 차단되어 있습니다. 브라우저 사이트 설정에서 알림을 허용해 주세요.",
    );
  }
  if (permission !== "granted") {
    throw new ApiError("OS 알림 권한을 허용해야 연결할 수 있습니다.");
  }
  const subscription = await registration.pushManager.getSubscription()
    ?? await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: fromBase64Url(publicKey),
    });
  const payload = {
    name,
    config: { subscription_info: JSON.stringify(subscription.toJSON()) },
    enabled: true,
  };
  if (existingChannelId) return updateNotificationChannel(existingChannelId, payload);
  return createNotificationChannel({ kind: "web_push", ...payload });
}
