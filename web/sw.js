/**
 * Service worker: makes the interface load offline.
 *
 * API calls are NEVER cached — a stale "connected" status would be a lie.
 */
/* Bump this on every interface release. The fetch handler answers from the
   cache first, so an already-installed PWA would keep serving the old shell
   until a new cache name makes install() re-fetch it and activate() delete the
   previous one. */
const CACHE = 'jarvis-v2-1-interface';
const SHELL = [
  '/',
  '/index.html',
  '/css/jarvis.css',
  '/manifest.webmanifest',
  '/icons/icon.svg',
  '/js/app.js',
  '/js/core/api.js',
  '/js/core/bus.js',
  '/js/core/state.js',
  '/js/avatar/index.js',
  '/js/avatar/rings.js',
  '/js/avatar/face.js',
  '/js/i18n/index.js',
  '/js/i18n/ro.js',
  '/js/i18n/en.js',
  '/js/i18n/fr.js',
  '/js/i18n/de.js',
  '/js/i18n/es.js',
  '/js/i18n/it.js',
  '/js/ui/armory.js',
  '/js/ui/chat.js',
  '/js/ui/eyes.js',
  '/js/ui/magnifier.js',
  '/js/ui/mic.js',
  '/js/ui/modes.js',
  '/js/ui/power.js',
  '/js/ui/settings.js',
  '/js/ui/status.js',
  '/js/ui/toast.js',
  '/js/ui/voice.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL).catch((err) => console.warn('[sw] partial cache', err)))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;      // always live

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
});
