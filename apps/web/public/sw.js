const CACHE = "railwait-shell-v5";
const APP_SHELL_URL = "/";
const SHELL = [
  "/",
  "/manifest.webmanifest?v=2",
  "/icons/app-icon-any-512-v2.png",
  "/icons/favicon-16.png",
  "/icons/favicon-32.png",
  "/icons/app-icon-maskable-512.png",
  "/icons/notification-badge-96.png",
];
const APP_NOTIFICATION_MESSAGE = "railwait:notification";
const APP_UI_PATHS = new Set(["", "new", "reservations", "settings"]);
const WATCH_STATUSES = new Set([
  "draft",
  "scheduled",
  "watching",
  "official_waitlist",
  "seat_found",
  "reserving",
  "payment_required",
  "completed",
  "paused",
  "cooldown",
  "auth_required",
  "expired",
  "failed",
]);
const CRITICAL_STATUSES = new Set([
  "official_waitlist",
  "seat_found",
  "reserving",
  "payment_required",
  "auth_required",
]);
const STATUS_TITLES = {
  draft: "대기 설정 중",
  scheduled: "공식 확인 대기",
  watching: "열차 대기 확인",
  official_waitlist: "공식 예약대기 가능",
  seat_found: "좌석 발견",
  reserving: "예매 진행 중",
  payment_required: "결제 필요",
  completed: "결제 완료",
  paused: "대기 일시정지",
  cooldown: "잠시 후 다시 확인",
  auth_required: "로그인 확인 필요",
  expired: "대기 종료",
  failed: "대기 확인 실패",
};

function record(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : {};
}

function text(value, maxLength = 500) {
  return typeof value === "string" && value.trim()
    ? value.trim().slice(0, maxLength)
    : null;
}

function watchStatus(value) {
  return typeof value === "string" && WATCH_STATUSES.has(value) ? value : null;
}

function readPushPayload(event) {
  try {
    return record(event.data?.json());
  } catch {
    return {};
  }
}

function appScopeUrl() {
  return new URL(self.registration.scope || "/", self.location.origin);
}

function safeAppUrl(value) {
  const fallback = appScopeUrl();
  try {
    const candidate = new URL(typeof value === "string" ? value : "/", fallback);
    if (
      candidate.origin !== fallback.origin
      || candidate.username
      || candidate.password
      || candidate.pathname !== fallback.pathname
    ) return fallback.href;
    return candidate.href;
  } catch {
    return fallback.href;
  }
}

function notificationHint(kind, data) {
  const watchId = text(data.watchId ?? data.watch_id, 128);
  const status = watchStatus(data.status);
  if (watchId === null || status === null) return null;
  return { type: APP_NOTIFICATION_MESSAGE, kind, watchId, status };
}

function postHint(client, hint) {
  if (hint !== null && typeof client.postMessage === "function") client.postMessage(hint);
}

function cacheableResponse(response) {
  return response && response.ok && ["basic", "default"].includes(response.type);
}

function navigationNetworkResponse(event) {
  return Promise.resolve(event.preloadResponse)
    .then((preloaded) => preloaded || fetch(event.request));
}

function updateCachedResponse(cacheKey, responsePromise) {
  return responsePromise.then(async (response) => {
    if (cacheableResponse(response)) {
      const cache = await caches.open(CACHE);
      await cache.put(cacheKey, response.clone());
    }
    return response;
  });
}

function networkFirstNavigation(event) {
  const networkResponse = updateCachedResponse(
    APP_SHELL_URL,
    navigationNetworkResponse(event),
  );
  return networkResponse.catch(async (error) => {
    const cached = await caches.match(APP_SHELL_URL);
    if (cached) return cached;
    throw error;
  });
}

function cachedStaticAsset(event) {
  const networkResponse = fetch(event.request);
  const update = updateCachedResponse(event.request, networkResponse);
  event.waitUntil(update.then(() => undefined).catch(() => undefined));
  return caches.match(event.request).then((cached) => cached || networkResponse);
}

function appClient(client) {
  try {
    const url = new URL(client.url);
    const scope = appScopeUrl();
    if (url.origin !== self.location.origin) return false;

    const scopePath = scope.pathname.endsWith("/") ? scope.pathname : `${scope.pathname}/`;
    const scopeRoot = scopePath === "/" ? "/" : scopePath.slice(0, -1);
    if (url.pathname === scopeRoot) return true;
    if (!url.pathname.startsWith(scopePath)) return false;

    const relativePath = url.pathname.slice(scopePath.length).replace(/\/$/, "");
    return APP_UI_PATHS.has(relativePath);
  } catch {
    return false;
  }
}

async function notifyVisibleClients(hint) {
  if (hint === null) return;
  const clientList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of clientList) {
    if (appClient(client) && client.visibilityState === "visible") postHint(client, hint);
  }
}

const CLIENT_SURFACE_TIMEOUT_MS = 3000;

function attemptClientAction(action) {
  try {
    return Promise.resolve(action()).catch(() => null);
  } catch {
    return Promise.resolve(null);
  }
}

function withSurfaceDeadline(work) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), CLIENT_SURFACE_TIMEOUT_MS);
    work.then((value) => {
      clearTimeout(timer);
      resolve(value);
    });
  });
}

// 일부 Android 빌드는 백그라운드 PWA의 notificationclick focus()가 성공 응답 뒤에도 전면
// 전환을 만들지 못하거나 응답을 멈추는 회귀가 있다. focus 결과가 제한 시간 안에 visible
// client로 확인될 때만 성공으로 보고, 아니면 같은 client를 동일 출처 내부 URL로 navigate해
// 전면 전환을 복구한다. 두 경로가 모두 실패한 경우에만 openWindow가 마지막 복구 경로다.
async function bringClientToFront(client, targetUrl) {
  const focused = await withSurfaceDeadline(attemptClientAction(() => client.focus()));
  if (focused && focused.visibilityState === "visible") return true;
  if (typeof client.navigate !== "function") return false;
  const navigated = await withSurfaceDeadline(
    attemptClientAction(() => client.navigate(targetUrl)),
  );
  if (!navigated) return false;
  await withSurfaceDeadline(attemptClientAction(() => navigated.focus()));
  return true;
}

async function focusOrOpenApp(data) {
  const targetUrl = safeAppUrl(data.url);
  const hint = notificationHint("click", data);
  const clientList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  const existing = clientList.find(appClient);
  if (existing) {
    postHint(existing, hint);
    if (await bringClientToFront(existing, targetUrl)) return;
  }
  const opened = await self.clients.openWindow(targetUrl);
  if (opened) postHint(opened, hint);
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  const navigationPreload = self.registration.navigationPreload;
  event.waitUntil(
    Promise.all([
      caches.keys().then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)),
      )),
      self.clients.claim(),
      navigationPreload ? navigationPreload.enable() : Promise.resolve(),
    ]),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (event.request.mode === "navigate") {
    // 온라인 배포 직후에는 새 index와 새 해시 bundle을 한 세대로 받는다. 네트워크가
    // 실제로 실패할 때만 기존 shell을 사용해 구 index + 삭제된 bundle 조합을 피한다.
    event.respondWith(networkFirstNavigation(event));
    return;
  }

  if (["font", "image", "script", "style"].includes(event.request.destination)) {
    event.respondWith(cachedStaticAsset(event));
  }
});

self.addEventListener("push", (event) => {
  const payload = readPushPayload(event);
  const status = watchStatus(payload.status);
  const watchId = text(payload.watch_id, 128);
  const title = text(payload.title, 100) ?? (status === null ? null : STATUS_TITLES[status]) ?? "레일웨잇";
  const body = text(payload.body) ?? text(payload.message) ?? "기차 대기 상태가 변경되었습니다.";
  const url = safeAppUrl(payload.url);
  const tag = watchId === null ? null : `railwait-watch:${watchId}`;
  const critical = status !== null && CRITICAL_STATUSES.has(status);
  const data = { url, watchId, status };
  const hint = notificationHint("push", data);
  event.waitUntil(
    Promise.all([
      self.registration.showNotification(title, {
        body,
        icon: "/icons/app-icon-any-512-v2.png",
        badge: "/icons/notification-badge-96.png",
        lang: "ko-KR",
        data,
        ...(tag === null ? {} : { tag, renotify: true }),
        ...(critical ? { requireInteraction: true, vibrate: [200, 100, 200] } : {}),
      }),
      notifyVisibleClients(hint).catch(() => undefined),
    ]),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(focusOrOpenApp(record(event.notification.data)));
});
