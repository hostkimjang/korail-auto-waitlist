const CACHE = "railwait-shell-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icons/app-icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || new URL(event.request.url).pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request).then((cached) => cached || caches.match("/"))));
});

self.addEventListener("push", (event) => {
  const payload = event.data?.json() ?? {};
  const statusTitles = {
    scheduled: "공식 확인 대기",
    watching: "열차 대기 확인",
    payment_required: "결제 필요",
    completed: "결제 완료",
    failed: "대기 확인 실패",
  };
  const title = payload.title ?? statusTitles[payload.status] ?? "레일웨잇";
  const body = payload.body ?? payload.message ?? "기차 대기 상태가 변경되었습니다.";
  const url = payload.url ?? payload.official_booking_url ?? "/";
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/icons/app-icon-512.png",
      badge: "/icons/app-icon-512.png",
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data?.url ?? "/"));
});
