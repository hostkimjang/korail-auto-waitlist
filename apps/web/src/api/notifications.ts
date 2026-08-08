import { ApiError, request } from "./client";

type UnknownRecord = Record<string, unknown>;

export type NotificationChannelKind =
  | "web_push"
  | "telegram"
  | "discord_webhook"
  | "generic_webhook";

export interface NotificationChannel {
  id: string;
  kind: NotificationChannelKind;
  name: string;
  enabled: boolean;
  configured: boolean;
  deviceKey: string | null;
  activeDeviceCount: number | null;
  createdAt: string;
  updatedAt: string;
}

export interface TelegramNotificationConfig {
  bot_token: string;
  chat_id: string;
}

export interface WebhookNotificationConfig {
  url: string;
  authorization?: string;
}

export interface WebPushNotificationConfig {
  subscription_info: string;
}

export type NotificationChannelEditorSubmission =
  | {
    kind: "telegram";
    name: string;
    config: TelegramNotificationConfig;
  }
  | {
    kind: "discord_webhook";
    name: string;
    config: WebhookNotificationConfig;
  }
  | {
    kind: "generic_webhook";
    name: string;
    config: WebhookNotificationConfig;
  };

export type NotificationChannelCreatePayload =
  | (NotificationChannelEditorSubmission & { enabled?: boolean })
  | {
    kind: "web_push";
    name: string;
    config: WebPushNotificationConfig;
    enabled?: boolean;
  };

export interface NotificationChannelUpdatePayload {
  name?: string;
  config?: TelegramNotificationConfig | WebhookNotificationConfig | WebPushNotificationConfig;
  enabled?: boolean;
}

export interface QueuedNotification {
  queued: true;
  eventId: string;
}

export type BrowserPushSupport = "checking" | "supported" | "unsupported" | "insecure";

export interface BrowserPushState {
  support: BrowserPushSupport;
  permission: NotificationPermission;
  subscribed: boolean;
  deviceKey: string | null;
}

const NOTIFICATION_CHANNEL_KINDS: ReadonlySet<string> = new Set([
  "web_push",
  "telegram",
  "discord_webhook",
  "generic_webhook",
]);

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function awareTimestamp(value: unknown): value is string {
  return typeof value === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
}

function requiredString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function isNotificationChannelKind(value: string): value is NotificationChannelKind {
  return NOTIFICATION_CHANNEL_KINDS.has(value);
}

export function mapNotificationChannel(value: unknown): NotificationChannel {
  if (!isRecord(value)) {
    throw new ApiError("알림 채널 응답 형식을 확인할 수 없습니다.");
  }
  const id = requiredString(value.id);
  const kind = requiredString(value.kind);
  const name = requiredString(value.name);
  const deviceKey = requiredString(value.device_key);
  const activeDeviceCount = typeof value.active_device_count === "number"
    && Number.isInteger(value.active_device_count)
    && value.active_device_count >= 0
    ? value.active_device_count
    : null;
  const validWebPushMetadata = kind === "web_push"
    ? deviceKey !== null
      && /^[A-Za-z0-9_-]{43}$/.test(deviceKey)
      && activeDeviceCount !== null
    : value.device_key === null && value.active_device_count === null;
  if (
    id === null
    || kind === null
    || !isNotificationChannelKind(kind)
    || name === null
    || typeof value.enabled !== "boolean"
    || typeof value.configured !== "boolean"
    || !validWebPushMetadata
    || !awareTimestamp(value.created_at)
    || !awareTimestamp(value.updated_at)
  ) {
    throw new ApiError("알림 채널 응답 형식을 확인할 수 없습니다.");
  }
  return {
    id,
    kind,
    name,
    enabled: value.enabled,
    configured: value.configured,
    deviceKey,
    activeDeviceCount,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  };
}

function mapNotificationChannels(value: unknown): NotificationChannel[] {
  if (!Array.isArray(value)) {
    throw new ApiError("알림 채널 목록 응답 형식을 확인할 수 없습니다.");
  }
  return value.map(mapNotificationChannel);
}

export async function fetchNotificationChannels(): Promise<NotificationChannel[]> {
  return mapNotificationChannels(await request("/notifications/channels"));
}

export async function createNotificationChannel(
  payload: NotificationChannelCreatePayload,
): Promise<NotificationChannel> {
  return mapNotificationChannel(await request("/notifications/channels", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
}

export async function updateNotificationChannel(
  id: string,
  payload: NotificationChannelUpdatePayload,
): Promise<NotificationChannel> {
  return mapNotificationChannel(await request(`/notifications/channels/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  }));
}

export async function deleteNotificationChannel(id: string): Promise<void> {
  await request(`/notifications/channels/${id}`, { method: "DELETE" });
}

export async function testNotificationChannel(id: string): Promise<QueuedNotification> {
  const payload = await request(`/notifications/channels/${id}/test-send`, { method: "POST" });
  const eventId = isRecord(payload) ? requiredString(payload.event_id) : null;
  if (!isRecord(payload) || payload.queued !== true || eventId === null) {
    throw new ApiError("시험 알림 응답 형식을 확인할 수 없습니다.");
  }
  return { queued: true, eventId };
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
    return {
      support: "unsupported",
      permission: "default",
      subscribed: false,
      deviceKey: null,
    };
  }
  if (window.isSecureContext === false) {
    return {
      support: "insecure",
      permission: Notification.permission,
      subscribed: false,
      deviceKey: null,
    };
  }
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = registration
    ? await registration.pushManager.getSubscription()
    : null;
  return {
    support: "supported",
    permission: Notification.permission,
    subscribed: subscription !== null,
    deviceKey: subscription === null ? null : await webPushDeviceKey(subscription.endpoint),
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
  const publicKey = isRecord(payload) ? requiredString(payload.public_key) : null;
  if (publicKey !== null && /^[A-Za-z0-9_-]+$/.test(publicKey)) {
    try {
      const decoded = fromBase64Url(publicKey);
      if (decoded.length === 65 && decoded[0] === 4) return publicKey;
    } catch {
      // Normalize malformed base64url data into the public API boundary error below.
    }
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

async function webPushDeviceKey(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(endpoint),
  );
  const base64 = btoa(String.fromCharCode(...new Uint8Array(digest)));
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export async function connectBrowserPush(
  name = "이 브라우저",
): Promise<NotificationChannel> {
  requireBrowserPushSupport();
  // iOS/iPadOS only keeps the Web Push permission prompt inside the direct user gesture.
  // Do not put network or service-worker awaits before this request.
  const permission = await Notification.requestPermission();
  if (permission === "denied") {
    throw new ApiError(
      "OS 알림 권한이 차단되어 있습니다. 브라우저 사이트 설정에서 알림을 허용해 주세요.",
    );
  }
  if (permission !== "granted") {
    throw new ApiError("OS 알림 권한을 허용해야 연결할 수 있습니다.");
  }
  const publicKey = publicKeyFrom(await request("/notifications/web-push/public-key"));
  const existingRegistration = await navigator.serviceWorker.getRegistration();
  if (!existingRegistration) await navigator.serviceWorker.register("/sw.js");
  const registration = await waitForServiceWorkerRegistration();
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
  return createNotificationChannel({ kind: "web_push", ...payload });
}
