const VERSION = "court-watch-live-2026-10-30-2";
self.addEventListener("install", event => {
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});
// Deliberately do not cache the app shell: always let the network provide the
// current HTML and code, avoiding stale Home Screen installs after updates.
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (event.request.mode === "navigate" || url.pathname.endsWith(".html")) {
    event.respondWith(fetch(new Request(event.request, { cache: "no-store" })));
  }
});
