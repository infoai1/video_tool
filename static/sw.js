// Minimal PWA service worker: network-first for pages, offline fallback page.
// Deliberately does NOT cache app pages (content is dynamic) or YouTube (cross-origin, ToS).
// ponytail: no precache of app shell; add stale-while-revalidate only if load speed matters.
const CACHE = 'sm-v1';
const OFFLINE = '/offline.html';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll([OFFLINE, '/static/icon-192.png'])));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const req = e.request;
  // Only handle top-level page navigations on our own origin.
  if (req.mode === 'navigate' && new URL(req.url).origin === self.location.origin) {
    e.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
  }
});
