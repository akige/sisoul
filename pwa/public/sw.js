// sisoul PWA service worker
// 策略: 静态资产 cache-first, /sisoul/* API network-first (offline → 503)
const CACHE_VERSION = "sisoul-v1";
const SHELL_CACHE = ["/", "/manifest.json", "/icon-192.svg", "/icon-512.svg"];

self.addEventListener("install", (evt) => {
  self.skipWaiting();
  evt.waitUntil(
    caches.open(CACHE_VERSION).then((c) => c.addAll(SHELL_CACHE))
  );
});

self.addEventListener("activate", (evt) => {
  evt.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (evt) => {
  const url = new URL(evt.request.url);
  // API requests: network-first, offline 503
  if (url.pathname.startsWith("/sisoul/")) {
    evt.respondWith(
      fetch(evt.request).catch(
        () =>
          new Response(JSON.stringify({ error: "offline", status: 503 }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          })
      )
    );
    return;
  }
  // Navigation: network-first → shell fallback
  if (evt.request.mode === "navigate") {
    evt.respondWith(
      fetch(evt.request).catch(() => caches.match("/"))
    );
    return;
  }
  // Static assets: cache-first
  evt.respondWith(
    caches.match(evt.request).then(
      (cached) => cached || fetch(evt.request).then((resp) => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(evt.request, clone));
        }
        return resp;
      })
    )
  );
});
